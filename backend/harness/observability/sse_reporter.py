# -*- coding: utf-8 -*-
"""
SSEReporter —— SSE 事件统一管理
"""

from utils.sse import SSEMessage, get_current_timestamp


class SSEReporter:
    """SSE 事件统一管理，通过 SSEManager 推送事件"""

    def __init__(self, sse_manager):
        self.sse = sse_manager

    def progress(self, requirement_id: int, percent: int, message: str = ""):
        self._send(requirement_id, "progress", {
            "current_agent": message, "progress": percent, "status": "processing"
        })

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
        self._send(requirement_id, "iteration_batch", data)

    def complete(self, requirement_id: int, code_files: list = None):
        self._send(requirement_id, "complete", {
            "requirement_id": requirement_id,
            "code_files": code_files or []
        })

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

    def checklist_update(self, requirement_id: int, ac_id: str, passed: bool, reason: str = ""):
        """推送验收条件检查结果更新"""
        self._send(requirement_id, "checklist_update", {
            "ac_id": ac_id, "passed": passed, "reason": reason
        })

    def evaluator_result(self, requirement_id: int, result: dict):
        """推送 Evaluator 代码评估结果（评分 + findings）"""
        self._send(requirement_id, "evaluator_result", result)

    def _send(self, requirement_id: int, event: str, data: dict):
        try:
            msg = SSEMessage.format_event(event, data)
            self.sse.broadcast(str(requirement_id), msg)
        except Exception:
            pass

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
