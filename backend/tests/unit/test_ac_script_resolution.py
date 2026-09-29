# -*- coding: utf-8 -*-
"""AC 脚本来源决策：翻译失败不能等于「不验收」。

背景（需求 206 实测）：`_translate_acs_to_scripts` 用 max_tokens=2000 且开了
thinking，reasoning 把预算吃光 → content 为空 → `return []`。而调用方写的是
`if ac_scripts:`，空列表会让**整段 AC 逐条验收被静默跳过**——没有 warning，
没有降级，用户看到的是"验收通过"，实际一条 AC 都没跑。

根因是空列表同时表示多种含义（"无需翻译"/"翻译失败"）。`_resolve_ac_scripts`
把这件事显式化：返回 `(scripts, source)`，source 区分
hit / fresh / stale_fallback / none。

这组用例锁住的是决策表本身，与 LLM 无关。
"""

import pytest

from harness.instructions.nodes import (
    _AC_TRANSLATE_BATCH,
    _ac_translate_timeout,
    _extract_visible_texts,
    _resolve_ac_scripts,
    _script_locating_stats,
    _translate_acs_to_scripts,
)

CACHED = [{"ac_id": "AC-1", "steps": [{"action": "click", "selector": "text=添加"}]}]
FRESH = [{"ac_id": "AC-1", "steps": [{"action": "click", "selector": "#add"}]}]


def _boom():
    raise AssertionError("缓存命中时不应触发翻译")


class TestResolveAcScripts:
    def test_hash_match_hits_cache_without_translating(self):
        scripts, source = _resolve_ac_scripts(CACHED, "h1", "h1", _boom)

        assert source == "hit"
        assert scripts == CACHED

    def test_hash_mismatch_and_translate_ok(self):
        scripts, source = _resolve_ac_scripts(CACHED, "old", "new", lambda: FRESH)

        assert source == "fresh"
        assert scripts == FRESH

    def test_translate_failure_falls_back_to_stale(self):
        """核心用例：翻译失败时宁可跑语义过期的旧脚本，也不能静默不验收。"""
        scripts, source = _resolve_ac_scripts(CACHED, "old", "new", lambda: [])

        assert source == "stale_fallback"
        assert scripts == CACHED
        assert scripts, "回退路径绝不能返回空列表——空列表会让调用方跳过整段 AC 验收"

    def test_translate_failure_without_cache_reports_none(self):
        scripts, source = _resolve_ac_scripts(None, None, "new", lambda: [])

        assert source == "none"
        assert scripts == []

    def test_no_cache_and_translate_ok(self):
        scripts, source = _resolve_ac_scripts(None, None, "new", lambda: FRESH)

        assert source == "fresh"
        assert scripts == FRESH

    def test_translate_fn_returning_none_is_treated_as_failure(self):
        scripts, source = _resolve_ac_scripts(CACHED, "old", "new", lambda: None)

        assert source == "stale_fallback"

    def test_empty_cached_list_is_not_usable(self):
        """缓存写入的是空列表（历史脏数据）时，不能当成"有脚本"。"""
        scripts, source = _resolve_ac_scripts([], "old", "new", lambda: [])

        assert source == "none"
        assert scripts == []

    def test_returned_list_is_a_copy(self):
        """回退时不能把缓存对象的引用直接交出去，下游会就地改 steps。"""
        scripts, source = _resolve_ac_scripts(CACHED, "old", "new", lambda: [])
        scripts.append({"ac_id": "AC-9"})

        assert len(CACHED) == 1, "修改返回值污染了缓存列表"

    @pytest.mark.parametrize("source_expected, cached, cached_hash, ac_hash, fresh", [
        ("hit", CACHED, "h", "h", []),
        ("fresh", CACHED, "old", "new", FRESH),
        ("stale_fallback", CACHED, "old", "new", []),
        ("none", None, None, "new", []),
    ])
    def test_decision_table(self, source_expected, cached, cached_hash, ac_hash, fresh):
        _, source = _resolve_ac_scripts(cached, cached_hash, ac_hash, lambda: fresh)
        assert source == source_expected


class _FakeResp:
    def __init__(self, content="", is_error=False):
        self.content = content
        self.is_error = is_error


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    def chat(self, **kwargs):
        return self._resp


ACS = [{"id": "AC-1", "label": "能添加记录", "feature": "能添加记录",
        "anchor": "页面顶部的输入框和右边的添加按钮",
        "how_to_verify": "输入内容后点击添加按钮，列表中出现这条记录"}]


