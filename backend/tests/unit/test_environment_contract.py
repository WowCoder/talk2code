# -*- coding: utf-8 -*-
"""
环境契约（EnvironmentContract）与缺陷类别路由单元测试

覆盖审查报告七连败失败模式的写入时刻判定逻辑：
- ES Module / CDN / 引用闭合检测
- 缺陷架构类 vs 局部类分类
- 根因卡片生成
"""

import pytest

from harness.constraints import environment_contract as env
from harness.constraints.plan_validator import (
    ACTIONABLE_VERBS,
    DEFAULT_STATE_TRIGGERS,
    OBSERVABLE_PATTERNS,
    validate_plan,
    build_plan_retry_feedback,
)


class TestRenderContract:
    def test_render_contains_all_rules(self):
        text = env.render_environment_contract()
        for rule in env.ENVIRONMENT_RULES:
            assert rule["id"] in text
            assert rule["title"] in text
        # 关键禁令必须在文案里出现
        assert "ES Module" in text or "module" in text.lower()
        assert "CDN" in text
        assert "try/catch" in text

    def test_get_rule(self):
        assert env.get_rule("ENV-3")["title"]
        assert env.get_rule("ENV-999") is None


class TestModuleDetection:
    def test_detects_script_module_tag(self):
        html = '<script type="module" src="js/app.js"></script>'
        assert len(env.find_module_violations(html)) == 1

    def test_ignores_classic_script(self):
        html = '<script src="js/app.js"></script>'
        assert env.find_module_violations(html) == []

    def test_detects_es_syntax(self):
        js = "import { x } from './y.js';\nexport function f() {}"
        findings = env.find_es_syntax(js)
        assert len(findings) == 2

    def test_ignores_iife(self):
        js = "(function (global) { global.init = function () {}; })(window);"
        assert env.find_es_syntax(js) == []


class TestCDNDetection:
    def test_detects_tailwind_cdn(self):
        html = '<script src="https://cdn.tailwindcss.com"></script>'
        refs = env.find_cdn_references(html)
        assert refs == ["https://cdn.tailwindcss.com"]

    def test_ignores_local_refs(self):
        html = '<script src="js/app.js"></script><link rel="stylesheet" href="css/style.css">'
        assert env.find_cdn_references(html) == []


class TestReferenceClosure:
    def test_dangling_ref_detected(self):
        dangling = env.check_reference_closure(
            '<script src="js/game.js"></script>',
            existing_files=["index.html", "js/app.js"],
        )
        assert dangling == ["js/game.js"]

    def test_pending_write_satisfies_closure(self):
        dangling = env.check_reference_closure(
            '<script src="js/game.js"></script>',
            existing_files=["index.html"],
            pending_writes=["js/game.js"],
        )
        assert dangling == []

    def test_planned_file_tolerated(self):
        """plan 承诺过的文件不算悬空（完成门禁兜底）"""
        dangling = env.check_reference_closure(
            '<script src="js/game.js"></script>',
            existing_files=["index.html"],
            planned_files=["js/game.js"],
        )
        assert dangling == []

    def test_external_and_data_ignored(self):
        html = (
            '<img src="data:image/png;base64,xxx">'
            '<a href="#top">top</a>'
            '<script src="https://x.com/y.js"></script>'
        )
        assert env.extract_local_refs(html) == []


class TestDefectClassification:
    def test_architectural_types_routed_out(self):
        defects = [
            {"type": "cdn_dependency", "message": "硬依赖 CDN"},
            {"type": "es_module_cors", "message": "CORS 拦截"},
            {"type": "storage_crash", "message": "localStorage 抛错"},
        ]
        arch, local = env.classify_defects(defects)
        assert {d["type"] for d in arch} == {"cdn_dependency", "es_module_cors"}
        assert [d["type"] for d in local] == ["storage_crash"]

    def test_message_level_arch_detection(self):
        """类型缺失时按消息内容兜底识别架构问题"""
        defects = [
            {"type": "pageerror", "message": "Failed to fetch dynamically imported module: CORS"},
            {"type": "request_failed", "message": "net::ERR_FILE_NOT_FOUND at js/ui.js"},
        ]
        arch, local = env.classify_defects(defects)
        assert len(arch) == 2

    def test_root_cause_card_contains_rule_text(self):
        card = env.build_root_cause_card([
            {"type": "es_module_cors", "message": "模块加载被拦截",
             "evidence": "Access to script ... blocked", "suggestion": "改 IIFE"}
        ])
        assert "ENV-3" in card
        assert "IIFE" in card
        assert "重构" in card

    def test_root_cause_card_empty_for_no_defects(self):
        assert env.build_root_cause_card([]) == ""


