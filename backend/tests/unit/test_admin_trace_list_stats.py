# -*- coding: utf-8 -*-
"""轨迹列表 / 概览指标的守卫。

这里盯的都是同一类事故：**数字看起来对，点进去对不上**。

1. `/stats` 的 `by_status` 之和必须等于「覆盖需求」数。两处必须是同一个分母，
   否则筛选 chip 上写着「待用户处理 23」，点进去只有 22 行。
2. `status=unknown` 必须能筛到**孤儿需求**（有事件、却查不到 Requirement 行）。
   列表页用的是 outerjoin、会把它显示成「未知」；若 `/stats` 改用 inner join，
   各状态之和就会少掉它们，而「未知」还会变成一个点进去永远为空的死 chip。
3. 需求级 `summary` 的耗时必须是**墙钟跨度**（首末事件时间差），不是各事件
   `duration_ms` 之和 —— 后者会把并行 / 包含的耗时重复计入，数字会虚高好几倍。

这些断言都用「基线 + 增量」而不是绝对值：测试库是 SQLite 文件（见 conftest 的
`DATABASE_NAME=':memory:'`），数据会跨用例留存，写死绝对值必然变成假失败。
"""
from datetime import datetime, timedelta, timezone

import pytest

from models import SessionLocal, User
from models.models import AdminUser, AgentEvent, AgentPayload, Requirement
from utils.security import hash_password

_run = __import__("uuid").uuid4().hex[:8]
# 孤儿需求要用「每次运行都不同」的 id：测试库是会留存的 SQLite 文件，
# 写死 990001 会在第二次运行时撞上 (requirement_id, seq) 唯一索引。
_ORPHAN_BASE = 900000 + (int(_run, 16) % 90000) * 2


@pytest.fixture
def admin_token(app_client):
    db = SessionLocal()
    db.query(AdminUser).filter(AdminUser.username == "tlsadmin").delete()
    db.add(AdminUser(username="tlsadmin", password_hash=hash_password("tlspass123")))
    db.commit()
    db.close()
    resp = app_client.post("/api/admin/login", json={
        "username": "tlsadmin", "password": "tlspass123"})
    return resp.get_json()["token"]


