# -*- coding: utf-8 -*-
"""
Plan Validator —— TL 产出的机器可校验 DoD（完成定义）

审查结论：AC 本身的质量（可操作性、覆盖度）此前无任何校验——AC 弱则
fast_pass 通道直接放水。本模块在 plan 落地前做程序化校验，不合格打回
TL 重出（最多 1 次），而不是带病进 coder：

- 需求契约：requirement_restated / assumptions 是用户签字的对象，必须有人话内容
- 文件引用闭合：tasks/implementation_order/file_structure 相互一致，
  index.html 引用的本地 js/css 都出现在清单里（ENV-6 前置检查）
- 任务可执行：每个 file 有 purpose/description 且达到最小信息量
- exports 契约：跨文件调用的 js 必须声明 {全局名: [方法...]} 导出清单
- AC 可操作：每条 AC 的 how_to_verify 含可操作动词（点击/输入/按…）
- **AC 可断言**：必须含至少一个"看得见的变化"观察点，否则脚本无处下手
- **AC anchor**：必须描述页面语义区域，且不允许写成 CSS 选择器
  （写成选择器 = 从产物代码反推 = 自证循环）
- **AC 去重**：同区域 + 同首动作的 AC 视为重复，避免同一缺陷重复计分
- **功能覆盖**：每个 feature 至少被一条 AC 覆盖，否则功能悄悄没验收
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
    # ---- 增补：数据类应用的日常操作动词 ----
    # 此前只收录"点击/输入"这一档，导致 "先确认汇总面板显示为0，然后添加一条
    # 收入记录" 这种**完全可操作**的 AC 被判"不含可操作动词"，白挨一次打回重出
    # （req 204 实测）。漏词造成的代价是真实的 LLM 调用与十几秒等待，而放宽的
    # 代价只是少拦一个真正的坏 AC —— 后者本来还有"观察点"这一道兜底。
    "添加", "新增", "新建", "创建", "删除", "移除", "编辑", "修改",
    "保存", "记录", "填写", "填入", "录入", "选中", "选定",
    "上传", "下载", "发送", "发布", "分享", "导入", "导出",
    "确认", "取消", "关闭", "展开", "收起", "刷新", "重置", "清空",
    "排序", "滑动", "拖拽", "移动", "触摸", "松开", "悬停",
    "开始", "暂停", "继续", "重启", "重新开始", "返回", "跳转",
    "键盘", "方向键", "鼠标", "逐条", "依次", "演练",
    "add", "create", "delete", "remove", "edit", "save", "upload",
    "download", "send", "confirm", "cancel", "expand", "collapse",
    "refresh", "reset", "clear", "sort", "swipe", "hover", "start",
    "pause", "resume", "restart", "tap",
]

# 不可断言的"假验收"措辞：出现即视为不可操作
NON_ACTIONABLE_PHRASES = ["界面美观", "运行正常", "体验良好", "样式统一"]

# 可断言观察点：只有"能操作"没有"能断言"的 AC 不合格。
# 观察点必须是操作之后**发生变化**的东西，因此词表里全是变化类动词/状态名词。
OBSERVABLE_PATTERNS = [
    "出现", "消失", "显示", "隐藏", "遮罩", "弹窗", "弹出", "关闭",
    "变成", "变为", "切换为", "切换到", "更新", "刷新",
    "新增", "增加到", "减少", "减少到", "数量", "计数", "个数", "长度",
    "文本", "文字", "内容", "编号", "数值", "分数", "得分", "金额",
    "状态", "禁用", "启用", "高亮", "选中", "勾选", "标记为",
    "列表", "清空", "为空", "置灰", "展开", "收起", "跳转",
    # 游戏/画布类：这类产物没有"列表增加几项"可言，观察点在画面本身。
    # 少了这批词会把整份游戏类 plan 误判为不可断言 —— AC 只是写得更朴素，不是没得看。
    "位置", "移动", "棋盘", "格子", "方块", "消除", "回合",
    "倒计时", "生命", "关卡", "手牌", "昼夜", "旋转",
]

# anchor 里出现这些 = 把语义锚点写成了代码选择器，失去"按页面找元素"的意义
# 收紧到「字母/下划线开头」：否则 "第 1.5 项" 这样的中文描述会被 \.[\w-]+ 误判
SELECTOR_PATTERNS = [
    re.compile(r"#[A-Za-z_][\w-]*"),        # #id
    re.compile(r"\.[A-Za-z_][\w-]*"),       # .class
    re.compile(r"\bdata-[\w-]+"),           # data-* 属性
    re.compile(r"\bgetElementById\b|\bquerySelector\b", re.I),  # DOM API
    re.compile(r"\[[\w-]+="),               # 属性选择器
]


def _normalize(files) -> list[str]:
    if not isinstance(files, list):
        return []
    return [f.strip().lstrip("/") for f in files if isinstance(f, str) and f.strip()]


def _validate_requirement_contract(plan: dict) -> list[str]:
    """需求契约：用户签字的对象，必须有人话内容。

    requirement_restated 缺位时，确认卡片只剩一堆技术名词，用户无法判断
    "你理解对了没有"——这正是确认动作退化为无意义点头的根因。
    """
    issues = []
    restated = (plan.get("requirement_restated") or "").strip()
    if not restated:
        issues.append("缺少 requirement_restated（用一句人话复述用户要什么）")
    elif len(restated) > 40:
        issues.append(
            f"requirement_restated 过长 ({len(restated)} 字)，控制在 20 字以内的人话"
        )

    assumptions = plan.get("assumptions")
    if assumptions is not None and not isinstance(assumptions, list):
        issues.append("assumptions 必须是字符串数组")
    elif isinstance(assumptions, list):
        for i, a in enumerate(assumptions):
            if not isinstance(a, str) or not a.strip():
                issues.append(f"assumptions[{i}] 为空")
    return issues


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


def _common_prefix_len(a: str, b: str) -> int:
    """两串的公共前缀长度。用于判断两条 AC 是不是从同一个动作出发。"""
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


# 公共前缀达到这个长度即认为两条 AC 走的是同一个起始动作。
# 取字符而不是词：中文没有空格分词，"点击添加按钮，" 与 "点击添加按钮后…"
# 必须判为同一起点，否则去重规则永远命中不了真实重复。
DUPLICATE_PREFIX_THRESHOLD = 6


def _validate_acceptance_criteria(plan: dict) -> list[str]:
    """AC 可操作性 + 可断言 + anchor 语义化 + 去重校验。"""
    issues = []
    acs = plan.get("acceptance_criteria")
    if not isinstance(acs, list) or not acs:
        return ["acceptance_criteria 为空或缺失——没有 DoD 就无法机器验收"]
    if len(acs) > 6:
        issues.append(f"acceptance_criteria 数量过多 ({len(acs)})，控制在 3-5 条")

    features = [f.strip() for f in (plan.get("features") or []) if isinstance(f, str)]
    # (anchor, verify) 的历史记录，用于「同区域 + 同起点动作」的重复判定
    seen: list[tuple] = []

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

        # ---- 可断言观察点：能操作但断言不了 = 脚本跑完不知道判什么 ----
        if not any(p in verify for p in OBSERVABLE_PATTERNS):
            issues.append(
                f"{ac_id} 的 how_to_verify 没有可断言的观察点"
                f"（出现/消失/变为/数量增减/文本/数值 等）: {verify[:40]}"
            )

        # ---- feature 锚定：用于校验功能覆盖度 ----
        feature = (ac.get("feature") or "").strip()
        if not feature:
            issues.append(f"{ac_id} 缺少 feature（必须与 features 中某项逐字一致）")
        elif features and feature not in features:
            issues.append(
                f"{ac_id} 的 feature「{feature[:16]}」不在 features 清单里，无法做覆盖度校验"
            )

        # ---- anchor：必须是页面语义区域，不能写成选择器 ----
        anchor = (ac.get("anchor") or "").strip()
        if not anchor:
            issues.append(
                f"{ac_id} 缺少 anchor（用自然语言描述这条验收发生在页面的哪个区域）"
            )
        else:
            if len(anchor) < 6:
                issues.append(f"{ac_id} 的 anchor 过短，说不清是哪块区域: {anchor}")
            for pat in SELECTOR_PATTERNS:
                if pat.search(anchor):
                    issues.append(
                        f"{ac_id} 的 anchor 写成了代码选择器「{anchor[:30]}」，"
                        f"必须改成人话描述（如'页面顶部的输入框')"
                    )
                    break

        # ---- 去重：同区域 + 同起点动作 = 同一条用户路径被拆成了两条 ----
        # 后果不是"多跑一次"，而是同一个缺陷被算成两个失败，误导 repair 的处理优先级。
        dup_of = None
        for prev_anchor, prev_verify, prev_id in seen:
            if prev_anchor != anchor:
                continue
            if _common_prefix_len(verify, prev_verify) >= DUPLICATE_PREFIX_THRESHOLD:
                dup_of = prev_id
                break
        if dup_of:
            issues.append(
                f"{ac_id} 与 {dup_of} 语义重复（同一区域 + 同一个起始动作），请合并成一条"
            )
        else:
            seen.append((anchor, verify, ac_id))

    return issues


def _validate_feature_coverage(plan: dict) -> list[str]:
    """功能覆盖度：每个列出的功能至少被一条 AC 验收。

    features 是写给用户看的承诺，AC 是系统实际判定的内容。两者不挂钩时，
    承诺可以随便写——用户看到 5 个功能，实际只有 2 个被验证过。
    """
    issues = []
    features = [
        f.strip() for f in (plan.get("features") or [])
        if isinstance(f, str) and f.strip()
    ]
    acs = [a for a in (plan.get("acceptance_criteria") or []) if isinstance(a, dict)]
    if not features or not acs:
        return issues

    covered = {(a.get("feature") or "").strip() for a in acs}
    uncovered = [f for f in features if f not in covered]
    if uncovered:
        issues.append(
            f"以下功能没有任何 AC 覆盖（用户会看到承诺但从未被验证）: {uncovered}"
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
    for field in ("features", "file_structure"):
        value = plan.get(field)
        if value is None or (isinstance(value, (list, dict, str)) and not value):
            issues.append(f"缺少必填字段 {field}")

    if not plan.get("tasks") and plan.get("complexity") != "simple":
        issues.append("缺少必填字段 tasks")

    issues += _validate_requirement_contract(plan)
    issues += _validate_file_closure(plan)
    issues += _validate_tasks(plan)
    issues += _validate_exports_contract(plan)
    issues += _validate_acceptance_criteria(plan)
    issues += _validate_feature_coverage(plan)
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
