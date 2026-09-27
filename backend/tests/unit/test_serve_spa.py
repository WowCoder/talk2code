# -*- coding: utf-8 -*-
"""前端页面路由（serve_spa）契约测试。

两个高频踩坑点各有回归：

1. **前端路由必须回退 index.html**：服务端没有 `/detail/162` 这个文件，
   但用户刷新页面不能 404。
2. **构建产物（/assets/*）必须 404，不能回退**：产物文件名内容寻址，
   不存在就是真的不存在（通常是页面停留在旧版本、引用了上一次构建已删除的
   chunk）。若回退 index.html，浏览器会拿到 200 + text/html 当 ES module
   执行，报出

       Expected a JavaScript-or-Wasm module script but the server responded
       with a MIME type of "text/html"

   把「chunk 没了，刷新即可」伪装成 MIME 配置错误。此前就是这个行为。

用临时目录替换 SPA_DIST，避免依赖真实前端产物是否已构建。
"""
import pytest

import factory as factory_module


@pytest.fixture
def spa_dist(tmp_path, monkeypatch):
    """伪造一份最小前端产物：index.html + 一个带 hash 的 assets 文件。"""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(
        '<!doctype html><html><body><script src="/assets/app-abc123.js"></script></body></html>',
        encoding="utf-8",
    )
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)\n", encoding="utf-8")
    monkeypatch.setattr(factory_module, "SPA_DIST", str(dist))
    return dist


# ---------- 1. 前端路由：必须回退 ----------

def test_frontend_route_falls_back_to_index_html(app_client, spa_dist):
    r = app_client.get("/detail/162")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    assert "assets/app-abc123.js" in r.get_data(as_text=True)


def test_root_returns_index_html(app_client, spa_dist):
    r = app_client.get("/")
    assert r.status_code == 200
    assert r.mimetype == "text/html"


def test_unknown_api_path_returns_json_404(app_client, spa_dist):
    """API 命名空间不能被 SPA 兜底吞掉。"""
    r = app_client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert r.mimetype == "application/json"
    assert r.get_json()["error"] == "Not found"


# ---------- 2. 构建产物：未命中必须 404，且绝不回退 ----------

def test_missing_asset_returns_404_not_html(app_client, spa_dist):
    """核心回归：缺失的 chunk 必须 404，而不是 200 + index.html。

    这是 MIME 报错的根因 —— 修好前这里返回 200 + text/html。
    """
    r = app_client.get("/assets/DetailView-DELETED1.js")
    assert r.status_code == 404, "缺失产物被 SPA 兜底了（会伪装成 MIME 报错）"
    assert r.mimetype != "text/html", "不能拿 index.html 当 JS 返回"
    assert "doctype" not in r.get_data(as_text=True).lower()

    # 动态 import 失败会得到清晰的「模块取不到」，而不是令人困惑的 MIME 错误
    r2 = app_client.get("/assets/DetailView-DELETED1.js")
    assert r2.status_code == 404


def test_missing_asset_body_is_actionable(app_client, spa_dist):
    """404 的 body 要能直接告诉人怎么恢复（强制刷新）。"""
    r = app_client.get("/assets/gone-x1y2z3.js")
    body = r.get_json()
    assert "gone-x1y2z3.js" in body["error"]
    assert "强制刷新" in body["hint"]


def test_existing_asset_is_served_and_immutable(app_client, spa_dist):
    """命中的产物要可长期缓存：文件名内容寻址，不需要反复回源校验。

    `.js` 的 MIME 拼写随平台变：Linux 的 `mimetypes` 读 /etc/mime.types 得到
    `application/javascript`，macOS 走 Python 内置表得到 `text/javascript`。
    两者都是合法 JS MIME（`text/javascript` 是 WHATWG 标准，`application/*`
    是旧别名），浏览器都按脚本执行。所以只断言"确实是 JS"这一跨平台事实，
    别把某个 OS 的 mime 库输出当成契约。
    """
    r = app_client.get("/assets/app-abc123.js")
    assert r.status_code == 200
    assert r.mimetype in {"text/javascript", "application/javascript"}
    assert r.get_data(as_text=True) == "console.log(1)\n"
    assert "immutable" in r.headers.get("Cache-Control", "")
    assert "max-age=31536000" in r.headers.get("Cache-Control", "")


def test_index_html_must_revalidate(app_client, spa_dist):
    """入口 HTML 必须每次都回源校验 —— 否则会一直引用被删掉的旧 chunk。"""
    r = app_client.get("/")
    assert "no-cache" in r.headers.get("Cache-Control", "")


def test_asset_path_traversal_is_not_served(app_client, spa_dist):
    """`..` 逃逸只能得到 404，不能读到产物目录之外的文件。"""
    r = app_client.get("/assets/../../factory.py")
    assert r.status_code == 404
