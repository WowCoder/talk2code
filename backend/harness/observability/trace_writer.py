# -*- coding: utf-8 -*-
"""
TraceWriter —— 可观测性事件写入器

把 Agent 执行过程中的事件写入 `agent_events`（索引）与 `trace_message_blobs`
（内容寻址正文），支撑运营后台的「轨迹列表 / 时间线 / Prompt 展开」。

核心设计：content 寻址 + 有序索引
--------------------------------
LLM 每轮的 request.messages[] 是**累积快照**，相邻轮次重复率最高 97%，
直接存全量会让单需求日志膨胀到数百 KB。

但**不能用差分**（只存本轮新增）：上下文管线的三重机制会改写 messages[]——

  - L3b 存量遮蔽：历史 read_file 结果 → 占位符（替换）
  - L5 Compaction：最旧段 → 一条 LLM summary（替换 + 删除）
  - 交付边界折叠：追加需求时工具轨迹整体不带入（大幅重建）

差分是 add-only 语义，无法表达"替换"与"删除"，还原出的 request 与实际发给
LLM 的**不一致**。

因此改为：每条 message 的正文按 hash 去重存储 + 每轮存一个有序
`[[content_hash, role], ...]` 索引。改写发生时正文是新 hash，索引自然指向它，
三种机制全部被吸收。实测 7 个样本（含最高 26 次遮蔽的重度样本）**逐字节精确还原**。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

# 写入失败计数 —— 埋点失败只告警不抛出（不能阻断主生成流程），但绝不能静默：
# 「后台没数据」和「没产生数据」是两件完全不同的事，靠这个计数区分。
# 经 /api/admin/traces/stats 的 writer_failures 透出。
_WRITE_FAILURES = 0
_WRITE_FAILURES_LOCK = threading.Lock()


def _count_failure() -> None:
    global _WRITE_FAILURES
    with _WRITE_FAILURES_LOCK:
        _WRITE_FAILURES += 1


def write_failure_count() -> int:
    """累计的埋点写入失败次数（进程内，重启归零）。"""
    return _WRITE_FAILURES


# 工具参数中单个字符串超过这个长度就走 content 寻址 —— 典型是 write_file 的
# 整个文件内容。直接塞进 payload 的话，每个写文件事件都各存一份产物，
# 3.2x 的压缩成果会被吃掉；而且同一份产物往往还会出现在后续 read_file 结果里。
_MAX_ARG_LEN = 2000

# 事件类型常量 —— 唯一定义处是 event_contract.py（那里还带中文名与配色，
# 前端直接拉那份契约渲染）。此处 re-export 只为兼容既有导入路径。
from harness.observability.event_contract import (  # noqa: F401
    KIND_INTENT, KIND_MEMORY, KIND_CLARIFY, KIND_PLAN, KIND_CONFIRM,
    KIND_CODING, KIND_LLM_TURN, KIND_TOOL_CALL, KIND_VERIFY, KIND_REPAIR,
    KIND_QUALITY_GATE, KIND_ROLLBACK, KIND_DELIVER,
    STAGE_PLANNING, STAGE_CODING, STAGE_VERIFYING, STAGE_REPAIRING,
    STAGE_DELIVERING,
)


def content_hash(content) -> str:
    """内容寻址键：sha256 前 16 位（16 hex chars ≈ 64 bit，碰撞概率可忽略）。"""
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _as_text(content) -> str:
    """把 message content 归一化成存储用的字符串。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, sort_keys=True)


def _message_hash(message: dict) -> str:
    """整条 message 的内容寻址键。

    必须把 role/content 之外的字段（name / tool_calls / tool_call_id）一起算进
    哈希：只按 content 算的话，正文相同但附加字段不同的两条 message 会被合并成
    一条，还原时就分不清了。

    实测：全库 34067 条 message 中 3141 条（9.2%）带 name="System"（每轮注入的
    工作区状态）；只按 content 去重的旧实现把它们还原成了与普通用户输入无异的
    裸 message，运营后台上看不出区别。
    """
    return content_hash(_as_text(message))