def _get(client, token, path):
    resp = client.get(path, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, f"{path} -> {resp.status_code}"
    return resp.get_json()


def _mk_user(db):
    u = db.query(User).filter(User.username == f"tl_{_run}").first()
    if u is None:
        u = User(username=f"tl_{_run}", password_hash=hash_password("x12345678"))
        db.add(u)
        db.commit()
        db.refresh(u)
    return u


def _mk_req(db, user, status, title=None):
    r = Requirement(user_id=user.id, title=title or f"tl-{status}",
                    content="c", status=status)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def _set_created(db, req, when_utc):
    """显式改创建时间（用于「按创建时间排序 / 筛选」的用例）。"""
    req.create_time = when_utc
    db.commit()
    db.refresh(req)
    return req


def _utc_of_local(naive_local):
    """本地 naive 时间 → 库里存的 UTC naive 时间。

    与后端 `_range_since` 同一条链路：naive 值按**本地**解释再转 UTC。
    用例里的时间都要按「业务日界」给，不能直接写 utcnow - N 天 —— 那样
    在东八区会横跨到另一个日历日，断言会随机通过或失败。
    """
    return naive_local.astimezone(timezone.utc).replace(tzinfo=None)


def _mk_event(db, requirement_id, *, seq=1, kind="llm_turn", ts=None,
              duration_ms=0, status="ok", stage="coding", model=None,
              tokens_in=0, tokens_out=0, cached_tokens=None):
    """`cached_tokens` 默认 None —— 那就是「没上报」，与 0（上报了没命中）不同。"""
    ev = AgentEvent(
        requirement_id=requirement_id, trace_id="b" * 32, seq=seq,
        turn_index=0, iteration=1, ts=ts or datetime.utcnow(),
        kind=kind, stage=stage, label="x", status=status,
        duration_ms=duration_ms, model=model,
        tokens_in=tokens_in, tokens_out=tokens_out,
        cached_tokens=cached_tokens,
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def _counts(stats):
    return {x["status"]: x["count"] for x in stats["by_status"]}


def test_status_counts_include_orphan_and_sum_to_coverage(app_client, admin_token):
    """`by_status` 之和 == 覆盖需求数；孤儿需求算进「未知」而不是被丢掉。"""
    base = _get(app_client, admin_token, "/api/admin/traces/stats")
    base_counts, base_cov = _counts(base), base["total_requirements"]

    db = SessionLocal()
    try:
        u = _mk_user(db)
        r_fin = _mk_req(db, u, "finished")
        _mk_event(db, r_fin.id)
        r_fail = _mk_req(db, u, "failed")
        _mk_event(db, r_fail.id)
        # 孤儿：有事件，没有对应的 Requirement 行（线上实测存在 requirement_id=0 的一批）
        _mk_event(db, _ORPHAN_BASE)
    finally:
        db.close()

    stats = _get(app_client, admin_token, "/api/admin/traces/stats")
    counts = _counts(stats)

    assert counts.get("finished", 0) - base_counts.get("finished", 0) == 1
    assert counts.get("failed", 0) - base_counts.get("failed", 0) == 1
    assert counts.get("unknown", 0) - base_counts.get("unknown", 0) == 1
    assert stats["total_requirements"] - base_cov == 3

    # 核心不变式：同一个分母。inner join 会让等式差掉孤儿那个 1。
    assert sum(counts.values()) == stats["total_requirements"]


def test_unknown_status_filter_matches_stats_count(app_client, admin_token):
    """`?status=unknown` 的行数必须等于 chip 上的数字 —— chip 不能点进去是空的。"""
    db = SessionLocal()
    try:
        _mk_event(db, _ORPHAN_BASE + 1)
    finally:
        db.close()

    stats = _get(app_client, admin_token, "/api/admin/traces/stats")
    expected = _counts(stats).get("unknown", 0)

    data = _get(app_client, admin_token,
                "/api/admin/traces?status=unknown&page_size=100")
    assert data["total"] == expected
    assert all(r["status"] == "unknown" for r in data["items"])
    assert any(r["requirement_id"] == _ORPHAN_BASE + 1 for r in data["items"])


def test_status_filter_returns_only_that_status(app_client, admin_token):
    """`status` 过滤要真的生效（这个参数此前只在 docstring 里存在过）。"""
    db = SessionLocal()
    try:
        u = _mk_user(db)
        for st in ("finished", "failed"):
            r = _mk_req(db, u, st)
            _mk_event(db, r.id)
    finally:
        db.close()

    for st in ("finished", "failed"):
        data = _get(app_client, admin_token,
                    f"/api/admin/traces?status={st}&page_size=100")
        assert data["total"] >= 1
        assert {r["status"] for r in data["items"]} == {st}


def test_summary_duration_is_wall_clock_not_sum(app_client, admin_token):
    """耗时 = 首末事件墙钟跨度，不是各事件 duration_ms 求和。

    造两个相隔 10 秒、各自 duration_ms=60 秒的事件：墙钟应约 10s；
    求和会得到 120s —— 那样把并行 / 包含的耗时重复计入，数字虚高十几倍。
    """
    db = SessionLocal()
    try:
        u = _mk_user(db)
        r = _mk_req(db, u, "finished")
        t0 = datetime(2026, 1, 1, 10, 0, 0)
        _mk_event(db, r.id, seq=1, kind="llm_turn", ts=t0, duration_ms=60000)
        _mk_event(db, r.id, seq=2, kind="tool_call",
                  ts=t0 + timedelta(seconds=10), duration_ms=60000)
        rid = r.id
    finally:
        db.close()

    data = _get(app_client, admin_token, f"/api/admin/traces/{rid}/turns")
    s = data["summary"]

    assert s["duration_ms"] == 10000, f"应为墙钟跨度 10s，实际 {s['duration_ms']}"
    assert s["events"] == 2
    assert s["llm_calls"] == 1
    assert s["tool_calls"] == 1
    # 轮次条目数与 summary.turns 同源，不能各算各的
    assert s["turns"] == len(data["items"])


def test_stats_exposes_writer_failures(app_client, admin_token):
    """埋点写入失败计数要能被看到 —— 否则「后台没数据」和「这次没产生数据」无法区分。"""
    stats = _get(app_client, admin_token, "/api/admin/traces/stats")
    assert isinstance(stats["writer_failures"], int)
    assert stats["writer_failures"] >= 0


# ==================== 状态聚合桶（筛选条的 chip） ====================

def test_status_buckets_sum_to_coverage(app_client, admin_token):
    """三桶计数之和必须等于「覆盖需求」数 —— chip 的口径不能漏掉任何一类。

    桶映射（active / done / failed）同时是列表页 `?bucket=` 过滤的依据：
    少一个桶、或者桶把某个状态漏掉，就会出现「chip 上写 5、点进去 4」。
    「全部」与各桶之和必须相等，这条不变式比任何单个数字都重要。
    """
    stats = _get(app_client, admin_token, "/api/admin/traces/stats")
    buckets = {b["key"]: b["count"] for b in stats["status_buckets"]}

    assert set(buckets) == {"active", "done", "failed", "unknown"}
    assert sum(buckets.values()) == stats["total_requirements"]
    # 细粒度状态折成桶后同样守恒 —— 两个视角必须是同一份数据
    assert sum(_counts(stats).values()) == stats["total_requirements"]


def test_bucket_filter_matches_chip_count(app_client, admin_token):
    """`?bucket=X` 的行数必须等于 chip 上的数字 —— chip 不能点进去是空的。"""
    stats = _get(app_client, admin_token, "/api/admin/traces/stats")
    buckets = {b["key"]: b["count"] for b in stats["status_buckets"]}

    for key in ("active", "done", "failed"):
        n = buckets.get(key, 0)
        if n == 0 or n > 100:
            continue  # 超过一页就只验证筛选生效，不验证总数（分页各有各的守卫）
        data = _get(app_client, admin_token,
                    f"/api/admin/traces?bucket={key}&page_size=100")
        assert data["total"] == n, f"{key}: chip 写 {n}，点进去 {data['total']}"
        assert {r["bucket"] for r in data["items"]} == {key}


def test_list_rows_expose_creator(app_client, admin_token):
    """列表要带创建人，且查不到人不能丢行（孤儿需求没有 Requirement，也就没有 User）。"""
    db = SessionLocal()
    try:
        u = _mk_user(db)
        r = _mk_req(db, u, "finished")
        _mk_event(db, r.id)
        uname = u.username
    finally:
        db.close()

    data = _get(app_client, admin_token, "/api/admin/traces?page_size=100")
    hit = [x for x in data["items"] if x["title"] == "tl-finished"]
    assert hit, "刚建的需求应该出现在列表里"
    # 用「存在一条是本次建的用户」而不是「全部等于」：测试库是跨运行留存的
    # SQLite 文件，上一次运行建的 tl-finished 行还在，creator 是另一批用户。
    assert any(x["creator"] == uname for x in hit), "刚建的需求应带上创建人"
    # 孤儿行（无 Requirement）不能因为查不到人就整行丢掉
    assert all("creator" in x for x in data["items"])


def test_days_filter_narrows_and_survives_garbage(app_client, admin_token):
    """`days=N` 按最近活跃收窄；传非数字不能把接口打挂（查询参数是用户输入）。"""
    all_rows = _get(app_client, admin_token, "/api/admin/traces?page_size=100")
    recent = _get(app_client, admin_token, "/api/admin/traces?page_size=100&days=7")
    assert recent["total"] <= all_rows["total"]

    # 参数是用户可控的：脏值应当被当成「没传」，而不是 500
    dirty = _get(app_client, admin_token, "/api/admin/traces?days=abc&page_size=5")
    assert "items" in dirty


# ==================== 排序 / 时间范围 ====================

def test_list_sorts_by_created_time_by_default(app_client, admin_token):
    """默认按**创建时间**倒序，且能切回最近活跃。

    此前默认按最近活跃倒序：跑着的需求每推一条事件就把自己顶回第一行，
    正在看的表会自己重排 —— 用户反馈就是「排序很乱」。
    """
    db = SessionLocal()
    try:
        u = _mk_user(db)
        # 标题带运行号并用 q 收窄：测试库是留存的 SQLite 文件，历史行会让
        # 这两个需求落在第 100 行之外，不收窄就永远筛不到它们。
        tag = f"tlsort{_run}"
        older = _mk_req(db, u, "finished", title=tag)
        newer = _mk_req(db, u, "finished", title=tag)
        _set_created(db, older, datetime.utcnow() - timedelta(days=6))
        _set_created(db, newer, datetime.utcnow() - timedelta(days=1))
        # 活跃时间故意反过来：旧需求刚刚还在跑，新需求早就没动静了
        _mk_event(db, older.id, seq=1, ts=datetime.utcnow())
        _mk_event(db, newer.id, seq=1, ts=datetime.utcnow() - timedelta(days=5))
        ids = {older.id, newer.id}
    finally:
        db.close()

    def _pos(data):
        order = [r["requirement_id"] for r in data["items"]
                 if r["requirement_id"] in ids]
        return order

    default = _get(app_client, admin_token,
                   f"/api/admin/traces?page_size=100&sort=created&q={tag}")
    seq = _pos(default)
    assert seq == [newer.id, older.id], f"创建时间倒序应为新→旧，实际 {seq}"

    # 不传 sort 时的默认行为必须与 sort=created 一致（默认参数不能是另一个口径）
    bare = _get(app_client, admin_token,
                f"/api/admin/traces?page_size=100&q={tag}")
    assert _pos(bare) == seq, "不传 sort 时也必须按创建时间倒序"

    active = _get(app_client, admin_token,
                  f"/api/admin/traces?page_size=100&sort=active&q={tag}")
    assert _pos(active) == [older.id, newer.id], "sort=active 应改按最近活跃倒序"


def test_range_filter_windows_on_creation_day(app_client, admin_token):
    """当天 / 近 N 天按**创建时间**收窄（自然日对齐），且与排序同一口径。

    用例刻意让三条需求的「最近活跃」都是刚刚：若窗口按活跃时间算，三者会同时
    进窗，这条断言就抓不到口径漂移。
    """
    local_midnight = datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0)
    db = SessionLocal()
    try:
        u = _mk_user(db)
        tag = f"tlrange{_run}"
        today_req = _mk_req(db, u, "finished", title=tag)
        d3_req = _mk_req(db, u, "finished", title=tag)
        d10_req = _mk_req(db, u, "finished", title=tag)
        _set_created(db, today_req,
                     _utc_of_local(local_midnight + timedelta(hours=1)))
        _set_created(db, d3_req,
                     _utc_of_local(local_midnight - timedelta(days=2, hours=-1)))
        _set_created(db, d10_req,
                     _utc_of_local(local_midnight - timedelta(days=10)))
        for r in (today_req, d3_req, d10_req):
            _mk_event(db, r.id, seq=1, ts=datetime.utcnow())
        ids = (today_req.id, d3_req.id, d10_req.id)
    finally:
        db.close()

    def _ids_of(rng):
        data = _get(app_client, admin_token,
                    f"/api/admin/traces?page_size=100&range={rng}&q={tag}")
        return {r["requirement_id"] for r in data["items"]}

    today_set = _ids_of("today")
    assert ids[0] in today_set, "今天创建的需求必须出现在「当天」"
    assert ids[1] not in today_set and ids[2] not in today_set

    d3_set = _ids_of("3")
    assert ids[0] in d3_set and ids[1] in d3_set, "近 3 天应含今天与前 2 个日历日"
    assert ids[2] not in d3_set

    d7_set = _ids_of("7")
    assert ids[0] in d7_set and ids[1] in d7_set
    assert ids[2] not in d7_set

    all_set = _ids_of("all")
    assert set(ids) <= all_set, "全部时间不该筛掉任何一条"


def test_range_counts_match_between_stats_and_list(app_client, admin_token):
    """换用 `range` 后 chip 与列表仍必须同窗口（旧参数 `days` 同样要兼容）。

    这是本文件最核心的不变式：窗口解释分散在两处时，chip 会写 17 而列表给 15。
    """
    for param in ("range", "days"):
        win_stats = _get(app_client, admin_token,
                         f"/api/admin/traces/stats?{param}=7")
        win_list = _get(app_client, admin_token,
                        f"/api/admin/traces?{param}=7&page_size=100")
        assert win_stats["total_requirements"] == win_list["total"], \
            f"{param}: chip 写 {win_stats['total_requirements']}，列表 {win_list['total']}"
        assert sum(b["count"] for b in win_stats["status_buckets"]) \
            == win_stats["total_requirements"]

    # 脏值退化成「不过滤」，不能 500
    dirty = _get(app_client, admin_token, "/api/admin/traces?range=abc&page_size=5")
    assert "items" in dirty


def test_stats_counts_follow_days_window(app_client, admin_token):
    """`/stats?days=N` 的计数必须与列表**同窗口**。

    列表页默认带时间窗（前端默认 days=7），计数若仍按全时段算，就会出现
    「chip 上写着已完成 17、点进去只有 15 行」—— 数字对不上比没有这个数字更糟，
    这条守卫盯的就是它。顺便钉住「耗时样本不超过同窗口的已完成数」。
    """
    all_stats = _get(app_client, admin_token, "/api/admin/traces/stats")
    win_stats = _get(app_client, admin_token, "/api/admin/traces/stats?days=7")
    win_list = _get(app_client, admin_token,
                    "/api/admin/traces?days=7&page_size=100")

    # 「全部 N」chip 的 N 就是同窗口下的行数
    assert win_stats["total_requirements"] == win_list["total"]
    # 窗口内各桶之和仍守恒
    assert sum(b["count"] for b in win_stats["status_buckets"]) \
        == win_stats["total_requirements"]
    # 窗口只可能收窄，不可能变大；脏值退化成「不过滤」
    assert win_stats["total_requirements"] <= all_stats["total_requirements"]
    dirty = _get(app_client, admin_token, "/api/admin/traces/stats?days=abc")
    assert dirty["total_requirements"] == all_stats["total_requirements"]

    # 耗时样本不能超过同窗口的「已完成」数 —— 否则 KPI 会写「20 个样本」，
    # 而「已完成」chip 只有 15。
    done = {b["key"]: b["count"] for b in win_stats["status_buckets"]}["done"]
    assert win_stats["duration"]["sample"] == done


# ==================== 需求级指标（设计稿的汇总卡） ====================

def test_summary_exposes_stage_model_and_repair(app_client, admin_token):
    """分阶段 LLM 调用 / 按模型调用数 / 首个 LLM 耗时 / 修复轮次都要给出来。

    这四项分别是设计稿上「TL 1 · Coder 17 · AC 1」「qwen-plus 18」、
    「首 Token 4.2s」、「重试/修复 1 / 2」三张卡的唯一数据来源 —— 缺一项
    界面上就是一行空白，或者更糟：被前端拿别的数字凑出来。
    """
    db = SessionLocal()
    try:
        u = _mk_user(db)
        r = _mk_req(db, u, "finished")
        t0 = datetime(2026, 2, 1, 8, 0, 0)
        _mk_event(db, r.id, seq=1, stage="planning", model="m-a",
                  ts=t0, duration_ms=4000)
        _mk_event(db, r.id, seq=2, stage="coding", model="m-a",
                  ts=t0 + timedelta(seconds=20), duration_ms=9000)
        _mk_event(db, r.id, seq=3, stage="verifying", model="m-b",
                  ts=t0 + timedelta(seconds=30), duration_ms=1000)
        _mk_event(db, r.id, seq=4, kind="verify", stage="verifying",
                  ts=t0 + timedelta(seconds=31))
        _mk_event(db, r.id, seq=5, kind="repair", stage="repairing",
                  ts=t0 + timedelta(seconds=32))
        rid = r.id
    finally:
        db.close()

    s = _get(app_client, admin_token, f"/api/admin/traces/{rid}/turns")["summary"]

    # 阶段：按阶段聚合，一条事件也不许漏
    by_stage = {x["stage"]: x for x in s["stages"]}
    assert set(by_stage) == {"planning", "coding", "verifying", "repairing"}
    assert by_stage["coding"]["llm_calls"] == 1
    assert by_stage["coding"]["events"] == 1
    assert by_stage["repairing"]["events"] == 1

    # 按模型：m-a 两次、m-b 一次，且按调用数降序
    assert {x["model"]: x["calls"] for x in s["by_model"]} == {"m-a": 2, "m-b": 1}

    # 首个 LLM 耗时 + 长尾 == 总耗时（少一毫秒都说明口径不是同一套）
    assert s["first_llm_ms"] == 4000
    assert s["first_llm_ms"] + s["tail_ms"] == s["duration_ms"]
    assert s["duration_ms"] == 32000

    # Token 分列与合计自洽
    assert s["tokens"] == s["tokens_in"] + s["tokens_out"]

    # 重试 / 修复：repair 事件数就是修复轮数；没有 verify 结论时不能假装通过
    assert s["repair_rounds"] == 1
    assert s["verify_verdicts"] == []
    assert s["passed"] is False


def test_duration_sample_counts_only_done_requirements(app_client, admin_token):
    """耗时均值 / P95 的样本只算「已完成」的需求，样本数要能对上增量。

    样本量必须透出：几十条样本上的 P95 没有统计意义，界面上得说清「基于几个
    样本」，否则会被当成稳定承诺。
    """
    base = _get(app_client, admin_token, "/api/admin/traces/stats")

    db = SessionLocal()
    try:
        u = _mk_user(db)
        r_done = _mk_req(db, u, "finished")
        t0 = datetime(2026, 3, 1, 9, 0, 0)
        _mk_event(db, r_done.id, seq=1, ts=t0)
        _mk_event(db, r_done.id, seq=2, ts=t0 + timedelta(seconds=6))
        # 未完成的需求不该进样本 —— 它还没「完成」，拿它的耗时当完成耗时是错的
        r_run = _mk_req(db, u, "processing")
        _mk_event(db, r_run.id, seq=1, ts=t0)
        _mk_event(db, r_run.id, seq=2, ts=t0 + timedelta(seconds=600))
    finally:
        db.close()

    after = _get(app_client, admin_token, "/api/admin/traces/stats")
    assert after["duration"]["sample"] - base["duration"]["sample"] == 1
    assert after["duration"]["avg_ms"] >= 0
    assert after["duration"]["p95_ms"] >= 0


def test_today_metrics_shape(app_client, admin_token):
    """今日指标只验结构与不变式，不写死数字。

    「今日」取决于跑测试的时刻，断言具体数值会在跨零点时变成假失败 ——
    这类"看起来在测、其实在碰运气"的断言比没有更糟。
    """
    stats = _get(app_client, admin_token, "/api/admin/traces/stats")
    today, active = stats["today"], stats["active"]

    for key in ("finished", "finished_prev", "events", "llm_calls"):
        assert isinstance(today[key], int) and today[key] >= 0, key
    assert today["cost"] >= 0

    for key in ("total", "awaiting_user"):
        assert isinstance(active[key], int) and active[key] >= 0, key
    # 「等待用户确认」是「进行中」的子集 —— 大于它说明两个口径算的不是一回事
    assert active["awaiting_user"] <= active["total"]

    # 副标题「最近更新于 N 分钟前」要有值可算
    assert stats["last_event_at"] is not None


# ==================== 缓存命中（KV-cache） ====================
#
# 这一组盯的是**三态语义**：NULL（这次没上报）/ 0（上报了、确实没命中）/ >0（命中）。
# 三者混成一个值之后，就再也分不出「供应商停报了」和「缓存真的失效了」——
# 而这两件事的处置方式完全相反：前者要去修埋点，后者要去查上下文前缀。
#
# 另一个容易踩的坑在分母：命中量是 input token 的**子集**，命中率的分母
# 只能含「确实上报过缓存信息」的那些调用。混进没上报的调用（它们的输入 token
# 照样会记账）会把命中率系统性算低，而这个数字看起来依然「像那么回事」。


def _cleanup_reqs(*ids):
    """清掉用例自己造的数据。

    测试库是会留存的文件级 SQLite，残留行会让「基线 + 增量」的断言在
    第二次运行时偏移，也会污染别的用例。
    """
    db = SessionLocal()
    try:
        for rid in ids:
            db.query(AgentEvent).filter(AgentEvent.requirement_id == rid).delete()
            db.query(AgentPayload).filter(AgentPayload.requirement_id == rid).delete()
            db.query(Requirement).filter(Requirement.id == rid).delete()
        db.commit()
    finally:
        db.close()


def test_cached_from_usage_three_states():
    """提取函数必须区分「没上报」与「上报为 0」。"""
    from harness.observability.trace_writer import _cached_from_usage

    # 没 usage、或 usage 里根本没有缓存字段 → 未知
    assert _cached_from_usage(None) is None
    assert _cached_from_usage({}) is None
    assert _cached_from_usage({"prompt_tokens": 100}) is None

    # 上报了、确实没命中 —— 这是 0，不是「未知」
    assert _cached_from_usage(
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 0}}) == 0

    # 三家供应商的字段名都要认
    assert _cached_from_usage(
        {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 64}}) == 64
    assert _cached_from_usage(
        {"prompt_tokens": 100, "prompt_cache_hit_tokens": 32}) == 32
    assert _cached_from_usage(
        {"prompt_tokens": 100, "cache_read_input_tokens": 16}) == 16


