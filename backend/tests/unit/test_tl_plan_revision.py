# -*- coding: utf-8 -*-
"""TeamLeader 的三个行为守卫。

守三件事（都是"看起来能用、实际空转"的高危点）：

1. **需求确认门禁真的上锁** —— 触发条件从"输入过短（<8 字符）"改为"未经用户确认"，
   否则「帮我做一个个人记账本应用」这种 12 字需求会直接放行，视觉风格与数据落地
   方式全靠 TL 猜，猜错不是重出一版计划，而是整条编码链路白跑。

2. **澄清不会重复问第二遍** —— 澄清表单回填带 `[用户补充说明]`、plan 反馈重跑带
   `[用户反馈]`；两个标记都要认，缺一个用户就会被同一个问题连问两遍。

3. **反馈走增量修订而非从零重分析** —— 用户在确认卡片上提了修改意见时，服务层会
   清掉 checkpoint 重跑 TL（plan 必须变，省不掉）。若不给它上一版 plan，TL 会把
   用户已经认可的技术栈/文件结构也一并改掉，用户看到的是"我只说加个按钮，计划全变了"。
"""

import json
from unittest.mock import patch

import pytest

from harness.instructions.nodes import (
    _plan_timeout,
    _recover_previous_plan,
    _render_previous_plan_section,
    team_leader_node,
)

# ---------------------------------------------------------------- fixtures

# 一份能通过 DoD 校验的 plan：features 全被 AC 覆盖、每条 AC 都有 anchor
# 与可断言的观察点、无重复（同区域 + 同起始动作）。
VALID_PLAN = {
    "requirement_restated": "一个能记每天花费、按月看汇总的记账本",
    "features": ["能记一笔支出", "能按月看汇总"],
    "assumptions": ["数据存在这台设备的浏览器里（你没提云同步，默认不做）"],
    "complexity": "standard",
    "tech_stack": {"framework": "vanilla", "storage": "localStorage", "css": "native"},
    "file_structure": ["index.html", "css/style.css", "js/app.js"],
    "implementation_order": ["css/style.css", "js/app.js", "index.html"],
    "tasks": [
        {"file": "js/app.js", "purpose": "实现记账与汇总的全部交互逻辑",
         "description": "读写 localStorage 并渲染列表", "dependencies": [],
         "exports": {"Ledger": ["addEntry", "monthlyTotal"]}},
        {"file": "css/style.css", "purpose": "全局样式与布局定义，包含卡片与按钮",
         "description": "写样式", "dependencies": []},
        {"file": "index.html", "purpose": "页面结构骨架，负责挂载容器与引用资源",
         "description": "写结构", "dependencies": []},
    ],
    "acceptance_criteria": [
        {"id": "AC-1", "label": "能记一笔支出", "feature": "能记一笔支出",
         "anchor": "页面顶部的新建输入框和右边的保存按钮",
         "how_to_verify": "填入金额后点击保存按钮，列表中出现这条新记录"},
        {"id": "AC-2", "label": "能按月看汇总", "feature": "能按月看汇总",
         "anchor": "顶部的月份切换区域与合计金额文本",
         "how_to_verify": "切换到下一个月，合计金额的数值发生变化"},
    ],
}


class _Resp:
    """假的 LLM 响应对象（只保留调用方真正读的属性）。"""

    def __init__(self, content: str, is_error: bool = False, finish_reason: str = "stop"):
        self.content = content
        self.is_error = is_error
        self.finish_reason = finish_reason


