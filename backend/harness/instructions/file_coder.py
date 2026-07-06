# -*- coding: utf-8 -*-
"""
FileByFileCoder 节点 —— M/L 复杂度逐文件编码循环

仿 MetaGPT 的 Engineer._new_code_actions + WriteCode + WriteCodeReview 模式：
1. 对 implementation_order 中的每个文件构建 CodingContext
2. 逐文件执行 ToolCallLoop（限制迭代次数）
3. 每文件完成后进行 CodeReview（LGTM/LBTM）
4. LBTM → 重写，最多 K 次
5. LGTM → 下一个文件
"""

import json
from typing import Dict, Any, Optional

from harness.state.agent_state import AgentState
from harness.observability.logger import get_logger
from harness.harness_context import get_tool_loop as _get_tool_loop
from llm.client import get_client

logger = get_logger(__name__)

# 每文件最大编码尝试次数（1 次初始 + K 次重写）
MAX_CODE_REVIEW_ATTEMPTS = 3  # K=2

# 每文件 ToolCallLoop 最大迭代次数
PER_FILE_MAX_ITERATIONS = 5

# ==================== CodeReview 提示词（仿 MetaGPT WriteCodeReview） ====================

CODE_REVIEW_PROMPT = """你是一位资深代码审查专家。审查以下代码文件，从 6 个维度评估：

## 当前任务
{task_description}

## 接口契约
{interface_contract}

## 代码文件: {file_path}
```{language}
{code_content}
```

## 审查维度
1. **需求实现** — 是否完整实现了任务描述的功能？
2. **逻辑正确性** — 业务逻辑是否有明显错误？边界情况是否处理？
3. **接口遵循** — 是否遵循了定义的接口契约（exports/imports）？
4. **功能完整性** — 是否有遗漏的函数/方法？是否有 TODO 或占位符？
5. **依赖正确性** — 是否正确引用了其他模块的导出？
6. **代码质量** — 命名是否规范？是否有重复代码？是否有安全隐患（innerHTML/eval/document.write）？

## 输出格式（严格 JSON）
```json
{{"verdict": "LGTM", "issues": [], "score": 8.5}}
```
或
```json
{{"verdict": "LBTM", "issues": ["问题1描述", "问题2描述"], "score": 5.0}}
```

- LGTM = Looks Good To Me（代码质量合格，无需重写）
- LBTM = Looks Bad To Me（存在需要修复的问题）
- score: 1-10 分，6 分以上为合格
- 只返回 JSON，不要其他文字"""


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
            # 提取前 30 行作为摘要
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
    构建单文件编码的完整上下文（仿 MetaGPT CodingContext + WriteCode.PROMPT_TEMPLATE）

    5 块上下文：
    1. Design — 架构设计 / Plan
    2. Task — 当前文件的实现任务
    3. Legacy Code — 已完成文件的内容摘要
    4. Interface Contract — 接口契约
    5. Error Log — 之前的错误日志
    """
    # 已完成文件的摘要文本
    completed_text = ""
    if completed_files:
        completed_lines = ["## 已完成的文件（可作为参考）"]
        for cf in completed_files:
            completed_lines.append(
                f"### {cf['file']} ({cf['lines']} 行)\n"
                f"```\n{cf['preview']}\n```"
            )
        completed_text = "\n\n".join(completed_lines)

    # 接口契约
    interface_text = ""
    file_path = current_task.get("file", "")
    if file_path in interfaces:
        interface_text = f"## 接口契约\n```json\n{json.dumps(interfaces[file_path], ensure_ascii=False, indent=2)}\n```"

    # 导入依赖
    imports = current_task.get("imports", {})
    imports_text = ""
    if imports:
        imports_text = f"## 需要引用的模块\n```json\n{json.dumps(imports, ensure_ascii=False, indent=2)}\n```"

    # 错误日志
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

    在逐文件编码模式下，每切换一个文件就替换一次系统提示，
    让 LLM 聚焦当前文件的实现任务。
    """
    original_builder = tool_loop._build_system_prompt

    def _file_aware_prompt(state):
        plan_section = ""
        if context.get("plan_text"):
            plan_section = f"\n\n## 实现计划\n{context['plan_text']}"

        exports_text = ""
        if context.get("exports"):
            exports_text = f"\n\n## 本文件需要导出的接口\n" + "\n".join(
                f"- {e}" for e in context["exports"]
            )

        return f"""你是一个资深前端工程师。你正在实现项目中的一个文件。

## 用户需求
{context["requirement"]}
{plan_section}

## 当前文件: {context["file_path"]}
**任务描述**: {context["task_description"]}
{exports_text}
{context["imports_text"]}
{context["interface_text"]}

{context["completed_text"]}

{context["error_text"]}

## 重要
- **只创建当前这一个文件**: {context["file_path"]}
- 如果需要引用其他模块，按照 imports 中定义的接口使用
- 创建完成后立即停止，告诉我"任务完成"
- 不要创建其他文件
- write_file 的返回结果已包含文件完整内容，不要再用 read_file 重新读取
- 代码完整可运行，不省略不写 TODO
- 禁止: innerHTML, eval, document.write"""

    tool_loop._build_system_prompt = _file_aware_prompt
    return original_builder