class _FakeResp:
    """record_llm_turn 需要的最小响应形态。"""

    def __init__(self, usage):
        self.content = "ok"
        self.reasoning_content = None
        self.tool_calls = []
        self.usage = usage
        self.is_error = False
        self.error = None


def test_record_llm_turn_persists_cached_tokens(app_client, admin_token):
    """端到端：埋点真的把命中量写进 agent_events，没上报时留 NULL。

    只测聚合函数的话，「埋点漏写」这种最致命的情况照样全绿 —— 而这个改动的
    全部意义就在于让数据真的落库。
    """
    from harness.observability.trace_writer import record_llm_turn

    db = SessionLocal()
    try:
        u = _mk_user(db)
        rid_hit = _mk_req(db, u, "finished").id
        rid_unknown = _mk_req(db, u, "finished").id
    finally:
        db.close()

    try:
        record_llm_turn(
            rid_hit, stage="coding", model="agnes-3.0-flash",
            messages=[{"role": "user", "content": "hi"}],
            response=_FakeResp({"prompt_tokens": 1000, "completion_tokens": 10,
                                "prompt_tokens_details": {"cached_tokens": 768}}),
        )
        # 有 usage、却没有缓存字段的调用（异构供应商 / 老数据）→ 必须留 NULL
        record_llm_turn(
            rid_unknown, stage="coding", model="m",
            messages=[{"role": "user", "content": "hi"}],
            response=_FakeResp({"prompt_tokens": 500, "completion_tokens": 5}),
        )

        db = SessionLocal()
        hit = db.query(AgentEvent).filter(
            AgentEvent.requirement_id == rid_hit,
            AgentEvent.kind == "llm_turn").one()
        unknown = db.query(AgentEvent).filter(
            AgentEvent.requirement_id == rid_unknown,
            AgentEvent.kind == "llm_turn").one()

        assert hit.cached_tokens == 768
        assert hit.tokens_in == 1000
        assert unknown.tokens_in == 500
        assert unknown.cached_tokens is None, "没上报必须留 NULL，落成 0 就再也分不出来了"
        db.close()
    finally:
        _cleanup_reqs(rid_hit, rid_unknown)


