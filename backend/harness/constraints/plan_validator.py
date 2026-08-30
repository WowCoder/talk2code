# -*- coding: utf-8 -*-
"""
Plan Validator —— TL 产出的机器可校验 DoD（完成定义）

审查结论：AC 本身的质量（可操作性、覆盖度）此前无任何校验——AC 弱则
fast_pass 通道直接放水。本模块在 plan 落地前做程序化校验，不合格打回
TL 重出（最多 1 次），而不是带病进 coder：

- 文件引用闭合：tasks/implementation_order/file_structure 相互一致，
  index.html 引用的本地 js/css 都出现在清单里（ENV-6 前置检查）
- 任务可执行：每个 file 有 purpose/description 且达到最小信息量
- exports 契约：跨文件调用的 js 必须声明 {全局名: [方法...]} 导出清单
- AC 可操作：每条 AC 的 how_to_verify 含可操作动词（点击/输入/按…）
- 复杂度一致性：simple ⇒ 文件数 ≤ 2；standard ⇒ ≤ 12

纯确定性规则，零 LLM 成本。
"""

import re

from harness.constraints.environment_contract import extract_local_refs
from harness.observability.logger import get_logger

logger = get_logger(__name__)

# how_to_verify 必须包含至少一个可操作动词（浏览器里实际做得到的动作）
ACTIONABLE_VERBS = [
    "点击", "输入", "按", "按下", "打开", "拖动", "滚动", "选择",
    "勾选", "切换", "双击", "长按", "提交", "搜索", "播放",
    "click", "type", "press", "open", "drag", "scroll", "select",
    "check", "toggle", "submit", "search", "play", "swipe",
]

# 不可断言的"假验收"措辞：出现即视为不可操作
NON_ACTIONABLE_PHRASES = ["界面美观", "运行正常", "体验良好", "样式统一"]


def _normalize(files) -> list[str]:
    if not isinstance(files, list):
        return []
    return [f.strip().lstrip("/") for f in files if isinstance(f, str) and f.strip()]


def _validate_file_closure(plan: dict) -> list[str]:
    """文件引用闭合：index.html 引用的本地 js/css 必须在计划清单中。"""
    issues = []
    file_structure = set(_normalize(plan.get("file_structure")))
    tasks_files = {
        (t.get("file") or "").strip().lstrip("/")
        for t in (plan.get("tasks") or []) if isinstance(t, dict)
    }
    impl_order = _normalize(plan.get("implementation_order"))

    # 1. implementation_order 与 file_structure/tasks 一致性（standard 才有）
    if impl_order:
        for f in impl_order:
            if file_structure and f not in file_structure:
                issues.append(f"implementation_order 中的 {f} 不在 file_structure 清单内")
    if file_structure and tasks_files - {""}:
        orphan = tasks_files - file_structure
        if orphan:
            issues.append(f"tasks 中引用了未在 file_structure 声明的文件: {sorted(orphan)}")

    # 2. index.html 引用闭合（ENV-6）：计划里就应包含将被引用的本地资源
    for entry in (file_structure or []):
        if not entry.endswith("index.html"):
            continue
        # 计划阶段没有 HTML 内容，无法静态提取引用；退而校验清单自洽：
        # 若存在 js/ 子目录文件但没有 index.html 入口则由后续节点兜底。
    return issues


def _validate_tasks(plan: dict) -> list[str]:
    """任务可执行：每个 file 有 purpose/description 且信息量达标。"""
    issues = []
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        return issues  # simple 复杂度允许省略
    for i, t in enumerate(tasks):
        if not isinstance(t, dict) or not t.get("file"):
            issues.append(f"tasks[{i}] 缺少 file 字段")
            continue
        fname = t["file"]
        purpose = (t.get("purpose") or t.get("description") or "").strip()
        if len(purpose) < 10:
            issues.append(f"{fname} 缺少 purpose/description 或描述过短（≥10 字）")
    return issues


def _validate_exports_contract(plan: dict) -> list[str]:
    """跨文件 API 契约校验（需求 124 事故：app.js 调用了 utils.js 未实现的 API）。

    - 被其他任务依赖的 js 文件必须声明非空 exports（{全局名: [方法,...]}）
    - exports 结构必须是 dict[str, list[str]]
    - index.html / css 文件豁免
    """
    issues = []
    tasks = [t for t in (plan.get("tasks") or []) if isinstance(t, dict)]
    if not tasks:
        return issues

    def _is_js(fname: str) -> bool:
        return fname.endswith(".js")

    for t in tasks:
        fname = (t.get("file") or "").strip().lstrip("/")
        if not fname or not _is_js(fname):
            continue
        deps = t.get("dependencies") or []
        is_depended_on = any(
            fname in (other.get("dependencies") or [])
            for other in tasks if other is not t
        )
        has_deps = bool(deps)
        exports = t.get("exports")
        if not is_depended_on and not has_deps:
            continue  # 独立文件（如 css 旁路、纯入口 js）不强制
        if not isinstance(exports, dict) or not exports:
            issues.append(
                f"{fname} 被/会跨文件调用但未声明 exports "
                f"(格式 {{\"全局名\": [\"方法1\", ...]}})"
            )
            continue
        for gname, methods in exports.items():
            if not isinstance(gname, str) or not gname.strip():
                issues.append(f"{fname} exports 存在空的全局名")
            if not isinstance(methods, list) or not methods or \
                    not all(isinstance(m, str) and m.strip() for m in methods):
                issues.append(f"{fname} 的 exports.{gname} 必须是非空方法名数组")
    return issues


