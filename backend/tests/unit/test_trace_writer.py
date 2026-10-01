# -*- coding: utf-8 -*-
"""
TraceWriter 单元测试 —— 重点是"content 寻址 + 有序索引"的还原正确性。

背景：上下文管线（存量遮蔽 / Compaction / 交付折叠）会**改写** messages[]，
add-only 差分无法表达"替换"与"删除"，会还原出不一致的 request。
这里的测试用真实执行日志做端到端校验，锁定这一不变量。
"""

import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REAL_LOG = "logs/agent_exec/157.jsonl"


@pytest.fixture(scope="module")
def engine():
    from models import models as _models
    import tempfile
    path = os.path.join(tempfile.gettempdir(), "test_trace_writer.db")
    if os.path.exists(path):
        os.remove(path)
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    _models.Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    # 清理，避免用例间互相污染
    sess.rollback()
    for tbl in ("agent_events", "agent_payloads", "trace_message_blobs"):
        sess.execute(__import__("sqlalchemy").text(f"DELETE FROM {tbl}"))
    sess.commit()
    sess.close()


def _load_real_turns(limit=None):
    """读真实日志里的 llm_turn，按 iteration 去重取最后一条。"""
    if not os.path.exists(REAL_LOG):
        pytest.skip(f"缺少样本日志 {REAL_LOG}")
    turns = []
    with open(REAL_LOG) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("kind") == "llm_turn" and isinstance(d.get("request"), dict):
                turns.append(d)
    byit = {}
    for t in turns:
        byit[t.get("iteration", -1)] = t
    out = [byit[k] for k in sorted(byit) if k >= 0]
    return out[:limit] if limit else out