class TestPlanValidator:
    def _base_plan(self):
        return {
            "requirement_restated": "一个能用方向键操作的贪吃蛇小游戏",
            "features": ["贪吃蛇游戏", "得分记录"],
            "complexity": "standard",
            "file_structure": ["index.html", "js/game.js", "css/style.css"],
            "tasks": [
                {"file": "js/game.js", "purpose": "游戏核心循环与碰撞检测逻辑",
                 "dependencies": [],
                 "exports": {"SnakeGame": ["start", "pause", "reset"]}},
                {"file": "index.html", "purpose": "页面入口与布局结构定义", "dependencies": ["js/game.js"]},
            ],
            "implementation_order": ["js/game.js", "index.html"],
            "acceptance_criteria": [
                {"id": "AC-1", "label": "开始游戏后蛇移动", "feature": "贪吃蛇游戏",
                 "anchor": "开始遮罩层上的开始按钮，以及键盘的方向键输入",
                 "how_to_verify": "点击开始按钮，按方向键，画面中蛇的位置发生变化"},
                {"id": "AC-2", "label": "得分记录", "feature": "得分记录",
                 "anchor": "结算弹层里的名字输入框与提交按钮，以及下方的排行榜列表",
                 "how_to_verify": "输入名字后点击提交，排行榜出现新的记录"},
            ],
        }

    def _plan_with_js_edge(self):
        """带一条 **js→js 调用边** 的计划。

        exports 契约只在 js↔js 之间成立（index.html 的 dependencies 表达的是"引入
        脚本"而不是"调用方法"），所以测这条规则必须真的造一条 js 调 js 的边。
        """
        plan = self._base_plan()
        plan["file_structure"] = ["index.html", "js/utils.js", "js/game.js", "css/style.css"]
        plan["tasks"] = [
            {"file": "js/utils.js", "purpose": "通用工具函数库：选择器与事件绑定快捷方法",
             "dependencies": [], "exports": {"Utils": ["$", "on"]}},
            {"file": "js/game.js", "purpose": "游戏核心循环与碰撞检测逻辑实现",
             "dependencies": ["js/utils.js"],
             "exports": {"SnakeGame": ["start", "pause", "reset"]}},
            {"file": "index.html", "purpose": "页面入口与布局结构定义",
             "dependencies": ["js/game.js"]},
        ]
        plan["implementation_order"] = ["js/utils.js", "js/game.js", "index.html"]
        return plan

    def test_valid_plan_passes(self):
        ok, issues = validate_plan(self._base_plan())
        assert ok, issues

    def test_missing_requirement_restated_rejected(self):
        """需求复述缺失 = 确认卡片没有给人看的内容，必须拦住"""
        plan = self._base_plan()
        del plan["requirement_restated"]
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("requirement_restated" in i for i in issues)

    def test_ac_anchor_written_as_selector_rejected(self):
        """anchor 写成 CSS 选择器就失去了「按页面语义定位」的意义"""
        plan = self._base_plan()
        plan["acceptance_criteria"][0]["anchor"] = "#start-btn 按钮"
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("选择器" in i for i in issues)

    @pytest.mark.parametrize("anchor,is_selector", [
        # 正常的人话描述不能被误伤（误报会让 plan 白挨一次打回重出）
        ("页面顶部的输入框与添加按钮", False),
        ("第 1.5 项旁边的按钮", False),      # 数字里的点不是 class 选择器
        ("列表中的每一项右侧的删除按钮", False),
        ("顶部的月份切换区域与合计金额文本", False),
        # 真正的选择器必须拦下
        ("#add-btn", True),
        (".todo-item 那一行", True),
        ("带 data-role 的容器", True),
        ("document.querySelector 拿到的元素", True),
        ("[type=text] 输入框", True),
    ])
    def test_anchor_selector_detection_precision(self, anchor, is_selector):
        plan = self._base_plan()
        plan["acceptance_criteria"][0]["anchor"] = anchor
        ok, issues = validate_plan(plan)
        flagged = any("选择器" in i for i in issues)
        assert flagged == is_selector, f"anchor={anchor!r} issues={issues}"

    def test_ac_without_observable_change_rejected(self):
        """能操作但没有可断言观察点的 AC 不合格"""
        plan = self._base_plan()
        plan["acceptance_criteria"][0]["how_to_verify"] = "点击开始按钮并把鼠标移开"
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("观察点" in i for i in issues)

    def test_vague_promise_still_rejected_after_widening(self):
        """放宽"观察点"词表后，真正的假验收措辞仍必须被拦下（防止放宽变成放水）"""
        plan = self._base_plan()
        plan["acceptance_criteria"][0]["how_to_verify"] = "点击开始按钮，整个流程顺畅自然"
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("观察点" in i for i in issues)

    @pytest.mark.parametrize("verify", [
        # 2026-10-07 评测 t01 真实原文：观察点是"颜色/渐变"，能用 computed style 断言
        "打开页面，body 背景非单一纯色，视觉可从左上角颜色平滑过渡到右下角颜色",
        # t05 真实原文：观察点是"尺寸的相对比较"，能用 boundingBox 断言
        "鼠标悬停在某张图片上，该图片尺寸明显大于相邻未悬停图片",
        # t04 真实原文：观察点是"元素可见 + 文本内容"，却因词表只收"出现/显示"被误判
        "打开页面，主体区域可见三张并排卡片，卡片标题依次为「基础」「专业」「企业」",
        # 其它常见的视觉属性观察点
        "点击切换按钮后，卡片背景颜色变为深色",
        "滚动到页面底部，导航栏高度变小且透明度降低",
    ])
    def test_visual_property_is_a_valid_observable(self, verify):
        """视觉属性/相对比较同样是可断言的观察点

        词表此前只收「出现/消失/文本/数量」这一类变化，视觉属性（颜色/尺寸/
        透明度…）一个词都不占，导致这类完全合格的 AC 被误判、白赔一轮重规划。
        """
        assert any(p in verify for p in OBSERVABLE_PATTERNS), verify

    @pytest.mark.parametrize("verify", [
        # 2026-10-07 评测（颜色转换器）真实原文：三条 AC 全被判"不含可操作动词"，
        # 因为词表收了「拖动/拖拽」却没收回一个动作的「拖到」。
        "将R滑块从默认最右端拖到最左端，R右侧数值标签由255变为0",
        "将B滑块从默认最右端拖到最左端，预览色块背景色由白色变为红色",
    ])
    def test_action_verb_stem_matches_its_variants(self, verify):
        """动作词表要收**词干**：判定是子串包含，只收完整词形会漏同义说法"""
        assert any(v.lower() in verify.lower() for v in ACTIONABLE_VERBS), verify

    @pytest.mark.parametrize("verify", [
        # 2026-10-07 全量重跑真实原文：8 条打回里这两条是词表缺口
        "将R、G、B滑块分别设为255、0、0，十六进制文本显示#FF0000",
        "让蛇撞墙，出现「游戏结束」文字遮罩",
    ])
    def test_action_verb_covers_control_and_in_game_words(self, verify):
        """控制件取值（设为/调整）与游戏内动作（撞墙）也是用户操作

        词表此前只有「设置/调整」没有「设为」，游戏类只收了「方向键」而没收
        「撞」这类动作 —— 两条 AC 完全可操作（改滑块数值 / 按方向键撞墙后断言
        遮罩出现），却各白赔一轮 TL 重规划。
        """
        assert any(v.lower() in verify.lower() for v in ACTIONABLE_VERBS), verify

    @pytest.mark.parametrize("verify", [
        # t16 真实原文：明确的观察点（出现 + 数量 + 间隙），但"没人点"→ 曾被拦
        "页面加载完成后，柱状图区域出现20根等宽蓝色竖条，相邻竖条间隙一致",
        "打开页面后，初始状态列出全部待办项，已完成项显示删除线",
    ])
    def test_default_state_ac_is_valid(self, verify):
        """「打开页面即成立」的默认态同样是可执行的验收

        校验器原本把「必须由用户操作触发」当成必要条件，但初始状态用 Playwright
        打开页面即可断言，完全可落地。豁免只在**未命中操作动词**时生效，且后面
        还有「可断言观察点」这道独立关卡兜底。
        """
        assert any(t in verify for t in DEFAULT_STATE_TRIGGERS), verify

    def test_default_state_exemption_does_not_leak(self):
        """豁免不能把真正空洞的 AC 放进来

        「排行榜存在新纪录」既没有操作、也没有默认态触发词，必须继续被拦
        —— 放宽判定时最容易出的事就是顺手把判据放没了。
        """
        assert not any(t in "排行榜存在新纪录" for t in DEFAULT_STATE_TRIGGERS)
        assert not any(v in "排行榜存在新纪录" for v in ACTIONABLE_VERBS)

    def test_uncovered_feature_rejected(self):
        """承诺了功能却没有对应验收项，该功能等于从没被验证过"""
        plan = self._base_plan()
        plan["features"].append("暂停/继续")
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("没有任何 AC 覆盖" in i for i in issues)

    def test_duplicate_ac_rejected(self):
        """同区域 + 同起点动作的两条 AC 会让同一缺陷被算两次"""
        plan = self._base_plan()
        plan["acceptance_criteria"].append({
            "id": "AC-3", "label": "开始游戏后画面变化", "feature": "贪吃蛇游戏",
            "anchor": "开始遮罩层上的开始按钮，以及键盘的方向键输入",
            "how_to_verify": "点击开始按钮之后，画面出现可操作的棋盘",
        })
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("语义重复" in i for i in issues)

    def test_missing_exports_rejected(self):
        """被另一个 js 调用的 js 未声明 exports → 打回（需求 124 跨文件 API 断层）"""
        plan = self._plan_with_js_edge()
        plan["tasks"][0].pop("exports")           # js/utils.js，被 js/game.js 调用
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("exports" in i and "utils.js" in i for i in issues)

    def test_exports_bad_structure_rejected(self):
        plan = self._plan_with_js_edge()
        plan["tasks"][0]["exports"] = ["Utils"]   # 应为 dict[str, list[str]]
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("exports" in i for i in issues)

    def test_missing_acceptance_criteria_fails(self):
        plan = self._base_plan()
        plan["acceptance_criteria"] = []
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("acceptance_criteria" in i for i in issues)

    def test_non_actionable_ac_rejected(self):
        plan = self._base_plan()
        plan["acceptance_criteria"][0]["how_to_verify"] = "界面美观大方"
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("不可断言" in i for i in issues)

    def test_ac_without_action_verb_rejected(self):
        plan = self._base_plan()
        plan["acceptance_criteria"][1]["how_to_verify"] = "排行榜存在新纪录"
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("可操作动词" in i for i in issues)

    def test_task_missing_purpose_rejected(self):
        plan = self._base_plan()
        plan["tasks"][0].pop("purpose")
        plan["tasks"][0]["description"] = "短"
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("purpose" in i for i in issues)

    def test_task_not_in_structure_flagged(self):
        plan = self._base_plan()
        plan["file_structure"].append("js/orphan.js")
        ok, issues = validate_plan(plan)
        # file_structure 多出的文件本身不阻断（可选产物），但 tasks 引用未声明文件要报
        plan["tasks"].append({"file": "js/extra.js", "purpose": "额外模块文件的处理逻辑"})
        ok2, issues2 = validate_plan(plan)
        assert not ok2
        assert any("orphan" not in i and "extra" in i for i in issues2) or any("未在 file_structure" in i for i in issues2)

    def test_simple_with_many_files_coerced_not_rejected(self):
        """simple 但文件数超限 = 标错档，就地纠正为 standard，不打回重出。

        complexity 是路由标签（决定迭代预算 / CompletionContract / Phase 2 补全），
        自洽性由文件数唯一确定 —— 可确定性判定的缺陷不值得花一整轮 LLM 让模型改口。
        2026-10-07 评测实测：8 题里 5 题命中「simple 但 3 个文件」。
        """
        plan = self._base_plan()
        plan["complexity"] = "simple"
        ok, issues = validate_plan(plan)
        assert ok, issues
        assert plan["complexity"] == "standard", "纠正必须真的写回 plan，否则下游仍按 simple 走"

    def test_simple_within_file_limit_untouched(self):
        """1~2 个文件的 simple 是正牌 simple，不许被纠正（这是单文件快速通道）"""
        plan = self._base_plan()
        plan["complexity"] = "simple"
        plan["file_structure"] = ["index.html", "css/style.css"]
        plan["implementation_order"] = ["index.html"]
        plan["tasks"] = [
            {"file": "index.html", "purpose": "页面入口与布局结构定义，内含样式引用"},
        ]
        ok, issues = validate_plan(plan)
        assert ok, issues
        assert plan["complexity"] == "simple"

    def test_standard_over_capacity_still_rejected(self):
        """standard 装不下 12 个以上文件是真的做不完，必须打回让模型拆小"""
        plan = self._base_plan()
        plan["file_structure"] = [f"js/m{i}.js" for i in range(13)]
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("超出单次交付能力" in i for i in issues)

    def test_plan_without_tasks_accepted(self):
        """tasks 是可选的：下游有 implementation_order / _derive_implementation_order 兜底。

        曾经的规则是「非 simple 就必须有 tasks」，但 complexity 恰恰会被
        coerce_complexity 改写 —— 3 文件 + 无 tasks 的计划刚被纠正成 standard，
        立刻又因缺 tasks 被打回，纠正是白做的。
        """
        plan = self._base_plan()
        del plan["tasks"]
        ok, issues = validate_plan(plan)
        assert ok, issues

    def test_js_depending_on_css_needs_no_exports(self):
        """js 依赖 css（要按类名操作 DOM）不是跨文件调用，不该被要求声明 exports。

        2026-10-07 评测 t07 实测：js/app.js 写 dependencies:["css/style.css"]、
        exports:{}，而它是个自执行 IIFE，「无需暴露全局对象」的自我判断是对的。
        """
        plan = self._base_plan()
        plan["file_structure"] = ["index.html", "css/style.css", "js/app.js"]
        plan["implementation_order"] = ["css/style.css", "js/app.js", "index.html"]
        plan["tasks"] = [
            {"file": "css/style.css", "purpose": "计数器页面全部样式与按钮交互态",
             "dependencies": [], "exports": {}},
            {"file": "js/app.js", "purpose": "计数器全部交互逻辑，自执行无需对外暴露",
             "dependencies": ["css/style.css"], "exports": {}},
            {"file": "index.html", "purpose": "唯一入口页面，引入样式与脚本",
             "dependencies": ["css/style.css", "js/app.js"], "exports": {}},
        ]
        ok, issues = validate_plan(plan)
        assert ok, issues

    def test_js_depending_on_js_still_needs_exports(self):
        """js→js 的调用边仍然要求声明 exports（需求 124 事故的护栏不能松掉）"""
        plan = self._plan_with_js_edge()
        plan["tasks"][1]["exports"] = {}      # js/game.js 调 js/utils.js，但自己不声明
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("exports" in i and "game.js" in i for i in issues)

    def test_retry_feedback_lists_issues(self):
        text = build_plan_retry_feedback(["问题A", "问题B"])
        assert "问题A" in text and "问题B" in text


