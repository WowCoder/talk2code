# -*- coding: utf-8 -*-
"""
Targeted Recovery —— 批量编码后的定向补全模块

Batch-First with Targeted Recovery 架构：
1. coder_node 始终走批量编码（ToolCallLoop + coder_ml.md + batch_hint）
2. 批量编码完成后，检查文件系统是否有缺失文件
3. 对缺失文件调用 targeted_recovery 逐个补全

设计原则:
- 无 CompletionContract（Phase 1 的 contract 已由 hook 自动更新）
- 无 CodeReview（质量评估统一由 verify_node 负责）
- 每个文件独立运行 ToolCallLoop，临时计数器由 Fix 1 自动重置
- 只验证文件是否存在且非空
"""

import json
from typing import Dict, Any, Optional

from harness.state.agent_state import AgentState
from harness.agent_names import DEV_NAME
from harness.observability.logger import get_logger
from harness.harness_context import get_tool_loop as _get_tool_loop
from harness.instructions.prompts import load_prompt_template

logger = get_logger(__name__)

# 每文件 ToolCallLoop 最大迭代次数
PER_FILE_MAX_ITERATIONS = 5


def _find_task(tasks: list, file_path: str) -> Optional[dict]:
    """在任务列表中查找对应文件的任务"""
    for task in tasks:
        if task.get("file") == file_path:
            return task
    return None


def _get_completed_files(workspace, implementation_order: list,
                          current_file: str) -> list[dict]:
    """获取当前文件之前已完成文件的摘要"""
    completed = []
    try:
        current_idx = implementation_order.index(current_file)
    except ValueError:
        return completed

    for prev_file in implementation_order[:current_idx]:
        try:
            content = workspace.read(prev_file)
            line_count = content.count('\n') + 1 if content else 0
            lines = content.split('\n')[:30]
            preview = '\n'.join(lines)[:500]
            completed.append({
                "file": prev_file,
                "lines": line_count,
                "preview": preview,
            })
        except Exception:
            completed.append({"file": prev_file, "lines": 0, "preview": "(无法读取)"})

    return completed


def _build_coding_context(requirement: str, plan: dict, current_task: dict,
                           interfaces: dict, completed_files: list,
                           errors: list, task_package: str) -> dict:
    """
    构建单文件编码的完整上下文。

    5 块上下文：
    1. Design — 架构设计 / Plan
    2. Task — 当前文件的实现任务
    3. Legacy Code — 已完成文件的内容摘要
    4. Interface Contract — 接口契约
    5. Error Log — 之前的错误日志
    """
    completed_text = ""
    if completed_files:
        completed_lines = ["## 已完成的文件（可作为参考）"]
        for cf in completed_files:
            completed_lines.append(
                f"### {cf['file']} ({cf['lines']} 行)\n"
                f"```\n{cf['preview']}\n```"
            )
        completed_text = "\n\n".join(completed_lines)

    interface_text = ""
    file_path = current_task.get("file", "")
    if file_path in interfaces:
        interface_text = f"## 接口契约\n```json\n{json.dumps(interfaces[file_path], ensure_ascii=False, indent=2)}\n```"

    imports = current_task.get("imports", {})
    imports_text = ""
    if imports:
        imports_text = f"## 需要引用的模块\n```json\n{json.dumps(imports, ensure_ascii=False, indent=2)}\n```"

    error_text = ""
    if errors:
        error_text = "## 之前的错误日志（请避免）\n" + "\n".join(f"- {e}" for e in errors[-10:])

    return {
        "requirement": requirement,
        "plan_text": json.dumps(plan, ensure_ascii=False, indent=2) if plan else "",
        "task_description": current_task.get("description", ""),
        "file_path": file_path,
        "exports": current_task.get("exports", []),
        "imports_text": imports_text,
        "completed_text": completed_text,
        "interface_text": interface_text,
        "error_text": error_text,
        "task_package": task_package,
    }


def _inject_coding_context(tool_loop, context: dict):
    """
    将 CodingContext 注入到 ToolCallLoop 的系统提示中。

    替换 _build_system_prompt 为 file_aware_coder.md 模板，
    返回原始 builder 以便调用方在使用后恢复。
    """
    original_builder = tool_loop._build_system_prompt

    def _file_aware_prompt(state):
        plan_section = ""
        if context.get("plan_text"):
            plan_section = f"\n\n## 实现计划\n{context['plan_text']}"

        exports_text = ""
        if context.get("exports"):
            exports_text = "\n\n## 本文件需要导出的接口\n" + "\n".join(
                f"- {e}" for e in context["exports"]
            )

        return load_prompt_template("coding/file_aware_coder.md",
            requirement=context["requirement"],
            plan_section=plan_section,
            file_path=context["file_path"],
            task_description=context["task_description"],
            exports_text=exports_text,
            imports_text=context["imports_text"],
            interface_text=context["interface_text"],
            completed_text=context["completed_text"],
            error_text=context["error_text"],
        )

    tool_loop._build_system_prompt = _file_aware_prompt
    return original_builder