class _ScriptedClient:
    """按 prompt 内容分派响应的假 client。

    澄清调用与 plan 生成调用打在同一个 client 上，用 prompt 里的特征串区分，
    避免依赖调用顺序（顺序会随实现变化而脆弱）。
    """

    def __init__(self, clarify_response: str = "[]", plan_response: str | None = None):
        self.clarify_response = clarify_response
        self.plan_response = plan_response or json.dumps(VALID_PLAN)
        self.calls: list[str] = []

    def chat(self, prompt: str, **kwargs) -> _Resp:
        self.calls.append(prompt)
        # 澄清 prompt 由 intent/clarify_generate.md 渲染，带"澄清"字样
        if "澄清" in prompt and "开发计划" not in prompt:
            return _Resp(self.clarify_response)
        return _Resp(self.plan_response)


def _state(requirement: str, dialogue_history=None) -> dict:
    return {
        "requirement_id": 1,
        "requirement_content": requirement,
        "user_id": 1,
        "plan": None,
        "current_step": "starting",
        "dialogue_history": dialogue_history or [],
        "metadata": {},
        "visual_style": "",
    }


# ------------------------------------------------- 1. 需求确认门禁真的上锁


class TestClarificationGate:
    def test_unconfirmed_requirement_asks_first(self):
        """未经确认的需求：TL 必须先问澄清，而不是直接编 plan。"""
        client = _ScriptedClient(clarify_response=json.dumps([
            {"id": "data_scope", "type": "radio",
             "label": "数据存在哪里？", "options": ["本机浏览器", "云端"]},
        ]))
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("帮我做一个个人记账本应用"))

        assert result["current_step"] == "needs_clarification"
        assert result["plan"] == {}
        assert result["metadata"]["needs_clarification_reason"] == "not_confirmed_by_user"
        form = result["metadata"]["question_form"]
        # LLM 的问题 + 必问的视觉风格 = 2 条
        assert len(form["questions"]) == 2
        # 问题必须落到对话消息上，否则刷新页面就丢了
        assert result["dialogue_history"][0]["question_form"] == form

    def test_style_question_asked_even_if_llm_returns_nothing(self):
        """LLM 判定无需追问，但需求没提风格时，仍必须问视觉风格。

        这条是回归守卫：改造前把"要不要问"完全交给 LLM，实测「做一个个人记账本
        应用」会被直接放行，风格由 TL 替用户猜。
        """
        client = _ScriptedClient(clarify_response="[]")
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("帮我做一个个人记账本应用"))

        assert result["current_step"] == "needs_clarification"
        assert result["metadata"]["needs_clarification_reason"] == "style_not_confirmed"
        qs = result["metadata"]["question_form"]["questions"]
        assert any(q.get("id") == "visual_style" for q in qs)

    def test_style_already_mentioned_not_asked_again(self):
        """用户已经说了风格就别再问，不给用户添没有意义的必答题。"""
        client = _ScriptedClient(clarify_response="[]")
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("做一个待办清单，极简风格，数据存本地"))

        assert result["current_step"] == "team_leader_done"

    def test_clarify_llm_failure_does_not_open_the_gate(self):
        """澄清调用失败 ≠ 需求明确：必须用兜底问题守住门禁，而不是静默放行。"""
        class _BrokenClient:
            def chat(self, *a, **kw):
                return _Resp("", is_error=True)

        with patch("harness.instructions.nodes.get_client", return_value=_BrokenClient()):
            result = team_leader_node(_state("帮我做一个个人记账本应用"))

        assert result["current_step"] == "needs_clarification"
        assert result["metadata"]["needs_clarification_reason"] == "clarify_unavailable"
        assert result["metadata"]["question_form"]["questions"]

    def test_code_fenced_array_is_parsed(self):
        """模型习惯把 JSON 包在 ```json 围栏里（实测最常见），必须接住。

        改造前用的是 `json.loads` + `re.search(r'\\[.*\\]')`，围栏本身能靠正则兜住，
        但一旦响应被 max_tokens 截断就整体丢弃 —— 退化成罐头提问。
        """
        payload = [
            {"id": "category", "type": "radio", "label": "需要账本分类维度吗？",
             "options": ["不需要（默认）", "需要，按餐饮/交通等分类"]},
        ]
        client = _ScriptedClient(
            clarify_response="```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"
        )
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("帮我做一个个人记账本应用"))

        labels = [q.get("label") for q in result["metadata"]["question_form"]["questions"]]
        assert "需要账本分类维度吗？" in labels

    def test_clarify_unparseable_reply_treated_as_failure(self):
        """LLM 返回散文而非 JSON 时，同样按失败处理（此前完全不可观测）。"""
        client = _ScriptedClient(clarify_response="我觉得这个需求挺清楚的，不需要问什么。")
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("帮我做一个个人记账本应用"))

        assert result["current_step"] == "needs_clarification"
        assert result["metadata"]["needs_clarification_reason"] == "clarify_unavailable"

    def test_bare_object_reply_is_kept_not_discarded(self):
        """模型只问一个问题时经常返回裸对象而不是数组，必须包一层留下。

        req 206 实测：模型问出了「是否需要账本分类维度」这种高质量问题，
        纯因为格式是对象而非数组就被丢掉、退化成罐头提问，是净损失。
        """
        client = _ScriptedClient(clarify_response=json.dumps({
            "id": "category", "type": "radio", "label": "需要账本分类维度吗？",
            "options": ["不需要", "需要"],
        }))
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("帮我做一个个人记账本应用"))

        qs = result["metadata"]["question_form"]["questions"]
        labels = [q.get("label") for q in qs]
        assert "需要账本分类维度吗？" in labels
        # 需求没提风格 → 必问的风格题也要在
        assert any(q.get("id") == "visual_style" for q in qs)

    def test_malformed_question_entries_dropped(self):
        """残缺条目会让前端渲染出空白选项，比不问更糟：必须丢掉。"""
        client = _ScriptedClient(clarify_response=json.dumps([
            {"id": "ok", "type": "radio", "label": "正常问题", "options": ["A"]},
            {"id": "broken"},          # 没有 label
            "完全不是对象",
        ]))
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("帮我做一个个人记账本应用"))

        qs = result["metadata"]["question_form"]["questions"]
        assert [q.get("label") for q in qs if isinstance(q, dict)].count("正常问题") == 1
        assert all(isinstance(q, dict) and q.get("label") for q in qs)

    def test_llm_says_no_question_needed_then_proceed(self):
        """需求已写明风格 + LLM 返回空数组 → 放行，不无谓卡一道。"""
        client = _ScriptedClient(clarify_response="[]")
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("做一个待办清单，极简风格，数据存本地"))

        assert result["current_step"] == "team_leader_done"
        assert result["plan"]["features"] == VALID_PLAN["features"]

    def test_short_input_blocked_even_if_llm_says_nothing(self):
        """极短输入 + LLM 没问出问题 → 用兜底问题拦住，不能交给 TL 去编。"""
        client = _ScriptedClient(clarify_response="[]")
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("帮我"))

        assert result["current_step"] == "needs_clarification"
        assert result["metadata"]["needs_clarification_reason"] == "input_too_short"

    @pytest.mark.parametrize("marker", ["[用户补充说明]", "[用户反馈]"])
    def test_markers_skip_clarification(self, marker):
        """澄清回填与 plan 反馈两个标记都要认，否则用户会被同一个问题问两遍。"""
        client = _ScriptedClient(clarify_response=json.dumps([
            {"question": "你希望是什么视觉风格？", "options": ["极简", "拟物"]},
        ]))
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(
                _state(f"做一个待办清单\n\n{marker}\n极简风格")
            )

        assert result["current_step"] == "team_leader_done"
        # 澄清那次调用不应发生：只应有 plan 生成那一次
        assert len(client.calls) == 1


