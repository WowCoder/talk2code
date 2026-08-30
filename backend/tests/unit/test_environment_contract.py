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
from harness.constraints.plan_validator import validate_plan, build_plan_retry_feedback


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
            "features": ["贪吃蛇"],
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
                {"id": "AC-1", "label": "开始游戏后蛇移动",
                 "how_to_verify": "点击开始按钮，按方向键，画面中蛇的位置发生变化"},
                {"id": "AC-2", "label": "得分记录",
                 "how_to_verify": "输入名字后点击提交，排行榜显示新纪录"},
            ],
        }

    def test_valid_plan_passes(self):
        ok, issues = validate_plan(self._base_plan())
        assert ok, issues

    def test_missing_exports_rejected(self):
        """被依赖的 js 未声明 exports → 打回（需求 124 跨文件 API 断层）"""
        plan = self._base_plan()
        plan["tasks"][0].pop("exports")
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("exports" in i for i in issues)

    def test_exports_bad_structure_rejected(self):
        plan = self._base_plan()
        plan["tasks"][0]["exports"] = ["SnakeGame"]  # 应为 dict
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

    def test_simple_with_many_files_flagged(self):
        plan = self._base_plan()
        plan["complexity"] = "simple"
        ok, issues = validate_plan(plan)
        assert not ok
        assert any("simple" in i for i in issues)

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

