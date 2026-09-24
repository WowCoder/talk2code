# -*- coding: utf-8 -*-
"""Ship C 复验决策测试（plan C2 / C3）。

不依赖 Chromium / 真实 DB：monkeypatch _run_verification，验证 decide_verify_status
三种结论分支——
- ok         : 复验全通过
- degraded   : 复验失败 / 异常（安全默认，绝不谎报成功）
- unverified : 未配置 apex（Host 路由未启用），无法对线上 URL 复验
- 非法 slug  → 返回 None（不写库）

另含两条回归（都是「静默判 degraded / 静默打不开」型缺陷）：
- 复验 URL 必须与展示链接同源（协议/端口来自配置），不得硬编码 https
- 复验必须能取到站点当前的 bundle hash（历史缺陷：调用了不存在的
  ``_current_hash()``，NameError 被 except 吞掉 → 每次复验恒判 degraded）
"""
import shutil
import tempfile
from pathlib import Path

import pytest

from config import settings
from services.publish.slug import new_slug
from services.publish.store import LocalFSStore
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


# ---------------------------------------------------------------------------
# 回归 1：复验 URL 必须走配置（协议 + 端口），不得硬编码 https://<slug>.<apex>
# ---------------------------------------------------------------------------
def test_verify_targets_configured_scheme_and_port(monkeypatch):
    """本地开发（http + 后端端口）时，复验的 URL 必须带协议与端口。

    历史缺陷：decide_verify_status 里写死 ``f"https://{slug}.{apex}"``，本地
    拼出 ``https://x.localhost``（无端口、错协议）→ 必然打不开 → 恒判 degraded。
    """
    seen = {}

    def _capture(url, slug, user_id, requirement_id, content_hash=None):
        seen["url"] = url
        return True

    slug = new_slug()
    monkeypatch.setattr(verify_mod, "_run_verification", _capture)
    monkeypatch.setattr(settings, "PUBLISH_APEX", "localhost")
    monkeypatch.setattr(settings, "PUBLISH_URL_SCHEME", "http")
    monkeypatch.setattr(settings, "PUBLISH_URL_PORT", "5001")

    assert verify_mod.decide_verify_status(slug, "abc") == "ok"
    assert seen["url"] == f"http://{slug}.localhost:5001"


def test_verify_url_omits_port_in_production(monkeypatch):
    """生产形态：https 且不带端口（nginx 终结 TLS）。"""
    seen = {}

    def _capture(url, slug, user_id, requirement_id, content_hash=None):
        seen["url"] = url
        return True

    slug = new_slug()
    monkeypatch.setattr(verify_mod, "_run_verification", _capture)
    monkeypatch.setattr(settings, "PUBLISH_APEX", "wowcoder.cn")
    monkeypatch.setattr(settings, "PUBLISH_URL_SCHEME", "https")
    monkeypatch.setattr(settings, "PUBLISH_URL_PORT", "")

    assert verify_mod.decide_verify_status(slug, "abc") == "ok"
    assert seen["url"] == f"https://{slug}.wowcoder.cn"


# ---------------------------------------------------------------------------
# 回归 2：复验必须能取到站点当前 bundle 的 index.html
#   历史缺陷：_load_published_index 调用了不存在的 _current_hash()，NameError
#   被 _run_verification 的 except 吞成 warning → 每次复验恒判 degraded。
# ---------------------------------------------------------------------------
@pytest.fixture
def store_dir(monkeypatch):
    d = Path(tempfile.mkdtemp(dir=Path(__file__).parent))
    monkeypatch.setattr(settings, "PUBLISH_STORE_DIR", str(d / "published"))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_load_published_index_reads_current_bundle(monkeypatch, store_dir):
    slug = new_slug()
    content_hash = "a" * 64
    html = b"<html><body>published</body></html>"
    LocalFSStore().put_bundle(content_hash, {"index.html": html})
    monkeypatch.setattr(verify_mod, "_current_hash_for_slug", lambda s: content_hash)

    path = verify_mod._load_published_index(slug)
    try:
        assert path is not None
        assert path.read_bytes() == html
    finally:
        if path:
            path.unlink(missing_ok=True)