def test_stats_cache_denominator_excludes_unreported_calls(app_client, admin_token):
    """命中率的分母只含「上报过缓存信息」的调用，不是全部 tokens_in。

    用一个夸张的对比把口径钉死：没上报的那条带着 8000 输入 token，
    若混进分母，命中率会从 40% 掉到 8%。两个数字都「看起来合理」，
    所以只能靠断言挡。
    """
    base = _get(app_client, admin_token, "/api/admin/traces/stats")

    db = SessionLocal()
    try:
        u = _mk_user(db)
        rid = _mk_req(db, u, "finished").id
        _mk_event(db, rid, seq=1, tokens_in=1000, cached_tokens=800)    # 上报·命中
        _mk_event(db, rid, seq=2, tokens_in=1000, cached_tokens=0)      # 上报·未命中
        _mk_event(db, rid, seq=3, tokens_in=8000, cached_tokens=None)   # 未上报
    finally:
        db.close()

    try:
        s = _get(app_client, admin_token, "/api/admin/traces/stats")
        d_cached = s["cached_tokens"] - base["cached_tokens"]
        d_calls = s["cache_reported_calls"] - base["cache_reported_calls"]
        d_denom = s["cache_reported_tokens_in"] - base["cache_reported_tokens_in"]
        d_all_in = s["tokens_in"] - base["tokens_in"]

        assert d_cached == 800
        assert d_calls == 2, "没上报的调用不该进入样本量"
        assert d_denom == 2000, "分母只含上报过的 1000+1000，不含没上报的 8000"
        # 但总输入 token 仍要如实包含没上报的那些，不能为了让分母好看就把它藏起来
        assert d_all_in == 10000
        assert round(d_cached / d_denom, 4) == 0.4
    finally:
        _cleanup_reqs(rid)