class TestCrossFileContract:
    """跨文件 API 契约检查（F3，需求 124/122 事故回归）"""

    FILES = {
        "js/utils.js": """(function () {
    var Utils = {
      $: function (s) { return document.querySelector(s); },
      on: function (t, e, h) { t.addEventListener(e, h); }
    };
    global.Utils = Utils;
  })(window);""",
        "js/app.js": """(function () {
    window.Utils.toast('hi');
    var x = Math.round(1.5);
    window.App = {
      start: function () {},
      stop: function () {}
    };
    window.App.start();
    Snake.run();
  })();""",
        "js/game.js": """function SnakeGame(c){ this.c=c; }
SnakeGame.prototype.start=function(){};
new SnakeGame().start();""",
        "css/style.css": ".overlay.hidden { display:none; }",
    }

    def _run(self, files=None):
        from harness.constraints.environment_contract import check_cross_file_contract
        return check_cross_file_contract(files or self.FILES)

    def test_missing_api_detected(self):
        defects, _ = self._run()
        types = {(d["type"], d.get("evidence")) for d in defects}
        assert ("missing_api", "Utils.toast(") in types

    def test_defined_method_not_flagged(self):
        defects, _ = self._run()
        assert all(d.get("evidence") != "App.start(" for d in defects)

    def test_undefined_global_detected(self):
        defects, _ = self._run()
        assert any(d["type"] == "missing_global" and "Snake" in d["message"] for d in defects)

    def test_browser_globals_whitelisted(self):
        defects, _ = self._run()
        assert not any("Math" in d["message"] for d in defects)

    def test_class_global_not_flagged(self):
        """构造函数/class 全局方法不可枚举 → 不做方法级误报"""
        defects, _ = self._run()
        assert not any("SnakeGame" in d["message"] for d in defects)

    def test_clean_project_passes(self):
        files = {
            "js/a.js": "(function(){ global.A = { go: function(){} }; })(window);",
            "js/b.js": "A.go();",
        }
        defects, warnings = self._run(files)
        assert defects == []

    def test_css_class_warning(self):
        files = {
            "js/x.js": "el.classList.add('fancy-open');",
            "css/style.css": ".overlay.hidden { display:none; }",
        }
        _, warnings = self._run(files)
        assert any(w["type"] == "css_class_missing" and ".fancy-open" in w["message"] for w in warnings)

    def test_defects_are_architectural(self):
        """missing_api/missing_global 必须路由为架构类缺陷（回 coder 重构）"""
        from harness.constraints.environment_contract import classify_defects
        defects, _ = self._run()
        arch, local = classify_defects(defects)
        assert len(arch) == len(defects)
        assert local == []


    def test_attr_method_assignment_export_recognized(self):
        """「先建空对象再逐个赋值」的封装写法必须被识别（需求 125 误报回归）"""
        files = {
            "js/utils.js": """;(function (global) {
            var Utils = {};
            Utils.$$ = function (s) { return document.querySelectorAll(s); };
            Utils.on = function (el, e, h) { el.addEventListener(e, h); };
            global.Utils = Utils;
        })(window);""",
            "js/app.js": "window.Utils.on(btn, 'click', fn);",
        }
        defects, _ = self._run(files)
        assert not any("Utils.on" in (d.get("evidence") or "") for d in defects)
        assert not any("Utils.$$" in (d.get("evidence") or "") for d in defects)

    def test_attr_method_unimplemented_still_detected(self):
        files = {
            "js/utils.js": ";var Utils={};Utils.$$=function(s){return [];};(function(g){g.Utils=Utils})(window);",
            "js/app.js": "window.Utils.toast('hi');",
        }
        defects, _ = self._run(files)
        assert any(d["type"] == "missing_api" and "Utils.toast" in (d.get("evidence") or "") for d in defects)


    def test_shorthand_ref_export_recognized(self):
        """对象字面量 `key: funcName`（ES5 简写引用）不得误报（需求 127 事故）"""
        files = {
            "js/snake.js": """;(function (g) {
            function init(c){ return c; }
            function start(){ return 1; }
            function beginGame(){ return 2; }
            var SnakeGame = {
              init: init,
              start: start,
              beginGame: beginGame,
              getScore: function(){ return 0; }
            };
            window.SnakeGame = SnakeGame;
            })(window);""",
            "js/app.js": "window.SnakeGame.init(c); window.SnakeGame.beginGame();",
        }
        defects, _ = self._run(files)
        assert defects == []

    def test_shorthand_ref_unimplemented_still_detected(self):
        files = {
            "js/snake.js": """;(function (g) {
            function init(c){ return c; }
            var SnakeGame = { init: init, start: start };
            window.SnakeGame = SnakeGame;
            })(window);""",
            "js/app.js": "window.SnakeGame.start();",
        }
        defects, _ = self._run(files)
        assert any(d["type"] == "missing_api" and "SnakeGame.start" in (d.get("evidence") or "") for d in defects)


