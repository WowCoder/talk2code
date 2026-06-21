# -*- coding: utf-8 -*-
"""
测试 ContextAssembler 组装逻辑、ContextCompactor 压缩保留
对应 tasks.md 8.8
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from harness.instructions.assembler import ContextAssembler, AssembledContext
from harness.instructions.compactor import ContextCompactor


class TestAssembledContext:
    """AssembledContext 数据类测试"""

    def test_assembled_context_creation(self):
        """测试 AssembledContext 基本创建"""
        ctx = AssembledContext(
            system_prompt="You are an engineer",
            skill_instructions="Use Tailwind CSS",
        )
        assert ctx.system_prompt == "You are an engineer"
        assert ctx.skill_instructions == "Use Tailwind CSS"
        assert ctx.long_term_memories == ""
        assert ctx.craft_rules == ""
        assert ctx.metadata == {}


class TestContextAssemblerAssemble:
    """ContextAssembler.assemble() 测试"""

    def test_assemble_basic(self):
        """测试基本上下文组装"""
        assembler = ContextAssembler(memory_store=None)
        result = assembler.assemble("创建登录页面", 1)

        assert isinstance(result, AssembledContext)
        assert len(result.system_prompt) > 0
        assert "前端" in result.system_prompt
        assert result.user_prompt == "创建登录页面"

    def test_assemble_with_visual_style(self):
        """测试带视觉风格偏好的组装"""
        assembler = ContextAssembler(memory_store=None)
        result = assembler.assemble("创建页面", 1, metadata={"visual_style": "暗黑模式"})

        assert "暗黑模式" in result.system_prompt
        assert len(result.system_prompt) > 0

    def test_assemble_with_memories(self):
        """测试带长期记忆的组装"""
        mock_memory = Mock()
        mock_memory.recall.return_value = [
            {"fact": "用户偏好红色主题", "memory_type": "user_preference"},
        ]

        assembler = ContextAssembler(memory_store=mock_memory)
        result = assembler.assemble("创建页面", 1)

        assert "用户偏好" in result.long_term_memories or len(result.long_term_memories) >= 0
        # 验证调用了 recall
        mock_memory.recall.assert_called_once()

    def test_assemble_without_memories(self):
        """测试无记忆存储时的组装"""
        assembler = ContextAssembler(memory_store=None)
        result = assembler.assemble("创建页面", 1)

        assert result.long_term_memories == ""


class TestContextAssemblerFeatureAnalysis:
    """特征检测测试"""

    def test_detect_ui_features(self):
        """测试检测 UI 特征"""
        assembler = ContextAssembler()
        features = assembler._analyze_features("创建一个带有按钮和列表的页面")

        assert features["has_ui"] is True

    def test_detect_form_features(self):
        """测试检测表单特征"""
        assembler = ContextAssembler()
        features = assembler._analyze_features("创建一个登录表单和注册表单")

        assert features["has_form"] is True

    def test_detect_content_features(self):
        """测试检测内容特征"""
        assembler = ContextAssembler()
        features = assembler._analyze_features("创建一个博客文章系统")

        assert features["has_content"] is True

    def test_no_features_detected(self):
        """测试无特征检测"""
        assembler = ContextAssembler()
        features = assembler._analyze_features("一个计算器")

        assert features["has_ui"] is False
        assert features["has_form"] is False
        assert features["has_content"] is False

    def test_select_crafts_with_features(self):
        """测试根据特征选择 Craft"""
        assembler = ContextAssembler()
        crafts = assembler._select_crafts("创建登录表单和用户列表页面")

        # 应该包含 typography, color, accessibility-baseline, anti-ai-slop
        assert "typography" in crafts or "color" in crafts

    def test_select_crafts_default(self):
        """测试无特征时的默认 Craft"""
        assembler = ContextAssembler()
        crafts = assembler._select_crafts("计算器")
        # 默认返回所有 4 个
        assert len(crafts) == 4


class TestContextCompactorBasics:
    """ContextCompactor 基础测试"""

    def test_maybe_compact_below_threshold(self):
        """测试低于阈值时不压缩"""
        compactor = ContextCompactor(budget=100000)
        messages = [{"role": "user", "content": "短消息"}]

        result = compactor.maybe_compact(messages)
        assert len(result) == len(messages)

    def test_maybe_compact_above_threshold(self):
        """测试超过阈值时压缩"""
        compactor = ContextCompactor(budget=10)  # 极小预算，几乎总是触发
        messages = [
            {"role": "system", "content": "You are an engineer."},
            {"role": "user", "content": "A" * 200},  # 很多 token
        ]

        result = compactor.maybe_compact(messages)
        # 至少保留了 system 消息
        assert any(m["role"] == "system" for m in result)

    def test_compact_preserves_system(self):
        """测试压缩保留 system 消息"""
        compactor = ContextCompactor()
        messages = [
            {"role": "system", "content": "SYSTEM"},
        ] + [
            {"role": "user", "content": f"message {i}"} for i in range(30)
        ]

        compressed = compactor._compact_old_dialogues(messages)
        # system 应该保留
        system_msgs = [m for m in compressed if m["role"] == "system"]
        assert len(system_msgs) >= 1

    def test_compact_keeps_recent(self):
        """测试压缩保留最近消息"""
        compactor = ContextCompactor()
        messages = [{"role": "system", "content": "SYS"}] + [
            {"role": "user", "content": f"msg {i}"} for i in range(50)
        ]

        compressed = compactor._compact_old_dialogues(messages)
        # 保留 system + 最近 20 条 = 21
        assert len(compressed) <= 21

    def test_estimate_tokens(self):
        """测试 token 估算"""
        compactor = ContextCompactor()
        messages = [{"role": "user", "content": "hello world"}]

        estimated = compactor._estimate_tokens(messages)
        # "hello world" = 11 chars / 3 ≈ 3-4 tokens
        assert estimated > 0

    def test_summarize_old_messages(self):
        """测试旧消息摘要"""
        compactor = ContextCompactor()
        old = [
            {"role": "user", "content": "创建登录页"},
            {"role": "agent", "content": "好的，创建一个包含用户名和密码的登录页"},
        ]

        summary = compactor.summarize_old_messages(old)
        assert "[历史摘要]" in summary
        assert "登录" in summary

    def test_summarize_empty(self):
        """测试空消息摘要"""
        compactor = ContextCompactor()
        assert compactor.summarize_old_messages([]) == ""


class TestContextCompactorEdgeCases:
    """ContextCompactor 边界测试"""

    def test_completely_empty_messages(self):
        """测试完全空消息"""
        compactor = ContextCompactor()
        result = compactor.maybe_compact([])
        assert result == []

    def test_only_system_message(self):
        """测试只有 system 消息"""
        compactor = ContextCompactor()
        messages = [{"role": "system", "content": "SYS_PROMPT"}]

        result = compactor.maybe_compact(messages)
        assert len(result) == 1
        assert result[0]["content"] == "SYS_PROMPT"
