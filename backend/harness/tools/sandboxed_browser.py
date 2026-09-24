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

import os
import signal
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

    仅作为「超时」信号；生产环境里 watchdog 直接杀掉 driver 进程强制中断，
    在途的 Playwright 操作会因管道断裂抛出自身的错误，效果等价（不会无限挂起）。
    """


def _find_browser_pid(browser) -> int | None:
    """尽量挖出 playwright driver 子进程 pid（watchdog 兜底用）。

    私有属性链（playwright 内部结构，随版本可能变化）：拿不到就返回 None，
    调用方自行降级。实测（playwright + PipeTransport）：browser._impl_obj
    ._connection._transport._proc.pid 可用。
    """
    try:
        obj = getattr(browser, "_impl_obj", browser)
        conn = getattr(obj, "_connection", None) or getattr(obj, "connection", None)
        transport = getattr(conn, "_transport", None) or getattr(conn, "transport", None)
        proc = getattr(transport, "_proc", None) or getattr(transport, "proc", None)
        pid = getattr(proc, "pid", None)
        return pid if isinstance(pid, int) else None
    except Exception:  # noqa: BLE001
        return None


class BrowserSessionTimeout(TimeoutError):
    """整个浏览器会话超出 wall-clock 预算（``run_browser_session_isolated``）。"""


def run_browser_session_isolated(fn, budget_s: float, *args, **kwargs):
    """把整个 Playwright 会话放进**全新线程**执行（req 154 根治）。

    背景（req 154 事故采样实证）：TaskQueue 的 worker 线程是复用的。同一 worker
    线程里第一次 sync_playwright().start() 正常，第二次会永久挂死在 greenlet
    线程本地状态检查（``check_switch_allowed`` / ``find_main_greenlet_in_lineage``
    自旋）——没有 driver 子进程、没有任何日志、``sandboxed_browser`` 的 watchdog
    定时器也未启动（它在 launcher 返回之后才启动）。整条任务链就此冻结。

    每次会话用全新线程 = 干净的线程本地状态，绕开复用线程的 greenlet/TLS 残留；
    同时提供整体 wall-clock 兜底：超时抛 ``BrowserSessionTimeout``，由调用方
    既有的 except 通道降级（AC→harness_errors / 冒烟→available=False /
    截图→None / run_preview→工具错误），浏览器会话失败不再能冻结任务。

    注意：Playwright sync 对象有线程亲和性——**会话内所有操作必须都在这个
    新线程里发生**（即只包装完整的会话函数，不能只包启动）。

    Args:
        fn: 会话函数（内部自带 sandboxed_browser + 异常处理）。
        budget_s: wall-clock 预算（秒）。
        *args / **kwargs: 原样透传给 fn。

    Raises:
        BrowserSessionTimeout: 超过 budget_s 仍未完成。fn 内抛出的其它异常
            原样透传到调用线程。
    """
    outcome: dict = {}

    def _target() -> None:
        try:
            outcome["result"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001  原样透传到调用线程
            outcome["error"] = e

    t = threading.Thread(target=_target, daemon=True, name="t2c-browser-session")
    t.start()
    t.join(budget_s)
    if "result" in outcome:
        return outcome["result"]
    if "error" in outcome:
        raise outcome["error"]
    raise BrowserSessionTimeout(
        f"浏览器会话在 {budget_s:.0f}s 内未完成（疑似 greenlet/启动挂死，"
        f"泄漏的会话线程为 daemon 不阻断进程；持续复现请重启服务）"
    )


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
                # req 154 修复（二）：watchdog 杀掉 driver 后，close() 会在死管道上
                # 永久挂起（实测：click 因 Connection closed 正常抛错后，close 无限等）。
                # - driver 已死 → 直接返回（close/stop 都没有意义，会话线程随即结束）；
                # - driver 还活着但 close 卡死 → 3s 守卫定时器杀 driver，让在途 close
                #   因管道断裂抛错返回。全程在调用线程内，无跨线程 fiber 切换。
                pid = _find_browser_pid(browser)
                if pid is not None:
                    try:
                        os.kill(pid, 0)
                    except ProcessLookupError:
                        return  # driver 已死
                    except Exception:  # noqa: BLE001
                        pass

                def _force_kill() -> None:
                    if pid is not None:
                        try:
                            os.kill(pid, signal.SIGKILL)
                        except Exception:  # noqa: BLE001
                            pass

                def _guarded(fn) -> None:
                    guard = threading.Timer(3.0, _force_kill)
                    guard.daemon = True
                    guard.start()
                    try:
                        fn()
                    except Exception:  # noqa: BLE001
                        pass
                    finally:
                        guard.cancel()

                _guarded(_orig_close)
                _guarded(lambda: p.stop())

            browser.close = _close  # type: ignore[assignment]
            return browser

        launcher = _default_launcher

    browser = launcher()
    _state = {"closed": False, "timed_out": False}

    def _on_timeout() -> None:
        _state["timed_out"] = True
        # req 154 修复：watchdog 原先跨线程调用 browser.close() —— sync API 非线程
        # 安全，这次非法的跨 fiber 切换会把会话线程的在途操作楔死（实测：click 的
        # 3s 超时与 watchdog 的 3s 同刻竞争 → dispatcher 永久卡死，无日志无报错）。
        # 改为杀 driver 进程：管道断裂让所有在途操作立刻抛 Target closed /
        # Connection closed，由会话自身的 except 通道降级，主流程不受阻。
        pid = _find_browser_pid(browser)
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)  # driver 一死，其拉起的 chromium 随管道 EOF 退出
            except Exception:  # noqa: BLE001
                pass
        else:
            # 私有结构变化拿不到 pid 时的兜底：close() 有楔死风险但聊胜于无
            try:
                browser.close()
            except Exception:  # noqa: BLE001
                pass
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