class TestTranslateAcsFallbackPath:
    """兜底提取 JSON 数组那一步曾经引用未导入的 `_re`，抛 NameError。

    症状有迷惑性：外层 `except Exception` 会把它打成
    "[AC Translate] 翻译失败: name '_re' is not defined"，
    盖掉真正的失败原因（超时/截断），让排查方向跑偏。
    """

    def _install(self, monkeypatch, content):
        import llm.client as llm_client
        monkeypatch.setattr(llm_client, "get_client", lambda: _FakeClient(_FakeResp(content)))

    def test_plain_json_content(self, monkeypatch):
        self._install(monkeypatch, '[{"ac_id": "AC-1", "steps": '
                                   '[{"action": "click", "selector": "text=添加"}]}]')
        scripts = _translate_acs_to_scripts(ACS, "<button id=\"add\">添加</button>", "做个记账本")

        assert len(scripts) == 1
        assert scripts[0]["ac_id"] == "AC-1"

    def test_json_wrapped_in_prose_uses_regex_fallback(self, monkeypatch):
        """模型前面写一段说明、后面才是 JSON —— 走 `_re.search` 兜底分支。"""
        self._install(
            monkeypatch,
            '好的，以下是翻译结果：\n```json\n[{"ac_id": "AC-1", "steps": '
            '[{"action": "click", "selector": "text=添加"}]}]\n```\n希望有帮助。',
        )
        scripts = _translate_acs_to_scripts(ACS, "<button id=\"add\">添加</button>", "做个记账本")

        assert len(scripts) == 1, "兜底正则分支没能提取出脚本（很可能是 _re 未导入）"

    def test_error_response_returns_empty(self, monkeypatch):
        self._install(monkeypatch, "")
        scripts = _translate_acs_to_scripts(ACS, "x", "y")
        assert scripts == []

    def test_no_acceptance_criteria_short_circuits(self, monkeypatch):
        self._install(monkeypatch, "不该被调用")
        assert _translate_acs_to_scripts([], "x", "y") == []


def _mk_acs(n):
    return [
        {"id": f"AC-{i}", "label": f"功能{i}", "feature": f"功能{i}",
         "anchor": f"页面第{i}块区域", "how_to_verify": f"点击第{i}个按钮后出现结果"}
        for i in range(1, n + 1)
    ]