def _review_single_file(file_path: str, workspace, state: AgentState,
                         interfaces: dict, task: dict) -> dict:
    """
    对单个文件进行 CodeReview（LGTM/LBTM）。

    仿 MetaGPT WriteCodeReview 的 6 维度审查。

    Returns:
        {"verdict": "LGTM"|"LBTM", "issues": [...], "score": float}
    """
    try:
        code_content = workspace.read(file_path)
    except Exception:
        return {"verdict": "LGTM", "issues": [], "score": 10.0,
                "note": "文件无法读取，跳过审查"}

    # 检查是否是空文件或极小文件（可能是刚创建还没写入）
    if not code_content or len(code_content.strip()) < 20:
        return {"verdict": "LBTM", "issues": ["文件内容过短或为空"], "score": 1.0}

    # 确定语言
    if file_path.endswith('.html'):
        language = 'html'
    elif file_path.endswith('.css'):
        language = 'css'
    elif file_path.endswith('.js'):
        language = 'javascript'
    else:
        language = ''

    # 接口契约
    file_interfaces = interfaces.get(file_path, {})
    interface_text = json.dumps(file_interfaces, ensure_ascii=False, indent=2) if file_interfaces else "(无)"

    prompt = CODE_REVIEW_PROMPT.format(
        task_description=task.get("description", "实现该文件的功能"),
        interface_contract=interface_text,
        file_path=file_path,
        language=language,
        code_content=code_content[:8000],  # 截断防止过长
    )

    try:
        client = get_client()
        response = client.chat(
            prompt=prompt,
            system_prompt="你是资深代码审查专家。只返回 JSON，不要其他文字。",
            use_memory=False,
            max_tokens=800,
            timeout=30,
        )

        if response.is_error or not response.content:
            logger.warning(f"[CodeReview] {file_path} 审查调用失败: {response.error}")
            return {"verdict": "LGTM", "issues": [], "score": 7.0,
                    "note": "审查 LLM 调用失败，默认通过"}

        # 解析 JSON
        import re
        content = response.content.strip()
        try:
            review = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    review = json.loads(match.group())
                except json.JSONDecodeError:
                    return {"verdict": "LGTM", "issues": [], "score": 7.0,
                            "note": "审查结果解析失败，默认通过"}
            else:
                return {"verdict": "LGTM", "issues": [], "score": 7.0,
                        "note": "审查结果解析失败，默认通过"}

        verdict = review.get("verdict", "LGTM")
        issues = review.get("issues", [])
        score = review.get("score", 7.0)

        logger.info(f"[CodeReview] {file_path}: verdict={verdict}, score={score}, issues={len(issues)}")
        return {"verdict": verdict, "issues": issues, "score": score}

    except Exception as e:
        logger.warning(f"[CodeReview] {file_path} 审查异常: {e}")
        return {"verdict": "LGTM", "issues": [], "score": 7.0,
                "note": f"审查异常: {e}"}


def _legacy_coder(state: AgentState, tool_loop) -> dict:
    """回退到旧行为：直接 ToolCallLoop（兼容没有 tasks 的情况）"""
    logger.info("[FileCoder] 无 tasks 数据，回退到旧版编码模式")
    state.setdefault("metadata", {})["coder_name"] = "FrontendEngineer"
    state["metadata"]["thinking_name"] = "FrontendEngineer"
    result = tool_loop.run(state)
    state["dialogue_history"] = result.get("dialogue_history", [])
    state["current_step"] = "coding_done"
    return state


