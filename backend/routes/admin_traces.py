# -*- coding: utf-8 -*-
"""运营后台 · Agent 可观测性 API

按需求查看完整 Agent 流程：阶段、LLM 调用、LLM 参数与返回值。

数据来源是 `agent_events`（事件索引）+ `trace_message_blobs`（内容寻址正文）
+ `agent_payloads`（明细），不扫描原始日志文件。

性能约定：列表页只碰 `agent_events`，明细（response / 完整 messages）在点开
某个事件时才查 `agent_payloads` 与 blob，避免把数十 KB 正文拉进列表。
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from flask import request, jsonify
from sqlalchemy import func, case, or_, false

from factory import app, logger
from utils.db import transactional_db
from utils.admin_guard import admin_required


def _row_to_dict(row, keys):
    """把聚合查询结果转成 JSON 友好的 dict。"""
    return {k: getattr(row, k, None) for k in keys}


# 状态聚合桶 —— 筛选条只给「进行中 / 已完成 / 失败」三档，而库里状态有 8 种。
# 定义在这一处，供两侧共用：/stats 用它把状态折成桶（出 chip 上的计数），
# 列表接口用它把桶展开成状态（出查询条件）。
# 各写一份的后果很具体：上个月用 inner join 算状态分布，chip 计数之和比
# 「覆盖需求」少 1，点进去还多出一个永远为空的「未知」—— 数字对不上是最伤
# 信任的一类不一致，而根因就是两个地方各自解释同一份数据。
STATUS_BUCKETS: dict = {
    'active': ('pending', 'planning', 'processing', 'needs_user_input'),
    'done': ('finished', 'finished_with_issues'),
    'failed': ('failed', 'interrupted'),
}
_BUCKET_OF = {s: b for b, ss in STATUS_BUCKETS.items() for s in ss}



def _iso_utc(dt):
    """把库里存的 UTC naive datetime 输出成**带时区**的 ISO 串。

    不加 `+00:00` 的话，前端 `new Date(...)` 会按本地时区解析，界面上每个时间
    都差出一个时区偏移（本机实测差 8 小时）：绝对时间显示错，相对时间也跟着错
    （「9 小时前」其实只有 1 小时）。这类错误肉眼很难发现，只有对着两处数字
    比才会暴露。
    """
    return None if dt is None else dt.replace(tzinfo=timezone.utc).isoformat()


def _hit_rate(cached: int, reported_tokens_in: int):
    """缓存命中率 = 命中缓存的 input token / **上报过缓存信息的** input token。

    ⚠️ 分母既不是 `tokens_in + tokens_out`，也不是全部 `tokens_in`：
    - 加上 tokens_out：命中量本身就是 input 的子集，混进输出会让命中率被
      系统性算低，而这个数字看起来依然「像那么回事」，不会被任何人发现。
    - 用全部 tokens_in：没带缓存字段的那些调用会撑大分母、分子不动，
      命中率同样被算低 —— 实测 583 次有 usage 的调用里有 134 次没带缓存字段。
    只把「确实上报了」的调用计入分母，得到的才是这批调用的真实命中率。
    样本量（reported_calls）必须一并展示，否则读者无从判断这个率的可信度。

    分母为 0 时返回 None 而不是 0：0% 的语义是「查过，一次没命中」，
    「压根没数据」不该显示成同一个数字。
    """
    if not reported_tokens_in:
        return None
    return round(cached / reported_tokens_in, 4)


# ==================== 时间范围 ====================
#
# 筛选条给的四档：当天 / 近 3 天 / 近 7 天 / 近 30 天。
# 口径统一为**自然日对齐**：近 N 天 = 本地今天 0 点往前推 N-1 天（含今天共 N 个
# 日历日），「当天」就是 N=1。不按滚动的 now-N*24h 算 —— 那样「近 3 天」的边界
# 落在当天的某个时刻，同一个需求在上午和下午筛选结果不同，数字没法复述。
# 日界取**本地** 0 点（与 stats 里「今日完成需求」同一个日界），不硬编码时区，
# 部署机器在哪个时区，业务日界就在哪个时区。
#
# ⚠️ 列表与 /stats 必须共用这一个函数：两边各写一份时间窗，chip 上的计数就会
# 和点进去的行数对不上（「写着 17、点进去 15」是最伤信任的一类不一致）。
def _range_since(raw):
    """把时间范围参数解析成 UTC naive 下界；返回 None 表示不限。

    接受：`today`（当天）与数字（近 N 天）；空值 / `all` / 0 / 非法值一律
    当作「不限」—— 查询参数是用户输入，不能因为一个脏值把整个列表打挂。
    """
    key = str(raw if raw is not None else '').strip().lower()
    if key in ('', 'all', 'none', '0'):
        return None

    # 本地 0 点 → UTC。datetime.now() 是本地时间，astimezone 会把缺时区的
    # naive 值按本地解释，因此这条链路在任意部署时区下都成立。
    local_midnight = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc).replace(tzinfo=None)

    if key in ('today', 'day', '1', '1d'):
        return local_midnight
    try:
        n = int(key.rstrip('d'))
    except ValueError:
        return None
    if n <= 1:
        return local_midnight
    return local_midnight - timedelta(days=n - 1)


def _created_expr(requirement_ts, fallback_first_event_ts):
    """「创建时间」的统一表达式：需求创建时间，孤儿事件回退到首条事件时间。

    孤儿事件（requirement_id 在 requirements 里查不到，实测有 requirement_id=0
    的一批）没有创建时间。不回退的话它们会整批堆在排序末尾、且任何时间筛选
    都筛不到，与 /stats 的「未知」桶对不上。
    """
    return func.coalesce(requirement_ts, fallback_first_event_ts)


def _range_label(raw):
    """把范围参数翻成中文短标签（列表副标题与 KPI 的窗口标注共用）。"""
    key = str(raw if raw is not None else '').strip().lower()
    if key in ('today', 'day', '1', '1d'):
        return '当天'
    if key in ('', 'all', 'none', '0'):
        return ''
    try:
        n = int(key.rstrip('d'))
    except ValueError:
        return ''
    return f'近 {n} 天' if n > 1 else '当天'


# ==================== 列表 ====================

@app.route('/api/admin/traces', methods=['GET'])
@admin_required
def admin_trace_list():
    """轨迹列表 —— 按需求聚合，从事件索引 GROUP BY 得出，不扫明细。

    query: page, page_size, kind, status, bucket(状态桶), range(时间范围),
           sort(created|active), q(需求标题关键字)

    默认排序：**创建时间倒序**。此前按「最近活跃」倒序，跑着的需求会不断把
    自己顶到第一行，正在看的表会自己重排 —— 观感就是「排序很乱」。
    """
    from models.models import AgentEvent, Requirement, User

    page = max(1, int(request.args.get('page', 1)))
    page_size = min(100, max(1, int(request.args.get('page_size', 20))))
    kind = request.args.get('kind')
    status = request.args.get('status')
    bucket = (request.args.get('bucket') or '').strip()
    # 时间范围：`range` 是新参数（today / 3 / 7 / 30），`days` 作为旧参数保留兼容
    raw_range = request.args.get('range')
    if raw_range is None:
        raw_range = request.args.get('days')
    since = _range_since(raw_range)
    sort = (request.args.get('sort') or 'created').strip().lower()
    q = (request.args.get('q') or '').strip()

    with transactional_db() as db:
        base = db.query(
            AgentEvent.requirement_id,
            func.count(AgentEvent.id).label('event_count'),
            func.sum(case((AgentEvent.kind == 'llm_turn', 1), else_=0)).label('llm_calls'),
            func.sum(case((AgentEvent.kind == 'tool_call', 1), else_=0)).label('tool_calls'),
            func.count(func.distinct(AgentEvent.turn_index)).label('turn_count'),
            func.sum(AgentEvent.tokens_in).label('tokens_in'),
            func.sum(AgentEvent.tokens_out).label('tokens_out'),
            func.sum(AgentEvent.cost).label('cost'),
            func.min(AgentEvent.ts).label('started_at'),
            func.max(AgentEvent.ts).label('last_event_at'),
            func.sum(AgentEvent.duration_ms).label('total_duration_ms'),
            func.sum(case((AgentEvent.status == 'error', 1), else_=0)).label('error_count'),
        ).group_by(AgentEvent.requirement_id)

        if kind:
            # 只返回包含该类型事件的需求
            base = base.having(
                func.sum(case((AgentEvent.kind == kind, 1), else_=0)) > 0)

        rows = base.subquery()

        # 显式列出每一列 —— 直接 db.query(rows, title, status) 会把 rows 的
        # 所有列全部展开，解包时列数对不上。
        query = db.query(
            rows.c.requirement_id.label('requirement_id'),
            rows.c.event_count.label('event_count'),
            rows.c.llm_calls.label('llm_calls'),
            rows.c.tool_calls.label('tool_calls'),
            rows.c.turn_count.label('turn_count'),
            rows.c.tokens_in.label('tokens_in'),
            rows.c.tokens_out.label('tokens_out'),
            rows.c.cost.label('cost'),
            rows.c.started_at.label('started_at'),
            rows.c.last_event_at.label('last_event_at'),
            rows.c.total_duration_ms.label('total_duration_ms'),
            rows.c.error_count.label('error_count'),
            Requirement.title.label('title'),
            Requirement.status.label('status'),
            # 创建时间：列表默认排序与「当天 / 近 N 天」筛选都用它，
            # 与排序同一口径（此前排序看最近活跃、筛选也看最近活跃，
            # 跑着的需求会不停换位置，整张表看着就是乱的）。
            Requirement.create_time.label('created_at'),
            # 创建人：看这一列是为了回答「谁在用它」。走 outerjoin —— 孤儿事件
            # 没有 Requirement，自然也没有 User，不能因为查不到人就丢掉整行。
            User.username.label('creator'),
        ).outerjoin(Requirement, Requirement.id == rows.c.requirement_id
                    ).outerjoin(User, User.id == Requirement.user_id)

        if q:
            if q.isdigit():
                query = query.filter(
                    or_(Requirement.title.like(f'%{q}%'),
                        rows.c.requirement_id == int(q)))
            else:
                query = query.filter(Requirement.title.like(f'%{q}%'))

        # 按需求状态筛选。状态是 Requirement 的字段（不是事件字段），所以过滤
        # 发生在 outerjoin 之后。「未知」特指找不到 Requirement 行的孤儿事件
        # （实测有 requirement_id=0 的一批），与 /stats 的 by_status 同口径 ——
        # 否则 chip 上写着「未知 1」，点进去却是 0 行。
        if status:
            if status == 'unknown':
                query = query.filter(Requirement.id.is_(None))
            else:
                query = query.filter(Requirement.status == status)

        # 按桶筛选（筛选条的 chip 走这条）。桶 → 状态列表取自模块级
        # STATUS_BUCKETS，与 /stats 出计数时用的是同一份定义 ——
        # chip 上的数字与点进去的行数因此必然相等。
        if bucket:
            if bucket == 'unknown':
                query = query.filter(Requirement.id.is_(None))
            elif bucket in STATUS_BUCKETS:
                query = query.filter(Requirement.status.in_(STATUS_BUCKETS[bucket]))

        # 时间范围：按**创建时间**收窄，与默认排序同一口径。
        # 孤儿事件（查不到 Requirement）没有创建时间，回退到首条事件时间 ——
        # 否则它们永远落在窗口外，与 /stats 的「未知」桶对不上。
        if since is not None:
            query = query.filter(
                _created_expr(Requirement.create_time, rows.c.started_at) >= since)

        total = query.count()

        # 排序：默认创建时间倒序（新的在前）；sort=active 切回最近活跃倒序。
        # 两个方向都必须显式 nullslast —— PG 的 DESC 默认把 NULL 排在最前，
        # 孤儿行会整批压在真正的第一行之上。
        if sort == 'active':
            order = rows.c.last_event_at.desc().nullslast()
        else:
            order = _created_expr(
                Requirement.create_time, rows.c.started_at).desc().nullslast()
        items = query.order_by(order, rows.c.requirement_id.desc()).offset(
            (page - 1) * page_size).limit(page_size).all()

        data = []
        for r in items:
            started, last = r.started_at, r.last_event_at
            duration = int((last - started).total_seconds() * 1000) if (started and last) else (
                int(r.total_duration_ms or 0))
            data.append({
                'requirement_id': r.requirement_id,
                'title': r.title or f'需求 #{r.requirement_id}',
                'creator': r.creator or '',
                'status': r.status or 'unknown',
                'bucket': _BUCKET_OF.get(r.status or 'unknown', 'unknown'),
                'turn_count': int(r.turn_count or 1),
                'event_count': int(r.event_count or 0),
                'llm_calls': int(r.llm_calls or 0),
                'tool_calls': int(r.tool_calls or 0),
                'tokens_in': int(r.tokens_in or 0),
                'tokens_out': int(r.tokens_out or 0),
                'tokens': int(r.tokens_in or 0) + int(r.tokens_out or 0),
                'cost': round(float(r.cost or 0), 6),
                'duration_ms': duration,
                'started_at': _iso_utc(started),
                'last_event_at': _iso_utc(last),
                'created_at': _iso_utc(r.created_at),
                'error_count': int(r.error_count or 0),
            })

        return jsonify({
            'items': data,
            'total': total,
            'page': page,
            'page_size': page_size,
            'range': _range_label(raw_range) or 'all',
            'sort': sort if sort == 'active' else 'created',
        })


# ==================== 时间线 ====================

# ==================== 查询与组装（唯一定义处）====================
#
# 下面这几个 build_* 不碰 request / jsonify，只接受一个 session —— 于是同一段
# 查询能被两处复用：运营库里的需求轨迹，以及一次评测运行的某道题（数据在
# eval/runs/<run_id>/trace.db）。
#
# 为什么必须共用而不是各写一份：前端是同一套渲染组件（TraceTimeline），它吃的
# 就是这里的字段。两份实现一旦分叉，字段就会漂移，而漂移在界面上只表现为
# 「有个地方显示不出来」，排查成本远高于当初省下的抽取。


@contextmanager
def _use_db(session):
    """把已有 session 包成 contextmanager。

    存在的理由：同一段查询逻辑要能同时跑在 `transactional_db()`（生产）和
    评测库的 session 上。把「从哪拿库」这一件事抽出去，查询与组装才能保持
    一份实现。
    """
    yield session


def build_events_payload(requirement_id, session, *, turn=None, kind=None,
                         limit=500, offset=0) -> dict:
    """单条轨迹的完整事件时间线。

    seq 是需求内全局递增序号，不依赖时间戳精度（同一秒内多个事件也能正确排序）。
    query 参数由调用方解析（这里不碰 request）。
    """
    from models.models import AgentEvent

    with _use_db(session) as db:
        query = db.query(AgentEvent).filter(
            AgentEvent.requirement_id == requirement_id)
        if turn is not None and turn != '':
            query = query.filter(AgentEvent.turn_index == int(turn))
        if kind:
            query = query.filter(AgentEvent.kind == kind)

        total = query.count()
        rows = query.order_by(AgentEvent.seq).offset(offset).limit(limit).all()

        items = [{
            'id': r.id,
            'seq': r.seq,
            'turn_index': r.turn_index,
            'iteration': r.iteration,
            'ts': _iso_utc(r.ts),
            'kind': r.kind,
            'stage': r.stage,
            'label': r.label,
            'status': r.status,
            'model': r.model,
            'duration_ms': r.duration_ms,
            'tokens_in': r.tokens_in,
            'tokens_out': r.tokens_out,
            'cost': round(float(r.cost or 0), 6),
            'has_payload': bool(r.payload_id),
            'message_count': (r.meta or {}).get('message_count', 0),
            'meta': {k: v for k, v in (r.meta or {}).items()
                     if k != 'message_count'},
        } for r in rows]

        return {
            'requirement_id': requirement_id,
            'items': items,
            'total': total,
        }


@app.route('/api/admin/traces/<int:requirement_id>/events', methods=['GET'])
@admin_required
def admin_trace_events(requirement_id):
    """单个需求的完整事件时间线 —— 按 seq 排序。

    query: turn_index, kind, limit, offset
    """
    turn = request.args.get('turn_index')
    kind = request.args.get('kind')
    limit = min(1000, max(1, int(request.args.get('limit', 500))))
    offset = max(0, int(request.args.get('offset', 0)))

    with transactional_db() as db:
        return jsonify(build_events_payload(
            requirement_id, db,
            turn=turn, kind=kind, limit=limit, offset=offset))


# ==================== 轮次 ====================

def build_turns_payload(requirement_id, session) -> dict:
    """轮次 + 汇总 + 阶段时间线 + 按模型 —— 时间线的唯一实现。

    驱动详情页顶部的「轮次切换器」。turn_index 在**写入时**确定（存入
    agent_events），不做查询时派生：dialogue_history 里数 role='user'
    的方式既脆弱又慢。
    """
    from models.models import AgentEvent

    with _use_db(session) as db:
        rows = db.query(
            AgentEvent.turn_index,
            func.count(AgentEvent.id).label('event_count'),
            func.sum(case((AgentEvent.kind == 'llm_turn', 1), else_=0)).label('llm_calls'),
            func.min(AgentEvent.ts).label('started_at'),
            func.max(AgentEvent.ts).label('ended_at'),
            func.sum(AgentEvent.duration_ms).label('total_duration_ms'),
        ).filter(
            AgentEvent.requirement_id == requirement_id
        ).group_by(AgentEvent.turn_index).order_by(AgentEvent.turn_index).all()

        items = []
        for r in rows:
            started, ended = r.started_at, r.ended_at
            duration = int((ended - started).total_seconds() * 1000) if (started and ended) else 0
            items.append({
                'turn_index': int(r.turn_index or 0),
                'mode': '初次生成' if int(r.turn_index or 0) == 0 else f'第 {int(r.turn_index or 0) + 1} 轮对话',
                'event_count': int(r.event_count or 0),
                'llm_calls': int(r.llm_calls or 0),
                'started_at': _iso_utc(started),
                'ended_at': _iso_utc(ended),
                'duration_ms': duration,
            })

        # 需求级汇总 —— 详情页顶部的汇总指标。轮次可以单选切换，但「这个需求总共
        # 花了多少」必须一眼可见，否则得自己把各轮加起来。
        # 耗时取 max(ts) - min(ts) 的**墙钟跨度**，与列表页 duration_ms 同口径；
        # 不是把各事件的 duration_ms 相加 —— 那样会把并行/包含的耗时重复计入。
        tot = db.query(
            func.count(AgentEvent.id),
            func.sum(case((AgentEvent.kind == 'llm_turn', 1), else_=0)),
            func.sum(case((AgentEvent.kind == 'tool_call', 1), else_=0)),
            func.sum(case((AgentEvent.status == 'error', 1), else_=0)),
            func.sum(AgentEvent.tokens_in),
            func.sum(AgentEvent.tokens_out),
            func.sum(AgentEvent.cost),
            func.min(AgentEvent.ts),
            func.max(AgentEvent.ts),
            # ⚠️ 新字段一律追加在末尾：插在中间会让 tot[7]/tot[8] 的时间列整体
            # 错位，而 SQLAlchemy 不会报错，只会让 duration 静默算成 0。
            func.sum(AgentEvent.cached_tokens),
            # 命中率的分母与样本量只含「上报过缓存信息」的调用（见 _hit_rate）
            func.sum(case((AgentEvent.cached_tokens.isnot(None),
                           AgentEvent.tokens_in), else_=0)),
            func.sum(case((AgentEvent.cached_tokens.isnot(None), 1), else_=0)),
        ).filter(AgentEvent.requirement_id == requirement_id).first()

        first_ts, last_ts = tot[7], tot[8]
        duration_ms = int((last_ts - first_ts).total_seconds() * 1000) \
            if (first_ts and last_ts) else 0

        # ---- 阶段时间线 ----
        # 这里**按阶段聚合**，不是按连续段切分：横条要回答的是「每个阶段总共
        # 花了多久」，聚合才对。「验收→修复→再验收」的因果顺序由左栏时间线
        # （按连续段分组）保证 —— 两种视图各回答一个问题，不重复。
        stage_rows = db.query(
            AgentEvent.stage,
            func.count(AgentEvent.id),
            func.sum(case((AgentEvent.kind == 'llm_turn', 1), else_=0)),
            func.sum(AgentEvent.duration_ms),
            func.min(AgentEvent.ts),
            func.max(AgentEvent.ts),
        ).filter(
            AgentEvent.requirement_id == requirement_id,
            AgentEvent.stage.isnot(None),
        ).group_by(AgentEvent.stage).all()

        stages = []
        for s, cnt, llms, dur_sum, s_first, s_last in stage_rows:
            span = int((s_last - s_first).total_seconds() * 1000) \
                if (s_first and s_last) else 0
            stages.append({
                'stage': s,
                'events': int(cnt or 0),
                'llm_calls': int(llms or 0),
                # 只有一条里程碑事件的阶段（如「完成」）跨度是 0，退回该阶段
                # 事件耗时之和 —— 否则横条宽度为 0，在界面上等于不存在
                'ms': span or int(dur_sum or 0),
                'started_at': _iso_utc(s_first),
                'ended_at': _iso_utc(s_last),
            })

        # ---- 按模型：调用数与成本 ----
        # 「这个需求用了哪几个模型、各调了几次」——换模型排查时第一个要看的问题，
        # 而 model 是事件自带字段，不用另算。
        model_rows = db.query(
            AgentEvent.model,
            func.count(AgentEvent.id),
            func.sum(AgentEvent.cost),
        ).filter(
            AgentEvent.requirement_id == requirement_id,
            AgentEvent.kind == 'llm_turn',
            AgentEvent.model.isnot(None),
        ).group_by(AgentEvent.model).order_by(
            func.count(AgentEvent.id).desc()).all()
        by_model = [{'model': m, 'calls': int(c or 0),
                     'cost': round(float(cost or 0), 6)}
                    for m, c, cost in model_rows]

        # ---- 首个 LLM 调用耗时 ----
        # 字段名如实叫 first_llm_ms：我们没有 TTFT 埋点，拿不到真正的「首 Token」。
        # 用 first_token 命名就等于在界面上宣称一个没测过的量 —— 宁可名字长一点。
        first_llm = db.query(AgentEvent.duration_ms).filter(
            AgentEvent.requirement_id == requirement_id,
            AgentEvent.kind == 'llm_turn',
            AgentEvent.duration_ms.isnot(None),
        ).order_by(AgentEvent.seq).limit(1).scalar()

        # ---- 重试 / 修复 ----
        # 轮次结论直接取 verify 里程碑写入的 verdict，不靠猜。按 seq 排序就是
        # 验收发生的顺序，最后一条即最终判定。
        verdicts = [m.get('verdict') for (m,) in db.query(AgentEvent.meta).filter(
            AgentEvent.requirement_id == requirement_id,
            AgentEvent.kind == 'verify',
        ).order_by(AgentEvent.seq).all() if isinstance(m, dict)]
        repair_rounds = db.query(func.count(AgentEvent.id)).filter(
            AgentEvent.requirement_id == requirement_id,
            AgentEvent.kind == 'repair',
        ).scalar() or 0

        summary = {
            'turns': len(items),
            'events': int(tot[0] or 0),
            'llm_calls': int(tot[1] or 0),
            'tool_calls': int(tot[2] or 0),
            'error_count': int(tot[3] or 0),
            'tokens': int(tot[4] or 0) + int(tot[5] or 0),
            'tokens_in': int(tot[4] or 0),
            'tokens_out': int(tot[5] or 0),
            # cached 是 tokens_in 的子集，界面必须标清楚，别被读成第三类 token
            'cached_tokens': int(tot[9] or 0),
            'cache_hit_rate': _hit_rate(int(tot[9] or 0), int(tot[10] or 0)),
            'cache_reported_calls': int(tot[11] or 0),
            'cost': round(float(tot[6] or 0), 6),
            'duration_ms': duration_ms,
            'first_llm_ms': int(first_llm) if first_llm else 0,
            'tail_ms': max(0, duration_ms - int(first_llm or 0)),
            'started_at': _iso_utc(first_ts),
            'last_event_at': _iso_utc(last_ts),
            'stages': stages,
            'by_model': by_model,
            'verify_verdicts': verdicts,
            'repair_rounds': int(repair_rounds),
            # 「最终通过了吗」是汇总里最该一眼看到的结论
            'passed': bool(verdicts) and str(verdicts[-1]).upper() == 'PASS',
        }

        # 需求基本信息（标题 / 创建人 / 提交时间）—— 详情页头部要用。
        # 以前是前端去列表接口里把标题捞出来：多一次请求，而且需求翻不到那一页
        # 时标题就空了。挂在这里，一次请求把头部需要的东西给全。
        # 需求头信息只存在于主库；评测库里没有 requirements / users 两张表，
        # 查询会抛 OperationalError —— 降级为空信息，轨迹本身照常渲染。
        creator = ''
        try:
            from models.models import Requirement, User
            req_row = db.query(Requirement).filter(
                Requirement.id == requirement_id).first()
            if req_row and req_row.user_id:
                u = db.query(User).filter(User.id == req_row.user_id).first()
                creator = u.username if u else ''
        except Exception:
            req_row = None
        # trace_id 贯穿整个 run，是跨表 / 跨日志把一次执行串起来的唯一线索，
        # 头部要能一键复制。取任意一条事件的值即可（同一 run 内一致）；
        # 一条都没有（早期数据没埋）就留空，前端会自行隐藏复制入口。
        trace = db.query(AgentEvent.trace_id).filter(
            AgentEvent.requirement_id == requirement_id,
            AgentEvent.trace_id.isnot(None),
        ).order_by(AgentEvent.seq).limit(1).scalar()

        requirement = {
            'id': requirement_id,
            'title': req_row.title if req_row else f'需求 #{requirement_id}',
            'status': (req_row.status if req_row else 'unknown') or 'unknown',
            'creator': creator,
            'trace_id': trace or None,
            'created_at': _iso_utc(req_row.create_time if req_row else None),
        }

        return {'requirement_id': requirement_id,
                'requirement': requirement,
                'summary': summary,
                'items': items}


@app.route('/api/admin/traces/<int:requirement_id>/turns', methods=['GET'])
@admin_required
def admin_trace_turns(requirement_id):
    """对话轮次列表 —— 驱动详情页顶部的「轮次切换器」。"""
    with transactional_db() as db:
        return jsonify(build_turns_payload(requirement_id, db))


# ==================== 事件详情 ====================

def build_event_detail(event_id, session):
    """单个事件的完整还原（含 request / response / 完整 messages）。

    这是唯一会触碰 `trace_message_blobs` 与 `agent_payloads` 的查询：
    按 message_refs 批量取正文一次查询还原出完整 messages[]。
    返回 None 表示事件不存在，由调用方决定 404。
    """
    from harness.observability.trace_writer import TraceWriter
    from models.models import AgentEvent, AgentPayload

    with _use_db(session) as db:
        ev = db.query(AgentEvent).filter(AgentEvent.id == event_id).first()
        if not ev:
            return None

        result = {
            'id': ev.id,
            'requirement_id': ev.requirement_id,
            'seq': ev.seq,
            'turn_index': ev.turn_index,
            'iteration': ev.iteration,
            # trace_id 贯穿整个 run；call_id 是「跳到传输层原文」的唯一钥匙
            # （llm_traffic.log 按 call_id 索引）。少一个，界面上就没法从
            # 这条 LLM 事件追到当时的原始报文，等于埋点白埋。
            'trace_id': ev.trace_id,
            'call_id': ev.call_id,
            'ts': _iso_utc(ev.ts),
            'kind': ev.kind,
            'stage': ev.stage,
            'label': ev.label,
            'status': ev.status,
            'model': ev.model,
            'duration_ms': ev.duration_ms,
            'tokens_in': ev.tokens_in,
            'tokens_out': ev.tokens_out,
            'cost': round(float(ev.cost or 0), 6),
            'meta': ev.meta or {},
            'messages': [],
            'tools': None,
            'response': None,
            'tool_content': None,
        }

        # 还原 messages —— 一次批量查询，不是逐条
        messages = TraceWriter.restore_messages(
            db, ev.requirement_id, ev.message_refs or [])
        for i, m in enumerate(messages):
            m['index'] = i
            m['char_len'] = len(m.get('content') or '')
        result['messages'] = messages

        if ev.payload_id:
            payload = db.query(AgentPayload).filter(
                AgentPayload.id == ev.payload_id).first()
            if payload:
                body = payload.response or {}
                result['request_params'] = body.get('request')
                result['response'] = {k: v for k, v in body.items() if k != 'request'}
                result['tool_content'] = payload.tool_content
                result['tools_ref'] = payload.tools_ref

        # 大体积工具参数（write_file 的整个文件内容）落库时只存了预览 + hash，
        # 这里按需还原完整内容 —— 与 message 的还原走同一套 content 寻址。
        # arguments 保持预览不变（前端据此显示"已截断"），完整内容走 arguments_full。
        _refs = (result.get('request_params') or {}).get('arg_refs') or {}
        if _refs:
            try:
                from models.models import TraceMessageBlob
                _rows = db.query(TraceMessageBlob).filter(
                    TraceMessageBlob.requirement_id == ev.requirement_id,
                    TraceMessageBlob.content_hash.in_(list(_refs.values())),
                ).all()
                _store = {r.content_hash: r.content for r in _rows}
                _full = dict((result['request_params'] or {}).get('arguments') or {})
                for _k, _h in _refs.items():
                    if _h in _store:
                        _full[_k] = _store[_h]
                result['request_params'] = dict(result['request_params'],
                                                arguments_full=_full)
                result['args_resolved'] = all(h in _store for h in _refs.values())
            except Exception:
                result['args_resolved'] = False

        # tools schema 单独存放（整个 run 一份），按需取
        if ev.tools_ref:
            tools_row = db.query(AgentPayload).filter(
                AgentPayload.requirement_id == ev.requirement_id,
                AgentPayload.tools_ref == ev.tools_ref,
                AgentPayload.kind == 'tools_schema',
            ).first()
            if tools_row:
                result['tools'] = (tools_row.response or {}).get('tools')

        return result


@app.route('/api/admin/traces/events/<int:event_id>', methods=['GET'])
@admin_required
def admin_trace_event_detail(event_id):
    """单个事件详情 —— 完整还原 request / response。"""
    with transactional_db() as db:
        result = build_event_detail(event_id, db)
        if result is None:
            return jsonify({'error': '事件不存在'}), 404
        return jsonify(result)


# ==================== 事件契约 ====================

@app.route('/api/admin/traces/contract', methods=['GET'])
@admin_required
def admin_trace_contract():
    """事件契约 —— 阶段与事件类型的中文名 / 配色，前端据此渲染。

    新增阶段只改 `harness/observability/event_contract.py`，前端自动跟上，
    不再需要在前端 KIND_LABEL / KIND_COLOR 两张 map 里同步补一遍。
    """
    from harness.observability.event_contract import contract_payload
    return jsonify(contract_payload())


# ==================== 概览统计 ====================

@app.route('/api/admin/traces/stats', methods=['GET'])
@admin_required
def admin_trace_stats():
    """后台首页概览 —— 事件类型分布、需求状态分布与总量。

    首页指标卡与筛选条的唯一数据源。与列表页同一口径：只聚合 `agent_events`，
    不触碰明细表（见模块 docstring 的性能约定）。

    `range` / `days` 必须与列表页传同一个值。否则会出现「chip 上写着已完成 17，
    点进去只有 15 行」—— 因为列表带时间窗、计数却按全时段算。数字对不上比没有
    这个数字更糟，所以范围相关的指标一律按同一时间窗聚合；「今日 *」本身就是今天
    的量，不受窗口影响。

    窗口口径与列表一致：**按需求创建时间**（孤儿事件回退首条事件时间），
    不再是「窗口内有事件」。两边共用 `_range_since` 与 `_windowed_ids`，
    因此 chip 上的计数与点进去的行数必然相等。
    """
    from models.models import AgentEvent, Requirement

    # `range` 是新参数，`days` 保留兼容（旧前端 / 书签链接还在用）
    raw_range = request.args.get('range')
    if raw_range is None:
        raw_range = request.args.get('days')
    since = _range_since(raw_range)

    with transactional_db() as db:
        def _windowed_ids():
            """窗口内的需求 id 集合；since 为空返回 None（不过滤）。

            只算一次、两处复用（事件聚合与耗时样本），避免「同一次请求里
            两个窗口」——那正是数字对不上的成因。
            """
            if since is None:
                return None
            first = db.query(
                AgentEvent.requirement_id.label('rid'),
                func.min(AgentEvent.ts).label('t0'),
            ).group_by(AgentEvent.requirement_id).subquery()
            rows = db.query(first.c.rid).outerjoin(
                Requirement, Requirement.id == first.c.rid
            ).filter(
                _created_expr(Requirement.create_time, first.c.t0) >= since
            ).all()
            return {r[0] for r in rows}

        _ids = _windowed_ids()

        def windowed(q):
            """把查询限制在所选窗口内的**需求**上。

            按 requirement_id 收窄而不是按 `AgentEvent.ts` —— 列表页筛的是
            「某时创建的需求」，事件时间只是它的副产品。按事件时间过滤会把
            「3 天前创建、今天还在跑」的需求从窗口里划出去，于是 chip 写 17、
            点进去 15（这恰恰是本模块反复强调不能出现的那类不一致）。
            """
            if _ids is None:
                return q
            # 空集合直接判假：SQLAlchemy 对 `IN ()` 会告警，且语义不直观
            return q.filter(AgentEvent.requirement_id.in_(_ids)) if _ids \
                else q.filter(false())

        kind_rows = windowed(db.query(
            AgentEvent.kind, func.count(AgentEvent.id)
        )).group_by(AgentEvent.kind).all()

        agg = windowed(db.query(
            func.count(AgentEvent.id),
            func.count(func.distinct(AgentEvent.requirement_id)),
            func.sum(AgentEvent.tokens_in),
            func.sum(AgentEvent.tokens_out),
            func.sum(AgentEvent.cost),
            # ⚠️ 追加在末尾：agg[0..4] 的下标在多处被引用（含下方 token 输出），
            # 插在中间会静默错位 —— 不报错，只是数字悄悄错。
            func.sum(AgentEvent.cached_tokens),
            # 命中率的**分母与样本量**：只统计「上报过缓存信息」的调用。
            # 用全部 tokens_in 当分母的话，那些压根没带缓存字段的调用会把分母
            # 撑大而分子不动 —— 命中率被系统性算低，且看起来依然「像那么回事」。
            func.sum(case((AgentEvent.cached_tokens.isnot(None),
                           AgentEvent.tokens_in), else_=0)),
            func.sum(case((AgentEvent.cached_tokens.isnot(None), 1), else_=0)),
        )).first()

        # 状态分布的**分母是「有事件索引的需求」**，与列表页完全同源（列表页就是
        # 从 agent_events 聚合出来的）。用 outerjoin 而不是 join：`agent_events`
        # 里确实存在找不到 Requirement 行的孤儿事件（实测有 requirement_id=0 的
        # 83 条），列表页用的是 outerjoin、会把它显示成「未知」。这里若用 inner
        # join，各状态之和会比「覆盖需求」少 1，点「未知」却是 0 行 ——
        # 数字对不上是最伤信任的一类不一致。
        status_rows = windowed(
            db.query(Requirement.status,
                     func.count(func.distinct(AgentEvent.requirement_id)))
            .select_from(AgentEvent)
            .outerjoin(Requirement, Requirement.id == AgentEvent.requirement_id)
        ).group_by(Requirement.status).all()

        # 全部需求数（含还没產生轨迹的）：给「已接入 N」一个有意义的分母
        total_reqs = db.query(func.count(Requirement.id)).filter(
            Requirement.is_deleted.is_(False)).scalar() or 0

        # ---------- 今日指标 ----------
        # ts / update_time 一律按 UTC 存，而「今日」是业务上的今天。直接按 UTC
        # 切日的话，本地早上 8 点前发生的事会被算进前一天 —— 界面上写着
        # 「今天 07:30」，计数却进了昨天。这里取本地 0 点再换算成 UTC 比较，
        # 不硬编码时区名（部署机器在哪个时区，业务日界就在哪个时区）。
        local_now = datetime.now()
        today_start = local_now.replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(timezone.utc).replace(tzinfo=None)
        yday_start = today_start - timedelta(days=1)

        DONE_STATUSES = ('finished', 'finished_with_issues')
        today_finished = db.query(func.count(Requirement.id)).filter(
            Requirement.is_deleted.is_(False),
            Requirement.status.in_(DONE_STATUSES),
            Requirement.update_time >= today_start,
        ).scalar() or 0
        prev_finished = db.query(func.count(Requirement.id)).filter(
            Requirement.is_deleted.is_(False),
            Requirement.status.in_(DONE_STATUSES),
            Requirement.update_time >= yday_start,
            Requirement.update_time < today_start,
        ).scalar() or 0

        today_agg = db.query(
            func.sum(AgentEvent.cost),
            func.sum(case((AgentEvent.kind == 'llm_turn', 1), else_=0)),
            func.count(AgentEvent.id),
        ).filter(AgentEvent.ts >= today_start).first()

        # ---------- 进行中 / 等待用户确认 ----------
        # 用**有轨迹**的口径（与筛选 chip 同一分母），不是需求表全量：
        # 这个页面上的每个数字都要能对应到列表里的行。按全量算的话，KPI 会写
        # 「进行中 54」而 chip 写「进行中 30」—— 同一件事两个数字，正是最容易
        # 被读成「数据坏了」的那类不一致。全量需求数在 requirements_total 里
        # 单独给，不混进这里。
        active_total = sum(int(c or 0) for s, c in status_rows
                           if _BUCKET_OF.get(s or 'unknown') == 'active')
        awaiting_user = sum(int(c or 0) for s, c in status_rows
                            if (s or '') in ('needs_user_input', 'planning'))

        # ---------- 已完成需求的耗时：均值与 P95 ----------
        # 耗时取每个需求首末事件的墙钟跨度（与列表页同口径），不是各事件
        # duration_ms 之和 —— 后者会把并行/包含的耗时重复计入。
        # 样本只有几十条，直接排序取分位即可，不引 percentile_cont（换 SQLite
        # 跑单测时方言不一样）。
        # 差值必须在 Python 里算：SQLite 把 DATETIME 存成字符串，SQL 里的
        # `max(ts) - min(ts)` 会退化成数值 0，而 SQLAlchemy 仍按 DateTime 解释
        # 这个表达式，取回时抛 `fromisoformat: argument must be str`（单测跑
        # SQLite、线上跑 PG，只有把减法挪到 Python 两种方言才一致）。
        span_rows = db.query(
            AgentEvent.requirement_id,
            func.min(AgentEvent.ts).label('t0'),
            func.max(AgentEvent.ts).label('t1'),
        ).group_by(AgentEvent.requirement_id).all()
        done_ids = {rid for (rid,) in db.query(Requirement.id).filter(
            Requirement.status.in_(DONE_STATUSES)).all()}
        # 列序：0 = requirement_id，1 = t0，2 = t1。写错下标不会报错，
        # 只会让样本恒为空、avg/p95 静默变成 0 —— 所以这里由守卫测试兜着
        # （test_duration_sample_counts_only_done_requirements）。
        # 耗时跨度本身仍取全量（与列表页那一列逐行对得上），只把**样本**收窄到
        # 窗口内 —— 否则 KPI 会写「20 个样本」而「已完成」chip 只有 15。
        # 复用上面同一份窗口集合，不再另算一遍（另算 = 两个窗口 = 数字对不上）。
        windowed_ids = _ids
        dur_secs = sorted(
            (r[2] - r[1]).total_seconds() for r in span_rows
            if r[1] is not None and r[2] is not None and r[0] in done_ids
            and (windowed_ids is None or r[0] in windowed_ids))
        avg_ms = int(sum(dur_secs) / len(dur_secs) * 1000) if dur_secs else 0
        # 下标 clamp 一次：样本量为 1 时 int(0.95) = 0，为 20 时是 19，都在界内，
        # 但浮点误差不该有机会取到 len
        p95_ms = int(dur_secs[min(len(dur_secs) - 1, int(len(dur_secs) * 0.95))] * 1000) \
            if dur_secs else 0

        # ---------- 状态聚合桶 ----------
        # 映射表在模块级 STATUS_BUCKETS：列表接口按桶过滤时用的是同一份，
        # 所以 chip 上的计数与点进去的行数必然相等，不会出现「写着 5 点开 4」。
        buckets = {'active': 0, 'done': 0, 'failed': 0, 'unknown': 0}
        for s, c in status_rows:
            buckets[_BUCKET_OF.get(s or 'unknown', 'unknown')] += int(c or 0)

        # 全库最近一次事件时间 —— 首页副标题「最近更新于 N 分钟前」用。
        # 拿它当「数据是否还在进来」的活体信号：长时间不动通常意味着后台挂了。
        last_ts = db.query(func.max(AgentEvent.ts)).scalar()

        # 埋点自身的健康度：写入失败只告警不抛出（不能阻断主流程），
        # 但必须能被看到 —— 否则「后台没数据」和「这次没产生数据」无法区分。
        from harness.observability.trace_writer import write_failure_count

        return jsonify({
            'by_kind': [{'kind': k, 'count': c} for k, c in kind_rows],
            'by_status': [{'status': s or 'unknown', 'count': c}
                          for s, c in status_rows],
            'status_buckets': [
                {'key': k, 'count': buckets[k]}
                for k in ('active', 'done', 'failed', 'unknown')
            ],
            'total_events': int(agg[0] or 0),
            # 「覆盖需求」＝ 各状态桶之和（同一个窗口内），这样「全部 N」chip
            # 与点进去的行数必然相等。用 agg 的 distinct 计数会绕开时间窗。
            'total_requirements': sum(int(c or 0) for _, c in status_rows),
            'requirements_total': int(total_reqs),
            'total_tokens': int(agg[2] or 0) + int(agg[3] or 0),
            # cached 是 tokens_in 的子集；命中率分母只含「上报过缓存信息」的调用
            'tokens_in': int(agg[2] or 0),
            'cached_tokens': int(agg[5] or 0),
            'cache_hit_rate': _hit_rate(int(agg[5] or 0), int(agg[6] or 0)),
            # 样本量：命中率是在多少次调用、多少 input token 上算出来的。
            # 不带上它，一个 3 次调用得出的 99% 和一个 500 次得出的 60%
            # 在界面上没有区别。
            'cache_reported_calls': int(agg[7] or 0),
            'cache_reported_tokens_in': int(agg[6] or 0),
            'total_cost': round(float(agg[4] or 0), 6),
            'last_event_at': _iso_utc(last_ts),
            'today': {
                'finished': int(today_finished),
                'finished_prev': int(prev_finished),
                'cost': round(float(today_agg[0] or 0), 6),
                'llm_calls': int(today_agg[1] or 0),
                'events': int(today_agg[2] or 0),
            },
            'active': {
                'total': int(active_total),
                'awaiting_user': int(awaiting_user),
            },
            'duration': {
                'avg_ms': avg_ms,
                'p95_ms': p95_ms,
                'sample': len(dur_secs),
            },
            'writer_failures': write_failure_count(),
            # 当前窗口与排序：前端副标题与 KPI 的窗口标注直接读，不在前端再拼一次
            'range': _range_label(raw_range) or 'all',
        })
