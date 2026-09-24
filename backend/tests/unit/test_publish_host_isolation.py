# -*- coding: utf-8 -*-
"""发布域名（子域形态）的隔离与可用性守卫。

已发布站点跑在 <slug>.<PUBLISH_APEX>，与主站同属一个可注册域。以下四条
约束都是「破了就出事、且出事后很难复盘」的类型，用测试锁死：

1. 主站子域不被劫持 —— www.<apex> 也以 .<apex> 结尾，Host 路由必须只接管
   slug 形态的子域，否则配置 PUBLISH_APEX 之后主站会直接 404
2. 主站 cookie 必须 host-only —— 否则已发布子域上的生成代码能读走登录态
3. 发布域名请求豁免默认限流 —— 否则多资源页面刷新几次就被 429 打爆
4. apex 未配置时接口必须显式告警 —— 不得静默返回一个打不开的链接
"""
import shutil
import tempfile
from pathlib import Path

import pytest
from config import settings
from models import PublishedSite, Requirement, SessionLocal, User

from services.publish import verify as verify_mod
from services.publish.slug import is_valid_slug, new_slug
from utils.rate_limiter import is_published_site_request


APEX = "wowcoder.cn"

SAMPLE_FILES = [
    {"filename": "index.html", "content": "<html><body>hi</body></html>"},
    {"filename": "app.js", "content": "console.log(1)"},
]


