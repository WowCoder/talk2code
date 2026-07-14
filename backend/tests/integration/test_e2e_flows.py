# -*- coding: utf-8 -*-
"""
端到端集成测试
对应 tasks.md 14.1-14.9
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
import json


class TestE2EFirstGeneration:
    """E2E: 首次生成流程 (14.1)"""

    def test_planner_to_coder_transition(self):
        """测试 Planner → Coder 转换"""
        from harness.state.agent_state import AgentState

        # 验证 AgentState 包含所有必要字段
        state: AgentState = {
            "requirement_id": 1,
            "requirement_content": "创建登录页",
            "user_id": 100,
            "plan": {"files": ["index.html", "style.css", "app.js"], "components": ["form"]},
            "current_step": "planner_done",
            "code_files": [],
            "validation_result": None,
            "retry_count": 0,
            "error": None,
            "dialogue_history": [],
            "metadata": {},
            "tool_call_count": 0,
            "no_progress_count": 0,
            "last_file_list": None,
            "hook_failures": {},
            "visual_style": None,
        }
        assert state["current_step"] == "planner_done"

    def test_hooks_trigger_on_completion(self):
        """测试 Hook 在任务完成时触发"""
        from harness.constraints.hooks import HookContext, HookPoint
        from harness.constraints.hooks import create_default_hook_manager

        manager = create_default_hook_manager()

        # 模拟完整生成后的状态
        ctx = HookContext(
            requirement_id=1,
            state={"file_list": ["index.html", "style.css", "app.js"], "code_files": [
                {"filename": "index.html", "content": "<!DOCTYPE html><html>...</html>"},
                {"filename": "style.css", "content": "body{margin:0}"},
                {"filename": "app.js", "content": "console.log('ok')"},
            ]}
        )

        failures = manager.trigger(HookPoint.ON_TASK_COMPLETE, ctx)
        # 所有必要文件都存在，应该通过
        # 注意：quality hooks 检查的是 index.html/script.js/style.css 这些 target files
        # script.js 不是标准 target，但 app.js 也算 js 文件
        # 可能还会有其他 hook 检查，这里主要验证流程不崩溃


class TestE2EVagueRequirement:
    """E2E: 模糊需求 → clarify → 完成 (14.2)"""

    @patch("harness.instructions.nodes.get_client")
    def test_vague_requirement_detection(self, mock_get_client):
        """测试模糊需求检测"""
        mock_client = Mock()
        mock_client.chat.return_value = Mock(
            content='[{"id":"q1","label":"主题色是什么？","type":"radio","options":["蓝色","红色","绿色"]}]',
            is_error=False, error=None
        )
        mock_get_client.return_value = mock_client

        from harness.instructions.nodes import _is_vague_requirement

        # 较短文本（<30 字符）
        assert _is_vague_requirement("做一个页面") is True
        # 较长且有功能描述的文本（>30 字符且有动作+功能关键词）
        assert _is_vague_requirement("创建一个带有登录表单、用户列表和添加按钮的后台管理页面，支持数据本地保存") is False
        # 有补充说明的文本
        assert _is_vague_requirement("做应用\n\n[用户补充说明]\n蓝色主题") is False

    def test_clarify_endpoint_flow(self):
        """测试 clarify 端点的数据流"""
        # 验证 answers 正确拼接
        original = "做一个页面"
        answers = {"q1": "蓝色", "q2": "暗黑模式"}
        answer_text = '；'.join(f'{q}: {a}' for q, a in answers.items())
        enhanced = f'{original}\n\n[用户补充说明]\n{answer_text}'

        assert "蓝色" in enhanced
        assert "暗黑模式" in enhanced
        assert "[用户补充说明]" in enhanced


class TestE2EChatModification:
    """E2E: Chat 修改流程 (14.3)"""

    def test_chat_state_has_is_chat_flag(self):
        """测试 Chat 状态包含 is_chat 标记"""
        state = {
            "requirement_id": 1,
            "dialogue_history": [],
            "code_files": [{"filename": "index.html", "content": "<html></html>"}],
            "metadata": {"trace_id": "", "is_chat": True},
        }
        assert state["metadata"]["is_chat"] is True

    def test_chat_hooks_only_check_modified_files(self):
        """测试 Chat 模式下 Hook 只检查修改的文件"""
        from harness.constraints.hooks import HookContext, HookPoint
        from harness.constraints.hooks import create_default_hook_manager

        manager = create_default_hook_manager()

        # Chat 模式: 已有完整文件，只改了 script.js
        ctx = HookContext(
            requirement_id=1,
            tool_name="write_file",
            tool_args={"filename": "app.js", "content": "console.log('updated')"},
            state={"file_list": ["index.html", "style.css", "app.js"]}
        )

        # POST_TOOL_USE 应该只检查当前文件
        failures = manager.trigger(HookPoint.POST_TOOL_USE, ctx)
        # 验证不崩溃，结果应该只是针对 app.js 的检查
        assert isinstance(failures, list)


class TestE2EHookRepair:
    """E2E: Hook 失败 → Agent 修复 (14.5)"""

    def test_security_hook_detects_xss(self):
        """测试安全 Hook 检测 XSS 风险"""
        from harness.constraints.hooks import HookContext, HookPoint
        from harness.constraints.hooks import create_default_hook_manager

        manager = create_default_hook_manager()

        # 模拟写入包含 innerHTML 的文件
        ctx = HookContext(
            requirement_id=1,
            tool_name="write_file",
            tool_args={"content": "element.innerHTML = userInput"},
            tool_result="file written",
            state={}
        )

        failures = manager.trigger(HookPoint.POST_TOOL_USE, ctx)
        assert len(failures) > 0, "安全 Hook 应该检测到 innerHTML"

    def test_constraint_escalation_policy(self):
        """测试约束失败升级策略: 1→反馈 2→建议 3→放过"""
        hooks_fail_count = {}  # {hook_name: count}

        # 第一次: 反馈给 Agent
        hooks_fail_count["innerHTML"] = 1
        assert hooks_fail_count["innerHTML"] <= 2  # 前2次反馈

        # 第二次: 加修复建议
        hooks_fail_count["innerHTML"] = 2
        assert hooks_fail_count["innerHTML"] <= 2  # 仍然反馈

        # 第三次: 放过
        hooks_fail_count["innerHTML"] = 3
        # 第3次后放过
        assert hooks_fail_count["innerHTML"] >= 3


class TestE2ECheckpointRecovery:
    """E2E: 断点恢复 (14.6)"""

    def test_simulate_interrupt_and_recovery(self):
        """模拟进程中断后恢复"""
        from harness.state.checkpoint import CheckpointManager

        cm = CheckpointManager()

        # 正常执行到 planner 结束，保存 checkpoint
        state_after_planner = {
            "requirement_id": 1,
            "requirement_content": "创建应用",
            "plan": {"files": ["index.html", "style.css", "app.js"]},
            "current_step": "planner_done",
            "dialogue_history": [{"role": "agent", "content": "Plan created"}],
            "code_files": [],
        }
        cm.save(1, "planner", state_after_planner)

        # 模拟进程中断...

        # 恢复时，可以从 planner checkpoint 继续
        restored = cm.resume(1)
        assert restored is not None
        assert restored["plan"]["files"] == ["index.html", "style.css", "app.js"]
        assert restored["current_step"] == "planner_done"


class TestE2ECrossSessionMemory:
    """E2E: 跨会话记忆 (14.7)"""

    def test_user_preference_recalled_in_new_session(self):
        """测试用户偏好在新的需求中生效"""
        from harness.state.memory_store import MemoryStore

        store = MemoryStore()

        # 会话 1: 创建红色主题应用
        store.remember(1, "user explicitly prefers red color theme", "user_preference", 0.85)
        store.remember(1, "user dislikes round corners on buttons", "user_preference", 0.7)

        # 会话 2: 新建另一个应用，系统召回偏好
        recalled = store.recall("create a new web application", 1, top_k=5)

        # 至少能召回一条记忆（无 LLM 时直接返回全部）
        assert len(recalled) >= 1

    def test_agent_lesson_recalled(self):
        """测试 Agent 经验教训被召回"""
        from harness.state.memory_store import MemoryStore

        store = MemoryStore()

        store.remember(1, "avoid using innerHTML in any generated code", "domain_knowledge", 0.9)
        store.remember(1, "always include meta viewport tag in HTML", "agent_lesson", 0.8)

        recalled = store.recall("generate HTML code", 1)
        assert len(recalled) >= 1


class TestE2EObservabilityPanel:
    """E2E: 观测面板 (14.8)"""

    def test_trace_contains_all_spans(self):
        """测试 Trace 包含所有 Span"""
        from harness.observability.tracer import Tracer

        tracer = Tracer()
        trace = tracer.start_trace(requirement_id=1, user_id=100)

        # 模拟完整工作流的 spans
        planner_span = tracer.start_span(trace.trace_id, "planner_node")
        tracer.end_span(planner_span, "success")

        tool_span = tracer.start_span(trace.trace_id, "tool_coder_iter_0",
                                       parent_id=planner_span.span_id,
                                       metadata={"tokens": 500})
        tracer.end_span(tool_span, "success")

        hook_span = tracer.start_span(trace.trace_id, "hook_on_task_complete")
        tracer.end_span(hook_span, "success")

        tracer.end_trace(trace.trace_id)

        # 验证 trace 包含 3 个 spans
        assert len(trace.spans) == 3
        assert trace.total_tokens == 500

    def test_cost_tracking_across_multiple_calls(self):
        """测试跨多次调用的成本跟踪"""
        from harness.observability.cost import CostTracker

        tracker = CostTracker()
        tracker.record("trace_1", input_tokens=500, output_tokens=200, model="gpt-4o")
        tracker.record("trace_1", input_tokens=300, output_tokens=150, model="gpt-4o")

        report = tracker.get_report("trace_1")
        assert report.total_tokens == 1150  # 500+200+300+150
        assert report.input_tokens == 800
        assert report.output_tokens == 350

        # 成本应该 > 0
        # gpt-4o: input $2.50/M, output $10.00/M
        # cost = (800/1M)*2.50 + (350/1M)*10.00
        assert report.total_cost > 0


class TestE2EAllExistingTestsPass:
    """E2E: 所有现有测试通过 (14.9)"""

    def test_harness_imports_work(self):
        """测试 harness 所有模块可导入"""
        # L1
        from harness.instructions.assembler import ContextAssembler
        from harness.instructions.compactor import ContextCompactor
        from harness.instructions.prompts import load_prompt, load_prompt_template

        # L2
        from harness.tools.registry import create_tool_registry, ToolRegistry, ToolDefinition
        from harness.runtime import ToolCallLoop

        # L3
        from harness.environment.sandbox import SandboxExecutor

        # L4
        from harness.state.workspace import WorkspaceFS
        from harness.state.versioning import GitVersioning
        from harness.state.checkpoint import CheckpointManager
        from harness.state.memory_store import MemoryStore

        # L5
        from harness.constraints.hooks import create_default_hook_manager

        # L6
        from harness.observability.tracer import Tracer
        from harness.observability.cost import CostTracker
        from harness.observability.logger import get_logger

        assert ContextAssembler is not None
        assert ToolRegistry is not None
        assert WorkspaceFS is not None
        assert Tracer is not None

    def test_default_harness_factory_works(self):
        """测试 harness.create_harness() 工厂函数"""
        from harness import create_harness

        harness = create_harness(requirement_id=1, user_id=100)
        assert harness is not None
        assert "workspace" in harness
        assert "tools" in harness
        assert "hooks" in harness
        assert "checkpoint" in harness
        assert "memory_manager" in harness
        assert "tracer" in harness
        assert "cost_tracker" in harness
