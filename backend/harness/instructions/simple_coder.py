# -*- coding: utf-8 -*-
"""
SimpleCoder 节点 —— XS/S 复杂度简单编码
直接 ToolCallLoop + Hook 反馈，不拆分逐文件循环
"""

from typing import Dict, Any

from harness.state.agent_state import AgentState
from harness.observability.logger import get_logger
from harness.harness_context import get_tool_loop as _get_tool_loop

logger = get_logger(__name__)


def simple_coder_node(state: AgentState) -> Dict[str, Any]:
    """
    XS/S 简单编码节点。

    直接执行 ToolCallLoop，不进行逐文件拆分。
    关键改进：将 Hook 失败反馈注入到对话历史中，
    让 LLM 在下一轮迭代时能看到验证错误并主动修复。
    """
    tool_loop = _get_tool_loop(state)
    if not tool_loop:
        return {
            "current_step": "error",
            "error": "ToolCallLoop 未注入到 state",
        }

    # 设置角色名称
    state.setdefault("metadata", {})["coder_name"] = "Henry（开发）"
    state["metadata"]["thinking_name"] = "Henry（开发）"

    # 注入 Hook 失败历史到对话历史（关键改进！）
    # 让 LLM 在开始编码前就知道历史上有哪些验证失败
    hook_failures = state.get("hook_failures", {})
    if hook_failures:
        failure_lines = []
        for hook_name, count in hook_failures.items():
            if count > 0:
                failure_lines.append(f"- {hook_name}: 失败 {count} 次")
        if failure_lines:
            state.setdefault("dialogue_history", []).append({
                "role": "user",
                "name": "System",
                "content": (
                    "## 历史验证失败记录（请注意避免）\n"
                    + "\n".join(failure_lines)
                    + "\n\n请在编码时特别注意以上问题，避免重复出现。"
                ),
            })

    logger.info(f"[SimpleCoder] 启动 XS/S 编码，complexity={state.get('metadata', {}).get('complexity', 'S')}")

    try:
        result = tool_loop.run(state)

        # 将 tool_loop 产生的状态更新回 state
        state["dialogue_history"] = result.get("dialogue_history", [])
        state["code_files"] = result.get("code_files", [])
        state["current_step"] = result.get("current_step", "done")
        state["error"] = result.get("error", "")
        state["hook_failures"] = result.get("hook_failures", {})

        # 判断是否成功完成
        if result.get("current_step") == "task_complete":
            state["current_step"] = "coding_done"
        elif result.get("error"):
            state["current_step"] = "coding_error"

    except Exception as e:
        logger.error(f"[SimpleCoder] 执行失败：{e}")
        state["current_step"] = "coding_error"
        state["error"] = str(e)

    return state