def _fresh_requirement(code_files=SAMPLE_FILES) -> int:
    """建一条属于 test_func 的需求，并清掉它可能残留的发布记录。

    ⚠️ 测试库是**文件库**（models 在模块导入期就绑定了 engine，早于
    app_client 设 DATABASE_NAME=':memory:'），数据跨运行保留。若不清残留，
    `PublishService.publish` 的内容幂等早返回会带出上一次运行的 verify_status，
    让断言读到过期状态。
    """
    db = SessionLocal()
    user = db.query(User).filter(User.username == "test_func").first()
    req = Requirement(
        user_id=user.id, title="demo", content="x",
        status="finished", code_files=code_files,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    db.query(PublishedSite).filter_by(requirement_id=req.id).delete()
    db.commit()
    req_id = req.id
    db.close()
    return req_id


@pytest.fixture
def host_env(app_client, auth_token, monkeypatch):
    """发布环境：apex 已配、产物落到临时目录、复验避开 Chromium。

    显式钉住 scheme/port —— `.env` 里本地开发是 http+5001（生产是 https+空），
    不钉住断言会随开发者的 .env 变化，失去确定性。
    """
    d = tempfile.mkdtemp(dir=Path(__file__).parent)
    monkeypatch.setattr(settings, "PUBLISH_APEX", APEX)
    monkeypatch.setattr(settings, "PUBLISH_URL_SCHEME", "https")
    monkeypatch.setattr(settings, "PUBLISH_URL_PORT", "")
    monkeypatch.setattr(settings, "PUBLISH_STORE_DIR", str(Path(d) / "published"))
    # 复验在线上是后台线程（发布接口不等它）；单测要断言响应体里的
    # verify_status，故走同步路径 —— 与线上共用同一份 decide/write 逻辑。
    monkeypatch.setattr(settings, "PUBLISH_VERIFY_ASYNC", False)
    # 真浏览器复验需要能访问 <slug>.<apex>，单测里不存在，故直接放行
    monkeypatch.setattr(verify_mod, "_run_verification", lambda *a, **k: True)

    try:
        yield app_client, _fresh_requirement()
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------- 1. 主站子域不被劫持 ----------

def test_main_site_www_is_not_hijacked(host_env):
    """www.<apex> 是主站，不能被 Host 路由当成发布站点打成 404。"""
    app_client, _ = host_env
    resp = app_client.get("/", headers={"Host": f"www.{APEX}"})
    # 200=返回主站 SPA；503=前端未构建。两者都说明请求交回了正常路由。
    assert resp.status_code != 404, "主站 www 子域被发布路由劫持了"

    # apex 自身（不带子域）同样属于主站
    assert app_client.get("/", headers={"Host": APEX}).status_code != 404


def test_non_slug_subdomain_falls_through(host_env):
    """常规子域（api / blog / 多级）一律交回正常路由，不接管。"""
    app_client, _ = host_env
    for host in (f"api.{APEX}", f"blog.{APEX}", f"a.b.{APEX}"):
        assert app_client.get("/", headers={"Host": host}).status_code != 404, host


def test_unknown_but_valid_slug_still_404(host_env):
    """slug 形态但对不上任何站点 → 仍然是 404（没有放宽归属校验）。"""
    app_client, _ = host_env
    missing = new_slug()
    assert is_valid_slug(missing)
    resp = app_client.get("/", headers={"Host": f"{missing}.{APEX}"})
    assert resp.status_code == 404


def test_published_slug_is_served_without_login(host_env):
    """已发布站点免登录直开，并带 noindex —— 本次修复的核心诉求。"""
    app_client, req_id = host_env
    body = app_client.post("/api/publish", json={"requirement_id": req_id}).get_json()
    slug = body["slug"]

    resp = app_client.get("/", headers={"Host": f"{slug}.{APEX}"})
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == SAMPLE_FILES[0]["content"]
    assert resp.headers.get("X-Robots-Tag") == "noindex"
    # 不设 CSP sandbox：否则 localStorage 抛 SecurityError
    assert "sandbox" not in (resp.headers.get("Content-Security-Policy") or "")


def test_served_site_blocks_internal_paths(host_env):
    """历史产物里已存在的 .task/ 必须挡住 —— 打包侧过滤只对新发布生效。"""
    app_client, req_id = host_env
    body = app_client.post("/api/publish", json={"requirement_id": req_id}).get_json()
    slug, content_hash = body["slug"], body["current_hash"]

    # 手工往该 bundle 里塞一个内部文件，模拟修复前发布的产物
    target = Path(settings.PUBLISH_STORE_PATH) / content_hash[:2] / content_hash \
        / ".task" / "TASK_STATE.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("内部状态", encoding="utf-8")

    headers = {"Host": f"{slug}.{APEX}"}
    assert app_client.get("/.task/TASK_STATE.md", headers=headers).status_code == 404
    # 正常资源不受影响
    assert app_client.get("/index.html", headers=headers).status_code == 200


# ---------- 2. 主站 cookie 必须 host-only ----------

def test_jwt_cookie_is_host_only(app_client):
    """红线：JWT_COOKIE_DOMAIN 必须为空，cookie 只在主站主机名生效。

    设成 '.wowcoder.cn' 会让任意已发布子域（承载不可信的 AI 生成代码）
    读到登录态。
    """
    import app as app_module
    assert not app_module.app.config.get("JWT_COOKIE_DOMAIN"), (
        "JWT_COOKIE_DOMAIN 被设置后，已发布子域上的生成代码即可读取主站登录态"
    )


# ---------- 3. 发布域名豁免默认限流 ----------

@pytest.mark.parametrize("host,expected", [
    ("N4Z3XVYEFRJEJ28E52WY.wowcoder.cn", True),
    ("n4z3xvyefrjej28e52wy.wowcoder.cn", True),      # 大小写不敏感
    ("N4Z3XVYEFRJEJ28E52WY.wowcoder.cn:443", True),  # 带端口
    ("wowcoder.cn", False),                          # apex 自身 = 主站
    ("www.wowcoder.cn", False),                      # 主站子域
    ("api.wowcoder.cn", False),                      # 常规子域
    ("www.wowcoder.cn.evil.com", False),             # 后缀伪装
    ("evilwowcoder.cn", False),
])
def test_rate_limit_exemption_only_for_published_host(app_client, monkeypatch, host, expected):
    import app as app_module
    monkeypatch.setattr(settings, "PUBLISH_APEX", APEX)
    with app_module.app.test_request_context("/", headers={"Host": host}):
        assert is_published_site_request() is expected, host


def test_rate_limit_exemption_disabled_without_apex(app_client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(settings, "PUBLISH_APEX", "")
    with app_module.app.test_request_context(
        "/", headers={"Host": f"N4Z3XVYEFRJEJ28E52WY.{APEX}"}
    ):
        assert is_published_site_request() is False


# ---------- 4. apex 未配置时必须显式告警 ----------

def test_publish_warns_when_apex_missing(app_client, auth_token, monkeypatch):
    """未配置发布域名时：产物照常落盘，但 url 为空且必须给出 warnings。

    此前这里静默返回 url=null，前端再自拼 nip.io 兜底，用户点开落到主站
    SPA（要登录 + 首页）—— 就是「一键发布打开是项目主页」的根因。
    """
    d = tempfile.mkdtemp(dir=Path(__file__).parent)
    monkeypatch.setattr(settings, "PUBLISH_APEX", "")
    monkeypatch.setattr(settings, "PUBLISH_STORE_DIR", str(Path(d) / "published"))
    # 同上：断言响应体 → 走同步复验路径
    monkeypatch.setattr(settings, "PUBLISH_VERIFY_ASYNC", False)

    req_id = _fresh_requirement()
    try:
        resp = app_client.post("/api/publish", json={"requirement_id": req_id})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["url"] is None
        assert body["published_host"] is None
        assert body["warnings"], "缺 apex 时必须显式告警，不能静默成功"
        assert body["verify_status"] == "unverified"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_publish_no_warning_when_apex_configured(host_env):
    """反例：配了 apex 就不该有告警，且 url 是该 slug 的 https 地址。"""
    app_client, req_id = host_env
    body = app_client.post("/api/publish", json={"requirement_id": req_id}).get_json()
    assert body["warnings"] == []
    assert body["url"] == f"https://{body['slug']}.{APEX}"


def test_local_dev_url_uses_scheme_and_port(app_client, auth_token, monkeypatch):
    """本地开发形态：http + 端口，apex=localhost。

    *.localhost 由系统解析到 127.0.0.1（macOS/Chrome 均支持），因此本地
    调试不需要任何外部通配 DNS。此前链接被硬编码成 https 且不带端口，
    本地永远拼不出可用地址。
    """
    d = tempfile.mkdtemp(dir=Path(__file__).parent)
    monkeypatch.setattr(settings, "PUBLISH_APEX", "localhost")
    monkeypatch.setattr(settings, "PUBLISH_URL_SCHEME", "http")
    monkeypatch.setattr(settings, "PUBLISH_URL_PORT", "5001")
    monkeypatch.setattr(settings, "PUBLISH_STORE_DIR", str(Path(d) / "published"))
    monkeypatch.setattr(verify_mod, "_run_verification", lambda *a, **k: True)

    try:
        body = app_client.post(
            "/api/publish", json={"requirement_id": _fresh_requirement()}
        ).get_json()
        assert body["url"] == f"http://{body['slug']}.localhost:5001"
        assert body["warnings"] == []

        # 该形态下 Host 路由同样生效（免登录直开）
        resp = app_client.get("/", headers={"Host": f"{body['slug']}.localhost:5001"})
        assert resp.status_code == 200
        assert resp.get_data(as_text=True) == SAMPLE_FILES[0]["content"]
    finally:
        shutil.rmtree(d, ignore_errors=True)