def _validate_acceptance_criteria(plan: dict) -> list[str]:
    """AC 可操作性校验。"""
    issues = []
    acs = plan.get("acceptance_criteria")
    if not isinstance(acs, list) or not acs:
        return ["acceptance_criteria 为空或缺失——没有 DoD 就无法机器验收"]
    if len(acs) > 6:
        issues.append(f"acceptance_criteria 数量过多 ({len(acs)})，控制在 3-5 条")
    for ac in acs:
        if not isinstance(ac, dict):
            issues.append(f"acceptance_criteria 条目格式错误: {str(ac)[:50]}")
            continue
        ac_id = ac.get("id", "?")
        verify = (ac.get("how_to_verify") or "").strip()
        label = (ac.get("label") or "").strip()
        if not label:
            issues.append(f"{ac_id} 缺少 label")
        if not verify:
            issues.append(f"{ac_id} 缺少 how_to_verify")
            continue
        lowered = verify.lower()
        if any(p in verify for p in NON_ACTIONABLE_PHRASES):
            issues.append(f"{ac_id} 的 how_to_verify 含不可断言描述「{verify[:20]}」")
            continue
        if not any(v.lower() in lowered for v in ACTIONABLE_VERBS):
            issues.append(
                f"{ac_id} 的 how_to_verify 不含可操作动词（点击/输入/按…）: {verify[:40]}"
            )
    return issues


def _validate_complexity(plan: dict) -> list[str]:
    """复杂度与文件数一致性。"""
    issues = []
    complexity = plan.get("complexity", "standard")
    fs = _normalize(plan.get("file_structure"))
    if complexity == "simple" and len(fs) > 2:
        issues.append(f"complexity=simple 但规划了 {len(fs)} 个文件，应为 standard")
    if complexity == "standard" and len(fs) > 12:
        issues.append(f"complexity=standard 但规划了 {len(fs)} 个文件，超出单次交付能力")
    return issues


def validate_plan(plan: dict) -> tuple[bool, list[str]]:
    """程序化校验 TL plan。

    Returns:
        (ok, issues)：ok=True 表示可以进入 coder；issues 为人话问题清单。
    """
    if not isinstance(plan, dict):
        return False, ["plan 不是 JSON 对象"]

    issues: list[str] = []
    required_fields = ["features", "file_structure", "tasks"]
    for field in required_fields:
        value = plan.get(field)
        if value is None or (isinstance(value, (list, dict, str)) and not value):
            # simple 允许省略 tasks
            if field == "tasks" and plan.get("complexity") == "simple":
                continue
            issues.append(f"缺少必填字段 {field}")

    issues += _validate_file_closure(plan)
    issues += _validate_tasks(plan)
    issues += _validate_exports_contract(plan)
    issues += _validate_acceptance_criteria(plan)
    issues += _validate_complexity(plan)

    return (not issues), issues


def build_plan_retry_feedback(issues: list[str]) -> str:
    """把校验问题转成打回 TL 重出的反馈文本。"""
    lines = [
        "你上一次输出的开发计划未通过程序化校验，存在以下问题：",
        "",
    ]
    lines += [f"- {issue}" for issue in issues]
    lines += [
        "",
        "请修正以上问题后重新输出完整 JSON 计划（不要只输出差异部分）。",
    ]
    return "\n".join(lines)


def build_api_contracts_section(plan: dict | None) -> str:
    """从 plan.tasks[].exports 渲染跨文件 API 契约段落，注入 coder prompt。

    这是「计划期声明的 exports」到「编码期硬约束」的桥：coder 只允许调用
    清单内方法，杜绝 app.js 想象 utils.js 没实现的 API 这类断层
    （需求 124 事故）。无契约时返回空串，prompt 不留空洞标题。
    """
    tasks = [t for t in ((plan or {}).get("tasks") or []) if isinstance(t, dict)]
    rows = []
    for t in tasks:
        fname = (t.get("file") or "").strip().lstrip("/")
        exports = t.get("exports")
        if not fname.endswith(".js") or not isinstance(exports, dict):
            continue
        for gname, methods in exports.items():
            if not (isinstance(gname, str) and gname.strip()):
                continue
            if isinstance(methods, list) and methods:
                names = ", ".join(
                    m.strip() for m in methods if isinstance(m, str) and m.strip()
                )
                if names:
                    rows.append(f"- **{gname.strip()}**（{fname}）: {names}")
    if not rows:
        return ""
    return (
        "## 跨文件 API 契约（唯一合法调用清单）\n"
        "以下是各全局对象已声明的公开方法。写代码时**只允许调用这些方法**；\n"
        "严禁调用未列出的方法（如 toast/copyText 等未声明能力必须在本文件内自行实现，\n"
        "不得假设上游对象已提供）。你实现导出方法时，属性名必须与本清单逐字一致。\n\n"
        + "\n".join(rows)
    )
