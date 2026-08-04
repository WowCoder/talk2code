# -*- coding: utf-8 -*-
"""
进度约束 Hook —— 硬阻断不合理行为

- block_unnecessary_read: 写入后 2 轮内阻断 read_file 同一文件
- block_premature_completion: contract 未全部完成时阻断 task_complete

原则：基于可验证的客观事实做阻断判断，不依赖 LLM 的主观判断。
"""

from harness.constraints.hooks import HookContext
from harness.constraints.completion_contract import CompletionContract
from harness.observability.logger import get_logger

logger = get_logger(__name__)

# 写入后禁止回读的轮次数（可配置）
# 从 2 降低到 1：允许 Agent 在下一轮回读验证写入内容，
# 避免"盲写"导致连续 edit_file 失败后只能重写整个文件。
READ_BLOCK_WINDOW = 1


def _get_contract(ctx: HookContext):
    """从 HookContext 中获取 CompletionContract 实例"""
    # 优先级：state 中显式传入 > 从 workspace 创建
    contract = ctx.state.get("_completion_contract") if ctx.state else None
    if contract is not None:
        return contract

    workspace = ctx.state.get("_workspace") if ctx.state else None
    if workspace:
        return CompletionContract(workspace)

    return None


def block_unnecessary_read(ctx: HookContext) -> str | None:
    """阻断刚写入文件的 read_file 调用

    基于 _recent_writes 追踪（写入文件名 → 写入时的轮次），
    在 READ_BLOCK_WINDOW 轮内阻断对同一文件的 read_file。

    Returns:
        None = 允许通过
        str = 阻断原因（返回给 Agent）
    """
    if ctx.tool_name != "read_file":
        return None

    state = ctx.state or {}
    recent_writes = state.get("_recent_writes", {})
    if not recent_writes:
        return None

    filename = (ctx.tool_args or {}).get("filename", "")
    if not filename:
        return None

    if filename not in recent_writes:
        return None  # 不是刚写入的文件

    write_round = recent_writes[filename]
    current_round = state.get("tool_call_count", 0)

    # 如果 write_round > current_round，说明 tool_call_count 已跨节点重置
    # （例如修复循环回到 coder 节点），此时应清理过期记录并放行读取
    if write_round > current_round:
        del recent_writes[filename]
        return None

    rounds_since_write = current_round - write_round

    if rounds_since_write <= READ_BLOCK_WINDOW:
        msg = (
            f"[硬约束] 文件 {filename} 在第 {write_round} 轮刚刚写入完成，"
            f"当前第 {current_round} 轮（仅间隔 {rounds_since_write} 轮），"
            f"禁止 read_file 回读验证。文件已完整写入，无需验证。"
            f"请继续使用文件摘要或直接编辑下一个文件。"
        )
        logger.info(f"[ProgressHook] 阻断 read_file: {filename} (写入轮次={write_round}, 当前={current_round})")
        return msg

    # 超出窗口期，允许读取，清理追踪记录
    del recent_writes[filename]
    return None


def block_premature_completion(ctx: HookContext) -> str | None:
    """阻断未完成的 task_complete 声明

    当 Agent 尝试声明任务完成时，检查 CompletionContract：
    - 所有文件 created=true → 允许通过
    - 还有未完成文件 → 阻断，返回未完成文件列表

    Returns:
        None = 允许通过
        str = 阻断原因（含未完成文件列表）
    """
    # 通过 tool_name 检测 task_complete 声明
    # Agent 声明完成有两种方式：返回无 tool_calls 的文本、或 task_complete 工具
    is_complete_signal = (
        ctx.tool_name == "task_complete" or
        (ctx.tool_name is None and ctx.state.get("current_step") == "task_complete")
    )

    if not is_complete_signal:
        return None

    contract = _get_contract(ctx)
    if not contract or not contract.exists():
        return None  # 无 contract，不阻断

    if contract.all_completed():
        return None  # 全部完成，放行

    pending = contract.pending_files()
    msg = (
        f"[硬约束] 任务尚未完成！以下 {len(pending)} 个文件尚未创建：\n"
        + "\n".join(f"  - {f}" for f in pending)
        + f"\n\n进度: {contract.completed_count()}/{contract.total_files()} 已完成。"
        f"请继续用 write_file 创建剩余文件，全部完成后才能声明任务完成。"
    )
    logger.info(
        f"[ProgressHook] 阻断 task_complete: "
        f"pending={len(pending)}/{contract.total_files()}"
    )
    return msg


def track_write_success(ctx: HookContext) -> str | None:
    """追踪 write_file 成功后更新相关状态

    此 Hook 在 POST_TOOL_USE 触发，执行：
    1. 将写入的文件记录到 _recent_writes（用于 block_unnecessary_read）
    2. 更新 CompletionContract（mark_created）

    原则：成功静默，始终返回 None（不阻断）。

    Returns:
        始终返回 None
    """
    if ctx.tool_name != "write_file":
        return None

    # 检查写入是否成功（通过 tool_result 判断）
    if not ctx.tool_result:
        return None

    state = ctx.state or {}
    filename = (ctx.tool_args or {}).get("filename", "")
    if not filename:
        return None

    # 1. 追踪最近写入（用于防回读）
    current_round = state.get("tool_call_count", 0)
    state.setdefault("_recent_writes", {})[filename] = current_round

    # 2. 更新 CompletionContract
    contract = _get_contract(ctx)
    if contract and contract.exists():
        updated = contract.mark_created(filename)
        if updated:
            progress = contract.get_progress()
            logger.info(
                f"[ProgressHook] contract 进度: "
                f"{progress['completed']}/{progress['total']}"
            )

    # 静默通过
    return None
