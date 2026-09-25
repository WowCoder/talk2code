# -*- coding: utf-8 -*-
"""
能力边界与执行进度的不可回退约束守卫。

守两件事：

1. 平台只产出纯静态前端站点，这个边界**必须对用户可见**。
   此前边界只写在给 LLM 的 prompt 里（environment_contract / tl_analysis），
   用户说"做个带用户注册和数据库的系统"会直接进 TASK 流程，Coder 闷头用
   localStorage 伪造一个登录，验收不通过却不告知真实原因——用户只觉得
   "这平台做不好"。OUTPUT: 必须有一条从分类器到澄清表单的显式边界链路。

2. 编码/验证阶段**必须推送动作级进度**。
   此前 progress 推送整段包在 `if contract and contract.exists()` 内，
   contract 缺失时全程零推送，前端只能显示硬编码的"AI 正在处理…"。
"""

from pathlib import Path
from unittest import mock

from harness.constraints.hooks import HookContext
from harness.constraints.progress_hooks import track_write_success
from harness.instructions.intent_router import IntentRouter, IntentType

PROMPTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "harness" / "instructions" / "prompts"
)


# ==================== 1. 能力边界 ====================

def test_classify_prompt_declares_out_of_scope():
    """分类器必须知道「超出能力范围」这一类，否则边界永远到不了用户面前。"""
    text = (PROMPTS_DIR / "intent" / "classify.md").read_text(encoding="utf-8")
    assert "OUT_OF_SCOPE" in text
    # 只加枚举不够：必须同时写清"做不到什么"的判据，否则模型无从判断
    assert "数据库" in text
    assert "服务端" in text


def test_clarify_prompt_offers_data_scope_choice():
    """澄清环节必须给出「要后端」的可行替代方案，而不是默默降级。"""
    text = (PROMPTS_DIR / "intent" / "clarify_generate.md").read_text(encoding="utf-8")
    assert "data_scope" in text
    assert "本地存储" in text
    # 边界声明本身也要在：用户得先知道为什么被问这个问题
    assert "纯静态前端站点" in text or "纯前端" in text


def test_intent_type_has_out_of_scope():
    assert IntentType.OUT_OF_SCOPE.value == "out_of_scope"


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.is_error = False
        self.error = None


class _FakeClient:
    def __init__(self, content):
        self._content = content

    def chat(self, **_kwargs):
        return _FakeResp(self._content)


def _classify_with(raw_reply: str) -> IntentType:
    router = IntentRouter()
    router._client = _FakeClient(raw_reply)
    # 屏蔽工作流技能预匹配，隔离出纯粹的 LLM 分类解析路径
    with mock.patch(
        "harness.instructions.skill_loader.get_skill_loader"
    ) as loader:
        loader.return_value.match_workflow_skills.return_value = []
        return router.classify(
            "做个需要用户注册登录、数据存数据库的系统"
        ).intent


def test_out_of_scope_parsed_from_canonical_label():
    assert _classify_with("OUT_OF_SCOPE") is IntentType.OUT_OF_SCOPE


def test_out_of_scope_parsed_from_separator_variants():
    """模型常把下划线写成连字符或空格——不归一化，边界声明会被静默跳过。"""
    for raw in ("OUT-OF-SCOPE", "OUT OF SCOPE", "out-of-scope", "Out_Of_Scope"):
        assert _classify_with(raw) is IntentType.OUT_OF_SCOPE, raw


def test_task_still_routes_to_task():
    """回归护栏：加了 OUT_OF_SCOPE 不能把正常开发需求误判出界。"""
    assert _classify_with("TASK") is IntentType.TASK


# ==================== 2. 动作级进度推送 ====================

class _FakeSSE:
    def __init__(self):
        self.progress_calls = []
        self.task_updates = []

    def progress(self, req_id, percent, message="", stage=""):
        self.progress_calls.append((req_id, percent, message, stage))

    def task_update(self, req_id, file_path, status):
        self.task_updates.append((req_id, file_path, status))


class _FakeLoop:
    def __init__(self, sse):
        self.sse = sse


def _make_ctx(sse, state_overrides=None):
    state = {"metadata": {"_tool_loop": _FakeLoop(sse)}}
    state.update(state_overrides or {})
    return HookContext(
        requirement_id=42,
        tool_name="write_file",
        tool_args={"filename": "js/app.js"},
        tool_result="写入成功",
        state=state,
    )


def test_write_progress_pushed_without_contract():
    """核心回归点：contract 缺失时也必须推送，不能静默。

    这是「编码期进度条一动不动」的直接成因——推送曾被整段包在
    `if contract and contract.exists()` 里。contract 只让文案能带 (n/total)，
    不该成为推送的前提。
    """
    sse = _FakeSSE()
    assert track_write_success(_make_ctx(sse)) is None

    assert len(sse.progress_calls) == 1
    req_id, percent, message, stage = sse.progress_calls[0]
    assert req_id == 42
    assert "js/app.js" in message
    assert stage == "coding"
    assert 0 <= percent <= 100
    # TaskPanel 同步点亮
    assert sse.task_updates == [(42, "js/app.js", "completed")]


def test_write_progress_ignores_non_write_tools():
    sse = _FakeSSE()
    ctx = _make_ctx(sse)
    ctx.tool_name = "read_file"
    assert track_write_success(ctx) is None
    assert sse.progress_calls == []


def test_write_progress_never_raises():
    """进度推送是旁路，任何异常都不允许阻断写文件后的主流程。"""
    class _BrokenLoop:
        @property
        def sse(self):
            raise RuntimeError("SSE 不可用")

    ctx = HookContext(
        requirement_id=1,
        tool_name="write_file",
        tool_args={"filename": "a.js"},
        tool_result="ok",
        state={"metadata": {"_tool_loop": _BrokenLoop()}},
    )
    assert track_write_success(ctx) is None