class TestTranslateAcsBatching:
    """AC 一多必须分批，且单批失败不能把整批结果清零。

    实测（agnes-3.0-flash，需求 206 的 5 条 AC + 26k 字符代码）：
      5 条 / 2000 token → content 空，0 条脚本
      5 条 / 6000 token → content 空，0 条脚本（加大预算救不回来）
      2 条 / 4000 token → 2/2 成功
      1 条 / 2000 token → 1/1 成功
    """

    def test_split_into_batches_covering_all_acs(self, monkeypatch):
        from harness.instructions import nodes
        seen_batches = []

        def fake_batch(batch, selector_text, render_info, visible_text_text):
            seen_batches.append([ac["id"] for ac in batch])
            return [{"ac_id": ac["id"], "steps": [{"action": "click", "selector": "#x"}]}
                    for ac in batch]

        monkeypatch.setattr(nodes, "_translate_one_ac_batch", fake_batch)
        scripts = nodes._translate_acs_to_scripts(_mk_acs(5), "<div id=x></div>", "需求")

        assert seen_batches == [["AC-1", "AC-2"], ["AC-3", "AC-4"], ["AC-5"]]
        assert [s["ac_id"] for s in scripts] == ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5"]

    def test_one_failed_batch_keeps_other_batches(self, monkeypatch):
        """核心用例：一批失败不能让其它批的结果一起丢。"""
        from harness.instructions import nodes

        def fake_batch(batch, selector_text, render_info, visible_text_text):
            if any(ac["id"] == "AC-3" for ac in batch):
                return []
            return [{"ac_id": ac["id"], "steps": [{"action": "click", "selector": "#x"}]}
                    for ac in batch]

        monkeypatch.setattr(nodes, "_translate_one_ac_batch", fake_batch)
        scripts = nodes._translate_acs_to_scripts(_mk_acs(5), "<div id=x></div>", "需求")

        ids = [s["ac_id"] for s in scripts]
        assert ids == ["AC-1", "AC-2", "AC-5"], f"失败批污染了其它批: {ids}"

    def test_batch_exception_does_not_propagate(self, monkeypatch):
        from harness.instructions import nodes

        def fake_batch(batch, selector_text, render_info, visible_text_text):
            if any(ac["id"] == "AC-1" for ac in batch):
                raise RuntimeError("模拟端点崩了")
            return [{"ac_id": ac["id"], "steps": [{"action": "click", "selector": "#x"}]}
                    for ac in batch]

        monkeypatch.setattr(nodes, "_translate_one_ac_batch", fake_batch)
        scripts = nodes._translate_acs_to_scripts(_mk_acs(4), "<div id=x></div>", "需求")

        assert [s["ac_id"] for s in scripts] == ["AC-3", "AC-4"]

    def test_transient_batch_failure_is_retried_once(self, monkeypatch):
        """端点瞬时拥塞导致某批空产出时，重发一次就能救回来（实测如此）。"""
        from harness.instructions import nodes

        calls = {"AC-1": 0}

        def fake_batch(batch, selector_text, render_info, visible_text_text):
            if any(ac["id"] == "AC-1" for ac in batch):
                calls["AC-1"] += 1
                if calls["AC-1"] == 1:
                    return []          # 首次失败
            return [{"ac_id": ac["id"], "steps": [{"action": "click", "selector": "#x"}]}
                    for ac in batch]

        monkeypatch.setattr(nodes, "_translate_one_ac_batch", fake_batch)
        scripts = nodes._translate_acs_to_scripts(_mk_acs(2), "<div id=x></div>", "需求")

        assert calls["AC-1"] == 2
        assert [s["ac_id"] for s in scripts] == ["AC-1", "AC-2"]

    def test_retry_is_bounded(self, monkeypatch):
        """持续失败时不能无限重试——每批最多 _AC_TRANSLATE_ATTEMPTS 次。"""
        from harness.instructions import nodes

        calls = []

        def fake_batch(batch, selector_text, render_info, visible_text_text):
            calls.append([ac["id"] for ac in batch])
            return []

        monkeypatch.setattr(nodes, "_translate_one_ac_batch", fake_batch)
        scripts = nodes._translate_acs_to_scripts(_mk_acs(4), "<div id=x></div>", "需求")

        assert scripts == []
        assert len(calls) == 2 * 2, f"2 批各重试一次应为 4 次调用，实际 {len(calls)}"

    def test_all_batches_failed_returns_empty(self, monkeypatch):
        from harness.instructions import nodes
        monkeypatch.setattr(nodes, "_translate_one_ac_batch", lambda b, s, r, v: [])

        assert nodes._translate_acs_to_scripts(_mk_acs(3), "<div id=x></div>", "需求") == []

    def test_budget_scales_with_batch_size(self, monkeypatch):
        """预算必须跟批大小走：2 条 AC 给 2000 会被 reasoning 吃光。"""
        import llm.client as llm_client
        from harness.instructions import nodes

        budgets = []

        class _Client:
            def chat(self, **kwargs):
                budgets.append(kwargs.get("max_tokens"))
                assert kwargs.get("timeout") == nodes._ac_translate_timeout()
                return _FakeResp('[{"ac_id": "AC-1", "steps": [{"action": "click"}]}]')

        monkeypatch.setattr(llm_client, "get_client", lambda: _Client())
        nodes._translate_acs_to_scripts(_mk_acs(3), "<div id=x></div>", "需求")

        assert budgets == [4000, 2000], f"批大小 2/1 应对应 4000/2000，实际 {budgets}"

    def test_batch_size_is_two(self):
        assert _AC_TRANSLATE_BATCH == 2

    def test_translate_timeout_is_own_budget_not_aux(self):
        """翻译器输出是多步 JSON，不该用给"小输出"设计的 aux 档（60s）。"""
        from config import settings
        assert _ac_translate_timeout() == settings.LLM_AC_TRANSLATE_TIMEOUT
        assert _ac_translate_timeout() >= 90, (
            "实测单批 35s、单条最慢 78s；低于 90s 会周期性撞穿并触发无谓重试"
        )