def _norm(content):
    """与 TraceWriter._as_text 保持一致。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


# ---- 内容寻址基础 ----

class TestContentAddressing:

    def test_same_content_stored_once(self, db):
        from harness.observability.trace_writer import TraceWriter
        from models.models import TraceMessageBlob
        w = TraceWriter(db, requirement_id=9001)
        # 同一 content 出现在 3 轮里
        req = {"messages": [{"role": "system", "content": "SAME-SYSTEM-PROMPT"}]}
        for i in range(3):
            w.llm_call(call_id=f"c{i}", iteration=i, request=req, response={})

        rows = db.query(TraceMessageBlob).filter_by(requirement_id=9001).all()
        assert len(rows) == 1, "同一正文应只存一条 blob"
        assert rows[0].content == "SAME-SYSTEM-PROMPT"

    def test_different_content_stored_separately(self, db):
        from harness.observability.trace_writer import TraceWriter
        from models.models import TraceMessageBlob
        w = TraceWriter(db, requirement_id=9002)
        w.llm_call(iteration=0, request={"messages": [
            {"role": "system", "content": "A"},
            {"role": "user", "content": "B"},
        ]}, response={})

        rows = db.query(TraceMessageBlob).filter_by(requirement_id=9002).all()
        assert {r.content for r in rows} == {"A", "B"}


# ---- 核心：还原正确性 ----

class TestRestoreFidelity:

    def test_real_log_roundtrip_is_byte_exact(self, db):
        """★ 用真实执行日志端到端验证：写入 → 还原必须逐字节一致。"""
        from harness.observability.trace_writer import TraceWriter
        from models.models import AgentEvent
        turns = _load_real_turns()
        assert len(turns) >= 3, "样本轮次太少，无法验证累积行为"

        w = TraceWriter(db, requirement_id=9003)
        for i, t in enumerate(turns):
            w.llm_call(call_id=f"call{i}", iteration=t.get("iteration"),
                       request=t["request"], response=t.get("response"),
                       model="glm-5.3-flash")

        events = db.query(AgentEvent).filter_by(requirement_id=9003).order_by(
            AgentEvent.seq).all()
        assert len(events) == len(turns)

        for t, ev in zip(turns, events):
            restored = TraceWriter.restore_messages(db, 9003, ev.message_refs)
            original = t["request"].get("messages", [])
            assert len(restored) == len(original), f"iteration={ev.iteration} 条数不符"
            for j, (rb, og) in enumerate(zip(restored, original)):
                assert rb["role"] == og.get("role"), f"iter={ev.iteration} msg[{j}] role 不符"
                assert rb["content"] == _norm(og.get("content")), \
                    f"iter={ev.iteration} msg[{j}] content 不一致"

    def test_replaced_content_restores_new_value_not_old(self, db):
        """★ 模拟「存量遮蔽」：同一位置 content 被替换，必须还原出新版本。

        这正是 add-only 差分会出错的场景 —— 差分会把新版本当"新增"，
        还原时新旧两个版本同时存在。
        """
        from harness.observability.trace_writer import TraceWriter
        from models.models import AgentEvent
        w = TraceWriter(db, requirement_id=9004)

        # 第 1 轮：原文
        w.llm_call(iteration=0, request={"messages": [
            {"role": "system", "content": "HEAD"},
            {"role": "user", "content": "read_file 的完整返回结果 blah blah"},
        ]}, response={})

        # 第 2 轮：同一位置被替换成占位符（遮蔽发生）
        w.llm_call(iteration=1, request={"messages": [
            {"role": "system", "content": "HEAD"},
            {"role": "user", "content": "[内容已省略]"},
        ]}, response={})

        events = db.query(AgentEvent).filter_by(requirement_id=9004).order_by(
            AgentEvent.seq).all()

        first = TraceWriter.restore_messages(db, 9004, events[0].message_refs)
        second = TraceWriter.restore_messages(db, 9004, events[1].message_refs)

        assert first[1]["content"] == "read_file 的完整返回结果 blah blah"
        assert second[1]["content"] == "[内容已省略]", \
            "遮蔽后的新版本必须被还原出来，而不是退回旧版本"
        # 两个版本都存了 blob（原文仍需可回溯），但索引各指各的
        from models.models import TraceMessageBlob
        n = db.query(TraceMessageBlob).filter_by(requirement_id=9004).count()
        assert n == 3  # HEAD + 原文 + 占位符

    def test_restore_reports_missing_blobs(self, db, caplog):
        """正文缺失不能静默 —— 必须打 warning，且带 missing 标记。

        以前是静默降级成空串：运营后台上看不出「这条本来就是空的」和
        「正文没存下来」的区别，后者是事故却长得像正常数据。
        """
        from harness.observability.trace_writer import TraceWriter
        import logging
        with caplog.at_level(logging.WARNING):
            out = TraceWriter.restore_messages(db, 9999, [["nonexistent", "user"]])
        assert out == [{"role": "user", "content": None, "missing": True}]
        assert any("正文缺失" in r.message for r in caplog.records)

    def test_extra_fields_survive_roundtrip(self, db):
        """★ role/content 之外的字段必须原样回来。

        name="System" 是每轮注入的工作区状态；只按 content 去重的旧实现会把它
        还原成和普通用户输入无异的裸 message。切 OpenAI 原生 function calling
        后 tool_calls / tool_call_id 走的是同一条路。
        """
        from harness.observability.trace_writer import TraceWriter
        from models.models import AgentEvent
        w = TraceWriter(db, requirement_id=9011)
        w.llm_call(iteration=0, request={"messages": [
            {"role": "system", "content": "HEAD"},
            {"role": "user", "content": "帮我改一下", "name": "Human"},
            {"role": "user", "content": "工作区状态…", "name": "System"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "write_file",
                                          "arguments": "{}"}}]},
            {"role": "tool", "content": "ok", "tool_call_id": "c1"},
        ]}, response={})

        ev = db.query(AgentEvent).filter_by(requirement_id=9011).first()
        got = TraceWriter.restore_messages(db, 9011, ev.message_refs)

        assert got[1]["name"] == "Human"
        assert got[2]["name"] == "System", "系统注入必须能与真实用户输入区分"
        assert got[3]["tool_calls"][0]["function"]["name"] == "write_file"
        assert got[4]["tool_call_id"] == "c1"
        assert got[0]["content"] == "HEAD"

    def test_same_content_different_extra_not_merged(self, db):
        """正文相同但附加字段不同 —— 不能被去重合并成一条。"""
        from harness.observability.trace_writer import TraceWriter
        from models.models import TraceMessageBlob
        w = TraceWriter(db, requirement_id=9012)
        w.llm_call(iteration=0, request={"messages": [
            {"role": "user", "content": "同一段正文", "name": "Human"},
            {"role": "user", "content": "同一段正文", "name": "System"},
        ]}, response={})
        rows = db.query(TraceMessageBlob).filter_by(requirement_id=9012).all()
        assert len(rows) == 2, "附加字段不同的同正文 message 必须各存一份"


# ---- 事件字段 ----

class TestEventFields:

    def test_seq_increments_within_requirement(self, db):
        from harness.observability.trace_writer import TraceWriter
        from models.models import AgentEvent
        w = TraceWriter(db, requirement_id=9005)
        a = w.event("memory", "记忆匹配 · 5 条", meta={"hit": 2})
        b = w.event("plan", "规划完成")
        assert b == a + 1

        rows = db.query(AgentEvent).filter_by(requirement_id=9005).order_by(
            AgentEvent.seq).all()
        assert [r.kind for r in rows] == ["memory", "plan"]
        assert rows[0].meta == {"hit": 2}

    def test_turn_index_is_recorded(self, db):
        """turn_index 写入时确定：查询时从 dialogue_history 派生太脆弱。"""
        from harness.observability.trace_writer import TraceWriter
        from models.models import AgentEvent
        w = TraceWriter(db, requirement_id=9006, turn_index=2)
        w.event("coding", "第 3 轮编码")

        row = db.query(AgentEvent).filter_by(requirement_id=9006).first()
        assert row.turn_index == 2

    def test_tools_schema_deduped(self, db):
        """tools schema 每轮相同 → 整个 run 只存一份。"""
        from harness.observability.trace_writer import TraceWriter
        from models.models import AgentPayload
        tools = [{"name": "read_file"}, {"name": "write_file"}]
        w = TraceWriter(db, requirement_id=9007)
        for i in range(3):
            w.llm_call(iteration=i, request={"messages": [], "tools": tools},
                       response={})

        rows = db.query(AgentPayload).filter_by(
            requirement_id=9007, kind="tools_schema").all()
        assert len(rows) == 1

    def test_payload_id_populated_in_batch_mode(self, db):
        """★ 批量模式下 payload_id 不能为 None。

        曾出现：autocommit=False 时不 flush 就取 row.id → 得到 None →
        payload_id 关联丢失 → 点开事件查不到 response 明细。
        """
        from harness.observability.trace_writer import TraceWriter
        from models.models import AgentEvent, AgentPayload
        w = TraceWriter(db, requirement_id=9009, autocommit=False)
        w.llm_call(iteration=0,
                   request={"messages": [{"role": "user", "content": "hi"}],
                            "tools": [{"name": "read_file"}]},
                   response={"content": "world"}, model="m")
        w.flush()

        ev = db.query(AgentEvent).filter_by(requirement_id=9009).first()
        assert ev.payload_id is not None, "批量模式下 response 关联丢失"
        payload = db.query(AgentPayload).filter_by(id=ev.payload_id).first()
        assert payload is not None
        assert payload.response.get("content") == "world"
        assert ev.tools_ref is not None

    def test_tool_call_links_payload_with_args_and_content(self, db):
        """★ tool_call 的 payload 关联不能断。

        曾有两个 bug 叠加：① event() 没收 payload_id 参数（算完就丢）；
        ② _write_payload 在 response=None 时把 request_tail 整个丢掉，
        导致工具参数查不到、点开工具事件右栏空白。
        """
        from harness.observability.trace_writer import TraceWriter
        from models.models import AgentEvent, AgentPayload
        w = TraceWriter(db, requirement_id=9010, autocommit=False)
        w.tool_call(name="read_file", arguments={"filename": "a.css"},
                    content="文件内容 x" * 10, iteration=1)
        w.flush()

        ev = db.query(AgentEvent).filter_by(requirement_id=9010).first()
        assert ev.payload_id is not None, "tool_call 的 payload 关联丢失"
        p = db.query(AgentPayload).filter_by(id=ev.payload_id).first()
        assert p is not None
        assert p.tool_content and "文件内容 x" in p.tool_content
        assert p.response["request"]["name"] == "read_file"
        assert p.response["request"]["arguments"] == {"filename": "a.css"}

    def test_write_failure_does_not_raise(self, db):
        """可观测性写入失败不得阻断主流程。"""
        from harness.observability.trace_writer import TraceWriter

        class Broken:
            def add(self, *a, **k):
                raise RuntimeError("boom")
            def add_all(self, *a, **k):
                raise RuntimeError("boom")
            def commit(self, *a, **k):
                raise RuntimeError("boom")
            def rollback(self, *a, **k):
                pass

        w = TraceWriter(Broken(), requirement_id=9008)
        assert w.event("plan", "规划") == 0   # 返回 0 而不是抛异常


# ---- 统一埋点入口 ----

class TestRecordEntries:
    """`record_llm_turn` / `record_tool_call` / `record_event` 的行为契约。"""

    @pytest.fixture(autouse=True)
    def _silence_exec_log(self, monkeypatch):
        """屏蔽文件明细写入。

        record_llm_turn 会同步写 logs/agent_exec/<req>.jsonl（设计如此：文件明细
        是开发排查用的人读副本）。单测用假 requirement_id 跑，不屏蔽就会在真实
        日志目录里留下 9101.jsonl 这类垃圾文件，被后台当成真实需求。
        """
        import harness.observability.exec_log as exec_log
        monkeypatch.setattr(exec_log, "log_llm_turn",
                            lambda *a, **k: None)
        monkeypatch.setattr(exec_log, "log_tool_call",
                            lambda *a, **k: None)

    class _Resp:
        """最小 LLM 响应替身（只带埋点读取的字段）。"""
        def __init__(self, error=None, usage=None, content="ok"):
            self.content = content
            self.reasoning_content = None
            self.tool_calls = []
            self.usage = usage
            self.error = error
            self.finish_reason = "stop"

        @property
        def is_error(self):
            return self.error is not None

    def test_status_derived_from_response_error(self, db):
        """★ 调用方不传 status 时，失败的调用不能被记成 ok。

        否则列表页 error_count 失真、时间线上错误调用显示成功 ——
        排查时会直接跳过真正有问题的那一次调用。
        """
        from harness.observability.trace_writer import record_llm_turn
        from models.models import AgentEvent

        w = TraceWriterStub(db, requirement_id=9101)
        record_llm_turn(9101, stage="coding", response=self._Resp(error="boom"),
                        writer=w, status=None)
        record_llm_turn(9101, stage="coding", response=self._Resp(), writer=w,
                        status=None)

        rows = db.query(AgentEvent).filter_by(requirement_id=9101).order_by(
            AgentEvent.seq).all()
        assert [r.status for r in rows] == ["error", "ok"]

    def test_call_id_comes_from_context(self, db):
        """call_id 必须落库：它是 agent_events ↔ llm_traffic.log 的互查钥匙。"""
        from harness.observability.log_context import bind_call_id
        from harness.observability.trace_writer import record_llm_turn
        from models.models import AgentEvent

        bind_call_id("abcd1234")
        w = TraceWriterStub(db, requirement_id=9102)
        record_llm_turn(9102, stage="coding", response=self._Resp(), writer=w)

        ev = db.query(AgentEvent).filter_by(requirement_id=9102).first()
        assert ev.call_id == "abcd1234"

    def test_record_event_uses_contract_stage(self, db):
        """record_event 不传 stage 时取契约里该 kind 的默认阶段。"""
        from harness.observability.trace_writer import record_event
        from harness.observability.event_contract import kind_spec
        from models.models import AgentEvent

        w = TraceWriterStub(db, requirement_id=9103)
        record_event(9103, "verify", "验收未通过", writer=w,
                     meta={"failed_ac_ids": ["ac1"]})

        ev = db.query(AgentEvent).filter_by(requirement_id=9103).first()
        assert ev.stage == kind_spec("verify")["stage"]
        assert ev.meta["failed_ac_ids"] == ["ac1"]

    def test_large_tool_arguments_go_to_blob(self, db):
        """★ 大体积工具参数走 content 寻址：payload 只留预览，完整内容进 blob。

        write_file 的 arguments 里是整个产物文件，直接落 payload 等于每个
        写文件事件各存一份产物。
        """
        from models.models import AgentEvent, AgentPayload, TraceMessageBlob

        big = "x" * 5000
        w = TraceWriterStub(db, requirement_id=9104)
        w.tool_call(name="write_file",
                    arguments={"filename": "a.js", "content": big})

        ev = db.query(AgentEvent).filter_by(requirement_id=9104).first()
        refs = ev.meta["arg_refs"]
        assert refs and "content" in refs

        blob = db.query(TraceMessageBlob).filter_by(
            requirement_id=9104, content_hash=refs["content"]).first()
        assert blob is not None and blob.content == big, "完整内容必须可还原"

        payload = db.query(AgentPayload).filter_by(id=ev.payload_id).first()
        stored = payload.response["request"]["arguments"]
        assert len(stored["content"]) < 5000, "payload 里只留预览"
        assert stored["filename"] == "a.js", "小参数原样保留"

    def test_failure_counter_exposed(self, db):
        """埋点失败必须计数（后台 stats 透出），否则失败完全静默。"""
        from harness.observability.trace_writer import (
            TraceWriter, write_failure_count,
        )

        class Broken:
            def add(self, *a, **k):
                raise RuntimeError("boom")
            def add_all(self, *a, **k):
                raise RuntimeError("boom")
            def commit(self, *a, **k):
                raise RuntimeError("boom")
            def rollback(self, *a, **k):
                pass

        before = write_failure_count()
        TraceWriter(Broken(), requirement_id=9105).event("plan", "规划")
        assert write_failure_count() > before

    def test_shared_writer_reuses_seq_cache(self, db):
        """复用同一个 writer 时 seq 连续递增，且不重复回查 MAX。"""
        from models.models import AgentEvent

        w = TraceWriterStub(db, requirement_id=9106)
        seqs = [w.event("plan", f"e{i}") for i in range(3)]
        assert seqs == sorted(seqs) and len(set(seqs)) == 3

        rows = db.query(AgentEvent).filter_by(requirement_id=9106).all()
        assert sorted(r.seq for r in rows) == seqs


def TraceWriterStub(db, requirement_id, **kw):
    """测试用 TraceWriter（复用同一个 session，autocommit 打开）。"""
    from harness.observability.trace_writer import TraceWriter
    return TraceWriter(db, requirement_id=requirement_id, **kw)
