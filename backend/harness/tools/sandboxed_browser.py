"""统一出口封锁 + 资源看护的 Chromium 上下文管理器。

背景
----
服务端验收浏览器（``preview_runner.py`` 的 4 处 ``p.chromium.launch``）跑的是
LLM 刚写出来的 JS，原无网络隔离，存在两件事挡不住：

1. **网络出口**：页面里任何 ``<img src="http://x/?c=...">`` 都以**服务器 IP** 发出 →
   数据外带、SSRF 探内网（``169.254.169.254`` 元数据服务）。
2. **资源占用**：``while(true)`` 吃满一个 CPU 核；``TASK_QUEUE_MAX_WORKERS=3``，
   三发就能拖垮整机。

本模块把「出口双重封锁 + wall-clock watchdog」收敛到**单一入口**，供 ``preview_runner``
统一调用，替换那 4 处裸 ``launch``。

出口双重封锁的理由
------------------
- ``--host-resolver-rules MAP * ~NOTFOUND`` 只拦 DNS 名字，拦不住直连 IP 的请求
  （云元数据 ``169.254.169.254`` 这类靠 IP 直连）。
- ``--proxy-server=http://127.0.0.1:9``（黑洞代理）拦得住两者。
- ``--proxy-bypass-list`` 必须给**正向** loopback 清单；**绝对不能写** ``<-loopback>`` ——
  那个 token 是减法语义（去掉 Chrome 隐式 bypass loopback 的规则），会把
  ``127.0.0.1`` 的预览请求也送进黑洞代理，触发 ``preview_runner`` 的 iframe 失败降级
  分支 → 验收链路**静默假绿**。这条反向断言写进了单测（``test_sandboxed_browser`` A2）。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, Tuple

import playwright  # noqa: F401  (仅用于类型可达性 / 文档；真正用到在默认 launcher)


def build_chromium_args(allow_hosts: Tuple[str, ...] = ()) -> list[str]:
    """构造「零外部出口」的 Chromium 启动参数。

    Args:
        allow_hosts: 需要放行（端口白名单）的主机列表。为空时默认只放行
            ``localhost``；非空时把每个主机追加进 ``EXCLUDE`` 白名单。

    Returns:
        一份可直接传给 ``chromium.launch(args=...)`` 的参数列表。

    易错点：``<-loopback>`` 不得出现在任何参数里（见模块 docstring）。
    """
    if allow_hosts:
        # 非空：在「全拒」基础上开白名单。EXCLUDE 必须写在 MAP 之后，作为例外。
        excludes = ", ".join(f"EXCLUDE {h}" for h in allow_hosts)
        resolver = f"MAP * ~NOTFOUND, {excludes}"
    else:
        # 空：默认放行 loopback，保证 127.0.0.1 预览链路不受影响。
        # 注意：预览 URL 用的是**数字 IP** 127.0.0.1，而 MAP * 的 * 会匹配数字 IP，
        # 所以必须显式 EXCLUDE 127.0.0.1 —— 只写 EXCLUDE localhost 救不了数字 IP，
        # 否则预览请求会被解析成 NOTFOUND → 验收链路静默降级（假绿）。
        resolver = "MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"

    return [
        f"--host-resolver-rules={resolver}",
        "--proxy-server=http://127.0.0.1:9",
        "--proxy-bypass-list=127.0.0.1;localhost;<local>",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-component-update",
    ]


class BrowserWatchdogTimeout(Exception):
    """``sandboxed_browser`` 的 wall-clock watchdog 触发时抛出。

    仅作为「超时」信号；生产环境里 watchdog 直接 ``browser.close()`` 强制中断，
    在途的 Playwright 操作会因浏览器被关而抛出自身的错误，效果等价（不会无限挂起）。
    """


@contextmanager
def sandboxed_browser(
    *,
    launcher: Optional[Callable] = None,
    allow_hosts: Tuple[str, ...] = (),
    timeout_ms: int = 30_000,
) -> Iterator[object]:
    """统一出口封锁 + 资源看护的 Chromium 上下文。

    Args:
        launcher: 返回 browser 对象的可调用；缺省用 ``playwright.sync_api`` 拉起一个
            走 ``build_chromium_args`` 的 headless Chromium。
        allow_hosts: 见 ``build_chromium_args``。
        timeout_ms: wall-clock 预算。超过则 watchdog 强制 ``browser.close()`` 并
            置 ``timed_out`` 标记，由调用方在途操作失败来体现中断。

    退出语义：块退出（正常或异常）必 ``browser.close()``（``try/finally``）；
    严禁重复关闭——watchdog 已关过则 finally 跳过。
    """
    if launcher is None:
        from playwright.sync_api import sync_playwright

        def _default_launcher() -> object:
            p = sync_playwright().start()
            browser = p.chromium.launch(
                headless=True, args=build_chromium_args(allow_hosts)
            )

            # 关 browser 时一并停掉 Playwright 实例，避免句柄泄漏。
            _orig_close = browser.close

            def _close() -> None:
                try:
                    _orig_close()
                finally:
                    try:
                        p.stop()
                    except Exception:  # noqa: BLE001
                        pass

            browser.close = _close  # type: ignore[assignment]
            return browser

        launcher = _default_launcher

    browser = launcher()
    _state = {"closed": False, "timed_out": False}

    def _on_timeout() -> None:
        _state["timed_out"] = True
        try:
            browser.close()
        except Exception:  # noqa: BLE001
            pass
        finally:
            _state["closed"] = True

    timer = threading.Timer(timeout_ms / 1000.0, _on_timeout)
    timer.daemon = True
    timer.start()
    try:
        yield browser
    finally:
        timer.cancel()
        if not _state["closed"]:
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                _state["closed"] = True