class TraceWriter:
    """写入 `agent_events` + `trace_message_blobs`。

    用法::

        w = TraceWriter(db, requirement_id=123, turn_index=0)
        w.event(KIND_MEMORY, "记忆匹配 · 5 条", meta={"hit": 2})
        w.llm_call(call_id="abc", iteration=3, request=req, response=resp,
                   model="glm-5.3-flash", duration_ms=18400)

    写入失败只告警、不抛出：可观测性不得阻断主生成流程。
    """

    def __init__(self, db_session, requirement_id: int, trace_id: str = None,
                 turn_index: int = 0, autocommit: bool = True):
        self._db = db_session
        self.rid = requirement_id
        self.trace_id = trace_id
        self.turn_index = turn_index
        # 回填历史日志时要关掉：数千条记录逐条 commit 会慢一个数量级，
        # 由调用方分批 flush。
        self.autocommit = autocommit
        self._seq_cache = None
        # 去重缓存：同一个 run 内已写过的 blob hash / tools schema 不再回查 DB。
        # 命中率很高（跨轮次正文复用率 82%~92%），省掉的是每个事件的两次查询。
        # ⚠️ 只有**确认落库成功**的 hash 才能进 _seen_hashes（见 _promote_pending）：
        #    先把新 hash 记成"已存在"，一旦这次写入失败，之后永远不再补写这条正文，
        #    还原时就变成静默缺失。
        self._seen_hashes: set = set()
        self._pending_hashes: set = set()
        self._seen_tools: set = set()
        self._pending_tools: set = set()

    def flush(self):
        """批量模式下由调用方显式提交。"""
        try:
            self._db.commit()
            self._promote_pending()
        except Exception as e:
            logger.warning("[TraceWriter] flush 失败：%s", e)
            self._pending_hashes.clear()
            self._pending_tools.clear()
            try:
                self._db.rollback()
            except Exception:
                pass

    def _promote_pending(self):
        """把本批新写、**已确认提交**的 hash 从 pending 提升为 seen。"""
        if self._pending_hashes:
            self._seen_hashes.update(self._pending_hashes)
            self._pending_hashes.clear()
        if self._pending_tools:
            self._seen_tools.update(self._pending_tools)
            self._pending_tools.clear()

    def _commit_row(self, row, label: str) -> bool:
        """写入一行。批量模式下用 SAVEPOINT 隔离 —— 单条失败只回滚这一条。

        不能失败就直接 `rollback()`：批量模式（autocommit=False）下，同一批次
        里已 add 但未提交的记录会一起被带走。回填脚本每 50 条才 flush 一次，
        一条坏记录最坏会吞掉 49 条好的。
        """
        try:
            if self.autocommit:
                self._db.add(row)
                self._db.commit()
                # 提交成功才把本批新写的 blob / tools 登记为"已存在"
                self._promote_pending()
            else:
                with self._db.begin_nested():
                    self._db.add(row)
            return True
        except Exception as e:
            logger.warning("[TraceWriter] 写入%s失败（不阻断）：%s", label, e)
            _count_failure()
            # 本批未提交的新正文不能算已存在，否则后续不会补写
            self._pending_hashes.clear()
            self._pending_tools.clear()
            if self.autocommit:
                try:
                    self._db.rollback()
                except Exception:
                    pass
            return False

    def _commit_event_row(self, row, label: str = "事件") -> bool:
        """事件行写入 + `(requirement_id, seq)` 唯一冲突重试一次。

        seq 是「读 MAX + 1」分配，并发写（chat 与 resume 竞争、回填与运行并行）
        可能撞上唯一索引。撞了就重读 MAX 重排一次 —— 时间线排序依赖 seq 唯一，
        不重试等于丢一条事件。
        """
        if self._commit_row(row, label):
            return True
        if not self.autocommit:
            return False
        # 唯一冲突（或其他可恢复错误）：重读当前最大 seq 后重排重试
        try:
            self._db.rollback()
            self._seq_cache = self._current_max_seq() + 1
            row.seq = self._seq_cache
            self._db.add(row)
            self._db.commit()
            logger.info("[TraceWriter] %s seq 冲突后已重排为 %s", label, row.seq)
            return True
        except Exception as e:
            logger.warning("[TraceWriter] %s seq 冲突重试失败：%s", label, e)
            _count_failure()
            try:
                self._db.rollback()
            except Exception:
                pass
            return False

    # ---------- 序号 ----------

    def _next_seq(self) -> int:
        """需求内全局递增序号。首次调用回溯 DB 当前最大值。"""
        if self._seq_cache is None:
            self._seq_cache = self._current_max_seq()
        self._seq_cache += 1
        return self._seq_cache

    def _current_max_seq(self) -> int:
        try:
            from models.models import AgentEvent
            from sqlalchemy import func as _func
            return self._db.query(_func.max(AgentEvent.seq)).filter(
                AgentEvent.requirement_id == self.rid
            ).scalar() or 0
        except Exception as e:
            logger.warning("[TraceWriter] 读取最大 seq 失败，从 0 开始：%s", e)
            return 0

    # ---------- 内容寻址 ----------

    def _write_messages(self, messages: list) -> list:
        """把整条 message 落 blob 表，返回有序索引 [[hash, role], ...]。

        正文（content）仍单独存一列 —— 跨轮次的正文复用是压缩率的来源
        （整体 3.21x），不能因为附加字段不同而重复存整份正文。
        附加字段（name / tool_calls / tool_call_id …）只在非空时落到 msg_json。
        """
        items = [m for m in messages if isinstance(m, dict)]
        refs = [[_message_hash(m), m.get("role")] for m in items]

        try:
            from models.models import TraceMessageBlob
        except Exception as e:
            logger.warning("[TraceWriter] 导入 TraceMessageBlob 失败：%s", e)
            return refs

        unique = {}
        for m in items:
            text = _as_text(m.get("content"))
            extra = {k: v for k, v in m.items() if k not in ("role", "content")}
            unique[_message_hash(m)] = (text, m.get("role"), extra or None)

        try:
            # 内存缓存里已有的 hash 直接跳过：DB 已确认存在的（_seen_hashes）+
            # 本次运行已排队待提交的（_pending_hashes）。后者必须一起跳过 ——
            # 批量模式下 add 了但没 commit 的行查不出来，重复 add 同一个
            # (requirement_id, content_hash) 主键会在提交时炸。
            pending = {h: v for h, v in unique.items()
                       if h not in self._seen_hashes
                       and h not in self._pending_hashes}
            if pending:
                existing = {
                    row[0] for row in self._db.query(TraceMessageBlob.content_hash).filter(
                        TraceMessageBlob.requirement_id == self.rid,
                        TraceMessageBlob.content_hash.in_(list(pending.keys())),
                    ).all()
                }
                self._seen_hashes.update(existing)
                new_rows = [
                    TraceMessageBlob(
                        requirement_id=self.rid,
                        content_hash=ch,
                        role=role,
                        content=text,
                        char_len=len(text),
                        msg_json=extra,
                    )
                    for ch, (text, role, extra) in pending.items()
                    if ch not in existing
                ]
                # 排队登记：提交成功后才提升为 seen（见 _promote_pending）。
                # 提交失败会清空 pending，下一次写入会把正文补上。
                self._pending_hashes.update(pending.keys())
                if new_rows:
                    self._db.add_all(new_rows)
        except Exception as e:
            # blob 写失败不阻断：索引仍然生成，还原时按 missing 显式暴露
            logger.warning("[TraceWriter] 写入 message blob 失败（不阻断）：%s", e)
            _count_failure()
            self._pending_hashes.clear()

        return refs

    def _write_blobs(self, pairs: list) -> list:
        """把 [(content, role)] 落 blob 表。工具结果等非 message 正文走这条。"""
        return self._write_messages(
            [{"role": r, "content": c} for c, r in pairs])

    # ---------- 事件写入 ----------

    def event(self, kind: str, label: str, *, stage=None, status="ok",
              call_id=None, iteration=None, meta=None, duration_ms=None,
              model=None, tokens_in=0, tokens_out=0, cached_tokens=None, cost=0.0,
              payload_id=None, ts=None) -> int:
        """写一条事件索引，返回 seq；失败返回 0。"""
        try:
            from models.models import AgentEvent
            seq = self._next_seq()
            row = AgentEvent(
                requirement_id=self.rid,
                trace_id=self.trace_id,
                call_id=call_id,
                seq=seq,
                turn_index=self.turn_index,
                iteration=iteration,
                ts=ts or datetime.utcnow(),
                kind=kind,
                stage=stage,
                label=label,
                status=status,
                model=model,
                duration_ms=duration_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cached_tokens=cached_tokens,
                cost=cost,
                payload_id=payload_id,
                meta=meta or {},
            )
            return seq if self._commit_event_row(row, "事件") else 0
        except Exception as e:
            logger.warning("[TraceWriter] 写入事件失败（不阻断）：%s", e)
            _count_failure()
            return 0

    def llm_call(self, *, call_id=None, iteration=None, request: dict = None,
                 response: dict = None, model=None, duration_ms=None,
                 tokens_in=0, tokens_out=0, cached_tokens=None, cost=0.0, stage=None,
                 label=None, status="ok", meta=None, ts=None) -> int:
        """写一次 LLM 调用。

        request.messages[] 走内容寻址只存索引；tools schema 单独去重。
        response 与其余非 message 字段落到 agent_payloads。
        """
        refs = []
        tools_ref = None
        request_tail = None

        if isinstance(request, dict):
            messages = request.get("messages") or []
            # 整条 message 一起存：只存 role/content 会丢掉 name / tool_calls
            refs = self._write_messages(messages)
            tools = request.get("tools")
            if tools:
                tools_ref = self._write_tools(tools)
            # 除 messages/tools 之外的顶层参数（temperature / stream 等）
            request_tail = {k: v for k, v in request.items()
                            if k not in ("messages", "tools")}

        payload_id = self._write_payload(
            request_tail=request_tail, response=response, tools_ref=tools_ref,
            kind=KIND_LLM_TURN,
        )

        n_msg = len(refs)
        try:
            from models.models import AgentEvent
            seq = self._next_seq()
            row = AgentEvent(
                requirement_id=self.rid,
                trace_id=self.trace_id,
                call_id=call_id,
                seq=seq,
                turn_index=self.turn_index,
                iteration=iteration,
                ts=ts or datetime.utcnow(),
                kind=KIND_LLM_TURN,
                stage=stage,
                label=label or f"LLM 调用 · {n_msg} messages",
                status=status,
                model=model or (request_tail or {}).get("model") if isinstance(request_tail, dict) else model,
                duration_ms=duration_ms,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cached_tokens=cached_tokens,
                cost=cost,
                message_refs=refs,
                tools_ref=tools_ref,
                payload_id=payload_id,
                meta={**(meta or {}), "message_count": n_msg},
            )
            return seq if self._commit_event_row(row, "LLM 事件") else 0
        except Exception as e:
            logger.warning("[TraceWriter] 写入 LLM 事件失败（不阻断）：%s", e)
            _count_failure()
            return 0

    def tool_call(self, *, name: str, arguments=None, content=None,
                  iteration=None, duration_ms=None, status="ok",
                  meta=None, ts=None, stage=None) -> int:
        """写一次工具调用。content 同样走内容寻址（read_file 结果高度重复）。

        stage 默认编码阶段；修复阶段的 write_file 必须显式传 repairing，
        否则修复轮的工具调用会被算进编码阶段 —— 时间线上看不出哪次改动是修 bug。
        """
        content_key = None
        if content is not None:
            content_key = self._write_blobs([(content, "tool")])[0][0]

        # write_file 的参数里是整个产物文件：超过阈值走寻址，避免每个写文件
        # 事件各存一份产物（与 message 同一套去重机制）。
        args_stored, arg_refs = self._prepare_arguments(arguments)

        payload_id = self._write_payload(
            kind=KIND_TOOL_CALL, tool_content=content,
            request_tail={"name": name, "arguments": args_stored,
                          "arg_refs": arg_refs or None},
        )

        return self.event(
            KIND_TOOL_CALL,
            label=f"{name}",
            stage=stage or STAGE_CODING,
            status=status,
            iteration=iteration,
            duration_ms=duration_ms,
            payload_id=payload_id,
            meta={**(meta or {}), "content_ref": content_key,
                  "arg_refs": arg_refs or None},
            ts=ts,
        )

    def _prepare_arguments(self, arguments):
        """大体积字符串参数走 content 寻址，返回 (落库参数, {key: hash})。

        运行时与回填共用这一套（回填不再另行截断）：同一份数据在新旧需求里
        必须是同一种形态，否则按参数检索历史数据会对不上。
        """
        if not isinstance(arguments, dict):
            return arguments, {}
        out, refs = {}, {}
        for k, v in arguments.items():
            if isinstance(v, str) and len(v) > _MAX_ARG_LEN:
                refs[k] = self._write_blobs([(v, "tool")])[0][0]
                out[k] = (v[:_MAX_ARG_LEN]
                          + f"... [完整内容已归档，共 {len(v)} 字符]")
            else:
                out[k] = v
        return out, refs

    # ---------- payload ----------

    def _write_tools(self, tools) -> str:
        """tools schema 去重：整个 run 内只存一份。"""
        key = content_hash(tools)
        # seen=已提交，pending=本批已排队 —— 两者都要跳过，理由同 _write_messages
        if key in self._seen_tools or key in self._pending_tools:
            return key
        try:
            from models.models import AgentPayload
            exists = self._db.query(AgentPayload.id).filter(
                AgentPayload.requirement_id == self.rid,
                AgentPayload.tools_ref == key,
            ).first()
            if exists:
                self._seen_tools.add(key)
            else:
                self._pending_tools.add(key)
                self._db.add(AgentPayload(
                    requirement_id=self.rid,
                    kind="tools_schema",
                    tools_ref=key,
                    response={"tools": tools},
                ))
        except Exception as e:
            logger.warning("[TraceWriter] 写入 tools schema 失败（不阻断）：%s", e)
            _count_failure()
            self._pending_tools.discard(key)
        return key

    def _write_payload(self, *, kind, request_tail=None, response=None,
                       tools_ref=None, tool_content=None) -> int:
        try:
            from models.models import AgentPayload
            # 合并规则：response 为主，request_tail 并入同一 JSON。
            # 注意 tool_call 只带 request_tail（name/arguments）没有 response ——
            # 曾因 response=None 时直接跳过合并，把参数整个丢掉。
            body = response if isinstance(response, dict) else (
                {"raw": response} if response is not None else {})
            if request_tail:
                body = {**body, "request": request_tail}
            row = AgentPayload(
                requirement_id=self.rid,
                kind=kind,
                tools_ref=tools_ref,
                response=body or None,
                tool_content=tool_content,
            )
            # 必须先 flush 拿到自增 id：批量模式（autocommit=False）下不 flush
            # 的话 row.id 是 None，payload_id 关联会丢失，点开事件查不到明细。
            if self.autocommit:
                self._db.add(row)
                self._db.flush()
                self._db.commit()
                # 这个 commit 同时把前面 add 的 blob / tools 行落库了，
                # 所以待提交登记在此提升为"已存在"
                self._promote_pending()
            else:
                # SAVEPOINT 里 flush：拿到 id 的同时把这条 INSERT 与同批次隔离
                with self._db.begin_nested():
                    self._db.add(row)
                    self._db.flush()
            return row.id
        except Exception as e:
            logger.warning("[TraceWriter] 写入 payload 失败（不阻断）：%s", e)
            _count_failure()
            if self.autocommit:
                try:
                    self._db.rollback()
                except Exception:
                    pass
            return None

    # ---------- 还原 ----------

    @staticmethod
    def restore_messages(db_session, requirement_id: int, refs) -> list:
        """按有序索引还原 messages[]。

        一次批量查询取出全部正文，再按 refs 顺序拼回。role/content 之外的字段
        从 msg_json 合并回来。

        缺正文的条目带 `missing=True`，不再静默降级成空串 —— 空串在运营后台上
        看不出是「这条 message 本来就空」还是「正文没存下来」，后者是事故。
        """
        if not refs:
            return []
        try:
            from models.models import TraceMessageBlob
            hashes = [r[0] for r in refs if r]
            rows = db_session.query(TraceMessageBlob).filter(
                TraceMessageBlob.requirement_id == requirement_id,
                TraceMessageBlob.content_hash.in_(hashes),
            ).all()
            # getattr：msg_json 是后加的列，迁移未跑到时不能让整次还原失败
            store = {r.content_hash: (r.content, getattr(r, "msg_json", None))
                     for r in rows}
        except Exception as e:
            logger.warning("[TraceWriter] 还原 messages 失败：%s", e)
            return []

        missing = [h for h in hashes if h not in store]
        if missing:
            # 不静默：正文缺失说明 blob 写入失败或被清理，必须暴露
            logger.warning(
                "[TraceWriter] req=%s 有 %d/%d 条 message 正文缺失（blob 丢失）",
                requirement_id, len(missing), len(hashes),
            )

        out = []
        for r in refs:
            h, role = r[0], r[1]
            if h not in store:
                out.append({"role": role, "content": None, "missing": True})
                continue
            content, extra = store[h]
            item = {"role": role, "content": content}
            if extra:
                item.update(extra)
            out.append(item)
        return out


