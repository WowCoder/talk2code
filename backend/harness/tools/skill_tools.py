# -*- coding: utf-8 -*-
"""Skill 调用工具：run_skill

让 Agent（Coder）能显式规划、调用并组合工作流技能（Workflow Skills）。
底层委托给 SkillLoader.build_skill_dispatch，返回技能的执行步骤、
输入/输出契约、工具白名单以及组合子技能的说明。
"""
from __future__ import annotations

from typing import Optional

from harness.tools.registry import register_tool, ToolHandler, ToolResult
from harness.instructions.skill_loader import build_skill_dispatch


@register_tool(
    "run_skill",
    description=(
        "执行一个已注册的工作流技能（Skill）。传入技能名，返回该技能的执行步骤、"
        "输入/输出契约、工具白名单以及组合子技能的说明，供你按步骤调用白名单内的工具"
        "完成子任务。当用户需求命中某个可复用业务技能（如代码审查、测试生成、"
        "项目脚手架、API 接入等）时使用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {
                "type": "string",
                "description": "技能名称，例如 code_review / test_generation / scaffold",
            },
            "skill_args": {
                "type": "object",
                "description": "调用该技能时已收集的参数（可选）",
            },
        },
        "required": ["skill_name"],
    },
    permission="read",
)
class RunSkillHandler(ToolHandler):
    def execute(self, args: dict, workspace=None, state=None) -> ToolResult:
        skill_name = (args or {}).get("skill_name")
        if not skill_name:
            return ToolResult(error="run_skill: 缺少必填参数 skill_name")
        skill_args = (args or {}).get("skill_args") or {}
        try:
            dispatch = build_skill_dispatch(skill_name, skill_args)
        except Exception as e:
            return ToolResult(error=f"run_skill 执行失败: {e}")
        return ToolResult(content=dispatch)


def register_skill_tools(registry):
    """注册 Skill 相关工具。

    run_skill 已通过 @register_tool 装饰器在模块导入时自注册到全局 registry，
    此处保留统一入口以便 create_tool_registry 显式调用（触发导入即可）。
    """
    _ = registry
