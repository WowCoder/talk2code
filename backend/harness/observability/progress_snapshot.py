# -*- coding: utf-8 -*-
"""需求实时进度快照 —— 让「刷新页面」不丢进度。

背景（本次要修的现象）：正在处理需求时刷新页面，顶部的阶段、百分比、
「已完成 js/app.js（2/3）」全部消失，界面退回「准备中」，而后端其实还在跑。

原因是进度此前只活在 SSE 实时通道里。SSE 管理器确实会给迟到客户端回放缓冲，
但缓冲只有最近 200 条消息，而一次生成会推送几百条事件（迭代步骤、QA 每一步、
验收子步骤……）——等用户刷新时，最早那批 progress 早被挤出去了。也就是说
「重连回放」能兜住的是**刚刚断线**，兜不住**刷新页面**（请求重发、缓冲早已滚走）。

所以把最新一条进度落到库里：写进度的地方顺手写一行，进页面时读一行。

设计约束：
- **一个需求一行**（requirement_id 作主键），不累积历史 —— 它只回答
  「现在到哪一步了」，历史轨迹由 agent_events 负责。
- **永不阻断主流程**：进度是展示信息，落库失败只记日志。
- started_at 只在**新一轮开始**时重置（见 `reset`），否则刷新后
  「已等待 9 分钟」会被算成 0。
"""
from datetime import datetime, timezone

from utils.db import transactional_db
from harness.observability.logger import get_logger

logger = get_logger(__name__)


def _iso(dt):
    """库里存的是 UTC naive 时间，出参必须**带时区**。

    不加 +00:00 的话前端 `new Date(naive)` 会按本地时区解析，等待时长凭空多出
    一个时区偏移（东八区就是 8 小时）——「已等待 8 小时」这种错值肉眼极难发现。
    """
    return None if dt is None else dt.replace(tzinfo=timezone.utc).isoformat()


def record(requirement_id: int, percent=None, message=None, stage=None) -> None:
    """写入最新进度（一个需求只保留一行）。

    三个字段都可选：只更新传进来的那些。stage 传空串表示「沿用上一阶段」
    （后端部分埋点不带 stage，写空会让阶段指示器闪回未知）。
    """
    if not requirement_id:
        return
    try:
        from models.models import RequirementProgress

        with transactional_db() as db:
            row = db.query(RequirementProgress).filter(
                RequirementProgress.requirement_id == requirement_id
            ).first()
            if row is None:
                row = RequirementProgress(requirement_id=requirement_id)
                db.add(row)
            if percent is not None:
                row.percent = int(percent)
            if message is not None:
                row.message = str(message)[:500]
            if stage:  # 空串 = 沿用上一阶段，不覆盖
                row.stage = str(stage)[:32]
            if row.started_at is None:
                row.started_at = datetime.utcnow()
            row.updated_at = datetime.utcnow()
    except Exception as e:
        # 进度是展示信息：落库失败不能影响生成主流程
        logger.debug(f"[ProgressSnapshot] 写入失败（忽略）req={requirement_id}: {e}")


def reset(requirement_id: int) -> None:
    """新一轮开始：清掉上一轮残留，重新计时。

    必须在任务入队的地方调用（新建需求 / 澄清后重跑 / 确认计划后编码 /
    断点续跑）。不清的话，上一轮的 started_at 会让「已等待」显示成几小时前
    就开始等了 —— 用户看到的等待时长比实际长得多。
    """
    if not requirement_id:
        return
    try:
        from models.models import RequirementProgress

        with transactional_db() as db:
            db.query(RequirementProgress).filter(
                RequirementProgress.requirement_id == requirement_id
            ).delete()
    except Exception as e:
        logger.debug(f"[ProgressSnapshot] 重置失败（忽略）req={requirement_id}: {e}")


def read(requirement_id: int):
    """读取进度快照；没有记录返回 None。"""
    if not requirement_id:
        return None
    try:
        from models.models import RequirementProgress

        with transactional_db() as db:
            row = db.query(RequirementProgress).filter(
                RequirementProgress.requirement_id == requirement_id
            ).first()
            if row is None:
                return None
            return {
                'stage': row.stage or '',
                'percent': int(row.percent or 0),
                'message': row.message or '',
                'started_at': _iso(row.started_at),
                'updated_at': _iso(row.updated_at),
            }
    except Exception as e:
        logger.debug(f"[ProgressSnapshot] 读取失败（忽略）req={requirement_id}: {e}")
        return None


def started_at(requirement_id: int):
    """本次运行的起始时刻（UTC naive），供心跳算「已等待多久」。

    单独查一次而不是复用 `read()` 的字典：字典里的时间是带时区的 ISO 串，
    解析回来再和 naive 的 epoch 相减会抛 TypeError，而这类错误只会在
    「15 秒心跳」这条很少被注意的路径上出现。
    """
    if not requirement_id:
        return None
    try:
        from models.models import RequirementProgress

        with transactional_db() as db:
            row = db.query(RequirementProgress.started_at).filter(
                RequirementProgress.requirement_id == requirement_id
            ).first()
            return row[0] if row else None
    except Exception as e:
        logger.debug(f"[ProgressSnapshot] 读取起始时间失败（忽略）req={requirement_id}: {e}")
        return None
