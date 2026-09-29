# -*- coding: utf-8 -*-
"""执行进度的唯一分配表。

为什么需要它
------------
进度百分比此前散落在四处各写各的：

- `services/requirement_service.py` 的 `_progress_map`：20 / 70 / 90 / 85
- `harness/runtime.py` 的 `_push_activity`：`20 + 75 * ratio`
- `harness/constraints/progress_hooks.py`：`20 + 75 * ratio`
- `harness/instructions/nodes.py` 的验证阶段：裸写的 80 / 85 / 90

拼起来是**非单调**的：编码按 `20 + 75*ratio` 能冲到 95，验证却从 80 重来
（每次需求都必然倒退 15 个点）；修复节点又是 85，比验证的 90 还低。
进度条来回跳之后，它唯一的作用——"现在跑到哪一步了"——就失效了。

现在的分配（单调不减，见 `full_sequence()` 的单测）
--------------------------------------------------
    需求分析             20
    编码（第 r 轮）      20+6r .. 75+6r
    验证（第 r 轮）      80+6r .. 86+6r   （7 个子步骤，见 VERIFY_STEPS）
    修复（第 r 轮）      86+6r            （等于本轮验证的收尾值）
    完成                 100

`r = min(repair_count, _MAX_ROUND)`：验证/修复循环每多跑一轮，整段右移 6 个点，
这样"修复 → 再验证"不会退回上一轮的位置；轮次超过上限后收敛到封顶值。

仍然存在的**有意行为**：coder 因架构类缺陷被重新召回时，编码带会回到本轮
起点（r 已右移，故起点仍高于上一轮修复值）。前端的 `useSSE` 对同一需求的
进度取**历史最大值**，所以即便后端推了更小的值，用户看到的进度条也不会回退。
"""

from typing import Any, Dict, Tuple

# ---- 阶段基准 ----
START = 0                 # 收到需求
PLANNING = 20             # 需求分析（TL 拆解计划）
PLAN_CONFIRMED = 20       # 用户确认 Plan（与 PLANNING 对齐，不回退）
DONE = 100                # 终态

# ---- 编码带 ----
_CODING_BASE = 20
_CODING_SPAN = 55         # 20..75；上界必须低于验证起点，否则验证一亮相就回退
_CODING_MAX = _CODING_BASE + _CODING_SPAN

# ---- 验证 / 修复带（按轮次右移）----
VERIFY_BASE = 80
_ROUND_SPAN = 6           # 每轮占 6 个点
_MAX_ROUND = 2            # 轮次上限：再往后收敛到封顶，避免无限逼近 100

# 验证阶段的子步骤：key / 给用户看的动作文案 / 轮内偏移(0..6)
# 文案必须描述**动作**而不是角色名；偏移必须严格递增，否则进度条会原地不动。
VERIFY_STEPS: Tuple[Tuple[str, str, int], ...] = (
    ('preview', '正在浏览器里打开页面，检查报错', 0),
    ('ac', '正在逐条验证验收标准', 1),
    ('smoke', '正在做通用交互冒烟测试', 2),
    ('contract', '正在检查类名契约与全局接口', 3),
    ('dod', '正在核对验收标准与实现的对应关系', 4),
    ('vision', '正在采集页面的视觉证据', 5),
    ('evaluate', '正在做最终质量评估', 6),
)

STEP_ORDER: Tuple[str, ...] = tuple(key for key, _label, _off in VERIFY_STEPS)
STEP_LABEL: Dict[str, str] = {key: label for key, label, _off in VERIFY_STEPS}
STEP_OFFSET: Dict[str, int] = {key: off for key, _label, off in VERIFY_STEPS}


def _normalize_round(round_index: int) -> int:
    """轮次归一到 0.._MAX_ROUND（越界收敛，负数当 0）。"""
    try:
        value = int(round_index)
    except (TypeError, ValueError):
        return 0
    return min(max(value, 0), _MAX_ROUND)


