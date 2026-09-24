# -*- coding: utf-8 -*-
"""
进度约束 Hook —— 硬阻断不合理行为

- block_premature_completion: contract 未全部完成时阻断 task_complete
- track_write_success: write_file 成功后更新 CompletionContract（成功后静默）

原则：基于可验证的客观事实做阻断判断，不依赖 LLM 的主观判断。

（原 block_unnecessary_read 已移除：v2 把 read_file 定位为 just-in-time 内容通道，
"写入后 N 轮内禁止回读" 会拦截内容恢复型读取，与设计冲突。）
"""

from harness.constraints.hooks import HookContext
from harness.constraints.completion_contract import CompletionContract
from harness.observability.logger import get_logger

logger = get_logger(__name__)


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


def block_premature_completion(ctx: HookContext) -> str | None:
    """阻断未完成的 task_complete 声明（两级契约校验）

    当 Agent 尝试声明任务完成时：
    1. 文件级：CompletionContract 中所有文件 created=true？
    2. AC 级（v2）：每条验收条件在 index.html 中能找到交互元素证据？
       （静态启发式预检，真正的 DOM 验证由 verify_node Playwright 承担）

    Returns:
        None = 允许通过
        str = 阻断原因（含未完成清单/AC 缺口）
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

    messages = []

    # ---- 第一级：文件创建完整性 ----
    if not contract.all_completed():
        pending = contract.pending_files()
        messages.append(
            f"以下 {len(pending)} 个文件尚未创建：\n"
            + "\n".join(f"  - {f}" for f in pending)
            + f"\n进度: {contract.completed_count()}/{contract.total_files()} 已完成。"
            f"请继续用 write_file 创建剩余文件。"
        )
        logger.info(
            f"[ProgressHook] 阻断 task_complete: "
            f"pending={len(pending)}/{contract.total_files()}"
        )

    # ---- 第二级：AC 交互元素证据预检（零 LLM） ----
    acs_pending = contract.pending_acs()
    if acs_pending:
        workspace = ctx.state.get("_workspace") if ctx.state else None
        index_html = ""
        if workspace is not None:
            try:
                index_html = workspace.read("index.html")
            except Exception:
                index_html = ""
        from harness.constraints.completion_contract import find_ac_evidence
        no_evidence = [
            ac for ac in acs_pending
            if not find_ac_evidence(ac, index_html)
        ]
        if no_evidence:
            messages.append(
                "以下验收条件在 index.html 中找不到对应的交互元素证据：\n"
                + "\n".join(
                    f"  - [{ac['id']}] {ac.get('label', '')}"
                    f"（验证方式: {ac.get('how_to_verify', '')[:60]}）"
                    for ac in no_evidence
                )
                + "\n请确认这些功能的界面元素与交互逻辑确实已实现——"
                  "缺少元素的 AC 在最终验收时必然失败。"
            )
            logger.info(
                f"[ProgressHook] AC 预检发现 {len(no_evidence)} 条无证据: "
                f"{[ac['id'] for ac in no_evidence]}"
            )

    if not messages:
        return None

    return "[硬约束] 任务尚未完成！\n" + "\n\n".join(messages)


def track_write_success(ctx: HookContext) -> str | None:
    """write_file 成功后更新 CompletionContract

    此 Hook 在 POST_TOOL_USE 触发：write_file 成功后调用 contract.mark_created。

    原则：成功静默，始终返回 None（不阻断）。

    Returns:
        始终返回 None
    """
    if ctx.tool_name != "write_file":
        return None

    # 检查写入是否成功（通过 tool_result 判断）
    if not ctx.tool_result:
        return None

    filename = (ctx.tool_args or {}).get("filename", "")
    if not filename:
        return None

    # 更新 CompletionContract
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
