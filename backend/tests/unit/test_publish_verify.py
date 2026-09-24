# -*- coding: utf-8 -*-
"""Ship C 复验决策测试（plan C2 / C3）。

不依赖 Chromium / 真实 DB：monkeypatch _run_verification，验证 decide_verify_status
三种结论分支——
- ok         : 复验全通过
- degraded   : 复验失败 / 异常（安全默认，绝不谎报成功）
- unverified : 未配置 apex（Host 路由未启用），无法对线上 URL 复验
- 非法 slug  → 返回 None（不写库）
"""
from config import settings
from services.publish.slug import new_slug
from services.publish import verify as verify_mod


def test_verify_ok_on_pass(monkeypatch):
    monkeypatch.setattr(verify_mod, "_run_verification", lambda *a, **k: True)
    monkeypatch.setattr(settings, "PUBLISH_APEX", "publish.test")
    assert verify_mod.decide_verify_status(new_slug(), "abc") == "ok"


def test_verify_degraded_on_fail(monkeypatch):
    monkeypatch.setattr(verify_mod, "_run_verification", lambda *a, **k: False)
    monkeypatch.setattr(settings, "PUBLISH_APEX", "publish.test")
    assert verify_mod.decide_verify_status(new_slug(), "abc") == "degraded"


def test_verify_unverified_when_apex_off(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISH_APEX", "")  # Host 路由未启用
    assert verify_mod.decide_verify_status(new_slug(), "abc") == "unverified"


def test_verify_invalid_slug_none(monkeypatch):
    monkeypatch.setattr(settings, "PUBLISH_APEX", "publish.test")
    assert verify_mod.decide_verify_status("not-a-valid-slug", "abc") is None
