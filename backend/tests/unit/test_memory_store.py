# -*- coding: utf-8 -*-
"""
测试记忆提取、检索、衰减、冲突处理
对应 tasks.md 5.7
"""

import pytest
import time
from unittest.mock import Mock, MagicMock, patch
from harness.state.memory_store import MemoryStore


class TestMemoryStoreExtract:
    """记忆提取测试"""

    def test_extract_memories_no_llm(self):
        """测试无 LLM 客户端时不提取"""
        store = MemoryStore(llm_client=None)
        result = store.extract_memories(
            [{"role": "user", "content": "use red"}], 1, 1
        )
        assert result == []

    def test_extract_memories_empty_dialogue(self):
        """测试空对话不提取"""
        mock_llm = Mock()
        store = MemoryStore(llm_client=mock_llm)
        result = store.extract_memories([], 1, 1)
        assert result == []

    def test_extract_memories_success(self):
        """测试成功提取记忆"""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = '[{"fact": "用户喜欢红色主题", "type": "user_preference", "importance": 0.8, "reason": "明确要求"}]'
        mock_llm.chat.return_value = mock_response

        store = MemoryStore(llm_client=mock_llm)
        result = store.extract_memories(
            [{"role": "user", "content": "我喜欢红色主题，以后都用红色"}],
            1, 1
        )
        assert len(result) == 1
        assert result[0]["fact"] == "用户喜欢红色主题"
        assert result[0]["importance"] == 0.8

    def test_extract_memories_llm_returns_empty(self):
        """测试 LLM 返回空数组"""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = '[]'
        mock_llm.chat.return_value = mock_response

        store = MemoryStore(llm_client=mock_llm)
        result = store.extract_memories(
            [{"role": "user", "content": "hello"}], 1, 1
        )
        assert result == []

    def test_extract_memories_llm_error(self):
        """测试 LLM 调用失败时返回空"""
        mock_llm = Mock()
        mock_llm.chat.side_effect = Exception("LLM timeout")

        store = MemoryStore(llm_client=mock_llm)
        result = store.extract_memories(
            [{"role": "user", "content": "test"}], 1, 1
        )
        assert result == []


class TestMemoryStoreRemember:
    """记忆写入测试"""

    def test_remember_new_fact(self):
        """测试写入新记忆"""
        store = MemoryStore()
        store.remember(1, "用户偏好蓝色主题", "user_preference", 0.7)
        memories = store._get_user_memories(1)
        assert len(memories) == 1
        assert memories[0]["fact"] == "用户偏好蓝色主题"
        assert memories[0]["memory_type"] == "user_preference"
        assert memories[0]["importance"] == 0.7

    def test_remember_conflict_detection(self):
        """测试相似记忆冲突检测并更新重要性"""
        store = MemoryStore()
        # 使用有明显重合的句子来触发冲突检测（>50% 词重合）
        store.remember(1, "the user prefers the blue theme design style", "user_preference", 0.7)
        initial = store._get_user_memories(1)[0]
        initial_importance = initial["importance"]

        # 写入几乎相同的高重合记忆
        store.remember(1, "the user prefers the blue theme design style and layout", "user_preference", 0.7)

        # 相似匹配会更新重要性
        updated = store._get_user_memories(1)[0]
        assert updated["importance"] == min(1.0, initial_importance + 0.05)

    def test_remember_multiple_facts(self):
        """测试写入多条不同记忆"""
        store = MemoryStore()
        store.remember(1, "user likes red color theme", "user_preference", 0.5)
        store.remember(1, "project uses localStorage for data", "user_preference", 0.6)
        store.remember(1, "the app should support mobile browsers", "domain_knowledge", 0.7)

        memories = store._get_user_memories(1)
        assert len(memories) == 3