def test_load_published_index_none_when_hash_unknown(monkeypatch, store_dir):
    monkeypatch.setattr(verify_mod, "_current_hash_for_slug", lambda s: None)
    assert verify_mod._load_published_index(new_slug()) is None


def test_current_hash_for_slug_unknown_slug_is_none():
    """真库查询形状：不存在的 slug 返回 None（而不是抛异常）。"""
    assert verify_mod._current_hash_for_slug(new_slug()) is None


def test_run_verification_does_not_swallow_nameerror(monkeypatch, store_dir):
    """冒烟/AC 之外，取产物这一步不得因内部符号缺失而整段失败。

    这是上面那个缺陷的端到端形态：修好之前 _run_verification 恒返回 False。
    """
    slug = new_slug()
    content_hash = "b" * 64
    LocalFSStore().put_bundle(
        content_hash, {"index.html": b"<html><body>x</body></html>"}
    )
    monkeypatch.setattr(verify_mod, "_current_hash_for_slug", lambda s: content_hash)
    monkeypatch.setattr(verify_mod, "_load_ac_scripts", lambda uid, rid: [])
    monkeypatch.setattr(verify_mod, "_check_same_origin", lambda url: True)

    import harness.tools.preview_runner as pr
    monkeypatch.setattr(pr, "run_universal_smoke", lambda *a, **k: {"available": True})

    assert verify_mod._run_verification(
        f"http://{slug}.localhost:5001", slug, None, None, content_hash
    ) is True


# ---------------------------------------------------------------------------
# 回归 3：AC 脚本缓存必须按「验收同源」的路径与形状读取
#   历史缺陷：import 了一个不存在的 services.workspace_service，且把
#   {"ac_hash","scripts"} 当裸数组返回 —— 结果是 AC 永远被静默跳过，
#   复验退化成「只跑冒烟」，覆盖面比宣称的窄。
# ---------------------------------------------------------------------------
def test_load_ac_scripts_reads_cache_via_workspace(tmp_path, monkeypatch):
    from harness.state.workspace import WorkspaceFS

    user_id, requirement_id = 987654, 987655
    ws_dir = tmp_path / str(user_id) / str(requirement_id)
    (ws_dir / ".task").mkdir(parents=True)
    (ws_dir / ".task" / "ac_scripts.json").write_text(
        '{"ac_hash": "x", "scripts": [{"ac_id": "AC1", "steps": []}]}'
    )
    monkeypatch.setattr(
        WorkspaceFS, "_get_base_dir", staticmethod(lambda: tmp_path)
    )

    out = verify_mod._load_ac_scripts(user_id, requirement_id)
    assert isinstance(out, list) and out and out[0]["ac_id"] == "AC1"


def test_load_ac_scripts_tolerates_bad_cache(tmp_path, monkeypatch):
    """缓存是裸数组 / 坏 JSON → 返回空列表（跳过 AC），不得抛异常。"""
    from harness.state.workspace import WorkspaceFS

    user_id, requirement_id = 987656, 987657
    ws_dir = tmp_path / str(user_id) / str(requirement_id)
    (ws_dir / ".task").mkdir(parents=True)
    monkeypatch.setattr(
        WorkspaceFS, "_get_base_dir", staticmethod(lambda: tmp_path)
    )

    cache = ws_dir / ".task" / "ac_scripts.json"
    cache.write_text('[{"ac_id": "AC1"}]')          # 旧形状：裸数组
    assert verify_mod._load_ac_scripts(user_id, requirement_id) == []

    cache.write_text('not json')                      # 坏内容
    assert verify_mod._load_ac_scripts(user_id, requirement_id) == []

    cache.unlink()                                    # 不存在
    assert verify_mod._load_ac_scripts(user_id, requirement_id) == []


def test_load_ac_scripts_empty_without_ids():
    assert verify_mod._load_ac_scripts(None, None) == []
    assert verify_mod._load_ac_scripts(1, None) == []