class TestBuildApiContractsSection:
    """plan.tasks[].exports → coder prompt 契约段落（F2）"""

    def test_renders_exported_methods(self):
        from harness.constraints.plan_validator import build_api_contracts_section
        plan = {"tasks": [
            {"file": "js/utils.js", "dependencies": [],
             "exports": {"Utils": ["$", "on", "off"]}},
            {"file": "js/game.js", "dependencies": ["js/utils.js"],
             "exports": {"SnakeGame": ["start", "pause"]}},
        ]}
        out = build_api_contracts_section(plan)
        assert "跨文件 API 契约" in out
        assert "**Utils**（js/utils.js）: $, on, off" in out
        assert "**SnakeGame**（js/game.js）: start, pause" in out

    def test_skips_css_and_no_exports(self):
        from harness.constraints.plan_validator import build_api_contracts_section
        plan = {"tasks": [
            {"file": "css/style.css"},
            {"file": "index.html"},
        ]}
        assert build_api_contracts_section(plan) == ""

    def test_no_tasks_returns_empty(self):
        from harness.constraints.plan_validator import build_api_contracts_section
        assert build_api_contracts_section(None) == ""
        assert build_api_contracts_section({"tasks": []}) == ""


class TestDomIdContract:
    """需求 132：JS 引用了 HTML 不存在的 id，Utils.on 静默 no-op，交互失效无报错。"""

    def test_catches_id_referenced_but_not_in_html(self):
        # blogs.js 绑 #blogForm，但 HTML 只有 #createForm
        js = "Utils.on(Utils.$('#blogForm'), 'submit', cb);"
        html = '<form id="createForm"></form>'
        d = env.check_dom_id_contract({"js/app.js": js}, html)
        assert len(d) == 1
        assert d[0]["type"] == "dom_id_mismatch"
        assert "blogForm" in d[0]["evidence"]
        assert d[0]["source_file"] == "js/app.js"
        assert d[0]["severity"] == "critical"

    def test_passes_when_id_exists_in_html(self):
        js = "Utils.on(Utils.$('#createForm'), 'submit', cb);"
        html = '<form id="createForm"></form>'
        assert env.check_dom_id_contract({"js/app.js": js}, html) == []

    def test_getelementby_queryselector_dollar_patterns(self):
        js = ("document.getElementById('missingEl').focus();"
              "document.querySelector('#gone').click();"
              "Utils.$('#ok').on('click', f);")
        html = '<div id="ok"></div>'
        evs = [d["evidence"] for d in env.check_dom_id_contract({"js/a.js": js}, html)]
        assert any("missingEl" in e for e in evs)
        assert any("gone" in e for e in evs)
        assert all("ok" not in e for e in evs)  # #ok 存在于 HTML，不报

    def test_dynamic_id_assignment_not_flagged(self):
        # el.id= / setAttribute('id',...) 动态创建的 id 不应误报
        js = ("var el = document.createElement('div');"
              "el.id = 'dynId';"
              "el.setAttribute('id', 'attrId');"
              "Utils.$('#dynId'); Utils.$('#attrId');")
        assert env.check_dom_id_contract({"js/a.js": js}, "") == []

    def test_innerhtml_template_id_not_flagged(self):
        # innerHTML 模板里写的 id="tpl" 后续引用不应误报
        js = ("list.innerHTML = '<li id=\"tpl\">x</li>';"
              "Utils.$('#tpl').focus();")
        html = '<ul id="list"></ul>'
        assert env.check_dom_id_contract({"js/a.js": js}, html) == []

    def test_integrates_into_cross_file_contract(self):
        files = {
            "index.html": '<form id="createForm"></form>',
            "js/app.js": "Utils.$('#blogForm').on('submit', f);",
        }
        defects, _ = env.check_cross_file_contract(files)
        assert any(d["type"] == "dom_id_mismatch" for d in defects)

    def test_routed_as_architectural(self):
        # dom_id_mismatch 应归架构类（回 coder 携根因卡片）
        d = env.check_dom_id_contract({"js/a.js": "Utils.$('#nope');"},
                                      '<div id="ok"></div>')
        arch, local = env.classify_defects(d)
        assert len(arch) == 1 and len(local) == 0

    def test_empty_inputs_safe(self):
        assert env.check_dom_id_contract({}, '<div id="x"></div>') == []
        assert env.check_dom_id_contract({"js/a.js": "$('#x')"}, "") == []