# ---------- 统一埋点入口 ----------
# 运行时各阶段（planning / coding / verifying / repairing）一律走这三个函数，
# 不要再各自拼 exec_log + TraceWriter —— 分开写的结果就是「文件里有、库里没有」，
# 运营后台的时间线缺了验收与修复两段却没人发现。


def _usage_dict(usage) -> dict:
    """usage 可能是 dict（jsonl 回放）也可能是响应对象（运行时），统一成 dict。

    缓存命中字段必须一并带出来：只保留 prompt/completion 四个键的话，
    运行时（对象路径）拿到的 usage 里没有任何缓存的痕迹，落库只能是 0 ——
    而 jsonl 回放路径（dict 原样返回）却带着，同一份数据两条路两种结果。
    """
    if not usage:
        return {}
    if isinstance(usage, dict):
        return usage
    out = {k: getattr(usage, k, None)
           for k in ("prompt_tokens", "completion_tokens",
                     "input_tokens", "output_tokens")}
    for k in ("prompt_tokens_details", "prompt_cache_hit_tokens",
              "cache_read_input_tokens"):
        v = getattr(usage, k, None)
        if v is None:
            continue
        # SDK 对象形态下 prompt_tokens_details 是带 cached_tokens 属性的对象
        out[k] = ({"cached_tokens": v.cached_tokens}
                  if hasattr(v, "cached_tokens") else v)
    return out


