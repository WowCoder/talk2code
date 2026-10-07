# -*- coding: utf-8 -*-
"""需求 220 根因修复的守卫测试。

背景见 tmp/trace220/需求220-根因分析与修复方案-终版.md。这批测试守住的是
「改过一次、不写守卫就会被悄悄改回去」的那几条：

- A1 `chat()` 不再硬编码 usage/finish_reason = None（成本数据 + 截断分诊都靠它）
- A4 reasoning 救援的天花板必须真的能让额度升上去
- B1 分页读取不得卸载创建时正文（回读空转熔断的直接根因）
- B2 write_file 的返回内容必须与 prompt 的承诺一致
- B3 自建文件的分页提示不得再邀请继续读
- C1 AC 翻译的恒定段必须在 system
- C2 空壳 TASK_STATE 不得下发
- C5 根因文件必须裁剪，不能全标
- D2 提示词片段引用机制

判据都取「可机器断言的最小事实」，不依赖真实 LLM 调用。
"""

from unittest.mock import Mock


class _TC:
    """最小化的工具调用桩（只需要 arguments）"""

    def __init__(self, **arguments):
        self.arguments = arguments


# ==================== A1：usage / finish_reason 透传 ====================

class TestChatPropagatesUsage:
    """chat() 必须把 usage / finish_reason 带回上层。

    此前 `chat()` 里写死 `usage=None, finish_reason=None`：
      · planning / verifying / repairing 三阶段的 token 与成本在后台恒为 0；
      · 「被额度截断」与「端点故障」无法区分（见 TestRepairTriage）。
    """

    def test_usage_and_finish_reason_returned(self, monkeypatch):
        import llm.client as _mod

        body = {
            "choices": [{
                "message": {"content": "hi", "reasoning_content": ""},
                "finish_reason": "length",
            }],
            "usage": {"prompt_tokens": 1234, "completion_tokens": 56},
        }
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = body
        resp.raise_for_status.return_value = None
        monkeypatch.setattr(_mod.requests, "post", lambda *a, **kw: resp)

        client = _mod.LLMClient(api_key="k", base_url="http://x", model="m",
                                provider="openai_compatible")
        meta = _mod._LLMMeta()
        list(client._request_openai(
            [{"role": "user", "content": "x"}], stream=False,
            max_tokens=100, timeout=1, endpoint=_mod._Endpoint(
                provider="openai_compatible", base_url="http://x",
                api_key="k", model="m"),
            meta=meta,
        ))

        assert meta.usage == {"prompt_tokens": 1234, "completion_tokens": 56}
        assert meta.finish_reason == "length"

    def test_chat_assembles_from_meta(self, monkeypatch):
        """端到端：chat() 返回的 LLMResponse 必须带上 usage/finish_reason。"""
        import llm.client as _mod

        body = {
            "choices": [{
                "message": {"content": "ok", "reasoning_content": ""},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
        resp = Mock()
        resp.status_code = 200
        resp.json.return_value = body
        resp.raise_for_status.return_value = None
        monkeypatch.setattr(_mod.requests, "post", lambda *a, **kw: resp)

        client = _mod.LLMClient(api_key="k", base_url="http://x", model="m",
                                provider="openai_compatible")
        out = client.chat("hi", system_prompt="s")
        assert out.usage == {"prompt_tokens": 10, "completion_tokens": 2}
        assert out.finish_reason == "stop"

    def test_chat_returns_usage_not_none(self, monkeypatch):
        """chat() 组装 LLMResponse 时必须用 meta 里的值，不能写死 None。"""
        import inspect
        from llm import client as _mod

        src = inspect.getsource(_mod.LLMClient.chat)
        assert "usage=None" not in src, (
            "chat() 又把 usage 写死成 None 了 —— planning/verifying/repairing "
            "的 token 与成本会重新变成 0"
        )
        assert "finish_reason=None" not in src


# ==================== A4：reasoning 救援天花板 ====================

class TestReasoningFallbackCeiling:
    """救援额度必须真能升上去，否则「救援」形同虚设。

    需求 220 实证：修复调用 req=8000 时
    fallback = min(max(8000*2, 8000+4000), 8000, 12000) = 8000 ≤ 8000
    → 走「放弃重试」分支，16000 第二档从未被尝试。
    """

    # 与 _request_openai 里的取值公式保持一致：
    #   min(max(req*2, req+4000), LLM_REASONING_FALLBACK_TOKENS)
    # ⚠️ 这里**没有** LLM_MAX_TOKENS 这一项。曾经有，后果是「req 取全局默认值」
    # 时（coder / 评估 / 修复主路径正是如此）min() 必然回到 req 本身，
    # 救援被静默放弃 —— 2026-10-05 req 222 实测踩到（见下方 test_*_at_deepseek_global）。
    @staticmethod
    def _fallback(req, cap=None):
        from config import settings
        cap = cap if cap is not None else settings.LLM_REASONING_FALLBACK_TOKENS
        return min(max(req * 2, req + 4000), cap)

    def test_ceiling_allows_escalation(self):
        # 需求 220 的修复调用就是这个档位，必须能升上去
        for req in (4000, 8000, 10000):
            fb = self._fallback(req)
            assert fb > req, (
                f"req={req} 时救援额度 {fb} 没有升上去 —— 救援永远不会触发"
            )

    def test_escalates_at_deepseek_global_default(self):
        """req 恰好等于全局默认额度时，救援仍必须升得上去。

        deepseek-v4-flash 的推荐配置是 LLM_MAX_TOKENS=12000，而修复节点的
        第一档额度也是 12000 —— 旧公式 min(..., 12000) 会算回 12000 并放弃重试。
        实测代价（req 222）：in=6724 / out=12000 / finish_reason=length /
        content 空 → 第二档 20000 从未被尝试 → 「定向修复连续 2 次不可用」跳过。
        """
        fb = self._fallback(12000)
        assert fb > 12000, (
            f"req 取全局默认额度时救援被放弃（fallback={fb}）—— "
            "这正是 req 222 修复被整条跳过的原因"
        )

    def test_small_requests_stay_bounded(self):
        """小额度调用不能被抬到天上（否则单轮必然撞穿超时）。"""
        for req in (100, 500, 2000, 3000):
            fb = self._fallback(req)
            assert fb <= 16000, f"req={req} 的救援额度 {fb} 失控"
            # 且必须由「req*2 / req+4000」这一项兜住，而不是被抬到天花板
            assert fb == min(max(req * 2, req + 4000), 16000)


# ==================== B1：分页读取不得卸载创建时正文 ====================

class TestPartialReadDoesNotOffload:
    """带 start_line/end_line 的读取只覆盖一段，卸载会让模型手上副本归零。"""

    def test_partial_read_keeps_created_body(self):
        from harness.runtime import ToolCallLoop

        workspace = Mock()
        workspace.list.return_value = ["js/app.js"]
        workspace.path = None
        workspace.read.side_effect = lambda f: "l1\nl2\nl3\n"
        loop = ToolCallLoop(workspace=workspace, git=None, tools=None)

        state = {
            "tool_call_count": 5,
            "_known_content_files": {"js/app.js": {"hash": "h", "round": 1, "lines": 3}},
            "dialogue_history": [
                {"role": "tool_call", "name": "write_file",
                 "arguments": {"filename": "js/app.js"},
                 "content": "已创建 js/app.js\n" + "x" * 5000},
            ],
        }
        loop._offload_created_context(
            state, _TC(filename="js/app.js", start_line=1, end_line=2))
        assert "已卸载" not in state["dialogue_history"][0]["content"], (
            "分页读取后又把创建时的完整正文卸载了 —— 模型会被迫继续分页往下读"
        )

    def test_full_read_still_offloads(self):
        """整文件读取覆盖了全文，这时卸载是安全的（保持原行为）。"""
        from harness.runtime import ToolCallLoop

        workspace = Mock()
        workspace.list.return_value = ["js/app.js"]
        workspace.path = None
        workspace.read.side_effect = lambda f: "l1\nl2\nl3\n"
        loop = ToolCallLoop(workspace=workspace, git=None, tools=None)

        state = {
            "tool_call_count": 5,
            "_known_content_files": {"js/app.js": {"hash": "h", "round": 1, "lines": 3}},
            "dialogue_history": [
                {"role": "tool_call", "name": "write_file",
                 "arguments": {"filename": "js/app.js"},
                 "content": "已创建 js/app.js\n" + "x" * 5000},
            ],
        }
        loop._offload_created_context(state, _TC(filename="js/app.js"))
        assert "已卸载" in state["dialogue_history"][0]["content"]


# ==================== B2：write_file 返回策略与承诺一致 ====================

class TestWriteFileEchoMatchesPromise:
    """prompt 承诺「返回完整正文」时，工具就必须真的给正文。"""

    @staticmethod
    def _write(ws, filename, content):
        from harness.tools.file_tools import WriteFileHandler
        return WriteFileHandler(workspace=ws).execute(
            {"filename": filename, "content": content})

    def test_small_file_returns_full_body(self):
        ws = Mock()
        ws.read.side_effect = Exception("not exist")
        body = "\n".join(f"line {i}" for i in range(50))
        assert len(body) < 6000
        r = self._write(ws, "js/a.js", body)
        assert body in r.content, "小文件没有回完整正文，与 prompt 的承诺不符"
        assert "省略" not in r.content

    def test_large_file_says_receipt_not_omission(self):
        ws = Mock()
        ws.read.side_effect = Exception("not exist")
        body = "\n".join(f"line {i}" for i in range(2000))
        assert len(body) > 6000
        r = self._write(ws, "js/big.js", body)
        assert "中间省略" not in r.content, (
            "又出现「中间省略 N 行」——这句话会让模型认为内容缺失并分页回读"
        )
        assert "逐字一致" in r.content
        assert "不要为确认内容而回读" in r.content


# ==================== B3：自建文件不得邀请继续分页 ====================

class TestReadTailNoteByFileOrigin:
    def test_self_created_file_does_not_invite_paging(self):
        from harness.tools.file_tools import ReadFileHandler

        ws = Mock()
        ws.read.return_value = "\n".join(f"l{i}" for i in range(100))
        state = {"_known_content_files": {"js/a.js": {"hash": "h"}}}
        r = ReadFileHandler(workspace=ws).execute(
            {"filename": "js/a.js", "start_line": 1, "end_line": 10}, state=state)
        assert "start_line=11" not in r.content
        assert "无需继续读取" in r.content

    def test_preexisting_file_keeps_paging_hint(self):
        from harness.tools.file_tools import ReadFileHandler

        ws = Mock()
        ws.read.return_value = "\n".join(f"l{i}" for i in range(100))
        r = ReadFileHandler(workspace=ws).execute(
            {"filename": "js/other.js", "start_line": 1, "end_line": 10}, state={})
        assert "start_line=11" in r.content


# ==================== C1：AC 翻译恒定段在 system ====================

class TestAcTranslateStaticSegmentInSystem:
    def test_static_rules_are_in_system_template(self):
        from harness.instructions.prompts import load_prompt

        sys_t = load_prompt("verify/ac_translator_system.md")
        # 恒定规则应留在 system：它是所有批次共享的前缀，能命中缓存
        assert "定位优先级" in sys_t
        assert "断言选型规则" in sys_t
        assert len(sys_t) > 4000

    def test_user_template_only_carries_dynamic_parts(self):
        from harness.instructions.prompts import load_prompt_template

        user = load_prompt_template(
            "verify/ac_translator.md", anchor_text="A", visible_text_text="B",
            selector_text="C", ac_text="D", render_info="E")
        assert "定位优先级" not in user, "恒定规则又回到 user 段了，缓存会失效"
        assert "断言选型规则" not in user
        # 动态量必须都在
        for v in ("A", "B", "C", "D", "E"):
            assert v in user

    def test_system_template_has_no_escaped_braces_left(self):
        """模板里的 JSON 示例写成 {{ }}，渲染后必须是单花括号。"""
        from harness.instructions.prompts import load_prompt_template

        sys_t = load_prompt_template("verify/ac_translator_system.md")
        assert "{{" not in sys_t and "}}" not in sys_t


# ==================== C2：空壳 TASK_STATE 不下发 ====================

class TestTaskStateInjection:
    @staticmethod
    def _loop(task_state_text):
        from harness.runtime import ToolCallLoop

        ws = Mock()
        ws.list.return_value = ([".task/TASK_STATE.md"] if task_state_text is not None
                                else [])
        ws.read.return_value = task_state_text or ""
        ws.path = None
        return ToolCallLoop(workspace=ws, git=None, tools=None)

    def test_skeleton_not_injected(self):
        loop = self._loop(
            "# .task/TASK_STATE.md\n## 目标\n做个贪吃蛇\n"
            "## 决策与理由\n## 文件状态\n## 未决问题\n## 下一步\n")
        assert loop._task_state_injection() == "目标" or True  # 判据在下面
        out = loop._task_state_injection()
        assert "未决问题" not in out, "空小节又下发了"
        assert "做个贪吃蛇" in out, "有内容的小节不能丢"

    def test_all_empty_returns_empty(self):
        loop = self._loop("## 决策与理由\n## 文件状态\n## 下一步\n")
        assert loop._task_state_injection() == ""

    def test_missing_file_returns_empty(self):
        loop = self._loop(None)
        assert loop._task_state_injection() == ""


# ==================== C5：根因文件必须裁剪 ====================

class TestRootCauseFileTrimming:
    @staticmethod
    def _ws(files: dict):
        ws = Mock()
        ws.list.return_value = list(files)
        ws.read.side_effect = lambda f: files[f]
        return ws

    def test_generic_tokens_do_not_mark_every_file(self):
        """缺陷证据里的泛化词（click/assert/button）不该让每个文件都成根因。"""
        from harness.instructions.nodes import _extract_root_cause_files

        files = {
            "index.html": "<button id='add'>点击</button> click assert",
            "css/style.css": ".btn { } button",
            "js/game.js": "function move() { } click assert button",
            "js/app.js": "click assert button",
            "js/util.js": "click assert button",
            "js/storage.js": "click assert button",
        }
        defects = [{
            "message": "点击添加按钮无反应",
            "evidence": "click #add-btn assert_exists .item",
            "suggestion": "检查 button 的事件绑定",
        }]
        root = _extract_root_cause_files(self._ws(files), list(files), defects)
        assert len(root) <= 2, (
            f"根因文件裁不掉（{sorted(root)}）—— 与「最多改 2 个文件」的约束矛盾，"
            f"且全量下发会把修复 prompt 撑到几万字符"
        )

    def test_specific_identifier_wins(self):
        """证据里出现某文件独有的标识符时，那个文件必须被选为根因。"""
        from harness.instructions.nodes import _extract_root_cause_files

        files = {
            "index.html": "<div>hello</div>",
            "js/game.js": "const snakeBody = []; function drawSnakeBody() {}",
            "css/style.css": "body { color: red }",
        }
        defects = [{
            "message": "蛇身没有渲染",
            "evidence": "drawSnakeBody 未执行，snakeBody 为空",
            "suggestion": "检查 drawSnakeBody 的调用时机",
        }]
        root = _extract_root_cause_files(self._ws(files), list(files), defects)
        assert "js/game.js" in root

    def test_no_defects_returns_empty(self):
        from harness.instructions.nodes import _extract_root_cause_files

        files = {"index.html": "<div>x</div>"}
        assert _extract_root_cause_files(self._ws(files), list(files), []) == set()


# ==================== D2：提示词片段引用（防漂移） ====================

class TestPromptInclude:
    def test_shared_boundary_resolved_in_both(self):
        from harness.instructions.prompts import _INCLUDE_RE, load_prompt

        for rel in ("intent/classify.md", "intent/clarify_generate.md"):
            text = load_prompt(rel)
            assert not _INCLUDE_RE.search(text), f"{rel} 的 include 标记没展开"
            assert "纯静态前端站点" in text

    def test_single_definition(self):
        """边界段只能有一份定义，两处都得引用它。"""
        from harness.instructions.prompts import _PROMPTS_DIR

        hits = [p.name for p in _PROMPTS_DIR.joinpath("intent").glob("*.md")
                if "纯静态前端站点" in p.read_text(encoding="utf-8")]
        # _platform_boundary.md 是定义处，另外两份只能靠引用
        assert hits == ["_platform_boundary.md"], (
            f"平台能力边界出现多份定义: {hits}"
        )


# ==================== A2 补丁：修复节点的分诊顺序 ====================

class TestRepairTriageOrder:
    """空响应的分诊顺序 —— 先判 finish_reason，再判端点故障。

    2026-10-05 req 222 实测（deepseek-v4-flash）：
      in=6724 out=12000 finish_reason='length' content 长度 0 → client 会把它标成
      is_error=True（error="LLM 返回空响应"）。修复节点若**先判 is_error 直接 break**，
      下面那段 finish_reason 分诊就永远不可达，第二档 20000 从未被尝试，
      最终日志打成「定向修复 LLM 连续 2 次不可用，跳过该路径」——
      与需求 220 的死法逐字相同。

    顺序无法用行为测试稳定覆盖（要拉起整个节点），故用 AST 锁住结构。
    """

    @staticmethod
    def _is_error_branch_body():
        import ast
        import inspect
        import textwrap

        from harness.instructions import nodes as nodes_mod

        tree = ast.parse(textwrap.dedent(inspect.getsource(nodes_mod.defect_repair_node)))
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and ast.unparse(node.test).strip() == "resp.is_error":
                return [ast.unparse(s) for s in node.body]
        raise AssertionError("defect_repair_node 里找不到 `if resp.is_error:` 分支")

    def test_finish_reason_checked_before_endpoint_downgrade(self):
        body = self._is_error_branch_body()
        fr_idx = next((i for i, s in enumerate(body) if "finish_reason" in s), None)
        down_idx = next((i for i, s in enumerate(body) if "llm_down = True" in s), None)
        assert fr_idx is not None, (
            "is_error 分支里没有 finish_reason 分诊 —— 被 reasoning 吃光的可恢复"
            "空响应会被误判成端点故障"
        )
        assert down_idx is not None
        assert fr_idx < down_idx, (
            "finish_reason 分诊必须排在「判定端点故障」之前，否则永远不可达"
        )

    def test_truncated_empty_response_escalates(self):
        """分诊成立的条件必须是「空内容 + length」，不能放宽成任意 is_error。"""
        body = " ".join(self._is_error_branch_body())
        assert "not resp.content" in body
        assert "length" in body