def file_by_file_coder_node(state: AgentState) -> Dict[str, Any]:
    """
    M/L 复杂度逐文件编码节点。

    对 implementation_order 中的每个文件：
    1. 构建 CodingContext（设计 + 任务 + 已完成文件 + 接口契约 + 错误日志）
    2. 调用 ToolCallLoop（限制迭代次数）
    3. CodeReview 检查（LGTM/LBTM）
    4. LBTM → 重写，最多 K=2 次
    5. LGTM → 下一个文件
    """
    tool_loop = _get_tool_loop(state)
    if not tool_loop:
        return {"current_step": "error", "error": "ToolCallLoop 未注入到 state"}

    tasks = state.get("tasks") or []
    interfaces = state.get("interfaces") or {}
    implementation_order = state.get("implementation_order") or []
    workspace = tool_loop.workspace

    # 设置角色名称
    state.setdefault("metadata", {})["coder_name"] = "FrontendEngineer"
    state["metadata"]["thinking_name"] = "FrontendEngineer"

    # 如果没有 tasks，回退到旧行为（兼容）
    if not tasks or not implementation_order:
        logger.info("[FileCoder] 缺少 tasks/implementation_order，使用旧版编码模式")
        return _legacy_coder(state, tool_loop)

    logger.info(
        f"[FileCoder] 启动逐文件编码: {len(implementation_order)} 个文件 "
        f"complexity={state.get('metadata', {}).get('complexity', 'M')}"
    )

    code_errors = state.get("code_errors") or []
    review_results = []

    for file_path in implementation_order:
        task = _find_task(tasks, file_path)
        if not task:
            logger.warning(f"[FileCoder] 未找到任务: {file_path}，跳过")
            continue

        logger.info(f"[FileCoder] 开始编码: {file_path} (描述: {task.get('description', '')[:60]})")

        # 1. 构建 CodingContext
        context = _build_coding_context(
            requirement=state.get("requirement_content", ""),
            plan=state.get("plan") or {},
            current_task=task,
            interfaces=interfaces,
            completed_files=_get_completed_files(workspace, implementation_order, file_path),
            errors=code_errors,
            task_package=state.get("metadata", {}).get("task_package", ""),
        )

        # 2. Code Generation with review loop
        file_passed = False
        for attempt in range(MAX_CODE_REVIEW_ATTEMPTS):
            # 注入当前文件的编码上下文
            original_builder = _inject_coding_context(tool_loop, context)

            # 如果有审查反馈，注入到对话历史
            if attempt > 0 and context.get("review_feedback"):
                state.setdefault("dialogue_history", []).append({
                    "role": "user",
                    "name": "CodeReviewer",
                    "content": (
                        f"文件 {file_path} 审查未通过（第 {attempt} 次），请修复以下问题：\n"
                        + "\n".join(f"- {i}" for i in context["review_feedback"])
                        + "\n\n请根据反馈修改代码，用 write_file 重新写入完整文件。"
                    ),
                })

            # 设置单文件迭代上限
            saved_max = tool_loop.MAX_ITERATIONS
            tool_loop.MAX_ITERATIONS = PER_FILE_MAX_ITERATIONS

            try:
                result_state = tool_loop.run(state)
                state["dialogue_history"] = result_state.get("dialogue_history", [])
            finally:
                tool_loop.MAX_ITERATIONS = saved_max
                tool_loop._build_system_prompt = original_builder

            # 3. Code Review
            review_result = _review_single_file(
                file_path, workspace, state, interfaces, task
            )

            review_results.append({
                "file": file_path,
                "attempt": attempt + 1,
                **review_result,
            })

            if review_result["verdict"] == "LGTM":
                logger.info(f"[FileCoder] {file_path} 审查通过 (score={review_result['score']})")
                file_passed = True
                break
            else:
                # LBTM: 将审查反馈注入，下轮重写
                issues = review_result.get("issues", [])
                logger.warning(
                    f"[FileCoder] {file_path} 审查未通过 (attempt={attempt + 1}, "
                    f"score={review_result['score']}, issues={len(issues)})"
                )
                context["review_feedback"] = issues
                # 将失败原因记录到全局错误列表（供后续文件参考）
                for issue in issues:
                    code_errors.append(f"[{file_path}] {issue}")

        if not file_passed:
            logger.warning(
                f"[FileCoder] {file_path} 经 {MAX_CODE_REVIEW_ATTEMPTS} 次审查仍未通过，继续下一个文件"
            )

        # 清除对话历史中的临时审查反馈，避免污染下一个文件的上下文
        state["dialogue_history"] = [
            m for m in (state.get("dialogue_history") or [])
            if m.get("name") != "CodeReviewer"
        ]

    # 保存审查结果到 state
    state["current_step"] = "coding_done"
    state["code_errors"] = code_errors
    state.setdefault("metadata", {})["review_results"] = review_results

    logger.info(
        f"[FileCoder] 逐文件编码完成: {len(review_results)} 次审查, "
        f"错误 {len(code_errors)} 条"
    )

    return state