def _tokens_from_usage(usage: dict) -> tuple:
    u = usage or {}
    return (int(u.get("prompt_tokens") or u.get("input_tokens") or 0),
            int(u.get("completion_tokens") or u.get("output_tokens") or 0))


def _cached_from_usage(usage: dict):
    """命中 KV 缓存的 input token 数（**是 tokens_in 的子集**，不是第三类 token）。

    ⚠️ 返回 `None` 表示**这次调用没上报**缓存信息，与 `0`（上报了、确实没命中）
    是两件完全不同的事。落库必须保留这个区别 —— 否则哪天供应商不再返回这个
    字段，界面上会表现为「命中率掉到 0%」，与「缓存真的失效了」长得一模一样，
    而这两者的处置方式完全相反。

    解析规则复用 `CostTracker.extract_cache_hit`：后台展示的命中量必须与
    累计成本里记的命中量同源。各写一份的话，两个数字迟早会互相矛盾。
    """
    u = usage or {}
    if not u:
        return None
    det = u.get("prompt_tokens_details")
    det = det if isinstance(det, dict) else {}
    reported = (det.get("cached_tokens") is not None
                or u.get("prompt_cache_hit_tokens") is not None
                or u.get("cache_read_input_tokens") is not None)
    if not reported:
        return None
    try:
        from harness.observability.cost import CostTracker
        return int(CostTracker.extract_cache_hit(u) or 0)
    except Exception:
        return None