# ------------------------------------------------- 2. 上一版 plan 的回捞


class TestRecoverPreviousPlan:
    def test_picks_latest_plan(self):
        older = {"features": ["旧功能"]}
        newer = {"features": ["新功能"]}
        dh = [
            {"role": "agent", "plan": older},
            {"role": "user", "content": "改一下"},
            {"role": "agent", "plan": newer},
        ]
        assert _recover_previous_plan(dh)["features"] == ["新功能"]

    def test_ignores_confirmed_card(self):
        """确认卡片用的是 plan_confirmed 字段，不是 plan，不能被误当成上一版计划。"""
        assert _recover_previous_plan([{"plan_confirmed": {"features": ["x"]}}]) is None

    def test_ignores_empty_plan(self):
        """TL 失败时会写一条 plan 为空的错误消息，回捞时必须跳过。"""
        dh = [{"role": "agent", "plan": {}}, {"role": "agent", "plan": {"features": []}}]
        assert _recover_previous_plan(dh) is None

    def test_tolerates_dirty_history(self):
        """历史里混进 None / 字符串 / 缺字段时不能抛异常。"""
        dh = [None, "垃圾数据", 42, {"role": "user"}, {"role": "agent", "plan": "not-a-dict"}]
        assert _recover_previous_plan(dh) is None

    def test_empty_history(self):
        assert _recover_previous_plan([]) is None
        assert _recover_previous_plan(None) is None


