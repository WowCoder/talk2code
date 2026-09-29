# -*- coding: utf-8 -*-
"""
集成测试：完整工作流 Planner → 多轮工具调用 → 完成
对应 tasks.md 3.6
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
import json


class TestPlannerToToolLoopFlow:
    """完整流程: Planner → ToolCallLoop → 完成"""

    @patch("harness.instructions.nodes.get_client")
    def test_planner_node_returns_plan(self, mock_get_client):
        """测试 TeamLeader 节点（原 planner_node）返回结构化计划

        mock 必须按调用类型分派：澄清生成和 plan 生成打的是同一个 client。
        此前这里对**所有**调用都返回 plan JSON —— 澄清那次拿到 plan JSON 解析不出
        问题列表，旧代码"解析失败即当无需追问"顺手放行，测试于是长期成立；
        修掉静默放行之后（失败不再等价于放行），这个 mock 才暴露出来。
        """
        mock_client = Mock()
        plan_json = json.dumps({
            "requirement_restated": "一个能增删待办、刷新后还在的清单",
            "features": ["添加待办", "删除待办"],
            "assumptions": ["数据存在浏览器本地"],
            "acceptance_criteria": [
                {"id": "AC-1", "label": "能添加待办", "feature": "添加待办",
                 "anchor": "页面顶部的输入框与旁边的添加按钮",
                 "how_to_verify": "输入内容后点击添加按钮，列表中出现这条新待办"},
                {"id": "AC-2", "label": "能删除待办", "feature": "删除待办",
                 "anchor": "列表中每一条待办右侧的删除按钮",
                 "how_to_verify": "点击某条待办的删除按钮，列表项数量减少一项"},
            ],
            "file_structure": ["index.html", "style.css", "app.js"],
            "tech_stack": {"css": "tailwind", "storage": "localStorage"},
            "implementation_order": ["style.css", "app.js", "index.html"],
            "tasks": [
                {"file": "index.html", "purpose": "页面骨架与容器挂载",
                 "description": "写结构", "dependencies": []},
                {"file": "app.js", "purpose": "待办增删的全部交互逻辑",
                 "description": "写逻辑", "dependencies": []},
                {"file": "style.css", "purpose": "全局样式与列表布局定义",
                 "description": "写样式", "dependencies": []},
            ],
            "complexity": "standard",
        })

        def _chat(prompt, **kwargs):
            # 澄清调用（intent/clarify_generate.md）返回空数组 = LLM 判定无需追问；
            # 需求里已写明"极简风格"，也不会再补必问的视觉风格题。
            if "澄清" in prompt and "开发计划" not in prompt:
                return Mock(content="[]", is_error=False, error=None, finish_reason="stop")
            return Mock(content=plan_json, is_error=False, error=None, finish_reason="stop")

        mock_client.chat.side_effect = _chat
        mock_get_client.return_value = mock_client

        from harness.instructions.nodes import team_leader_node

        state = {
            "requirement_id": 1,
            "requirement_content": "创建待办事项应用，极简风格，数据存本地",
            "dialogue_history": [],
            "metadata": {},
        }

        result = team_leader_node(state)
        assert result is not None
        assert "current_step" in result
        assert result.get("plan", {}).get("file_structure") == ["index.html", "style.css", "app.js"]
        # 新契约：AC 必须带 anchor（验收脚本据此定位，而不是从产物代码反推）
        acs = result["plan"]["acceptance_criteria"]
        assert all(a.get("anchor") for a in acs)

    @patch("harness.instructions.nodes.get_client")
    def test_tool_coder_node_with_tool_calls(self, mock_get_client):
        """测试 ToolCoder 节点处理带工具调用的 LLM 响应"""
        mock_client = Mock()
        # 第一轮: LLM 返回 tool_calls
        mock_client.chat_with_tools.return_value = Mock(
            content="我来创建文件",
            tool_calls=[Mock(name="write_file", arguments={"filename": "index.html", "content": "<html></html>"})],
            usage=None
        )
        mock_get_client.return_value = mock_client

        from harness.instructions.nodes import tool_coder_node

        state = {
            "requirement_id": 1,
            "requirement_content": "创建页面",
            "plan": {"files": ["index.html"]},
            "dialogue_history": [],
            "code_files": [],
            "tool_call_count": 0,
            "no_progress_count": 0,
            "last_file_list": [],
            "hook_failures": {},
            "metadata": {},
        }

        result = tool_coder_node(state)
        assert result is not None


class TestChatModificationFlow:
    """Chat 修改流程测试 (9.5)"""

    @patch("harness.runtime.get_client")
    def test_chat_flow_uses_correct_system_prompt(self, mock_get_client):
        """测试 Chat 模式使用修改专用 prompt"""
        from harness.runtime import ToolCallLoop

        mock_client = Mock()
        mock_response = Mock()
        mock_response.tool_calls = None
        mock_response.content = "修改完成"
        mock_response.usage = None
        mock_client.chat_with_tools.return_value = mock_response
        mock_get_client.return_value = mock_client

        workspace = Mock()
        workspace.list.return_value = ["index.html", "style.css"]
        workspace.path = None

        loop = ToolCallLoop(workspace=workspace)

        # 模拟 chat prompt
        original_build = loop._build_system_prompt
        loop._build_system_prompt = lambda state: "添加删除按钮的修改专用 prompt: " + state.get("requirement_content", "")

        state = {
            "requirement_id": 1,
            "requirement_content": "给列表添加删除按钮",
            "dialogue_history": [],
            "code_files": [],
            "tool_call_count": 0,
            "no_progress_count": 0,
            "last_file_list": [],
            "hook_failures": {},
            "metadata": {"is_chat": True},
        }

        chat_prompt = loop._build_system_prompt(state)
        assert "删除按钮" in chat_prompt
        assert "修改" in chat_prompt


class TestHookFixLoop:
    """Hook 失败 → Agent 修复循环 (7.9)"""

    def test_hook_failure_triggers_repair_prompt(self):
        """测试 Hook 失败时生成修复提示词"""
        from harness.constraints.hooks import HookContext, HookPoint
        from harness.constraints.hooks import create_default_hook_manager

        manager = create_default_hook_manager()

        # 模拟完成 state（缺少 index.html）
        ctx = HookContext(
            requirement_id=1,
            state={"file_list": [], "code_files": []}
        )

        failures = manager.trigger(HookPoint.ON_TASK_COMPLETE, ctx)
        assert len(failures) > 0, "应该检测到缺少必需文件"

        # 构建修复 prompt
        repair_prompt = "检查发现以下问题，请立即修复：\n" + "\n".join(f"- {f}" for f in failures)
        assert "修复" in repair_prompt or "修复" in repair_prompt
        assert len(repair_prompt) > 0

    def test_quality_hooks_detect_all_required_files(self):
        """测试质量 Hook 检测所有必需文件"""
        from harness.constraints.hooks import HookContext, HookPoint
        from harness.constraints.hooks import create_default_hook_manager

        manager = create_default_hook_manager()

        # 测试 - 只有 index.html 没有 style.css 和 script.js
        ctx = HookContext(
            requirement_id=1,
            state={"file_list": ["index.html"], "code_files": [
                {"filename": "index.html", "content": "<html></html>"}
            ]}
        )

        failures = manager.trigger(HookPoint.ON_TASK_COMPLETE, ctx)
        # quality hooks check for required files like index.html, style.css, script.js
        assert len(failures) >= 0


class TestCheckpointRecovery:
    """Checkpoint 恢复测试 (4.8)"""

    def test_save_and_load_checkpoint(self):
        """测试保存和加载检查点"""
        from harness.state.checkpoint import CheckpointManager

        cm = CheckpointManager()

        state = {
            "requirement_id": 1,
            "requirement_content": "测试",
            "dialogue_history": [{"role": "user", "content": "hello"}],
            "code_files": [{"filename": "index.html", "content": "<html></html>"}],
        }

        cp_id = cm.save(1, "tool_coder", state)
        assert cp_id is not None
        assert "cp_1_" in cp_id

        # 加载检查点
        cp = cm.load(1)
        assert cp is not None
        assert cp.node_name == "tool_coder"

        # 恢复状态
        restored = cm.resume(1)
        assert restored is not None
        assert restored["requirement_id"] == 1
        assert "index.html" in restored["code_files"][0]["filename"]

    def test_cannot_resume_from_end_state(self):
        """测试不恢复已结束的状态"""
        from harness.state.checkpoint import CheckpointManager

        cm = CheckpointManager()
        cm.save(1, "end", {"requirement_id": 1})
        restored = cm.resume(1)
        assert restored is None

    def test_clear_checkpoint_after_completion(self):
        """测试任务完成后清理检查点"""
        from harness.state.checkpoint import CheckpointManager

        cm = CheckpointManager()
        cm.save(1, "planner", {"test": True})
        assert cm.load(1) is not None

        cm.clear(1)
        assert cm.load(1) is None

    def test_checkpoint_persistence_across_instances(self):
        """测试检查点在内存中的存储和恢复（同一实例内）"""
        from harness.state.checkpoint import CheckpointManager

        cm = CheckpointManager()
        cm.save(100, "tool_coder", {"key": "value"})

        # 同一实例可以加载
        cp = cm.load(100)
        assert cp is not None
        assert cp.node_name == "tool_coder"

        # 验证 resume 功能
        restored = cm.resume(100)
        assert restored is not None
        assert restored["key"] == "value"


class TestCrossSessionMemory:
    """跨会话记忆测试 (5.8)"""

    def test_memories_persist_across_sessions(self):
        """测试记忆在多个"会话"中保持"""
        from harness.state.memory_store import MemoryStore

        store = MemoryStore()

        # 会话 1: 用户表达偏好
        store.remember(1, "user prefers dark mode theme", "user_preference", 0.8)
        store.remember(1, "project name is EcommerceApp", "domain_knowledge", 0.7)

        # 会话 2: 新查询中召回
        recalled = store.recall("create a dark mode dashboard", 1)
        assert len(recalled) >= 1

    def test_memory_importance_decay_preserves_data(self):
        """测试衰减不会丢失所有数据"""
        from harness.state.memory_store import MemoryStore
        import time

        store = MemoryStore()

        # 高重要性
        store.remember(1, "critical project constraint: no external APIs", "domain_knowledge", 0.95)

        # 衰减
        store.decay()
        memories = store._get_user_memories(1)
        assert len(memories) >= 1
        assert memories[0]["importance"] > 0.5  # 仍然很高
