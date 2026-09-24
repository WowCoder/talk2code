# -*- coding: utf-8 -*-
"""启动期僵尸需求清理 —— 让被重启打断的需求重新可续跑。

进程重启后，上一进程正在执行的需求会永久停在 processing，成因是三层叠加：

1. ``TaskQueue`` 是纯内存的（ThreadPoolExecutor + dict），重启即清空，
   没有任何持久化重放；
2. 检查点（``agent_checkpoints``）虽然跨重启持久化，但只有"用户主动提交"
   才会消费它；
3. ``process_requirement`` 的状态守卫把 ``status='processing'`` 直接跳过，
   于是即便重新提交也会被静默吞掉。

结果：这类需求既不会被自动拉起，也无法被重新提交，只能永久卡住。

本模块在启动时把它们改判为 ``interrupted``，使其重新进入可续跑状态。
真正的续跑由用户显式触发（``POST /api/requirements/<id>/resume``），
工作流经检查点恢复上下文 —— 不自动重灌队列，避免幂等性与半写工作区风险。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

from config import BACKEND_DIR, settings

logger = logging.getLogger(__name__)

# 同一次启动只扫一遍。多 worker（gunicorn -w N）会各自执行启动期代码，
# 若无此闸门，后启动的 worker 会把先启动 worker 刚接手的需求误判为僵尸。
# 窗口取 120s：正常的多 worker 启动间隔远小于它，而真正的僵尸至少已停滞数分钟。
_SWEEP_WINDOW_S = 120

_INTERRUPTED_MESSAGE = '服务重启导致执行中断，可从断点继续'


def _marker_path():
    return BACKEND_DIR / settings.LOG_DIR / 'stale_sweep.marker'


def _already_swept() -> bool:
    """本轮启动是否已由其他 worker 扫过。"""
    try:
        marker = _marker_path()
        return marker.exists() and (time.time() - marker.stat().st_mtime) < _SWEEP_WINDOW_S
    except OSError:
        return False


def _touch_marker() -> None:
    try:
        marker = _marker_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError as e:
        # 标记写不进去只影响多 worker 下的重复扫描，不影响本次结果
        logger.debug("写入清理标记失败（不影响结果）：%s", e)


def _another_instance_alive(timeout: float = 0.5) -> bool:
    """同一端口上是否已有实例在服务。

    启动期清理的前提是"本进程即将成为唯一在跑的实例"。开发机上常同时开着
    另一个实例（或测试直接 import factory），此时它的需求正在执行，若照样
    扫描就会把它正在跑的需求误判为僵尸 —— 用端口探活挡掉这种情况。
    """
    import urllib.request

    url = f"http://127.0.0.1:{settings.APP_PORT}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def sweep_stale_processing(boot_ts: float | None = None) -> int:
    """把僵死在 processing 的需求改判为 interrupted，返回改判条数。

    只处理 ``update_time`` 早于本进程启动时刻的行：比它新的行意味着是本次
    启动之后才被接手的（多 worker 场景），不能当僵尸。

    任何异常都不阻断启动 —— 启动期清理失败的最坏结果只是回到修复前的行为。
    """
    if not getattr(settings, 'MARK_STALE_ON_BOOT', True):
        return 0

    if _already_swept():
        logger.debug("启动期僵尸清理：本窗口内已执行过，跳过")
        return 0

    if _another_instance_alive():
        logger.info("启动期僵尸清理：同端口已有实例在服务，跳过（避免误判它正在跑的需求）")
        return 0

    boot_ts = time.time() if boot_ts is None else boot_ts

    count = 0
    try:
        from models import Requirement
        from utils.db import get_db

        with get_db() as db:
            # 已删除（回收站）的需求不参与清理：它们不会再被打开续跑
            rows = db.query(Requirement).filter(
                Requirement.status == 'processing',
                Requirement.update_time < datetime.utcfromtimestamp(boot_ts),
                Requirement.is_deleted.isnot(True),
            ).all()
            for row in rows:
                row.status = 'interrupted'
                row.error_message = _INTERRUPTED_MESSAGE
                count += 1
            if count:
                db.commit()
    except Exception as e:
        logger.warning("启动期僵尸清理失败（不阻断启动）：%s", e)
        return 0

    _touch_marker()

    if count:
        logger.info(
            "启动期僵尸清理：%d 条 processing 需求改判为 interrupted（可经 /resume 续跑）",
            count,
        )
    return count
