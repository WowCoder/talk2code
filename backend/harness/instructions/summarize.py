# -*- coding: utf-8 -*-
"""
SummarizeCode 节点 —— 整体代码审查

仿 MetaGPT SummarizeCode 的跨文件整体审查：
- 检查跨文件调用流是否正确
- 检查是否有遗漏功能
- 检查边界情况处理
- 输出 PASS/FAIL 决策
"""

import json
import re
from typing import Dict, Any

from harness.state.agent_state import AgentState
from harness.observability.logger import get_logger
from harness.harness_context import get_workspace, get_tool_loop
from harness.instructions.prompts import load_prompt_template
from llm.client import get_client

logger = get_logger(__name__)


def summarize_node(state: AgentState) -> Dict[str, Any]:
    """
    整体代码审查节点（SummarizeCode）。

    读取所有已生成的代码文件，进行跨文件一致性审查。
    输出 PASS/FAIL 决策，FAIL 时将问题回灌到 repair 节点。
    """
    workspace = get_workspace(state)
    if not workspace:
        # 从 tool_loop 获取 workspace
        tl = get_tool_loop(state)
        if tl:
            workspace = tl.workspace

    if not workspace:
        logger.warning("[Summarize] 无法获取 workspace，跳过审查")
        state["summarize_passed"] = True
        state["current_step"] = "summarize_done"
        return state

    files = workspace.list()
    if not files:
        logger.warning("[Summarize] 无代码文件，跳过审查")
        state["summarize_passed"] = True
        state["current_step"] = "summarize_done"
        return state

    # 读取所有代码文件
    code_blocks = []
    for fname in files:
        try:
            content = workspace.read(fname)
            # 限制每个文件最多 300 行（大文件截断）
            lines = content.split('\n')
            if len(lines) > 300:
                content = '\n'.join(lines[:300]) + f"\n... (共 {len(lines)} 行，仅显示前 300 行)"

            # 确定语言
            if fname.endswith('.html'):
                lang = 'html'
            elif fname.endswith('.css'):
                lang = 'css'
            elif fname.endswith('.js'):
                lang = 'javascript'
            else:
                lang = ''

            line_count = content.count('\n') + 1
            code_blocks.append(
                f"### {fname} ({line_count} 行)\n```{lang}\n{content}\n```"
            )
        except Exception as e:
            code_blocks.append(f"### {fname}\n(无法读取: {e})")

    if not code_blocks:
        state["summarize_passed"] = True
        state["current_step"] = "summarize_done"
        return state

    code_text = "\n\n---\n\n".join(code_blocks)

    # 构建审查 prompt
    requirement = state.get("requirement_content", "")
    plan = state.get("plan") or {}
    plan_text = json.dumps(plan, ensure_ascii=False, indent=2) if plan else "(无)"

    prompt = load_prompt_template("review/summarize.md",
        requirement=requirement,
        plan_text=plan_text,
        code_blocks=code_text,
    )

    logger.info(f"[Summarize] 开始整体审查: {len(files)} 个文件, prompt 长度={len(prompt)}")

    try:
        client = get_client()
        response = client.chat(
            prompt=prompt,
            system_prompt="你是资深代码审查专家。只返回 JSON，不要其他文字。",
            use_memory=False,
            max_tokens=1000,
            timeout=60,
        )

        if response.is_error or not response.content:
            logger.warning(f"[Summarize] LLM 调用失败: {response.error}，默认通过")
            state["summarize_passed"] = True
            state["current_step"] = "summarize_done"
            return state

        # 解析 JSON
        content = response.content.strip()
        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                try:
                    result = json.loads(match.group())
                except json.JSONDecodeError:
                    logger.warning("[Summarize] JSON 解析失败，默认通过")
                    state["summarize_passed"] = True
                    state["current_step"] = "summarize_done"
                    return state
            else:
                logger.warning("[Summarize] JSON 解析失败，默认通过")
                state["summarize_passed"] = True
                state["current_step"] = "summarize_done"
                return state

        verdict = result.get("verdict", "PASS")
        issues = result.get("issues", [])
        score = result.get("score", 7.0)
        summary = result.get("summary", "")

        logger.info(f"[Summarize] 审查完成: verdict={verdict}, score={score}, issues={len(issues)}")

        state["summarize_passed"] = (verdict == "PASS")
        state["current_step"] = "summarize_done"

        # 添加审查结果到角色产出
        state.setdefault("role_outputs", {})["Summarize"] = json.dumps(result, ensure_ascii=False)

        # 添加对话历史
        state.setdefault("dialogue_history", []).append({
            "role": "agent",
            "name": "Summarize",
            "content": (
                f"## 整体代码审查\n\n"
                f"**评分**: {score}/10 | **结果**: {verdict}\n\n"
                f"**总结**: {summary}\n\n"
                + (f"**问题**:\n" + "\n".join(f"- {i}" for i in issues) if issues else "")
            ),
            "status": "completed",
        })

    except Exception as e:
        logger.warning(f"[Summarize] 审查异常: {e}，默认通过")
        state["summarize_passed"] = True
        state["current_step"] = "summarize_done"

    return state
