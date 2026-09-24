"""Ship A（运行时沙箱加固）的 TDD 测试。

A1–A7 + A9b（结构性）：纯单测，不依赖真实 Chromium，整份 < 1s。
A9b（真出口自检）/ A10：真实 Chromium 集成测试，标记 ``@pytest.mark.slow``，
不进默认 CI（需要装了 Chromium 的机器）。
"""
from __future__ import annotations

import threading
import time

import pytest

from harness.tools.sandboxed_browser import (
    BrowserWatchdogTimeout,
    build_chromium_args,
    sandboxed_browser,
)


# ---------------------------------------------------------------------------
# A1（RED→GREEN）：Chromium 启动参数
# ---------------------------------------------------------------------------
def test_chromium_args_deny_all_egress():
    args = build_chromium_args(allow_hosts=())
    assert any("MAP * ~NOTFOUND" in a for a in args)
    assert any(a.startswith("--proxy-server=") for a in args)


# ---------------------------------------------------------------------------
# A2（反向断言）：绝不能出现 <-loopback>（减法语义会掐掉 127.0.0.1 预览链路）
# ---------------------------------------------------------------------------
def test_no_loopback_subtraction_token():
    args = build_chromium_args(allow_hosts=())
    joined = " ".join(args)
    assert "<-loopback>" not in joined


# ---------------------------------------------------------------------------
# A3（RED→GREEN）：allow_hosts 端口白名单必须能放行
# ---------------------------------------------------------------------------
def test_allow_hosts_adds_exclude_whitelist():
    args = build_chromium_args(allow_hosts=("198.18.0.1",))
    resolver = next(a for a in args if a.startswith("--host-resolver-rules="))
    assert "EXCLUDE 198.18.0.1" in resolver
    # 白名单必须以「例外」身份出现在全拒 MAP 之后，而不是被 NOTFOUND 兜底覆盖
    assert resolver.index("MAP * ~NOTFOUND") < resolver.index("EXCLUDE 198.18.0.1")


def test_allow_hosts_also_bypasses_proxy():
    """放行必须同时体现在代理 bypass 上。

    历史缺陷：allow_hosts 只写进 host-resolver-rules，没进 proxy-bypass-list。
    结果是名字能解析、请求仍被送进黑洞代理（127.0.0.1:9）→
    ERR_PROXY_CONNECTION_FAILED，「放行」形同虚设。
    """
    args = build_chromium_args(allow_hosts=("site.example.com",))
    bypass = next(a for a in args if a.startswith("--proxy-bypass-list="))
    assert "site.example.com" in bypass
    # 反向：默认 loopback 白名单不能被 allow_hosts 分支挤掉
    assert "127.0.0.1" in bypass
    assert "localhost" in bypass
    assert "<-loopback>" not in bypass


# ---------------------------------------------------------------------------
# A4 的同文件回归断言（空 allow_hosts 必须放行 localhost）
# ---------------------------------------------------------------------------
def test_empty_allow_hosts_excludes_localhost():
    args = build_chromium_args(allow_hosts=())
    resolver = next(a for a in args if a.startswith("--host-resolver-rules="))
    # 数字 IP 也必须被豁免，否则预览 URL（http://127.0.0.1:5001）会被 MAP * 掐断
    assert "EXCLUDE 127.0.0.1" in resolver
    assert "EXCLUDE localhost" in resolver


# ---------------------------------------------------------------------------
# A5 + A6（RED→GREEN）：上下文管理器必定关闭浏览器（正常 / 异常两次都只关一次）
# ---------------------------------------------------------------------------
class FakeBrowser:
    def __init__(self) -> None:
        self.close_calls = 0
        self._closed = False
        self._close_event = threading.Event()

    def new_page(self):
        # 模拟一个会阻塞的 Playwright 操作；watchdog 超时 close 后应当被中断
        self._close_event.wait(timeout=0.2)  # 等 200ms 或直到被 close
        if self._closed:
            raise BrowserWatchdogTimeout("interrupted by watchdog")
        return object()

    def close(self) -> None:
        if not self._closed:
            self.close_calls += 1
            self._closed = True
        self._close_event.set()


def test_context_manager_closes_browser_once():
    fb = FakeBrowser()
    with sandboxed_browser(launcher=lambda: fb):
        pass
    assert fb.close_calls == 1


def test_context_manager_closes_on_inner_exception():
    fb = FakeBrowser()
    with pytest.raises(ValueError):
        with sandboxed_browser(launcher=lambda: fb):
            raise ValueError("boom")
    assert fb.close_calls == 1