def targeted_recovery(state: AgentState, tool_loop, missing_files: list[str]) -> dict:
    """
    Phase 2: 对批量编码遗漏的文件进行定向补全。

    - 无 CompletionContract（Phase 1 的 contract 已由 hook 自动更新）
    - 无 CodeReview（质量评估由 verify_node 负责）
    - 每个文件独立运行，临时计数器由 ToolCallLoop.run() 开头重置（Fix 1）
    - 只验证文件是否存在且非空

    Args:
        state: AgentState（含 dialogue_history、plan、tasks 等）
        tool_loop: ToolCallLoop 实例（已注入 memory_aware_prompt）
        missing_files: 批量编码后仍缺失的文件路径列表

    Returns:
        {"current_step": "coding_done"}
    """
    workspace = tool_loop.workspace
    tasks = state.get("tasks") or []
    interfaces = state.get("interfaces") or {}
    implementation_order = state.get("implementation_order") or []
    code_errors = state.get("code_errors") or []
    req_id = state.get("requirement_id", 0)

    logger.info(f"[Recovery] 开始定向补全: {len(missing_files)} 个文件缺失: {missing_files}")

    # 推送任务状态
    if tool_loop.sse:
        for file_path in missing_files:
            try:
                tool_loop.sse.task_update(req_id, file_path, "in_progress")
            except Exception:
                pass

    for file_path in missing_files:
        task = _find_task(tasks, file_path)
        if not task:
            # tasks 中找不到，构建最小化 task
            task = {"file": file_path, "description": file_path, "imports": {}, "exports": []}
            logger.warning(f"[Recovery] 未找到 {file_path} 的 task，使用最小化上下文")

        # 1. 构建编码上下文
        context = _build_coding_context(
            requirement=state.get("requirement_content", ""),
            plan=state.get("plan") or {},
            current_task=task,
            interfaces=interfaces,
            completed_files=_get_completed_files(workspace, implementation_order, file_path),
            errors=code_errors,
            task_package=state.get("metadata", {}).get("task_package", ""),
        )

        # 2. 注入文件感知提示 + 逐文件模式标记
        original_builder = _inject_coding_context(tool_loop, context)
        state["_per_file_mode"] = True
        state["_current_target_file"] = file_path

        # 3. 运行 ToolCallLoop（计数器由 Fix 1 自动重置）
        saved_max = tool_loop.MAX_ITERATIONS
        tool_loop.MAX_ITERATIONS = PER_FILE_MAX_ITERATIONS

        try:
            result_state = tool_loop.run(state)
            state["dialogue_history"] = result_state.get("dialogue_history", [])
        except Exception as e:
            logger.error(f"[Recovery] {file_path} 补全异常: {e}")
            code_errors.append(f"[{file_path}] 补全异常: {e}")
        finally:
            tool_loop.MAX_ITERATIONS = saved_max
            tool_loop._build_system_prompt = original_builder

        # 4. 简单验证：文件存在且非空
        try:
            content = workspace.read(file_path)
            if content and len(content.strip()) >= 20:
                logger.info(f"[Recovery] {file_path} 创建成功 ({len(content)} chars)")
                if tool_loop.sse:
                    try:
                        tool_loop.sse.task_update(req_id, file_path, "completed")
                    except Exception:
                        pass
            else:
                logger.warning(f"[Recovery] {file_path} 创建成功但内容过短")
                code_errors.append(f"[{file_path}] 文件内容过短或为空")
                if tool_loop.sse:
                    try:
                        tool_loop.sse.task_update(req_id, file_path, "failed")
                    except Exception:
                        pass
        except Exception:
            logger.warning(f"[Recovery] {file_path} 仍未创建")
            code_errors.append(f"[{file_path}] 文件未创建")
            if tool_loop.sse:
                try:
                    tool_loop.sse.task_update(req_id, file_path, "failed")
                except Exception:
                    pass

    # 清除逐文件模式标记
    state.pop("_per_file_mode", None)
    state.pop("_current_target_file", None)

    logger.info(f"[Recovery] 定向补全完成，剩余错误 {len(code_errors)} 条")
    return {"current_step": "coding_done", "code_errors": code_errors}
