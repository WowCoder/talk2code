# -*- coding: utf-8 -*-
"""
角色定义 —— 5 个专业角色的 System Prompt 和配置

SOP 在 Prompt 中：改行为 = 改 Prompt 文本，无需改代码。
"""

from harness.roles import Role, RoleRegistry
from harness.instructions.prompts import load_prompt


# ======================== 角色 Prompt 加载 ========================

TL_SYSTEM_PROMPT = load_prompt("roles/team_leader.md")
PM_SYSTEM_PROMPT = load_prompt("roles/product_manager.md")
ARCHITECT_SYSTEM_PROMPT = load_prompt("roles/architect.md")
ENGINEER_SYSTEM_PROMPT = load_prompt("roles/frontend_engineer.md")
QA_SYSTEM_PROMPT = load_prompt("roles/qa_reviewer.md")


# ======================== 角色注册表 ========================

def create_role_registry() -> RoleRegistry:
    """创建并初始化角色注册表"""
    registry = RoleRegistry()

    # TeamLeader: 纯决策角色，不需要工具
    registry.register(Role(
        name="TeamLeader",
        display_name="Leon（负责人）",
        system_prompt=TL_SYSTEM_PROMPT,
        description="调度中枢：需求分析 → 路由决策 → 收集产出 → 整合汇报",
        tools=[],  # 不需要工具，纯文本决策
        max_iterations=1,
        output_type="json",
    ))

    # ProductManager: 分析角色
    registry.register(Role(
        name="ProductManager",
        display_name="Catherine（产品经理）",
        system_prompt=PM_SYSTEM_PROMPT,
        description="需求分析 → PRD 生成 → 竞品调研",
        tools=["search_docs"],  # 可能需要搜索竞品信息
        max_iterations=3,
        output_type="text",
    ))

    # Architect: 设计角色
    registry.register(Role(
        name="Architect",
        display_name="Bob（架构师）",
        system_prompt=ARCHITECT_SYSTEM_PROMPT,
        description="技术选型 → 组件树设计 → 数据流设计 → 文件结构规划",
        tools=["read_file", "list_files"],  # 可能需要查看现有文件
        max_iterations=3,
        output_type="text",
    ))

    # FrontendEngineer: 编码角色
    registry.register(Role(
        name="FrontendEngineer",
        display_name="Henry（开发）",
        system_prompt=ENGINEER_SYSTEM_PROMPT,
        description="代码生成 → 文件创建 → 增量修改 → 验证修复",
        tools=[],  # 空 = 全部工具都可用
        max_iterations=15,
        output_type="files",
    ))

    # QAReviewer: 审查角色
    registry.register(Role(
        name="QAReviewer",
        display_name="Annie（测试）",
        system_prompt=QA_SYSTEM_PROMPT,
        description="代码审查 → 质量评分 → 问题识别 → 修复建议",
        tools=["read_file", "list_files", "validate_html",
               "lint_css", "lint_js", "run_preview"],
        max_iterations=5,
        output_type="json",
    ))

    return registry


# ======================== 路由表 ========================

# 复杂度 → 角色执行序列
COMPLEXITY_ROUTE = {
    "XS": ["FrontendEngineer"],
    "S":  ["ProductManager", "FrontendEngineer"],
    "M":  ["ProductManager", "Architect", "FrontendEngineer", "QAReviewer"],
    "L":  ["ProductManager", "Architect", "FrontendEngineer",
           "QAReviewer", "FrontendEngineer", "QAReviewer"],
}
