# -*- coding: utf-8 -*-
"""
规则化 lesson 兜底测试（req 147 复盘回归）

req 147 连跑多轮「1024 小游戏」，但 `agent_memories_v2` 里三条相关记忆的 lesson
全为空（反思 LLM 调用失败却照样入库），检索时被 `_has_injectable_content` 过滤，
等于系统从不积累经验 —— 下一次重跑从零开始。

修法分两步，这里锁死第二步：`after_task` 在拿不到 LLM 教训时不直接 return，
而是用确定性规则从**实测证据**抽结构化 lesson。它不漂亮，但不会臆造根因。
"""

import threading

import pytest

from harness.state.memory import (
    MemoryManager,
    _build_rule_based_reflection,
    _reflection_has_content,
)


FAILURE_CTX = (
    "- js/game.js: Uncaught TypeError: this._updateScoreDisplay "
    "is not a function (pageerror)"
)


class TestBuildRuleBasedReflection:
    def test_failure_lesson_contains_structured_fields(self):
        data = _build_rule_based_reflection(
            "做一个1024小游戏", "js/game.js 等 4 个文件", 4.8, FAILURE_CTX, True
        )
        assert _reflection_has_content(data), "规则化结果必须可注入，否则兜底没意义"
        lesson = data["lesson"]
        assert "TypeError" in lesson
        assert "js/game.js" in lesson
        assert "_updateScoreDisplay" in lesson
        assert "规则抽取" in lesson
        assert "rule_based_lesson" in data["tags"]
        assert "failure" in data["tags"]

    def test_failure_lesson_keeps_raw_evidence(self):
        """归因不可信时，原始证据必须一并留下，供下一轮 LLM 自行判断"""
        data = _build_rule_based_reflection(
            "x", "summary", 3.0, FAILURE_CTX, True
        )
        assert "this._updateScoreDisplay" in data["lesson"]

    def test_success_lesson_records_implementation_shape(self):
        data = _build_rule_based_reflection(
            "做一个贪吃蛇", "<canvas id=game> + js/snake.js", 8.2, "", False
        )
        assert _reflection_has_content(data)
        assert "canvas" in data["lesson"].lower()
        assert "failure" not in data["tags"]

    def test_empty_context_still_yields_injectable_lesson(self):
        """没有失败证据（成功任务）时也不能产出空记忆"""
        data = _build_rule_based_reflection("任意需求", "index.html", 7.0, "", False)
        assert _reflection_has_content(data)


class TestAfterTaskFallback:
    """after_task 在 LLM 反思不可用时必须降级入库，而不是静默丢弃"""

    @staticmethod
    def _bare_manager(monkeypatch):
        mgr = MemoryManager.__new__(MemoryManager)  # 绕过 __init__（避免连 DB）
        mgr._llm = None
        mgr._lock = threading.Lock()
        mgr._new_since_consolidate = 0
        mgr._indexed_memories = None
        stored = []

        def _store(_self, memory):
            stored.append(memory)

        monkeypatch.setattr(MemoryManager, "_store", _store, raising=True)
        monkeypatch.setattr(MemoryManager, "_maintain", lambda self: None, raising=True)
        return mgr, stored

    def test_no_llm_still_stores_rule_based_memory(self, monkeypatch):
        mgr, stored = self._bare_manager(monkeypatch)
        mgr.after_task(
            "做一个1024小游戏", "M",
            [{"filename": "js/game.js", "content": "function move(){}"}],
            qa_result={"passed": False, "score": 4.8,
                       "critical_issues": [FAILURE_CTX]},
            user_id=1,
        )
        assert len(stored) == 1, "反思 LLM 不可用时也必须留下一条可注入记忆"
        mem = stored[0]
        assert mem.lesson.strip(), "lesson 不能为空"
        assert "rule_based_lesson" in mem.tags
        assert "_updateScoreDisplay" in mem.lesson

    def test_reflection_failure_falls_back_instead_of_dropping(self, monkeypatch):
        """LLM 存在但两次反思都失败 → 降级，不是 return"""
        mgr, stored = self._bare_manager(monkeypatch)

        class _BrokenLLM:
            def chat(self, *a, **kw):
                raise RuntimeError("API timeout")

        mgr._llm = _BrokenLLM()
        monkeypatch.setattr(
            MemoryManager, "_reflect", lambda self, *a, **kw: {}, raising=True
        )
        mgr.after_task(
            "做一个2048游戏", "M",
            [{"filename": "js/game.js", "content": "function move(){}"}],
            qa_result={"passed": False, "score": 5.0,
                       "critical_issues": ["- TypeError: x is not a function"]},
            user_id=1,
        )
        assert len(stored) == 1
        assert stored[0].lesson.strip()
        assert "rule_based_lesson" in stored[0].tags


@pytest.mark.parametrize("bad", [{}, {"lesson": ""}, {"lesson": "无", "reusable_pattern": "无"}])
def test_reflection_has_content_rejects_empty(bad):
    assert _reflection_has_content(bad) is False
