# -*- coding: utf-8 -*-
"""验证阶段的可观测性：实时卡片 + 落库摘要共用同一份数据。

问题（req 207 验收反馈）
-----------------------
验证阶段此前只有 3 个进度百分比（80/85/90）和一条**不落库**的
`evaluator_result`：用户既看不到"质量工程师正在做什么"（实时），
刷新页面后也查不到"它做过什么"（持久化）。冒烟测试之后还有
类名契约检查、DoD 核对、视觉证据采集、深度评估四步，全程零推送，
req 207 实测约 2 分钟界面停在「正在做通用交互冒烟测试」不动。

设计
----
一个 `VerifyTrace` 同时喂两条通道，形状完全一致（`steps` 列表）：

- **实时**：`verify_start` 建卡 → 每个子步骤 `verify_step` 更新（不落库）
- **持久化**：验证结束时由 `to_card()` 生成一条 `qa_summary` 消息落进
  `dialogue_history`，前端用同一个组件渲染，刷新前后看到的是同一张卡

子步骤的 key / 文案 / 轮内偏移统一来自 `progress_plan.VERIFY_STEPS`，
避免"进度条上的文案"与"卡片里的文案"各写一份后悄悄分叉。
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from harness.observability import progress_plan


def _now() -> str:
    return datetime.now().isoformat()


class VerifyTrace:
    """验证阶段的步骤账本。"""

    def __init__(self, state: Any, sse: Any = None):
        self.state = state if isinstance(state, dict) else {}
        self.sse = sse
        # 卡片标签用**真实**轮次（不封顶）：进度百分比封顶是为防突破 100，
        # 但显示语义里「第 3 轮」与「第 4 轮」是不同事件，归一成同值会让用户
        # 误以为修复卡住。进度计算仍走 progress_plan.round_index（封顶）。
        self.round = progress_plan.actual_round(self.state)
        self.start_ts: Optional[str] = None
        self.end_ts: Optional[str] = None
        self._begun = False
        self._steps: Dict[str, Dict[str, Any]] = {
            key: {
                'key': key,
                'label': progress_plan.STEP_LABEL[key],
                'status': 'pending',
                'detail': '',
            }
            for key in progress_plan.STEP_ORDER
        }

    # ---- 读 ----
    def steps(self) -> List[Dict[str, Any]]:
        """按固定顺序返回步骤（顺序 = 实际发生顺序）。"""
        return [dict(self._steps[key]) for key in progress_plan.STEP_ORDER]

    def is_empty(self) -> bool:
        return all(s['status'] == 'pending' for s in self._steps.values())

    # ---- 写 ----
    def _emit(self, event: str, payload: Dict[str, Any]) -> None:
        """推送实时事件；失败静默——可观测性永远不允许阻断主流程。"""
        sender = getattr(self.sse, event, None) if self.sse is not None else None
        if sender is None:
            return
        try:
            sender(self.state.get('requirement_id'), payload)
        except Exception:
            pass

    def mark(self, key: str, status: str = 'done', detail: str = '') -> None:
        """记录并推送单个子步骤状态（未知 key 直接忽略，不炸）。

        `begin()` 之前只记账、不推送：打开页面 / AC 验收 / 冒烟这三步发生在
        建卡之前，它们的结论由 `begin()` 一次性带上（否则前端会先收到一堆
        指向"还不存在的卡片"的 verify_step 事件）。
        """
        step = self._steps.get(key)
        if step is None:
            return
        step['status'] = status
        if detail:
            step['detail'] = detail
        if self._begun:
            self._emit('verify_step', dict(step))

    def begin(self) -> None:
        """建卡：把已发生的步骤（打开页面 / AC 验收 / 冒烟）一并带上。

        必须在 AC 验收**之后**调用，这样卡片在对话流里的位置与落库的
        `qa_summary` 一致（前面是逐条 AC 卡，然后是这张总结卡），
        刷新前后顺序不会变。
        """
        if self.start_ts is None:
            self.start_ts = _now()
        self._begun = True
        self._emit('verify_start', {
            'round': self.round,
            'steps': self.steps(),
            'start_ts': self.start_ts,
        })

    def done(self, key: str, detail: str = '') -> None:
        self.mark(key, 'done', detail)

    def failed(self, key: str, detail: str = '') -> None:
        self.mark(key, 'failed', detail)

    def to_card(self, verdict: str = '', score: Any = None,
                findings: Optional[List[Dict[str, Any]]] = None,
                ac_results: Optional[List[Dict[str, Any]]] = None,
                smoke_result: Optional[Dict[str, Any]] = None,
                browser_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """生成落库卡片（`qa_summary` 消息的 `qa_summary` 字段）。"""
        self.end_ts = _now()
        # 卡片头部要显示时间区间。begin() 缺失时（异常路径）不能把 None 漏到界面上，
        # 用收尾时间兜底，宁可区间退化成 0 秒也不要显示空白。
        if self.start_ts is None:
            self.start_ts = self.end_ts
        findings = findings or []
        ac_results = ac_results or []
        smoke_result = smoke_result or {}
        browser_result = browser_result or {}

        severity: Dict[str, int] = {}
        for f in findings:
            key = str(f.get('severity') or 'major')
            severity[key] = severity.get(key, 0) + 1

        ac_passed = sum(
            1 for r in ac_results
            if r.get('passed') and not r.get('harness_errors')
        )
        browser_errors = [
            e for e in (browser_result.get('errors') or []) if e.get('message')
        ]

        return {
            'verdict': verdict,
            'score': score,
            'round': self.round,
            'steps': self.steps(),
            'ac': {'passed': ac_passed, 'total': len(ac_results)},
            'smoke': {
                'available': bool(smoke_result.get('available')),
                'checks': smoke_result.get('checks') or {},
                'defect_count': len(smoke_result.get('defects') or []),
            },
            'browser_errors': len(browser_errors),
            'severity': severity,
            'defect_count': len(findings),
            'start_ts': self.start_ts,
            'end_ts': self.end_ts,
        }


def build_summary_message(card: Dict[str, Any], name: str) -> Dict[str, Any]:
    """把卡片包成一条**可见**的对话消息。

    刻意不带 `hidden`：这条消息就是给用户看的"质量工程师做了什么"，
    也是刷新后唯一能查到验证结论的地方（evaluator_result 事件不落库）。
    `content` 写成一行纯文本，供不支持卡片渲染的场景（导出、日志）兜底。
    """
    verdict = card.get('verdict') or 'UNKNOWN'
    ac = card.get('ac') or {}
    summary = (
        f"验证完成：{verdict}"
        f"（验收 {ac.get('passed', 0)}/{ac.get('total', 0)} 项通过，"
        f"{card.get('defect_count', 0)} 条待修）"
    )
    return {
        'role': 'qa_summary',
        'name': name,
        'content': summary,
        'qa_summary': card,
        'timestamp': card.get('end_ts') or _now(),
        'preserve': True,
    }


def publish_summary(sse: Any, requirement_id: Any, message: Dict[str, Any]) -> None:
    """把落库的那条摘要**同时**推一份实时事件。

    为什么不能只靠 dialogue 事件：`routes/requirements.py` 的 dialogue 通道
    只透传 role/name/content/timestamp 等固定字段（见 useSSE.ts 的组装），
    结构化卡片字段会被丢掉 —— 那样"落库的卡"要等刷新才出现，与实时卡之间
    会有一个空窗。这里发与 `qa_result` 同构的专用事件，前端直接走
    `addDialogueMessage`，与刷新后从 DB 恢复的同一条消息共用幂等键
    （role+name+content+timestamp），不会重复出现。
    """
    if sse is None or not message:
        return
    sender = getattr(sse, 'qa_summary', None)
    if sender is None:
        return
    try:
        sender(requirement_id, message)
    except Exception:
        pass
