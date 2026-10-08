# -*- coding: utf-8 -*-
"""运营后台 · 评测观测 API（只读）

一次评测运行 = 一个自包含目录 `eval/runs/<run_id>/`：

    trace.db     过程：agent_events / trace_message_blobs / agent_payloads
    run.json     结果：运行元信息 + 逐题摘要

本模块只读文件，不碰运营库。轨迹部分的三个接口（events / turns / 单事件详情）
直接复用 `routes.admin_traces` 的 build_* —— 响应结构与需求轨迹页**逐字段一致**，
前端才能用同一套渲染组件。这条一致性由 tests/unit/test_admin_evals.py 钉住。

安全：run_id 与 task_id 都过白名单正则，并在解析后确认路径仍落在 runs/ 目录下。
少了这一步，`<run_id>` 传 `../..` 就能把任意 SQLite 文件读出来。
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from flask import jsonify, request
from sqlalchemy import create_engine, func
from sqlalchemy.orm import Session

from factory import app, logger
from routes.admin_traces import (  # noqa: F401 - 复用同一份查询与组装
    _iso_utc,
    build_event_detail,
    build_events_payload,
    build_turns_payload,
)
from utils.admin_guard import admin_required

# 运行目录：默认 <repo>/eval/runs，可用 EVAL_RUNS_DIR 覆盖（部署时 runs/ 可能
# 不在仓库里 —— 比如挂一个数据盘）。解析不到就是「暂无评测运行」，不是错误。
_DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "eval" / "runs"

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TASK_ID_RE = re.compile(r"^t?(\d{1,4})$", re.IGNORECASE)

# 按 run 缓存 engine。SQLite 打开成本不高，但每次请求都新建会累积连接池；
# 只留最近一个（同时看的运行不会多），旧的显式 dispose。
_ENGINES: dict = {}
_ENGINES_LOCK = threading.Lock()


def runs_dir() -> Path:
    override = (os.getenv("EVAL_RUNS_DIR") or "").strip()
    return Path(override) if override else _DEFAULT_RUNS_DIR


def _checked_run_dir(run_id):
    """校验 run_id 并返回运行目录；不合法或不存在时返回 None。"""
    if not _RUN_ID_RE.match(run_id or ""):
        return None
    root = runs_dir().resolve()
    target = (root / run_id).resolve()
    # 白名单之外再确认一次解析结果仍在 runs/ 下 —— 挡住符号链接之类的绕行
    if target != root and not str(target).startswith(str(root) + os.sep):
        return None
    return target if target.is_dir() else None


def _task_req_id(task_id):
    """题号 -> requirement_id。评测用题号当需求号（同一 run 库内靠它分组 21 题）。"""
    m = _TASK_ID_RE.match(str(task_id or ""))
    return int(m.group(1)) if m else None


def _read_manifest(run_dir: Path):
    """读 run.json；缺失或损坏一律返回 None（页面显示空态，不是 500）。"""
    path = run_dir / "run.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[admin_evals] run.json 解析失败 {path}: {e}")
        return None


_TASKS_CACHE = None


def _all_tasks() -> dict:
    """评测集定义：题号 -> {name, level}。读不到就返回空表（退化成裸题号）。"""
    global _TASKS_CACHE
    if _TASKS_CACHE is None:
        tasks = {}
        try:
            import yaml
            path = Path(__file__).resolve().parents[2] / "eval" / "tasks" / "tasks.yaml"
            for t in (yaml.safe_load(path.read_text(encoding="utf-8")).get("tasks") or []):
                tasks[str(t.get("id"))] = {"name": t.get("name") or "",
                                           "level": t.get("level")}
        except Exception as e:
            logger.warning(f"[admin_evals] 读 tasks.yaml 失败: {e}")
        _TASKS_CACHE = tasks
    return _TASKS_CACHE


def _read_progress(run_dir: Path):
    """运行中的进度快照（评测进程每跑完一题写一次）。

    读不到就返回 None —— 可能还没写第一份，也可能正好撞上原子替换的瞬间。
    两种情况都按「只知道跑到哪、不知道结论」处理，不是错误。
    """
    path = run_dir / "progress.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _derive_running(run_dir: Path):
    """没有 run.json —— 这是一次**正在进行**的运行，从过程库反推它的样子。

    为什么必须反推：结果是逐题攒出来的，`run.json` 要跑完才落盘；而 `trace.db`
    从第一次埋点起就在实时写。如果只认 `run.json`，一次 70 分钟的全量评测在
    整个过程中于页面上**完全不存在** —— 想「跑的时候看着进度」就无从谈起。

    反推的把握来自 `requirement_id == 题号`（t01 -> 1）：评测管线写事件时就是这个
    约定，所以 `group by requirement_id` 就能还原出跑到第几题、每题多少事件。

    两个状态都不能含糊：
      · **结论**：评测进程每跑完一题会写一份 `progress.json`（含真实 passed），
        有它就用真的；没有（老运行）只能留 None —— 「还没评」和「评了没过」
        是两件事，写成 False 会让中途的页面显示一个假的失败数。
      · **跑完没跑完**：评测是串行的（一题跑完才起下一题），所以「后面已有别的题
        在写事件」就等于这题已经跑完了。没有这条推断时，所有已开始的题都会被
        含糊地标成「进行中」，看不出它是逐题推进的。
    """
    db = run_dir / "trace.db"
    if not db.exists():
        return None

    from models.models import AgentEvent
    eng = None
    try:
        eng = create_engine(f"sqlite:///{db}",
                            connect_args={"check_same_thread": False})
        with Session(eng) as s:
            rows = s.query(AgentEvent.requirement_id,
                           func.count(AgentEvent.id),
                           func.min(AgentEvent.ts),
                           func.max(AgentEvent.ts)) \
                .group_by(AgentEvent.requirement_id).all()
            # 模型名也在事件里（每条 LLM 调用都记），顺手带出来，
            # 免得进行中的运行在模型列上一直是「—」
            mrow = s.query(AgentEvent.model) \
                .filter(AgentEvent.model.isnot(None)).first()
            model = (mrow[0] if mrow else "") or ""
            # 命令行参数还没落盘，但「走没走规划」能从事件里看出来：
            # 有 plan 事件就是走了真实规划（--no-plan 时不会有）
            has_plan = s.query(AgentEvent.id) \
                .filter(AgentEvent.kind == "plan").first() is not None
    except Exception as e:
        # 库正在写、表还没建、文件被锁 —— 都不是错误，是「还看不了」
        logger.warning(f"[admin_evals] 反推运行中状态失败 {run_dir.name}: {e}")
        return None
    finally:
        if eng is not None:
            eng.dispose()

    if not rows:
        return None

    prog = _read_progress(run_dir) or {}
    done = {}
    for t in (prog.get("tasks") or []):
        if t.get("id"):
            done[str(t["id"])] = t

    tasks_def = _all_tasks()
    last_ts = {}
    started = {}
    for rid, n, t0, t1 in rows:
        tid = f"t{int(rid):02d}"
        meta = tasks_def.get(tid) or {}
        last_ts[tid] = t1
        started[tid] = {
            "id": tid,
            "name": meta.get("name") or tid,
            "level": meta.get("level"),
            "passed": None,           # 还没跑出结论
            "finished": tid in done,  # 有结论 = 这题跑完了
            "duration_s": None,
            "rounds": None,
            "plan_used": False,
            "plan_files": [],
            "plan_error": "",
            "tool_sequence": [],
            "files": [],
            "workspace": "",
            "error": "",
            "assertions": [],
        }

    # 没有快照时按时间序兜底：事件最新的那题才是正在跑的，更早开始的都已跑完
    newest = max((v for v in last_ts.values() if v is not None), default=None)
    for tid, t in started.items():
        if t["finished"] or newest is None:
            continue
        own = last_ts.get(tid)
        if own is not None and own < newest:
            t["finished"] = True

    # 快照里已有结论的题：用真实结果覆盖占位，页面就能边跑边看到逐题成绩
    for tid, d in done.items():
        t = started.get(tid)
        if t is None:
            continue
        for k in ("name", "level", "duration_s", "rounds", "plan_used",
                  "plan_files", "plan_error", "tool_sequence", "files",
                  "workspace", "error", "assertions"):
            if k in d:
                t[k] = d[k]
        t["passed"] = None if d.get("passed") is None else bool(d.get("passed"))

    # 按评测集顺序排（跑的顺序就是 tasks.yaml 的顺序），未知题号排最后
    order = list(tasks_def.keys())
    tasks = [started[t] for t in order if t in started]
    tasks += [started[t] for t in sorted(started) if t not in order]

    first_ts = min((t0 for _, _, t0, _ in rows if t0 is not None), default=None)
    p_args = prog.get("args") or {}
    return {
        "run_id": run_dir.name,
        "started_at": _iso_utc(first_ts),
        "finished_at": None,
        "duration_s": None,
        "model": prog.get("model") or model,
        # 参数优先信快照（评测进程自己写的）；没有快照时才靠事件反推
        # 「走没走规划」，一律不编造
        "args": p_args if p_args else {"with_plan": has_plan},
        "totals": {"total": len(tasks),
                   "passed": sum(1 for t in tasks if t["passed"] is True),
                   # 已跑完的题数：列表页据此显示「N 题已跑完」，比「N 题已开始」
                   # 更接近真实进度（已开始里含正跑着、还没结论的那题）
                   "finished": sum(1 for t in tasks if t["finished"]),
                   "pass_rate": None, "duration_s": None},
        "full_set_size": len(tasks_def) or prog.get("tasks_total") or None,
        "report_path": "",
        "compare": None,
        "running": True,
        "tasks": tasks,
    }


def _engine_for(run_id):
    """按 run 缓存的只读 engine；过程库不存在时返回 None。"""
    path = runs_dir() / run_id / "trace.db"
    if not path.exists():
        return None
    key = str(path)
    with _ENGINES_LOCK:
        eng = _ENGINES.get(key)
        if eng is None:
            for old in _ENGINES.values():
                try:
                    old.dispose()
                except Exception:
                    pass
            _ENGINES.clear()
            eng = create_engine(f"sqlite:///{path}",
                                connect_args={"check_same_thread": False})
            _ENGINES[key] = eng
        return eng


def _run_session(run_id):
    eng = _engine_for(run_id)
    return Session(eng) if eng is not None else None


def _event_counts(run_id) -> dict:
    """每题的事件数 —— 一次 group by 查完，不为 21 题各开一次查询。"""
    session = _run_session(run_id)
    if session is None:
        return {}
    from models.models import AgentEvent
    try:
        rows = session.query(AgentEvent.requirement_id,
                             func.count(AgentEvent.id)) \
            .group_by(AgentEvent.requirement_id).all()
        return {int(r[0]): int(r[1]) for r in rows}
    except Exception as e:
        logger.warning(f"[admin_evals] 事件数统计失败 {run_id}: {e}")
        return {}
    finally:
        session.close()


# ==================== 运行列表 ====================

@app.route('/api/admin/evals', methods=['GET'])
@admin_required
def admin_eval_list():
    """所有评测运行 —— 按时间倒序（目录名即时间序）。

    生产环境通常没有 runs/ 目录（评测不在线上跑），返回空列表而不是报错。
    """
    root = runs_dir()
    runs = []
    if root.is_dir():
        dirs = sorted((d for d in root.iterdir() if d.is_dir()),
                      key=lambda d: d.name, reverse=True)
        for d in dirs:
            man = _read_manifest(d)
            if man is None:
                # 结果还没落盘，但过程已经在写 —— 这是一次正在跑的评测
                man = _derive_running(d)
            if man is None:
                continue
            runs.append({
                'run_id': man.get('run_id') or d.name,
                'started_at': man.get('started_at'),
                'finished_at': man.get('finished_at'),
                'duration_s': man.get('duration_s'),
                'model': man.get('model'),
                'args': man.get('args') or {},
                'totals': man.get('totals') or {},
                # 全量题数：前端据此把「只跑了几题」标出来，否则 2/7 的百分比
                # 会被当成一次正式成绩和 21/21 并排比较
                'full_set_size': man.get('full_set_size'),
                'report_path': man.get('report_path') or '',
                'compare': man.get('compare'),
                # 正在跑：结果尚未定论，页面上必须与「已跑完」区分开
                'running': bool(man.get('running')),
                # 没有过程库（早期运行、或只补了结果摘要）时前端要说明白，
                # 而不是给一个点了没反应的入口
                'has_trace': (d / 'trace.db').exists(),
            })
    return jsonify({'runs': runs, 'dir': str(root)})


# ==================== 单次运行 ====================

@app.route('/api/admin/evals/<run_id>', methods=['GET'])
@admin_required
def admin_eval_detail(run_id):
    """一次运行的逐题摘要 —— 驱动详情页左栏。"""
    run_dir = _checked_run_dir(run_id)
    if run_dir is None:
        return jsonify({'error': '运行不存在'}), 404
    man = _read_manifest(run_dir)
    if man is None:
        man = _derive_running(run_dir)
    if man is None:
        return jsonify({'error': '运行记录不存在（缺少 run.json）'}), 404

    counts = _event_counts(run_id)
    items = []
    for t in man.get('tasks') or []:
        rid = _task_req_id(t.get('id'))
        n_events = counts.get(rid, 0) if rid is not None else 0
        passed = t.get('passed')
        items.append({
            'id': t.get('id'),
            'name': t.get('name'),
            'level': t.get('level'),
            # 进行中的题还没有结论 —— 保留 None，别落成 False
            'passed': None if passed is None else bool(passed),
            # 「跑完了但结论未知」与「正在跑」也是两件事：串行评测里，后面已经
            # 有别的题在写事件，就说明这题已经跑完（详见 _derive_running）。
            # 已完成的运行里每题必然都跑完了，默认 True。
            'finished': bool(t.get('finished', True)),
            'duration_s': t.get('duration_s'),
            'rounds': t.get('rounds'),
            'plan_used': bool(t.get('plan_used')),
            'plan_files': t.get('plan_files') or [],
            'plan_error': t.get('plan_error') or '',
            'tool_sequence': t.get('tool_sequence') or [],
            'files': t.get('files') or [],
            'workspace': t.get('workspace') or '',
            'error': t.get('error') or '',
            'assertions': t.get('assertions') or [],
            'failed_assertions': [a.get('type') for a in (t.get('assertions') or [])
                                  if not a.get('passed')],
            'event_count': n_events,
        })

    return jsonify({
        'run_id': run_id,
        'started_at': man.get('started_at'),
        'finished_at': man.get('finished_at'),
        'duration_s': man.get('duration_s'),
        'model': man.get('model'),
        'args': man.get('args') or {},
        'totals': man.get('totals') or {},
        'full_set_size': man.get('full_set_size'),
        'report_path': man.get('report_path') or '',
        'compare': man.get('compare'),
        'running': bool(man.get('running')),
        'has_trace': (run_dir / 'trace.db').exists(),
        'items': items,
    })


# ==================== 单题过程（复用需求轨迹的同一套组装）====================

@app.route('/api/admin/evals/<run_id>/tasks/<task_id>/events', methods=['GET'])
@admin_required
def admin_eval_task_events(run_id, task_id):
    """单题的时间线。响应结构与 /api/admin/traces/<rid>/events 逐字段一致。"""
    rid = _task_req_id(task_id)
    if _checked_run_dir(run_id) is None or rid is None:
        return jsonify({'error': '运行或题号不存在'}), 404
    session = _run_session(run_id)
    if session is None:
        return jsonify({'error': '本次运行没有过程数据'}), 404

    turn = request.args.get('turn_index')
    kind = request.args.get('kind')
    limit = min(1000, max(1, int(request.args.get('limit', 500))))
    offset = max(0, int(request.args.get('offset', 0)))
    try:
        return jsonify(build_events_payload(rid, session, turn=turn, kind=kind,
                                            limit=limit, offset=offset))
    finally:
        session.close()


@app.route('/api/admin/evals/<run_id>/tasks/<task_id>/turns', methods=['GET'])
@admin_required
def admin_eval_task_turns(run_id, task_id):
    """单题的轮次 / 汇总 / 阶段时间线。结构与 /api/admin/traces/<rid>/turns 一致。"""
    rid = _task_req_id(task_id)
    if _checked_run_dir(run_id) is None or rid is None:
        return jsonify({'error': '运行或题号不存在'}), 404
    session = _run_session(run_id)
    if session is None:
        return jsonify({'error': '本次运行没有过程数据'}), 404

    try:
        payload = build_turns_payload(rid, session)
    finally:
        session.close()

    # requirement 段的结构与需求轨迹页保持一致（字段名相同），只是内容取自评测：
    # 评测库里没有 requirements / users 两张表，标题与创建人只能是题号与空。
    payload['requirement'] = {
        'id': rid,
        'title': f'评测题 {task_id}',
        'status': 'eval',
        'creator': '',
        'trace_id': run_id,
        'created_at': None,
    }
    return jsonify(payload)


@app.route('/api/admin/evals/<run_id>/events/<int:event_id>', methods=['GET'])
@admin_required
def admin_eval_event_detail(run_id, event_id):
    """单事件详情（完整 prompt / response）。结构同 /api/admin/traces/events/<id>。"""
    if _checked_run_dir(run_id) is None:
        return jsonify({'error': '运行不存在'}), 404
    session = _run_session(run_id)
    if session is None:
        return jsonify({'error': '本次运行没有过程数据'}), 404
    try:
        result = build_event_detail(event_id, session)
    finally:
        session.close()
    if result is None:
        return jsonify({'error': '事件不存在'}), 404
    return jsonify(result)