# ------------------------------------------------- 3. 增量修订指令段


class TestRenderPreviousPlanSection:
    def test_carries_all_must_keep_fields(self):
        section = _render_previous_plan_section(VALID_PLAN)

        assert "必须与上一版保持一致" in section
        assert "vanilla" in section          # 技术栈
        assert "能按月看汇总" in section       # 功能清单
        assert "js/app.js" in section         # 文件结构
        assert "AC-2" in section              # 验收条件
        assert "完整" in section               # 要求整份 JSON 而非补丁

    def test_carries_ac_anchor_and_feature(self):
        """AC 的 anchor / feature 必须一并渲染，不能只给 label + 验证步骤。

        实测（需求 206）踩到过：增量修订段渲染 AC 时丢了这两个字段。
        后果有二：
          1) `feature` 见不到原值 → 模型重新措辞 → `_validate_feature_coverage`
             （要求与 features 逐字一致）判失败 → 白跑一轮重试；
          2) `anchor` 见不到原值 → 模型自己重造参照物，而 anchor 恰恰是验收脚本
             唯一的「独立于实现」的外部参照，在最该稳住它的场景里反而被换掉。
        同时指令里写着「验收条件必须与上一版保持一致」，只给一半信息是矛盾的。
        """
        section = _render_previous_plan_section(VALID_PLAN)

        assert "顶部的月份切换区域与合计金额文本" in section      # AC-2 的 anchor
        assert "页面顶部的新建输入框和右边的保存按钮" in section    # AC-1 的 anchor
        assert "覆盖功能" in section
        assert "能按月看汇总" in section
        assert "页面位置" in section

    def test_anchor_absent_does_not_render_placeholder(self):
        """老库的 AC 可能没有 anchor（本字段是后加的），不能渲染出空占位行。"""
        plan = {
            "features": ["只有功能"],
            "acceptance_criteria": [
                {"id": "AC-1", "label": "能操作", "how_to_verify": "点击按钮后出现结果"},
            ],
        }
        section = _render_previous_plan_section(plan)

        assert "页面位置" not in section
        assert "覆盖功能" not in section
        assert "点击按钮后出现结果" in section

    def test_survives_minimal_plan(self):
        """老库里的 plan 可能只有 features，渲染不能 KeyError。"""
        section = _render_previous_plan_section({"features": ["只有功能"]})
        assert "只有功能" in section