def test_detail_cache_hit_rate_none_when_nothing_reported(app_client, admin_token):
    """整条需求都没上报缓存 → None，而不是 0%。

    显示 0% 会让人去排查一个根本不存在的缓存问题。
    """
    db = SessionLocal()
    try:
        u = _mk_user(db)
        rid = _mk_req(db, u, "finished").id
        _mk_event(db, rid, seq=1, tokens_in=5000, tokens_out=120, cached_tokens=None)
    finally:
        db.close()

    try:
        s = _get(app_client, admin_token,
                 f"/api/admin/traces/{rid}/turns")["summary"]
        assert s["cache_hit_rate"] is None
        assert s["cache_reported_calls"] == 0
        assert s["cached_tokens"] == 0
        assert s["tokens_in"] == 5000
    finally:
        _cleanup_reqs(rid)


def test_detail_reported_zero_hit_is_zero(app_client, admin_token):
    """上报了、确实没命中 → 0.0，必须与「没数据」区分开。"""
    db = SessionLocal()
    try:
        u = _mk_user(db)
        rid = _mk_req(db, u, "finished").id
        _mk_event(db, rid, seq=1, tokens_in=5000, cached_tokens=0)
    finally:
        db.close()

    try:
        s = _get(app_client, admin_token,
                 f"/api/admin/traces/{rid}/turns")["summary"]
        assert s["cache_hit_rate"] == 0.0
        assert s["cache_reported_calls"] == 1
    finally:
        _cleanup_reqs(rid)


def test_cached_tokens_never_exceed_input(app_client, admin_token):
    """命中量是输入 token 的子集 —— 超过就说明解析或聚合接错了字段。"""
    s = _get(app_client, admin_token, "/api/admin/traces/stats")
    assert s["cached_tokens"] <= s["tokens_in"]
    assert s["cache_reported_tokens_in"] <= s["tokens_in"]
    # 上报口径的命中率只可能落在 [0, 1]
    if s["cache_hit_rate"] is not None:
        assert 0.0 <= s["cache_hit_rate"] <= 1.0
    for b in s["status_buckets"]:
        assert b["count"] >= 0