def _open_writer(requirement_id, trace_id=None, turn_index=0):
    """开一个独立 session 的 TraceWriter。用完由调用方 close。

    每次调用开一个短 session 而不是复用主流程的：可观测性写入必须和生成主流程
    的事务解耦 —— 主流程后来回滚的话，不该把已经观测到的事实一起抹掉。

    高频路径（ToolCallLoop 的每个 LLM turn / 每次工具调用）不要用这个：
    传 `writer=` 复用调用方持有的长 writer，省掉每事件一次的 session 创建与
    MAX(seq) 查询（见 TraceWriter 的 run 级复用约定）。
    """
    from models.models import SessionLocal
    db = SessionLocal()
    return db, TraceWriter(db, requirement_id=requirement_id,
                           trace_id=trace_id, turn_index=turn_index)


def _call_id_or_none() -> str:
    """取当前上下文里最近一次 LLM 请求的 call_id（llm/client 发请求时绑定）。"""
    try:
        from harness.observability.log_context import current_call_id
        return current_call_id() or None
    except Exception:
        return None


def record_llm_turn(requirement_id, *, stage, iteration=None, model=None,
                    system_prompt=None, prompt=None, messages=None,
                    tools=None, response=None, thinking=None, latency_ms=None,
                    turn_index=0, trace_id=None, label=None, status=None,
                    writer=None):
    """一次 LLM 调用的统一埋点：写文件明细 + 运营后台索引（缺一不可）。

    只写文件 → 后台查不到；只写库 → reasoning 与原始 response 这些排查细节没了。

    `status` 不传时从 `response.is_error` 推导 —— 调用方漏传不能让失败的调用
    在后台显示成成功（列表页 error_count 会跟着失真）。
    `writer` 传入时复用它（ToolCallLoop 的高频路径），否则开短 session。
    """
    if status is None:
        status = "error" if getattr(response, "is_error", False) else "ok"

    msgs = messages if messages is not None else [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]

    try:
        from harness.observability import exec_log
        exec_log.log_llm_turn(
            requirement_id, iteration, model, msgs, tools or [], response,
            thinking=thinking, latency_ms=latency_ms, stage=stage,
        )
    except Exception:
        pass  # 文件明细失败无所谓，库里的索引才是后台的数据源

    if not requirement_id:
        return None

    db = None
    try:
        w = writer
        if w is None:
            db, w = _open_writer(requirement_id, trace_id, turn_index)
        usage = _usage_dict(getattr(response, "usage", None))
        tin, tout = _tokens_from_usage(usage)
        cached = _cached_from_usage(usage)
        resp = None
        if response is not None:
            resp = {
                "content": getattr(response, "content", None),
                "reasoning_content": getattr(response, "reasoning_content", None),
                "tool_calls": [
                    {"name": tc.name, "arguments": tc.arguments}
                    for tc in (getattr(response, "tool_calls", None) or [])
                ],
                "usage": usage or None,
                "is_error": getattr(response, "is_error", None),
                "error": getattr(response, "error", None),
            }
        try:
            from harness.observability.cost import estimate_cost_usd
            cost = estimate_cost_usd(model, tin, tout)
        except Exception:
            cost = 0.0
        return w.llm_call(
            # call_id 从上下文取：与 llm_traffic.log 里同一次请求的 call_id 一致，
            # 于是后台点开某个 llm_turn 能直接查到当时的传输层原文。
            call_id=_call_id_or_none(),
            iteration=iteration,
            request={"messages": msgs, "tools": tools or []},
            response=resp, model=model,
            duration_ms=int(latency_ms) if latency_ms else None,
            tokens_in=tin, tokens_out=tout, cached_tokens=cached, cost=cost,
            stage=stage, label=label, status=status,
            meta={"thinking": bool(thinking), "has_usage": bool(usage)},
        )
    except Exception as e:
        logger.warning("[TraceWriter] record_llm_turn 失败（不阻断）：%s", e)
        _count_failure()
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def record_tool_call(requirement_id, *, name, arguments=None, result=None,
                     iteration=None, stage=None, turn_index=0,
                     trace_id=None, status=None, writer=None):
    """一次工具调用的统一埋点。`writer` 传入时复用（见 record_llm_turn）。"""
    success = getattr(result, "success", None)
    content = getattr(result, "content", None)
    blocked = getattr(result, "blocked", None)

    try:
        from harness.observability import exec_log
        exec_log.log_tool_call(requirement_id, iteration, name, arguments, result)
    except Exception:
        pass

    if not requirement_id:
        return None

    db = None
    try:
        w = writer
        if w is None:
            db, w = _open_writer(requirement_id, trace_id, turn_index)
        return w.tool_call(
            name=name, arguments=arguments, content=content,
            iteration=iteration, stage=stage or STAGE_CODING,
            status=status or ("ok" if success else "error"),
            meta={"blocked": bool(blocked)},
        )
    except Exception as e:
        logger.warning("[TraceWriter] record_tool_call 失败（不阻断）：%s", e)
        _count_failure()
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def record_event(requirement_id, kind, label, *, stage=None, status="ok",
                 turn_index=0, trace_id=None, meta=None, duration_ms=None,
                 iteration=None, writer=None):
    """里程碑事件的统一埋点（意图 / 记忆 / 澄清 / 规划 / 确认 / 编码 /
    验收结论 / 修复 / 质量门禁 / 回滚 / 交付）。

    为什么必须有这一层：只记录 llm_turn 与 tool_call 的时间线是一串流水账，
    排查时得逐条点开猜「为什么没过验收」。里程碑事件把结论写进 `label` 与
    `meta`，时间线本身就能回答问题。

    `stage` 不传时取契约里该 kind 的默认阶段（唯一定义处见 event_contract.py）。
    """
    if not requirement_id:
        return None
    if stage is None:
        try:
            from harness.observability.event_contract import kind_spec
            stage = kind_spec(kind).get("stage")
        except Exception:
            stage = None

    db = None
    try:
        w = writer
        if w is None:
            db, w = _open_writer(requirement_id, trace_id, turn_index)
        return w.event(kind, label, stage=stage, status=status,
                       iteration=iteration, meta=meta or {},
                       duration_ms=duration_ms)
    except Exception as e:
        logger.warning("[TraceWriter] record_event 失败（不阻断）：%s", e)
        _count_failure()
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
