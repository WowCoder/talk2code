# -*- coding: utf-8 -*-
"""安全响应头的守卫测试。

★ 重点回归项：预览载荷（``/api/pt/``、``/api/preview/``）**不得**带
  ``X-Frame-Options``。

背景（2026-10-12 公网部署实测事故）
------------------------------------
服务端验收浏览器的宿主页是 `file://` 临时文件（见
`harness/tools/preview_runner._make_sandbox_wrapper_uri`），与预览地址跨源。
一旦预览响应带上 ``X-Frame-Options: SAMEORIGIN``：

1. iframe 直接**不导航**，`frame.url` 为空；
2. `_frame_load_failure` 命中「页面未导航 (url=空)」；
3. AC 逐条验收 / 冒烟 / 截图**静默回退直读工作区文件**——冒烟还会输出
   `available=True, page_loads=True`，因为它是读本地文件判的（假绿）。

实测（同一次会话内 A/B）：带该头 → iframe 地址为空、body 子元素 0；
剥掉该头 → iframe 正常加载、body 子元素 2。

预览载荷的隔离由 `routes/preview.py` 的 `CSP: sandbox allow-scripts allow-forms`
承担（唯一 opaque origin，读不到 cookie/localStorage、够不到父页），
主站与 `<slug>.<PUBLISH_APEX>` 已发布站点仍必须是 SAMEORIGIN。
"""

import pytest


class TestSecurityHeaders:
    """基础安全头 + 预览载荷豁免"""

    def test_main_app_response_uses_sameorigin(self, app_client):
        """主站响应保留 SAMEORIGIN（点击劫持防护），且 nosniff/Referrer-Policy 齐全"""
        resp = app_client.get('/api/health')
        assert resp.headers.get('X-Frame-Options') == 'SAMEORIGIN'
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
        assert (
            resp.headers.get('Referrer-Policy')
            == 'strict-origin-when-cross-origin'
        )

    @pytest.mark.parametrize(
        'path',
        [
            '/api/pt/1/deadbeefdeadbeefdeadbeefdeadbeef/index.html',
            '/api/preview/1/index.html',
        ],
    )
    def test_preview_payload_has_no_xfo(self, app_client, path):
        """★ 预览载荷必须不带 X-Frame-Options，否则服务端 iframe 验收链路静默降级"""
        resp = app_client.get(path)
        assert 'X-Frame-Options' not in resp.headers, (
            f'{path} 带上了 X-Frame-Options，会让 file:// 宿主页的 iframe 不导航，'
            f'AC/冒烟/截图全部回退直读文件（假绿）'
        )

    def test_preview_payload_keeps_other_hardening(self, app_client):
        """只豁免 XFO，其余加固照旧（nosniff）"""
        resp = app_client.get('/api/pt/1/deadbeefdeadbeefdeadbeefdeadbeef/index.html')
        assert resp.headers.get('X-Content-Type-Options') == 'nosniff'

    def test_preview_payload_isolation_comes_from_csp_sandbox(self, app_client):
        """豁免 XFO 的前提：载荷自身必须带 CSP sandbox（opaque origin）作为隔离手段"""
        from routes.preview import _preview_response

        resp = _preview_response('<html></html>', 'text/html')
        csp = resp.headers.get('Content-Security-Policy', '')
        assert 'sandbox' in csp
        # 不得放开 allow-same-origin，否则 opaque origin 失效、能读到主站存储
        assert 'allow-same-origin' not in csp

    def test_no_xfo_prefixes_match_actual_routes(self):
        """豁免前缀必须正好覆盖预览路由的两条 URL 前缀（改路由时同步改这里）"""
        from factory import _NO_XFO_PREFIXES

        assert set(_NO_XFO_PREFIXES) == {'/api/pt/', '/api/preview/'}