class TestPlanTimeoutBudget:
    """规划调用的超时预算不能拍脑袋定小。

    实测（agnes-3.0-flash，max_tokens=6000，thinking=enabled）：一次 TL 规划调用
    需要 ~116s（第一次 60s 超时 + 重试才成功）。此前硬编码 timeout=60，
    于是每次规划都要先白等 60s 再重试，运气差就连撞 3 次 60s 并把 TL 判失败。
    这条守卫防的是"以后有人把它改回偏紧的值"。
    """

    def test_plan_timeout_mirrors_config(self):
        from config import settings
        assert _plan_timeout() == settings.LLM_PLAN_TIMEOUT

    def test_plan_timeout_has_room_for_measured_latency(self):
        # 门槛取实测值 116s 的保守下界：低于 100s 就不是"够用"而是"赌运气"
        assert _plan_timeout() >= 100, (
            f"规划超时 {_plan_timeout()}s 低于实测所需（~116s），会退化成连续超时重试"
        )


class TestDodIssuesRideOnThePlan:
    """DoD 弱项必须挂在 plan 上，而不是只写进 metadata。

    metadata 在 LangGraph 里是「替换」语义、跨 checkpoint 不保靠；plan 才是唯一
    确定会被持久化、跨断点传递并进到 verify 的载体。挂在 metadata 上的后果是
    "发现了弱 AC，但没有任何下游知道"——带病放行和没校验一样。
    """

    def _weak_plan(self) -> dict:
        plan = json.loads(json.dumps(VALID_PLAN))
        # 断言不可观察 → 必然过不了 DoD
        plan["acceptance_criteria"][0]["how_to_verify"] = "点击保存按钮并把鼠标移开"
        return plan

    def test_issues_attached_to_plan(self):
        client = _ScriptedClient(plan_response=json.dumps(self._weak_plan()))
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("做一个记账本，极简风格，数据存本地"))

        assert result["plan"].get("_plan_dod_issues"), "弱 AC 没有被记录到 plan 上"
        assert any("观察点" in i for i in result["plan"]["_plan_dod_issues"])

    def test_clean_plan_has_no_issues_marker(self):
        client = _ScriptedClient()
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("做一个记账本，极简风格，数据存本地"))

        # 干净计划不该留下空标记：verify 那边用 truthiness 判断，留个空串会误判
        assert not result["plan"].get("_plan_dod_issues")


class TestFeedbackUsesIncrementalRevision:
    def test_previous_plan_injected_into_prompt(self):
        """带 [用户反馈] 时，上一版 plan 必须真的进到 TL 的 prompt 里。"""
        client = _ScriptedClient()
        dh = [
            {"role": "agent", "name": "Leon（负责人）", "content": "计划",
             "plan": {**VALID_PLAN, "features": ["上一版才有的功能"]}},
        ]
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state(
                "做一个记账本\n\n[用户反馈]\n再加一个预算提醒", dialogue_history=dh
            ))

        assert result["current_step"] == "team_leader_done"
        # 澄清被跳过 → 只剩 plan 生成这一次调用
        assert len(client.calls) == 1
        sent = client.calls[0]
        assert "上一版计划" in sent
        assert "上一版才有的功能" in sent

    def test_no_feedback_no_injection(self):
        """没有反馈时不能平白多塞一段，否则会稀释对真需求的注意力。"""
        client = _ScriptedClient()
        dh = [{"role": "agent", "plan": {**VALID_PLAN, "features": ["上一版才有的功能"]}}]
        with patch("harness.instructions.nodes.get_client", return_value=client):
            team_leader_node(_state(
                "做一个记账本\n\n[用户补充说明]\n极简风格", dialogue_history=dh
            ))

        assert "上一版计划" not in client.calls[0]

    def test_feedback_without_history_still_works(self):
        """回捞不到上一版 plan（如老数据）时降级为全新需求，不能崩。"""
        client = _ScriptedClient()
        with patch("harness.instructions.nodes.get_client", return_value=client):
            result = team_leader_node(_state("做一个记账本\n\n[用户反馈]\n再加一个提醒"))

        assert result["current_step"] == "team_leader_done"
        assert "上一版计划" not in client.calls[0]
