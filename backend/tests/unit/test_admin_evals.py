# -*- coding: utf-8 -*-
"""评测观测接口的守卫。

盯住三件事：
  1. **安全**：run_id / task_id 不能越出 eval/runs/（路径遍历读到任意 SQLite）
  2. **降级**：没有评测目录、或某次运行没有过程库时返回空态与说明，不是 500
  3. **契约**：单题 events / turns 与需求轨迹接口**逐字段一致** —— 这是前端
     能共用一套渲染组件的前提，也是两套接口不会各自演化的保证
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base, SessionLocal
from models.models import AdminUser, AgentEvent
from utils.security import hash_password

_TRACE_TABLES = ("agent_events", "trace_message_blobs",
                 "agent_payloads", "agent_traces")
_RUN_ID = "20261008_120000"


@pytest.fixture
def eval_admin_token(app_client):
    db = SessionLocal()
    try:
        db.query(AdminUser).filter(AdminUser.username == "evaladmin").delete()
        db.add(AdminUser(username="evaladmin",
                         password_hash=hash_password("evalpass123")))
        db.commit()
    finally:
        db.close()
    resp = app_client.post("/api/admin/login", json={
        "username": "evaladmin", "password": "evalpass123"})
    return resp.get_json()["token"]


@pytest.fixture
def auth(eval_admin_token):
    return {"Authorization": f"Bearer {eval_admin_token}"}


@pytest.fixture
def runs_dir(tmp_path, monkeypatch):
    """把 EVAL_RUNS_DIR 指到临时目录 —— 不碰真实的 eval/runs。"""
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setenv("EVAL_RUNS_DIR", str(root))
    return root


def _make_run(root: Path, run_id: str = _RUN_ID, *, with_trace: bool = True,
              tasks=None):
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    tasks = tasks if tasks is not None else [{
        "id": "t01", "name": "个人名片页", "level": 1, "passed": True,
        "duration_s": 85.1, "rounds": 7, "plan_used": True,
        "plan_files": ["index.html"], "plan_error": "",
        "tool_sequence": ["read_file", "write_file"], "files": ["index.html"],
        "workspace": "", "error": "",
        "assertions": [{"type": "preview_no_error", "passed": True, "detail": ""}],
    }]
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id,
        "started_at": "2026-10-08T12:00:00",
        "finished_at": "2026-10-08T12:30:00",
        "duration_s": 1800.0,
        "model": "agnes-3.0-flash",
        "args": {"with_plan": True, "with_memory": False,
                 "no_preview": False, "tasks": []},
        "totals": {"total": len(tasks), "passed": sum(1 for t in tasks if t["passed"]),
                   "pass_rate": 100.0, "duration_s": 85.1},
        "report_path": "eval/results/baseline_x.json",
        "tasks": tasks,
    }, ensure_ascii=False), encoding="utf-8")

    if with_trace:
        eng = create_engine(f"sqlite:///{run_dir / 'trace.db'}")
        tables = [t for t in Base.metadata.sorted_tables if t.name in _TRACE_TABLES]
        Base.metadata.create_all(eng, tables=tables)
        s = Session(eng)
        s.add(AgentEvent(requirement_id=1, trace_id=run_id, seq=1,
                         ts=datetime(2026, 10, 8, 12, 0, 0), kind="plan",
                         stage="planning", label="生成实现计划", status="ok"))
        s.add(AgentEvent(requirement_id=1, trace_id=run_id, seq=2,
                         ts=datetime(2026, 10, 8, 12, 0, 5), kind="coding",
                         stage="coding", iteration=1,
                         label="write_file index.html", status="ok"))
        s.commit()
        s.close()
    return run_dir


# ==================== 1. 安全：路径遍历 ====================

class TestPathSafety:
    def test_rejects_traversal(self):
        from routes.admin_evals import _checked_run_dir
        for bad in ("..", "../..", "../../etc", "a/b", "", ".", "x" * 65):
            assert _checked_run_dir(bad) is None, f"{bad!r} 不该被接受"

    def test_accepts_plain_id(self, runs_dir):
        from routes.admin_evals import _checked_run_dir
        _make_run(runs_dir, with_trace=False)
        assert _checked_run_dir(_RUN_ID) is not None

    def test_http_traversal_is_404(self, app_client, auth, runs_dir):
        resp = app_client.get("/api/admin/evals/%2e%2e/", headers=auth)
        assert resp.status_code == 404

    def test_task_id_rejects_non_numeric(self):
        from routes.admin_evals import _task_req_id
        assert _task_req_id("t01") == 1
        assert _task_req_id("12") == 12
        for bad in ("t", "t01x", "../1", "", None, "1e3"):
            assert _task_req_id(bad) is None, f"{bad!r} 不该被接受"


# ==================== 2. 降级：空态而非 500 ====================

class TestDegradation:
    def test_list_empty_when_dir_missing(self, app_client, auth, tmp_path,
                                         monkeypatch):
        monkeypatch.setenv("EVAL_RUNS_DIR", str(tmp_path / "nope"))
        resp = app_client.get("/api/admin/evals", headers=auth)
        assert resp.status_code == 200
        assert resp.get_json()["runs"] == []

    def test_list_skips_broken_manifest(self, app_client, auth, runs_dir):
        _make_run(runs_dir)
        bad = runs_dir / "20261009_000000"
        bad.mkdir()
        (bad / "run.json").write_text("{ 不是 json", encoding="utf-8")
        resp = app_client.get("/api/admin/evals", headers=auth)
        assert resp.status_code == 200
        assert [r["run_id"] for r in resp.get_json()["runs"]] == [_RUN_ID]

    def test_detail_404_when_run_missing(self, app_client, auth, runs_dir):
        resp = app_client.get("/api/admin/evals/20990101_000000", headers=auth)
        assert resp.status_code == 404

    def test_events_404_without_trace_db(self, app_client, auth, runs_dir):
        """只补了结果摘要（没跑过程）的运行：结果照常看，过程区给明确说明。"""
        _make_run(runs_dir, with_trace=False)
        resp = app_client.get(f"/api/admin/evals/{_RUN_ID}/tasks/t1/events",
                             headers=auth)
        assert resp.status_code == 404
        assert "过程" in resp.get_json()["error"]

    def test_list_marks_missing_trace(self, app_client, auth, runs_dir):
        _make_run(runs_dir, with_trace=False)
        runs = app_client.get("/api/admin/evals", headers=auth).get_json()["runs"]
        assert runs[0]["has_trace"] is False

    def test_requires_admin(self, app_client, runs_dir):
        assert app_client.get("/api/admin/evals").status_code in (401, 403)


def _make_running_run(root: Path, run_id: str, *, rids=(1,),
                      with_events: bool = True):
    """造一个「正在跑」的运行：只有 trace.db，**还没有 run.json**。

    真实的运行就是这个样子 —— 结果是逐题攒完才落盘的，过程从第一次埋点起
    就在实时写。
    """
    run_dir = root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    eng = create_engine(f"sqlite:///{run_dir / 'trace.db'}",
                        connect_args={"check_same_thread": False})
    tables = [t for t in Base.metadata.sorted_tables if t.name in _TRACE_TABLES]
    Base.metadata.create_all(eng, tables=tables)
    if with_events:
        s = Session(eng)
        seq = 0
        for rid in rids:
            for i in range(3):
                seq += 1
                s.add(AgentEvent(requirement_id=rid, trace_id=run_id, seq=seq,
                                 ts=datetime(2026, 10, 8, 12, 0, seq),
                                 kind="coding", stage="coding", iteration=i + 1,
                                 model="agnes-3.0-flash",
                                 label=f"write_file {i}", status="ok"))
        s.commit()
        s.close()
    eng.dispose()
    return run_dir


def _make_progress(run_dir: Path, tasks: list, *, tasks_total: int = 21):
    """评测进程每跑完一题写的进度快照（含真实结论）。"""
    (run_dir / "progress.json").write_text(
        json.dumps({"run_id": run_dir.name, "model": "agnes-3.0-flash",
                    "updated_at": "2026-10-08T12:10:00",
                    "args": {"with_plan": True}, "tasks_total": tasks_total,
                    "tasks": tasks}, ensure_ascii=False),
        encoding="utf-8")


# ==================== 3. 正常路径 ====================

class TestHappyPath:
    def test_list_returns_manifest_fields(self, app_client, auth, runs_dir):
        _make_run(runs_dir)
        runs = app_client.get("/api/admin/evals", headers=auth).get_json()["runs"]
        assert len(runs) == 1
        r = runs[0]
        assert r["run_id"] == _RUN_ID
        assert r["model"] == "agnes-3.0-flash"
        assert r["totals"]["total"] == 1 and r["totals"]["passed"] == 1
        assert r["has_trace"] is True

    def test_detail_includes_per_task_counts(self, app_client, auth, runs_dir):
        _make_run(runs_dir)
        data = app_client.get(f"/api/admin/evals/{_RUN_ID}",
                              headers=auth).get_json()
        assert data["has_trace"] is True
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert item["id"] == "t01"
        assert item["event_count"] == 2, "事件数应来自过程库"
        assert item["failed_assertions"] == []

    def test_detail_reports_failed_assertions(self, app_client, auth, runs_dir):
        _make_run(runs_dir, tasks=[{
            "id": "t06", "name": "待办清单", "level": 2, "passed": False,
            "duration_s": 379.3, "rounds": 7, "plan_used": True,
            "plan_files": [], "plan_error": "", "tool_sequence": [],
            "files": [], "workspace": "", "error": "预览报错",
            "assertions": [{"type": "preview_no_error", "passed": False,
                            "detail": "ReferenceError"}],
        }])
        data = app_client.get(f"/api/admin/evals/{_RUN_ID}",
                              headers=auth).get_json()
        assert data["items"][0]["failed_assertions"] == ["preview_no_error"]

    def test_task_events_returns_timeline(self, app_client, auth, runs_dir):
        _make_run(runs_dir)
        data = app_client.get(f"/api/admin/evals/{_RUN_ID}/tasks/t1/events",
                              headers=auth).get_json()
        assert data["requirement_id"] == 1
        assert data["total"] == 2
        assert [i["label"] for i in data["items"]] == [
            "生成实现计划", "write_file index.html"]
        assert [i["seq"] for i in data["items"]] == [1, 2], "必须按 seq 排序"

    def test_task_events_supports_kind_filter(self, app_client, auth, runs_dir):
        _make_run(runs_dir)
        data = app_client.get(
            f"/api/admin/evals/{_RUN_ID}/tasks/t1/events?kind=coding",
            headers=auth).get_json()
        assert data["total"] == 1
        assert data["items"][0]["kind"] == "coding"


# ==================== 4. 契约一致（前端共用渲染的前提）====================

class TestContractParity:
    def test_events_top_level_keys_match_traces(self, app_client, auth, runs_dir):
        """同一份响应结构：评测页与需求轨迹页的顶层键必须一致。"""
        _make_run(runs_dir)
        ev = app_client.get(f"/api/admin/evals/{_RUN_ID}/tasks/t1/events",
                            headers=auth).get_json()
        tr = app_client.get("/api/admin/traces/987654321/events",
                            headers=auth).get_json()
        assert set(ev) == set(tr)

    def test_turns_shape_matches_traces(self, app_client, auth, runs_dir):
        _make_run(runs_dir)
        ev = app_client.get(f"/api/admin/evals/{_RUN_ID}/tasks/t1/turns",
                            headers=auth).get_json()
        tr = app_client.get("/api/admin/traces/987654321/turns",
                            headers=auth).get_json()
        assert set(ev) == set(tr)
        assert set(ev["summary"]) == set(tr["summary"])
        assert set(ev["requirement"]) == set(tr["requirement"])

    def test_event_detail_keys_match_traces(self, app_client, auth, runs_dir):
        """单事件详情：两边都走 build_event_detail，键集合必须相同。"""
        _make_run(runs_dir)
        trace_db = runs_dir / _RUN_ID / "trace.db"
        eng = create_engine(f"sqlite:///{trace_db}")
        s = Session(eng)
        eid = s.query(AgentEvent.id).order_by(AgentEvent.seq).first()[0]
        s.close()
        eng.dispose()

        ev = app_client.get(f"/api/admin/evals/{_RUN_ID}/events/{eid}",
                            headers=auth)
        assert ev.status_code == 200
        data = ev.get_json()
        assert data["seq"] == 1
        assert data["kind"] == "plan"
        assert "messages" in data and "meta" in data

    def test_evals_routes_reuse_traces_builders(self):
        """反回退：评测接口必须复用 admin_traces 的 build_*。

        哪天有人在 admin_evals 里另写一份组装，字段就会与需求轨迹页漂移，
        而漂移在界面上只表现为「有个地方显示不出来」—— 很难事后归因。
        """
        src = (Path(__file__).resolve().parents[2]
               / "routes" / "admin_evals.py").read_text(encoding="utf-8")
        assert "from routes.admin_traces import" in src
        for fn in ("build_events_payload", "build_turns_payload",
                   "build_event_detail"):
            assert fn in src, f"{fn} 必须复用，不能另写一份"
        # 这些字段只有 build_* 会产出，本模块里出现即意味着有人开始自己组装时间线
        for marker in ("'has_payload'", "'message_count'", "'by_model'",
                       "'cache_hit_rate'", "'first_llm_ms'"):
            assert marker not in src, f"时间线字段 {marker} 必须由 build_* 产出"


class TestRunIdWhitelistParity:
    """run_id 白名单必须是同一条规则 —— 「文件在、页面 404」最难归因。

    `run_eval.py --backfill-runs` 会拿历史报告里的 timestamp 当目录名。
    两边的正则一旦分叉（比如这边允许 `:`、那边不允许），产出的目录名就会
    通过不了后台校验：数据明明在磁盘上，页面上却显示「运行不存在」。
    """

    def test_patterns_are_identical(self):
        import re
        repo = Path(__file__).resolve().parents[3]
        backend_src = (repo / "backend" / "routes" / "admin_evals.py") \
            .read_text(encoding="utf-8")
        eval_src = (repo / "eval" / "run_eval.py").read_text(encoding="utf-8")

        pats = {}
        for name, src in (("admin_evals", backend_src), ("run_eval", eval_src)):
            m = re.search(r'_RUN_ID_RE\s*=\s*re\.compile\(\s*r"([^"]+)"',
                          src) or re.search(
                r'_RUN_ID_SAFE\s*=\s*re\.compile\(\s*r"([^"]+)"', src)
            assert m, f"{name} 里找不到 run_id 白名单正则"
            pats[name] = m.group(1)

        assert pats["admin_evals"] == pats["run_eval"], (
            f"两侧 run_id 白名单必须一致，当前 "
            f"admin_evals={pats['admin_evals']!r} run_eval={pats['run_eval']!r}")

    def test_backfilled_ids_pass_the_whitelist(self):
        """实际产出的 id 必须过白名单（用真实历史 timestamp 试）。"""
        import re
        repo = Path(__file__).resolve().parents[3]
        src = (repo / "eval" / "run_eval.py").read_text(encoding="utf-8")
        safe = re.compile(re.search(r'_RUN_ID_SAFE\s*=\s*re\.compile\(\s*r"([^"]+)"',
                                    src).group(1))
        for rid in ("20261007_143036", "20261005_012838", "golden"):
            assert safe.match(rid), f"{rid} 应当通过白名单"
        # 反面：路径分隔符与空值必须被挡
        for bad in ("", "../etc", "a/b", "run id"):
            assert not safe.match(bad), f"{bad!r} 不该通过白名单"


# ==================== 4. 进行中的运行（只有过程库） ====================

class TestRunningRun:
    """跑的过程中也能看见。

    一次全量评测一个多小时。如果只认 `run.json`（跑完才写），整个过程里这次
    运行在页面上**完全不存在** —— 想「跑的时候看着」就无从谈起。过程库
    `trace.db` 从第一次埋点起就在实时写，所以这条通路是反推出来的。
    """

    RUN_ID = "20261008_130000"

    def test_running_run_appears_in_list(self, app_client, auth, runs_dir):
        _make_running_run(runs_dir, self.RUN_ID)
        runs = app_client.get("/api/admin/evals", headers=auth).get_json()["runs"]
        assert [r["run_id"] for r in runs] == [self.RUN_ID]
        assert runs[0]["running"] is True
        assert runs[0]["has_trace"] is True

    def test_running_detail_passed_is_null(self, app_client, auth, runs_dir):
        """进行中的题 `passed` 必须是 null —— 落成 false 会显示假的失败数。"""
        _make_running_run(runs_dir, self.RUN_ID, rids=(1, 2))
        data = app_client.get(f"/api/admin/evals/{self.RUN_ID}",
                              headers=auth).get_json()
        assert data["running"] is True
        assert [t["id"] for t in data["items"]] == ["t01", "t02"]
        assert all(t["passed"] is None for t in data["items"])
        # 题名取自评测集定义（requirement_id == 题号 这个约定要成立才有意义）
        assert data["items"][0]["name"] == "个人名片页"

    def test_running_totals_are_not_faked(self, app_client, auth, runs_dir):
        """没有耗时、没有通过率 —— 宁可为空也不能编一个出来。"""
        _make_running_run(runs_dir, self.RUN_ID, rids=(1, 3))
        r = app_client.get("/api/admin/evals", headers=auth).get_json()["runs"][0]
        assert r["duration_s"] is None
        assert r["totals"]["pass_rate"] is None
        assert r["model"] == "agnes-3.0-flash"        # 从事件里带出来的
        assert r["full_set_size"] >= 21               # 真实评测集规模

    def test_empty_trace_db_is_not_a_run(self, app_client, auth, runs_dir):
        """库建了但一条事件都没有（刚启动那一瞬）→ 还不算一次运行。"""
        _make_running_run(runs_dir, self.RUN_ID, with_events=False)
        runs = app_client.get("/api/admin/evals", headers=auth).get_json()["runs"]
        assert runs == []

    def test_finished_run_is_not_marked_running(self, app_client, auth, runs_dir):
        """有 run.json 的运行一律按「已跑完」处理，不受反推影响。"""
        _make_run(runs_dir)
        r = app_client.get("/api/admin/evals", headers=auth).get_json()["runs"][0]
        assert r["running"] is False
        assert r["totals"]["passed"] == 1

    def test_running_args_inferred_from_events(self, app_client, auth, runs_dir):
        """命令行参数还没落盘，但「走没走规划」能从 plan 事件推出来。

        推不出来的话前端会把 `undefined` 当「跳过规划」渲染 —— 一个与事实
        相反的结论，比留空糟糕得多。
        """
        run_dir = _make_running_run(runs_dir, self.RUN_ID)
        eng = create_engine(f"sqlite:///{run_dir / 'trace.db'}",
                            connect_args={"check_same_thread": False})
        s = Session(eng)
        s.add(AgentEvent(requirement_id=1, trace_id=self.RUN_ID, seq=99,
                         ts=datetime(2026, 10, 8, 12, 0, 1), kind="plan",
                         stage="planning", label="生成实现计划", status="ok"))
        s.commit()
        s.close()
        eng.dispose()

        r = app_client.get("/api/admin/evals", headers=auth).get_json()["runs"][0]
        assert r["args"]["with_plan"] is True

    def test_finished_tasks_are_not_marked_running(self, app_client, auth, runs_dir):
        """串行评测：后面已经有别的题在写事件，就说明这题已经跑完了。

        这一条不做的话所有已开始的题都会被含糊地标成「进行中」—— 一屏走过去
        看不出它是逐题推进的，看起来像 8 道题同时在跑。
        """
        _make_running_run(runs_dir, self.RUN_ID, rids=(1, 2))
        items = app_client.get(f"/api/admin/evals/{self.RUN_ID}",
                               headers=auth).get_json()["items"]
        assert [t["finished"] for t in items] == [True, False]

    def test_single_task_run_is_still_running(self, app_client, auth, runs_dir):
        """只有一题时没有「后面」可依 —— 它必然还在跑。"""
        _make_running_run(runs_dir, self.RUN_ID, rids=(1,))
        items = app_client.get(f"/api/admin/evals/{self.RUN_ID}",
                               headers=auth).get_json()["items"]
        assert items[0]["finished"] is False

    def test_progress_snapshot_gives_real_results(self, app_client, auth, runs_dir):
        """有进度快照时用真实结论，而不是一律「结论未知」。"""
        rd = _make_running_run(runs_dir, self.RUN_ID, rids=(1, 2))
        _make_progress(rd, [{"id": "t01", "name": "个人名片页", "level": 1,
                             "passed": True, "duration_s": 122.8, "rounds": 6,
                             "plan_used": True,
                             "assertions": [{"type": "has_title",
                                             "passed": True}]}])
        data = app_client.get(f"/api/admin/evals/{self.RUN_ID}",
                              headers=auth).get_json()
        assert data["running"] is True
        t1, t2 = data["items"]
        assert (t1["passed"], t1["finished"]) == (True, True)
        assert t1["duration_s"] == 122.8
        assert (t2["passed"], t2["finished"]) == (None, False)
        assert data["totals"]["passed"] == 1

        r = app_client.get("/api/admin/evals", headers=auth).get_json()["runs"][0]
        # 列表页显示的是「已跑完 N 题」：已开始数含正跑着的那题，会虚高一题
        assert r["totals"]["finished"] == 1
        assert r["totals"]["passed"] == 1
        assert r["args"]["with_plan"] is True, "参数优先信快照（评测进程自己写的）"

    def test_broken_progress_snapshot_degrades_quietly(self, app_client, auth,
                                                       runs_dir):
        """快照正好被写成一半时按「还没有快照」处理，不能报错。

        它是原子写，但读的一方仍要容错 —— 历史目录里也可能残留半截文件。
        """
        rd = _make_running_run(runs_dir, self.RUN_ID, rids=(1, 2))
        (rd / "progress.json").write_text("{ not json", encoding="utf-8")
        data = app_client.get(f"/api/admin/evals/{self.RUN_ID}",
                              headers=auth).get_json()
        assert data["running"] is True
        assert [t["passed"] for t in data["items"]] == [None, None]
        assert [t["finished"] for t in data["items"]] == [True, False]