class TestVisibleTextExtraction:
    """可见文案候选的确定性提取（P0-1 的落点）。

    anchor 只描述"哪一块"，不含能当文案用的字，所以"优先用可见文案定位"
    这条约束一直是**不可执行**的（req 206：11/11 次 prompt 都带 anchor，
    产出仍是 55/55 个 id/class）。这里把代码里真正看得见的字捞出来，
    约束才第一次变得可执行。提取规则全部是正则，不调 LLM —— 同一份代码
    每次给出同一份清单，否则提示词每轮都在变，"第一版就翻对"无从谈起。
    """

    def test_extracts_button_and_heading_text(self):
        html = '<h1>记账本</h1><button id="addBtn">添加</button><label>金额</label>'
        texts = _extract_visible_texts(html)

        assert "记账本" in texts
        assert "添加" in texts
        assert "金额" in texts

    def test_extracts_visible_attributes(self):
        texts = _extract_visible_texts('<input placeholder="输入金额" aria-label="备注">')

        assert "输入金额" in texts
        assert "备注" in texts

    def test_extracts_js_assigned_text(self):
        js = (
            'el.textContent = "暂无记录";\n'
            'node.innerText = "已保存";\n'
            'li.appendChild(document.createTextNode("支出"));\n'
        )
        texts = _extract_visible_texts(js)

        assert "暂无记录" in texts
        assert "已保存" in texts
        assert "支出" in texts

    def test_skips_template_placeholders(self):
        """`{count}` / `${total}` 渲染前不是文案，混进候选只会误导模型。"""
        assert _extract_visible_texts("<li>{count}</li><strong>${total}</strong>") == []

    def test_skips_identifier_like_strings(self):
        """`placeholder="amount"` 是标识符式取值，不是给用户看的文案。"""
        assert _extract_visible_texts('<input placeholder="amount">') == []

    def test_dedupes_and_keeps_order(self):
        html = "<button>添加</button><button>删除</button><button>添加</button>"

        assert _extract_visible_texts(html) == ["添加", "删除"]

    def test_respects_limit(self):
        html = "".join(f"<button>按钮{i}</button>" for i in range(100))

        assert len(_extract_visible_texts(html, limit=7)) == 7

    def test_empty_code_returns_empty(self):
        assert _extract_visible_texts("") == []


class TestScriptLocatingStats:
    """给「优先用可见文案定位」这条约束补上验证方式。

    req 206 实测 55/55 个选择器全是 id/class，却没有任何机制报告这件事 ——
    只有人事后手工去数才发现。约束缺验证方式 = 约束不存在。
    """

    def test_counts_text_located_selectors(self):
        scripts = [{"steps": [
            {"action": "click", "selector": "text=添加"},
            {"action": "click", "selector": "#addBtn"},
            {"action": "assert_text", "selector": ':has-text("暂无记录")'},
        ]}]

        assert _script_locating_stats(scripts) == (2, 3)

    def test_all_id_class_reports_zero_located(self):
        """这正是需要报警的情形。"""
        scripts = [{"steps": [
            {"action": "click", "selector": "#addBtn"},
            {"action": "type", "selector": ".amount-input"},
        ]}]

        located, total = _script_locating_stats(scripts)
        assert total == 2
        assert located == 0

    def test_tolerates_malformed_scripts(self):
        assert _script_locating_stats([None, "x", {"steps": None}, {}]) == (0, 0)

    def test_empty_list(self):
        assert _script_locating_stats([]) == (0, 0)


class TestVisibleTextIsWiredToTranslator:
    """端到端接线：提取出的文案必须真的进到翻译器 prompt 里。

    "提取了但没传"这类断线不会自己暴露 —— 本模块先前就栽过一次：
    复用 `_check_missing_files` 当提醒源，信号被它的死锁兜底静默吞掉。
    """

    def test_prompt_contains_extracted_texts(self, monkeypatch):
        import llm.client as llm_client
        from harness.instructions import nodes

        captured = {}

        class _Client:
            def chat(self, **kwargs):
                captured["prompt"] = kwargs.get("prompt", "")
                return _FakeResp('[{"ac_id": "AC-1", "steps": [{"action": "click"}]}]')

        monkeypatch.setattr(llm_client, "get_client", lambda: _Client())
        code = '<button id="addBtn">添加</button><h1>记账本</h1>'
        nodes._translate_acs_to_scripts(ACS, code, "做个记账本")

        assert "添加" in captured["prompt"]
        assert "记账本" in captured["prompt"]

    def test_prompt_no_longer_orders_css_selectors_first(self):
        """提示词里不能同时存在"优先文案"和"必须用 CSS 选择器"两条互斥指令。

        req 206 的 0/55 就是这两条打架的结果：模型照后者执行。
        """
        from harness.instructions.prompts import load_prompt

        template = load_prompt("verify/ac_translator.md")
        assert "- selector 必须从" not in template, (
            "又出现了强制单一选择器来源的措辞，会压过上面的定位优先级"
        )
