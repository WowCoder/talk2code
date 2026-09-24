# -*- coding: utf-8 -*-
"""AC 验收结果的「政策判定」（纯函数，可单测，与 verify 节点实现解耦）。

集中两个判定，便于 P2.5 / P4 共享同一份权威逻辑，也便于契约测试钉死：

1. ``_ac_state(result)``：把 ``run_ac_checks`` 单条结果归一到四态信号
   （passed / compromised / unverified / not_applicable，外加可信 fail / pending）。
   四态信号是 P4 前端可视化的数据来源，也是评估器降权（compromised）的依据。

2. ``_should_invalidate_ac_cache(results)``：P2.5 多数 AC「验不了 / 驱动不动」
   ⇒ 作废 ac_scripts.json 缓存，下一轮按当前代码重新翻译。

两个函数都不碰 app / DB / 浏览器，纯内存计算，单测可在沙箱里秒级跑完。
"""
from typing import Dict, List, Optional


def _ac_state(result: Dict) -> str:
    """把单条 AC 验收结果归一到渲染态（四态 + 两辅助态）。

    态优先级（互斥，自上而下命中即返回）：

    - ``passed``        : 真正通过——无产品失败、无脚本驱动失败、断言前提成立。
    - ``compromised``   : 脚本没跑成 **且** 断言失败 ⇒ 这条失败是幽灵
                          （点击步骤超时后，后续断言是在「没被点过的页面」上跑的）。
                          必须标记，否则会喂给 repair 去修不存在的问题（req 147 复盘，
                          实测 139 条 AC 中 45.3% 属此类）。
    - ``fail``          : 可信的产品断言失败（failures 非空，且非 compromised）。
    - ``unverified``    : 断言前提不成立（如页面无 canvas 却断言 canvas 变化）。
                          既非通过也非失败，交给评估器裁量，「未验证 ≠ 通过」。
    - ``not_applicable``: 断言对当前实现根本不适用（未触发任何验证）。
    - ``pending``       : 尚未得出结果（默认值）。
    """
    if result.get("passed"):
        return "passed"
    if result.get("compromised"):
        return "compromised"
    if result.get("failures"):
        return "fail"
    if result.get("unverified"):
        return "unverified"
    if result.get("not_applicable"):
        return "not_applicable"
    return "pending"


def _should_invalidate_ac_cache(ac_check_results: Optional[List[Dict]]) -> bool:
    """P2.5：多数 AC 验不了（unverified + harness_errors 占多数）⇒ 作废缓存重译。

    判据：不可信条数 ``>= max(2, 总数 // 2 + 1)``，即超过半数（且至少 2 条）未计入可信验收。

    ``unverified`` = 断言前提不成立；``harness_errors`` = 脚本驱动失败。二者都不是真实产品缺陷，
    说明翻译出来的 Playwright 脚本对当前实现不适用（选择器猜错、断言类型选错）。
    首轮锁定本来是为了防漂移，但锁死一个错脚本等于永久假绿 / 假红——
    这里作废缓存，下一轮按当前代码重新翻译（req 147 的 canvas 断言锁死即此来源）。
    """
    if not ac_check_results:
        return False
    _unverified = sum(1 for r in ac_check_results if r.get("unverified"))
    _harness_fail = sum(1 for r in ac_check_results if r.get("harness_errors"))
    return (_unverified + _harness_fail) >= max(2, len(ac_check_results) // 2 + 1)