# ---------- 入口 HTML ↔ JS 引用契约（需求 183 首轮 4.2 分根因） ----------

def test_entry_html_without_script_is_defect():
    from harness.constraints.environment_contract import check_entry_script_contract
    d = check_entry_script_contract(
        {"js/game.js": "var a = 1;"},
        {"index.html": "<!DOCTYPE html><html><body><div>x</div></body></html>"},
    )
    assert any(x["type"] == "entry_script_missing" for x in d)


def test_js_file_never_referenced_is_defect():
    from harness.constraints.environment_contract import check_entry_script_contract
    d = check_entry_script_contract(
        {"js/game.js": "var a = 1;", "js/main.js": "var b = 2;"},
        {"index.html": "<html><body><script src=\"js/main.js\"></script></body></html>"},
    )
    assert [x["type"] for x in d] == ["js_not_referenced"]


def test_all_referenced_is_clean():
    from harness.constraints.environment_contract import check_entry_script_contract
    d = check_entry_script_contract(
        {"js/game.js": "var a = 1;"},
        {"index.html": "<html><body><script src=\"js/game.js\"></script></body></html>"},
    )
    assert d == []


def test_dynamic_script_injection_not_flagged():
    """JS 动态注入 script 属于正常实现，不得误报"""
    from harness.constraints.environment_contract import check_entry_script_contract
    d = check_entry_script_contract(
        {"js/loader.js": "var s = document.createElement('script'); s.src='js/game.js';"},
        {"index.html": "<html><body><script src=\"js/loader.js\"></script></body></html>"},
    )
    assert d == []


