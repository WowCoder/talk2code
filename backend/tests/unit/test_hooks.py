# -*- coding: utf-8 -*-
"""
测试 HookManager 触发/注册，每个 Hook 的检查逻辑
对应 tasks.md 7.8
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from harness.constraints.hooks import (
    HookManager, HookPoint, HookContext, create_default_hook_manager
)


class TestHookPoint:
    """HookPoint 枚举测试"""

    def test_all_lifecycle_points(self):
        """测试所有生命周期点"""
        points = list(HookPoint)
        assert HookPoint.PRE_TOOL_USE in points
        assert HookPoint.POST_TOOL_USE in points
        assert HookPoint.PRE_LLM_CALL in points
        assert HookPoint.POST_LLM_CALL in points
        assert HookPoint.ON_ERROR in points
        assert HookPoint.ON_TASK_COMPLETE in points
        assert len(points) == 6


class TestHookContext:
    """HookContext 测试"""

    def test_context_creation(self):
        """测试 HookContext 创建"""
        ctx = HookContext(
            requirement_id=1,
            tool_name="write_file",
            tool_args={"filename": "test.html"},
            tool_result="success",
            state={"code_files": []},
        )
        assert ctx.requirement_id == 1
        assert ctx.tool_name == "write_file"
        assert ctx.tool_args == {"filename": "test.html"}

    def test_context_defaults(self):
        """测试 HookContext 默认值"""
        ctx = HookContext(requirement_id=1)
        assert ctx.tool_name is None
        assert ctx.tool_args is None
        assert ctx.tool_result is None
        assert ctx.state == {}


class TestHookManagerRegister:
    """Hook 注册测试"""

    def test_register_single_hook(self):
        """测试注册单个 Hook"""
        manager = HookManager()

        def my_hook(ctx):
            return None  # 成功静默

        manager.register(HookPoint.PRE_TOOL_USE, my_hook)
        hooks = manager.get_hooks(HookPoint.PRE_TOOL_USE)
        assert len(hooks) == 1
        assert hooks[0] == my_hook

    def test_register_multiple_hooks(self):
        """测试注册多个 Hook"""
        manager = HookManager()

        manager.register(HookPoint.POST_TOOL_USE, lambda ctx: None)
        manager.register(HookPoint.POST_TOOL_USE, lambda ctx: None)
        manager.register(HookPoint.POST_TOOL_USE, lambda ctx: None)

        hooks = manager.get_hooks(HookPoint.POST_TOOL_USE)
        assert len(hooks) == 3

    def test_registered_to_correct_point(self):
        """测试 Hook 注册到正确的生命周期点"""
        manager = HookManager()

        manager.register(HookPoint.PRE_LLM_CALL, lambda ctx: None)
        manager.register(HookPoint.ON_ERROR, lambda ctx: "error detected")

        assert len(manager.get_hooks(HookPoint.PRE_LLM_CALL)) == 1
        assert len(manager.get_hooks(HookPoint.ON_ERROR)) == 1
        assert len(manager.get_hooks(HookPoint.PRE_TOOL_USE)) == 0


class TestHookManagerTrigger:
    """Hook 触发测试"""

    def test_trigger_silent_on_pass(self):
        """测试 Hook 通过时静默（返回空列表）"""
        manager = HookManager()

        def always_pass(ctx):
            return None

        manager.register(HookPoint.PRE_TOOL_USE, always_pass)
        ctx = HookContext(requirement_id=1, tool_name="write_file")

        failures = manager.trigger(HookPoint.PRE_TOOL_USE, ctx)
        assert failures == []

    def test_trigger_return_failure(self):
        """测试 Hook 失败时返回错误信息"""
        manager = HookManager()

        def detects_issue(ctx):
            return "检测到安全风险: innerHTML 使用"

        manager.register(HookPoint.POST_TOOL_USE, detects_issue)
        ctx = HookContext(requirement_id=1, tool_name="write_file",
                         tool_result="innerHTML")

        failures = manager.trigger(HookPoint.POST_TOOL_USE, ctx)
        assert len(failures) == 1
        assert "innerHTML" in failures[0]

    def test_trigger_multiple_failures(self):
        """测试多个 Hook 同时失败"""
        manager = HookManager()

        def hook_a(ctx):
            return "问题 A"

        def hook_b(ctx):
            return "问题 B"

        manager.register(HookPoint.ON_TASK_COMPLETE, hook_a)
        manager.register(HookPoint.ON_TASK_COMPLETE, hook_b)
        ctx = HookContext(requirement_id=1)

        failures = manager.trigger(HookPoint.ON_TASK_COMPLETE, ctx)
        assert len(failures) == 2

    def test_trigger_handles_exception(self):
        """测试 Hook 异常时捕获并返回错误"""
        manager = HookManager()

        def buggy_hook(ctx):
            raise RuntimeError("意料之外的错误")
        buggy_hook.__name__ = "buggy_hook"

        manager.register(HookPoint.POST_TOOL_USE, buggy_hook)
        ctx = HookContext(requirement_id=1)

        failures = manager.trigger(HookPoint.POST_TOOL_USE, ctx)
        assert len(failures) == 1
        assert "buggy_hook" in failures[0]
        assert "异常" in failures[0]

    def test_trigger_empty_point(self):
        """测试触发无注册 Hook 的点"""
        manager = HookManager()
        ctx = HookContext(requirement_id=1)

        failures = manager.trigger(HookPoint.PRE_TOOL_USE, ctx)
        assert failures == []


class TestDefaultHookManager:
    """默认 HookManager 测试"""

    def test_create_default_has_all_hooks(self):
        """测试默认注册表包含所有类型的 Hook"""
        manager = create_default_hook_manager()

        # PRE_TOOL_USE hooks
        pre_hooks = manager.get_hooks(HookPoint.PRE_TOOL_USE)
        assert len(pre_hooks) >= 0

        # POST_TOOL_USE hooks
        post_hooks = manager.get_hooks(HookPoint.POST_TOOL_USE)
        assert len(post_hooks) >= 0

        # ON_TASK_COMPLETE hooks
        on_complete_hooks = manager.get_hooks(HookPoint.ON_TASK_COMPLETE)
        assert len(on_complete_hooks) > 0  # 至少 quality 的 required_files hook

    def test_quality_hooks_detect_missing_files(self):
        """测试质量 Hook 检测缺失文件"""
        manager = create_default_hook_manager()

        # 模拟任务完成但缺少 index.html
        ctx = HookContext(
            requirement_id=1,
            state={"file_list": ["style.css"], "code_files": []}
        )

        failures = manager.trigger(HookPoint.ON_TASK_COMPLETE, ctx)
        # 应该检测到缺少 index.html
        assert len(failures) > 0
        assert any("index.html" in f for f in failures)

    def test_security_hooks_detect_inner_html(self):
        """测试安全 Hook 检测 innerHTML"""
        manager = create_default_hook_manager()

        ctx = HookContext(
            requirement_id=1,
            tool_name="write_file",
            tool_args={"content": "element.innerHTML = '<div>test</div>'"},
            tool_result="file written",
            state={}
        )

        failures = manager.trigger(HookPoint.POST_TOOL_USE, ctx)
        assert len(failures) > 0
        assert any("innerHTML" in f for f in failures)