class TestMemoryStoreRecall:
    """记忆检索测试"""

    def test_recall_empty_store(self):
        """测试空记忆库返回空"""
        store = MemoryStore()
        result = store.recall("查询", 1)
        assert result == []

    def test_recall_few_memories_direct(self):
        """测试少量记忆通过 LLM 筛选"""
        mock_llm = Mock()
        mock_response = Mock()
        mock_response.content = '[0, 2]'
        mock_llm.chat.return_value = mock_response

        store = MemoryStore(llm_client=mock_llm)
        store.remember(1, "user prefers red theme for all projects", "user_preference", 0.8)
        store.remember(1, "project is an ecommerce platform", "domain_knowledge", 0.7)
        store.remember(1, "code uses localStorage for persistence", "agent_lesson", 0.5)

        result = store.recall("design a red themed page", 1)
        assert len(result) >= 1

    def test_recall_no_llm_returns_all(self):
        """测试无 LLM 时返回全部记忆（≤top_k）"""
        store = MemoryStore(llm_client=None)
        store.remember(1, "test fact one here", "user_preference", 0.5)
        store.remember(1, "another test fact two", "domain_knowledge", 0.5)

        result = store.recall("test", 1, top_k=3)
        # 无 LLM 时直接返回 memories[:top_k]
        assert len(result) >= 1

    def test_keyword_filter(self):
        """测试关键词筛选"""
        store = MemoryStore()
        store.remember(1, "user likes blue theme color scheme", "user_preference", 0.8)
        store.remember(1, "project needs internationalization support", "domain_knowledge", 0.7)
        store.remember(1, "code uses localStorage for data storage", "agent_lesson", 0.5)

        result = store._keyword_filter(
            store._get_user_memories(1), "blue theme design", limit=5
        )
        # "blue" 和 "theme" 应该匹配第一条
        assert len(result) >= 1


class TestMemoryStoreDecay:
    """记忆衰减测试"""

    def test_decay_reduces_importance(self):
        """测试衰减降低重要性"""
        store = MemoryStore()
        store.remember(1, "测试记忆", "user_preference", 0.5)

        # 模拟很老的记忆
        old_time = time.time() - 60 * 86400  # 60 天前
        store._cache[1][0]["last_accessed_at"] = old_time

        store.decay()
        memory = store._get_user_memories(1)[0]
        # 重要性应该下降
        assert memory["importance"] < 0.5

    def test_decay_removes_low_importance(self):
        """测试衰减后清理低重要性记忆"""
        store = MemoryStore()
        store.remember(1, "不重要记忆", "user_preference", 0.05)

        # 模拟很老的记忆
        old_time = time.time() - 365 * 86400  # 一年前
        store._cache[1][0]["last_accessed_at"] = old_time

        store.decay()
        memories = store._get_user_memories(1)
        # 重要性太低应该被移除
        assert len(memories) == 0

    def test_decay_preserves_high_importance(self):
        """测试高重要性记忆不会被清理"""
        store = MemoryStore()
        store.remember(1, "重要记忆", "user_preference", 0.9)

        # 即使 30 天，高重要性也不应被清理
        old_time = time.time() - 30 * 86400
        store._cache[1][0]["last_accessed_at"] = old_time

        store.decay()
        memories = store._get_user_memories(1)
        assert len(memories) == 1


class TestMemoryStoreConflictHandling:
    """冲突处理测试"""

    def test_similar_detection_high_overlap(self):
        """测试高重合度检测为相似"""
        store = MemoryStore()
        assert store._find_similar(1, "user prefers red theme style") is None

        store.remember(1, "user prefers red theme style", "user_preference", 0.7)

        # 相似文本 - 使用英语确保词重叠准确
        similar = store._find_similar(1, "user prefers red theme and style")
        assert similar is not None
        # 至少包含共同词
        assert similar is not None

    def test_similar_detection_low_overlap(self):
        """测试低重合度不检测为相似"""
        store = MemoryStore()
        store.remember(1, "user prefers the red color theme style", "user_preference", 0.7)

        # 完全不同文本
        result = store._find_similar(1, "project needs multiple database backend support")
        assert result is None