def test_iife_local_alias_not_reported():
    """`var U = global.Utils;` 是合法简写，U.setBestScore 不得误报 missing_global。

    需求 184 事故：5/5 验收条件全过、console 零报错，就因这条静态误报
    被硬性一致性规则判成 NEEDS_WORK（5.5 分）。
    """
    from harness.constraints.environment_contract import check_cross_file_contract
    utils = (
        "(function (global) {\n"
        "  'use strict';\n"
        "  var Utils = {\n"
        "    setBestScore: function (v) { return v; },\n"
        "    getBestScore: function () { return 0; }\n"
        "  };\n"
        "  global.Utils = Utils;\n"
        "})(window);\n"
    )
    app = (
        "(function (global) {\n"
        "  'use strict';\n"
        "  var U = global.Utils;\n"
        "  function over() { U.setBestScore(1); var t = U.getBestScore(); }\n"
        "  global.__app = { over: over };\n"
        "})(window);\n"
    )
    defects, _ = check_cross_file_contract(
        {"js/utils.js": utils, "js/app.js": app, "index.html": "<html></html>"}
    )
    assert not any(d.get("evidence") == "U.setBestScore(" for d in defects), defects
    assert not any(d["type"] == "missing_global" for d in defects), defects


# ---------- 实例导出 X = new Y()（需求 186 事故） ----------