# ---------------------------------------------------------------------------
# A7（RED→GREEN）：wall-clock watchdog 中断超时块
# ---------------------------------------------------------------------------
def test_watchdog_interrupts_long_block():
    fb = FakeBrowser()
    t0 = time.monotonic()
    with pytest.raises(BrowserWatchdogTimeout):
        with sandboxed_browser(launcher=lambda: fb, timeout_ms=50):
            fb.new_page()  # 睡 200ms；watchdog 50ms 触发 close → 抛 BrowserWatchdogTimeout
    elapsed = time.monotonic() - t0
    assert elapsed < 0.2  # 必须在约 75ms 内被中断，不能等到 new_page 自然返回
    assert fb.close_calls == 1


# ---------------------------------------------------------------------------
# A9b（结构性，快）：loopback 可达 + 外网全拒 的静态保证
# ---------------------------------------------------------------------------
def test_loopback_reachable_structural():
    args = build_chromium_args()
    bypass = next(a for a in args if a.startswith("--proxy-bypass-list="))
    assert "127.0.0.1" in bypass
    assert "localhost" in bypass
    assert "<-loopback>" not in bypass  # 反向：不能减法
    resolver = next(a for a in args if a.startswith("--host-resolver-rules="))
    assert "MAP * ~NOTFOUND" in resolver
    # 数字 IP 必须显式豁免，否则会被 MAP * 解析成 NOTFOUND（静默降级根因）
    assert "EXCLUDE 127.0.0.1" in resolver


# ---------------------------------------------------------------------------
# A9b（真出口自检）/ A10：真实 Chromium 集成测试，标记 slow
# 关键断言：loopback 可达 且 外网不可达（两者必须同时成立）
# ---------------------------------------------------------------------------
@pytest.mark.slow
def test_egress_blocks_external_but_allows_loopback():
    import http.server
    import socketserver
    from urllib.parse import urlparse

    # 起一个仅监听 127.0.0.1 的本地服务，作为「预览链路」替身
    handler = http.server.SimpleHTTPRequestHandler
    httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    serve_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    serve_thread.start()

    from playwright.sync_api import sync_playwright

    try:
        with sandboxed_browser(timeout_ms=15_000) as browser:
            context = browser.new_context()
            page = context.new_page()
            # 1) loopback 必须可达（否则预览链路静默降级）
            resp = page.goto(f"http://127.0.0.1:{port}/", timeout=10_000)
            assert resp is not None and resp.status == 200
            # 2) 外网必须不可达（数据外带 / SSRF 防护）
            result = page.evaluate(
                "fetch('http://example.com').then(r=>'ok').catch(e=>'blocked')"
            )
            assert result == "blocked", f"外网出口未被封锁，得到: {result!r}"
    finally:
        httpd.shutdown()


@pytest.mark.slow
def test_unsandboxed_control_reaches_external():
    """对照组：裸 launch（不带沙箱参数）应能发出外部请求。

    关键断言是上面的 sandboxed 用例必须 blocked；此用例仅用于确认
    「blocked」不是测试环境本身就没有外网——若环境本身无外网，本用例
    也会 blocked，此时上面的断言依旧成立，但说明对照组失效，需人工复核。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context()
            page = context.new_page()
            result = page.evaluate(
                "fetch('http://example.com').then(r=>'ok').catch(e=>'blocked')"
            )
            # 不强制断言；仅打印，供人工判断测试环境是否有外网
            print(f"[control] external fetch result = {result!r}")
        finally:
            browser.close()


# ---------------------------------------------------------------------------
# A11：preview_url 的主机名必须动态进入出口白名单
#   发布复验的 preview_url 是 <slug>.<apex> 形态（不是数字 IP），不在默认
#   豁免内；不放行就会加载失败并静默回退直读本地文件，复验丢掉「线上 URL
#   真的能打开」这一唯一有意义的信号。
# ---------------------------------------------------------------------------
def test_preview_allow_hosts_derives_from_url():
    from harness.tools.preview_runner import _preview_allow_hosts

    assert _preview_allow_hosts("http://N4Z3.localhost:5001") == ("n4z3.localhost",)
    assert _preview_allow_hosts("https://abc.wowcoder.cn") == ("abc.wowcoder.cn",)


def test_preview_allow_hosts_skips_default_loopback():
    """数字 IP / localhost 已在默认豁免里，不重复追加（避免参数噪音）。"""
    from harness.tools.preview_runner import _preview_allow_hosts

    assert _preview_allow_hosts("http://127.0.0.1:5001") == ()
    assert _preview_allow_hosts("http://localhost:5001") == ()
    assert _preview_allow_hosts(None) == ()
    assert _preview_allow_hosts("") == ()
