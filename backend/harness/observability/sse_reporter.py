# -*- coding: utf-8 -*-
"""
SSEReporter —— SSE 事件统一管理
"""

from utils.sse import SSEMessage, get_current_timestamp
from harness.observability.logger import get_logger

logger = get_logger(__name__)


class SSEReporter:
    """SSE 事件统一管理，通过 SSEManager 推送事件"""

    def __init__(self, sse_manager):
        self.sse = sse_manager

    def progress(self, requirement_id: int, percent: int, message: str = "", stage: str = ""):
        """推送执行进度。

        Args:
            requirement_id: 需求 ID
            percent: 百分比（0-100）
            message: 人类可读的「当前在做什么」文案，直接显示在前端进度条上
            stage: 阶段标识（planning / coding / verifying / repairing），
                   供前端渲染阶段指示器；留空表示沿用上一阶段。

        文案准则：`message` 必须描述**动作**（"正在写 js/app.js"），
        而不是角色名（"开发工程师"）——后者不携带任何进展信息，
        是「AI 正在处理…」这类无信息展示的根因。
        """
        payload = {
            "current_agent": message, "progress": percent, "status": "processing"
        }
        if stage:
            payload["stage"] = stage
        self._send(requirement_id, "progress", payload)

    def dialogue(self, requirement_id: int, role: str, name: str, content: str, status: str = ""):
        self._send(requirement_id, "dialogue", {
            "role": role, "name": name, "content": content,
            "timestamp": get_current_timestamp(), "status": status
        })

    def code(self, requirement_id: int, files: list):
        self._send(requirement_id, "code", {"files": files})

    def tool_call(self, requirement_id: int, tool_name: str, arguments: dict):
        readable = self._make_readable(tool_name, arguments)
        self._send(requirement_id, "tool_call", {
            "tool_name": tool_name, "arguments": arguments, "readable": readable
        })

    def tool_result(self, requirement_id: int, tool_name: str, success: bool,
                    summary: str = "", error: str = ""):
        self._send(requirement_id, "tool_result", {
            "tool_name": tool_name, "success": success,
            "summary": summary, "error": error
        })

    def thinking(self, requirement_id: int, content: str, name: str = ""):
        self._send(requirement_id, "thinking", {"content": content, "name": name})

    def hook_check(self, requirement_id: int, hook_name: str, passed: bool, message: str = ""):
        self._send(requirement_id, "hook_check", {
            "hook_name": hook_name, "passed": passed, "message": message
        })

    def preview(self, requirement_id: int, report: dict):
        """推送无头浏览器运行验证结果（console.error / JS 异常 / 资源加载失败）"""
        self._send(requirement_id, "preview", {
            "available": report.get("available", True),
            "passed": len(report.get("errors", [])) == 0,
            "errors": report.get("errors", []),
            "logs": report.get("logs", []),
            "url": report.get("url", ""),
        })

    def trace_summary(self, requirement_id: int, trace_data: dict):
        self._send(requirement_id, "trace_summary", trace_data)

    def iteration_batch(self, requirement_id: int, batch):
        """推送一轮迭代的批量事件（替代逐个 tool_call/tool_result/thinking SSE）

        接受 IterationBatchEvent 或 dict（向后兼容）。
        IterationBatchEvent 通过 .to_dict() 序列化为 dict 后通过 SSE 发送。
        """
        from harness.events import IterationBatchEvent
        if isinstance(batch, IterationBatchEvent):
            data = batch.to_dict()
        else:
            data = batch  # 兼容旧的 dict 调用方式
        # 根因防御（「开发工程师 0 个操作」幽灵卡片）：没有操作列表的迭代卡片对前端
        # 毫无意义，且会写入 SSE 缓冲，断线重连/页面重连整段回放时放大成一批空卡片。
        # 在唯一出口统一丢弃，保护所有客户端（含未刷新的旧版前端 bundle）。
        # 正常轮次不会误杀：runtime.py 只在 batch_tools 非空时才构造事件。
        if not (data.get("tools") or []):
            logger.warning(
                f"丢弃无操作列表的 iteration_batch 事件 (req_id={requirement_id}, "
                f"iteration={data.get('iteration')}, coder_name={data.get('coder_name')})"
            )
            return
        self._send(requirement_id, "iteration_batch", data)

    # ---- 迭代轮次实时累积（取代旧的整轮一次性 iteration_batch）----
    # 一轮开始 → 前端创建一张可累积的轮次卡片；过程每步 iteration_append 实时填充；
    # 轮次结束 → iteration_end 固定卡片。刷新页面时由 dialogue_history 的迭代记录恢复静态卡片。
    def iteration_start(self, requirement_id: int, batch):
        """一轮迭代开始：前端据此创建可实时累积的轮次卡片。"""
        from harness.events import IterationBatchEvent
        if isinstance(batch, IterationBatchEvent):
            data = batch.to_dict()
        else:
            data = dict(batch)
        data.setdefault("tools", [])
        self._send(requirement_id, "iteration_start", data)

    def iteration_append(self, requirement_id: int, tool):
        """一轮迭代中的单个工具操作：前端实时追加进当前轮次卡片。"""
        tool_dict = tool.to_dict() if hasattr(tool, "to_dict") else tool
        self._send(requirement_id, "iteration_append", {"tool": tool_dict})

    def iteration_end(self, requirement_id: int, iteration: int):
        """一轮迭代结束：前端固定当前轮次卡片（不再实时变化）。"""
        self._send(requirement_id, "iteration_end", {"iteration": iteration})

    def qa_step(self, requirement_id: int, step: dict):
        """QA 验收逐步操作：前端实时展示 Catherine 在浏览器里的每一步（点击/输入/断言）。

        注意：逐步事件**只用于实时展示，不落库**。落库由 qa_result 在 AC 结束时
        汇总成一条（含完整 steps），避免单条需求产出数百条 qa_step 记录刷屏
        （需求 196 实测 340 条）。
        """
        if not isinstance(step, dict):
            step = dict(step)
        if "timestamp" not in step:
            step["timestamp"] = get_current_timestamp()
        self._send(requirement_id, "qa_step", step)

    def qa_start(self, requirement_id: int, ac: dict):
        """一个验收项（AC）开始：前端据此创建可实时累积的 AC 验收卡片。"""
        if not isinstance(ac, dict):
            ac = dict(ac)
        ac.setdefault("steps", [])
        ac.setdefault("status", "running")
        if "start_ts" not in ac:
            ac["start_ts"] = get_current_timestamp()
        self._send(requirement_id, "qa_start", ac)

    def qa_result(self, requirement_id: int, ac: dict):
        """一个验收项（AC）结束：汇总结论 + 全部步骤，前端固定卡片（同时落库一条）。"""
        if not isinstance(ac, dict):
            ac = dict(ac)
        ac.setdefault("steps", [])
        if "end_ts" not in ac:
            ac["end_ts"] = get_current_timestamp()
        self._send(requirement_id, "qa_result", ac)

    def verify_start(self, requirement_id: int, payload: dict):
        """验证阶段建卡：前端据此创建可实时累积的「质量工程师」卡片。

        与 coder 的 iteration_start/append/end 同构——实时事件**只做展示、不落库**，
        落库由验证结束时的一条 `qa_summary` 汇总完成（含同一份 steps），
        因此刷新前后看到的是同一张卡，位置也一致。
        """
        if not isinstance(payload, dict):
            payload = dict(payload)
        payload.setdefault("start_ts", get_current_timestamp())
        payload.setdefault("steps", [])
        self._send(requirement_id, "verify_start", payload)

    def verify_step(self, requirement_id: int, step: dict):
        """验证阶段单个子步骤状态更新（实时，不落库）。"""
        if not isinstance(step, dict):
            step = dict(step)
        step.setdefault("timestamp", get_current_timestamp())
        self._send(requirement_id, "verify_step", step)

    def qa_summary(self, requirement_id: int, message: dict):
        """验证摘要卡（落库 + 实时同一条）。

        dialogue 通道只透传 role/name/content 等固定字段，结构化卡片会被丢掉，
        因此摘要有专用事件；前端与刷新后从 DB 恢复的同一条消息共用幂等键。
        """
        self._send(requirement_id, "qa_summary", message)

    def complete(self, requirement_id: int, status: str = None):
        """完成事件。status 传需求**终态**（finished 等）。

        必须带终态：前端 currentRequirement 是进页面时的 API 快照，不随 SSE 更新，
        不带终态时状态永远停在 processing，发布门禁（要求 finished）会一直拦着，
        用户只能手动刷新页面才能发布。
        """
        payload = {
            "requirement_id": requirement_id,
            "code_files": [],
        }
        if status:
            payload["status"] = status
        self._send(requirement_id, "complete", payload)

    def error(self, requirement_id: int, message: str):
        self._send(requirement_id, "error", {"message": message})

    # ---- SDD 新增事件 ----

    def spec(self, requirement_id: int, spec_data: dict):
        """推送 SPEC 文档数据（验收条件 + 文件规格）"""
        self._send(requirement_id, "spec", spec_data)

    def task_list(self, requirement_id: int, tasks: list):
        """推送开发任务清单（TodoList）"""
        self._send(requirement_id, "task_list", {"tasks": tasks})

    def task_update(self, requirement_id: int, file_path: str, status: str):
        """推送单个任务状态更新"""
        self._send(requirement_id, "task_update", {"file": file_path, "status": status})

    def checklist_update(self, requirement_id: int, ac_id: str, passed: bool, reason: str = "", state: str = None):
        """推送验收条件检查结果更新。

        state 为四态信号（passed/compromised/unverified/not_applicable/fail/pending），
        供前端 P4 可视化；旧前端只读 passed 也兼容（state 缺省为 None）。
        """
        self._send(requirement_id, "checklist_update", {
            "ac_id": ac_id, "passed": passed, "reason": reason, "state": state
        })

    def evaluator_result(self, requirement_id: int, result: dict):
        """推送 Evaluator 代码评估结果（评分 + findings）"""
        self._send(requirement_id, "evaluator_result", result)

    def _send(self, requirement_id: int, event: str, data: dict):
        try:
            msg = SSEMessage.format_event(event, data)
            self.sse.broadcast(str(requirement_id), msg)
        except Exception as e:
            # 不再静默吞掉：序列化失败/断连都要留痕，否则事件悄悄丢失无人知晓
            logger.warning(f"SSE 事件发送失败 (req_id={requirement_id}, event={event}): {e}")

    def _make_readable(self, tool_name: str, arguments: dict) -> str:
        if tool_name == "write_file":
            filename = arguments.get("filename", "unknown")
            content = arguments.get("content", "")
            lines = content.count('\n') + 1 if content else 0
            return f"📝 正在创建 {filename} ({lines} 行)"
        elif tool_name == "read_file":
            return f"📖 读取 {arguments.get('filename', 'unknown')}"
        elif tool_name == "list_files":
            return f"📋 列出所有文件"
        elif tool_name == "delete_file":
            return f"🗑 删除 {arguments.get('filename', 'unknown')}"
        elif tool_name == "execute_code":
            return f"▶ 正在运行代码验证..."
        elif tool_name == "validate_html":
            return f"🔍 HTML 语法检查：{arguments.get('filename', '')}"
        elif tool_name == "lint_css":
            return f"🔍 CSS 语法检查：{arguments.get('filename', '')}"
        elif tool_name == "lint_js":
            return f"🔍 JS 语法检查：{arguments.get('filename', '')}"
        elif tool_name == "search_docs":
            return f"🔎 搜索文档：{arguments.get('query', '')}"
        elif tool_name == "fetch_cdn_library":
            return f"📦 获取 {arguments.get('library', '')} CDN"
        return f"🔧 调用 {tool_name}"