def test_new_instance_inherits_prototype_methods():
    """`global.Game = new DragonGame()` 是最常见的 ES5 实例导出。

    186 事故：这种写法此前完全不被解析（_ALIAS_RE 要求 `= Y[,;\\n]`，
    而 `new DragonGame()` 后跟 `(`），Game 继承来的方法集全丢；
    调用方顺手挂的回调反而被当成 Game 的全部方法 → 正常调用被判 missing_api。
    """
    from harness.constraints.environment_contract import check_cross_file_contract
    game_js = (
        "function DragonGame() { this.score = 0; }\\n"
        "DragonGame.prototype.getScore = function () { return this.score; };\\n"
        "DragonGame.prototype.getHighScoreValue = function () { return 0; };\\n"
        "global.Game = new DragonGame();\\n"
    )
    app_js = (
        "(function (global) {\\n"
        "  function updateHUD() {\\n"
        "    var s = String(global.Game.getScore());\\n"
        "    var h = String(global.Game.getHighScoreValue());\\n"
        "  }\\n"
        "  global.Game.onScoreChange = function () {};\\n"
        "  global.__app = { updateHUD: updateHUD };\\n"
        "})(window);\\n"
    )
    defects, _ = check_cross_file_contract(
        {"js/game.js": game_js, "js/app.js": app_js, "index.html": "<html></html>"}
    )
    assert not any(d["type"] == "missing_api" for d in defects), defects
    assert not any(d["type"] == "missing_global" for d in defects), defects


def test_new_instance_defined_in_another_file():
    """prototype 与 new 分处两个文件时，跨文件兜底必须生效"""
    from harness.constraints.environment_contract import check_cross_file_contract
    game_js = (
        "function DragonGame() {}\\n"
        "DragonGame.prototype.start = function () {};\\n"
    )
    app_js = "global.Game = new DragonGame();\\nvar x = Game.start();\\n"
    defects, _ = check_cross_file_contract(
        {"js/game.js": game_js, "js/app.js": app_js, "index.html": "<html></html>"}
    )
    assert not any(d.get("evidence") == "Game.start(" for d in defects), defects


def test_new_builtin_not_reported():
    """`var d = new Date()` 之类的内置实例化不得产生任何缺陷"""
    from harness.constraints.environment_contract import check_cross_file_contract
    js = "var d = new Date();\\nvar t = d.getTime();\\n"
    html = '<html><body><script src="js/app.js"></script></body></html>'
    defects, _ = check_cross_file_contract({"js/app.js": js, "index.html": html})
    assert defects == []