def round_index(state: Any) -> int:
    """从 state 里取当前修复轮次（**封顶**，用于进度百分比计算）。

    `repair_count` 在 verify_node 判定 NEEDS_WORK 时自增，因此 verify 节点
    读到的值正好是"这是第几轮验证"（首轮 0）。封顶防止进度无限逼近 100。

    注意：进度计算要用封顶值（`verify_percent`/`coding_percent`/`repair_percent`），
    但卡片头部**显示**用真实轮次（见 `actual_round`），否则第 3、4 轮会被
    归一成相同值，用户看到两张「第 3 轮」的卡。
    """
    if not isinstance(state, dict):
        return 0
    meta = state.get('metadata')
    if not isinstance(meta, dict):
        return 0
    return _normalize_round(meta.get('repair_count') or 0)


def actual_round(state: Any) -> int:
    """取**真实**修复轮次（不封顶）。

    卡片标签「第 N 轮」用这个；`_MAX_ROUND` 只为进度百分比封顶设的，不该
   传染到显示语义——第 3、4 轮是真实发生过的，归一成「第 3 轮」会让用户
    以为修复卡了。
    """
    if not isinstance(state, dict):
        return 0
    meta = state.get('metadata')
    if not isinstance(meta, dict):
        return 0
    try:
        return int(meta.get('repair_count') or 0)
    except (TypeError, ValueError):
        return 0


def coding_percent(ratio: float, round_index_: int = 0) -> int:
    """编码进度。ratio 为 0..1 的完成比（文件数 / 迭代预算）。"""
    try:
        value = float(ratio)
    except (TypeError, ValueError):
        value = 0.0
    value = min(max(value, 0.0), 1.0)
    base = _CODING_BASE + _ROUND_SPAN * _normalize_round(round_index_)
    return base + int(_CODING_SPAN * value)


def verify_percent(step: str, round_index_: int = 0) -> int:
    """验证阶段某个子步骤的进度。未知 step 取轮内起点（不炸）。"""
    base = VERIFY_BASE + _ROUND_SPAN * _normalize_round(round_index_)
    return base + STEP_OFFSET.get(step, 0)


def repair_percent(round_index_: int = 0) -> int:
    """修复阶段：等于本轮验证的收尾值，使"验证 → 修复"持平而不回退。"""
    base = VERIFY_BASE + _ROUND_SPAN * _normalize_round(round_index_)
    return base + _ROUND_SPAN


def node_percent(node_name: str, round_index_: int = 0) -> int:
    """节点级进度（graph 每走过一个节点推一次）。

    coder 取编码带**起点**：进节点后 `_push_activity` 会把它一路推到带上界，
    若这里直接给上界，第一步动作就会把进度条拉回去。
    """
    if node_name == 'team_leader':
        return PLANNING
    if node_name == 'coder':
        return _CODING_BASE + _ROUND_SPAN * _normalize_round(round_index_)
    if node_name == 'verify':
        return verify_percent('preview', round_index_)
    if node_name == 'repair':
        return repair_percent(round_index_)
    return START


def single_pass_sequence(round_index_: int = 0) -> list:
    """单轮（编码 → 验证 → 修复）会推的进度，按发生顺序。

    这是**后端必须自己保证**的单调性：一轮之内进度条绝不能回退。
    """
    seq = [START, PLANNING, PLAN_CONFIRMED]
    seq.append(node_percent('coder', round_index_))
    for ratio in (0.0, 0.5, 1.0):
        seq.append(coding_percent(ratio, round_index_))
    seq.append(node_percent('verify', round_index_))
    for step in STEP_ORDER:
        seq.append(verify_percent(step, round_index_))
    seq.append(node_percent('repair', round_index_))
    return seq


def full_sequence(max_rounds: int = _MAX_ROUND + 1, clamp: bool = True) -> list:
    """多轮跑批的进度序列（编码 → 验证 → 修复 → 再编码 → …）。

    `clamp=True` 模拟前端的单调钳制（同一需求只认历史最大值）。
    `clamp=False` 是后端真实推送的原始序列——它**不是**单调的：
    coder 因架构类缺陷被重新召回时，编码带按轮次右移后仍可能低于上一轮修复值。
    这是有意的：coder 重入意味着重新产出代码，后端如实上报；用户看到的不回退
    由前端钳制兜住（`useSSE` 对整个需求取最大值）。
    """
    seq = [START, PLANNING, PLAN_CONFIRMED]
    for r in range(max_rounds):
        seq.extend(single_pass_sequence(r)[3:])
    seq.append(DONE)
    if not clamp:
        return seq
    out = []
    high = -1
    for value in seq:
        high = max(high, value)
        out.append(high)
    return out
