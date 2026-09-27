# -*- coding: utf-8 -*-
"""req 154 事故回归测试：浏览器会话线程隔离 + 整体超时兜底。

事故：TaskQueue 复用的 worker 线程里，第二次 sync_playwright().start()
永久挂死在 greenlet 线程本地状态检查（采样实证：check_switch_allowed 自旋、
无 driver 子进程、无日志、watchdog 未启动）→ 整条任务链冻结。

修复约定（锁死三件事）：
1. 每个浏览器会话必须在全新线程里执行（run_browser_session_isolated），
   且会话内所有 Playwright 操作同线程发生（sync API 线程亲和性）。
2. 会话整体有 wall-clock 预算，超时抛 BrowserSessionTimeout，绝不允许
   浏览器环节无限冻结任务链。
3. 各公开函数超时时按各自语义降级：run_ac_checks→全部 harness_errors、
   run_universal_smoke→available=False、capture_screenshot→None。
"""
import threading
import time

import pytest

from harness.tools import preview_runner
from harness.tools.sandboxed_browser import (
    BrowserSessionTimeout,
    run_browser_session_isolated,
)
from tests._browser_support import requires_chromium


class TestRunBrowserSessionIsolated:
    def test_result_passthrough(self):
        assert run_browser_session_isolated(lambda a, b: a + b, 5, 1, b=2) == 3

    def test_exception_passthrough(self):
        def _boom():
            raise ValueError("会话内部错误")

        with pytest.raises(ValueError, match="会话内部错误"):
            run_browser_session_isolated(_boom, 5)

    def test_timeout_raises_session_timeout(self):
        def _hang():
            time.sleep(5)

        t0 = time.time()
        with pytest.raises(BrowserSessionTimeout):
            run_browser_session_isolated(_hang, 0.3)
        assert time.time() - t0 < 3, "超时必须在预算附近返回，不能等会话真正结束"

    def test_session_thread_is_fresh_each_call(self):
        """每次会话都必须用新线程（复用线程的 TLS 残留正是挂死根因）"""
        seen = []

        def _probe():
            seen.append(threading.current_thread())

        run_browser_session_isolated(_probe, 5)
        run_browser_session_isolated(_probe, 5)
        assert len(seen) == 2
        assert seen[0] is not seen[1], "两次会话不能跑在同一个线程里"
        assert seen[0] is not threading.current_thread(), "会话不能跑在调用线程里"


class TestAcChecksWrapper:
    def test_happy_path_passthrough(self, monkeypatch):
        marker = [{"ac_id": "AC-1", "passed": True, "failures": []}]
        monkeypatch.setattr(preview_runner, "_run_ac_checks_session", lambda *a, **k: marker)
        assert preview_runner.run_ac_checks("/tmp/x.html", marker) is marker

    def test_timeout_degrades_to_harness_errors(self, monkeypatch):
        monkeypatch.setattr(preview_runner, "_SESSION_BUDGET_SLACK_S", 0.2)
        monkeypatch.setattr(preview_runner, "_watchdog_ms", lambda timeout_ms, n_ops: 100)

        def _hang(*a, **k):
            time.sleep(5)

        monkeypatch.setattr(preview_runner, "_run_ac_checks_session", _hang)
        acs = [{"ac_id": "AC-1"}, {"ac_id": "AC-2"}]
        t0 = time.time()
        results = preview_runner.run_ac_checks("/tmp/x.html", acs, timeout_ms=100)
        assert time.time() - t0 < 3, "超时必须快速返回"
        assert len(results) == 2
        assert all(r["passed"] is False for r in results)
        assert all(r["failures"] == [] for r in results)
        assert all(r["harness_errors"] for r in results), "超时必须标记为驱动失败（非产品断言失败）"


class TestSmokeWrapper:
    def test_timeout_degrades_to_unavailable(self, monkeypatch):
        monkeypatch.setattr(preview_runner, "_SESSION_BUDGET_SLACK_S", 0.2)
        monkeypatch.setattr(preview_runner, "_watchdog_ms", lambda timeout_ms, n_ops: 100)

        def _hang(*a, **k):
            time.sleep(5)

        monkeypatch.setattr(preview_runner, "_run_universal_smoke_session", _hang)
        result = preview_runner.run_universal_smoke("/tmp/x.html", timeout_ms=100)
        assert result["available"] is False
        assert result["defects"] == []
        assert result["logs"], "降级原因要写进 logs"


class TestScreenshotWrapper:
    def test_timeout_degrades_to_none(self, monkeypatch):
        monkeypatch.setattr(preview_runner, "_SESSION_BUDGET_SLACK_S", 0.2)
        monkeypatch.setattr(preview_runner, "_watchdog_ms", lambda timeout_ms, n_ops: 100)

        def _hang(*a, **k):
            time.sleep(5)

        monkeypatch.setattr(preview_runner, "_capture_screenshot_session", _hang)
        assert preview_runner.capture_screenshot("/tmp/x.html", "/tmp/out.png", timeout_ms=100) is None


@requires_chromium
@pytest.mark.slow
class TestWatchdogKillUnblocksSession:
    """req 154 第二层修复：watchdog 从「跨线程 browser.close()」改为「杀 driver 进程」。

    实测（fix 前）：watchdog 定时器与页面操作自身超时同刻竞争（如都是 3000ms）时，
    close() 的跨线程调用把会话线程的 dispatcher 楔死——在途操作永不返回、
    无日志无报错（与生产 req 154 的冻结同型）。修后 watchdog 杀 driver →
    管道断裂 → 在途操作立刻抛错 → 会话自身的 except 通道正常降级返回。
    """

    @staticmethod
    def _fixture_html(tmp_path):
        """在 pytest 临时目录里落一个最小页面（不写进仓库工作区）"""
        p = tmp_path / "index.html"
        p.write_text(
            '<!DOCTYPE html><html><body><button id="go">x</button></body></html>',
            encoding="utf-8",
        )
        return p

    def test_watchdog_race_does_not_wedge_session(self, tmp_path):
        """watchdog=2000 与 click 自身超时=2000 同刻竞争——会话必须仍能快速返回"""
        html = self._fixture_html(tmp_path)
        t0 = time.time()
        results = preview_runner.run_ac_checks(
            html,
            [{"ac_id": "AC-1", "label": "竞态", "steps": [{"action": "click", "selector": "#absent"}]}],
            timeout_ms=2000,
        )
        elapsed = time.time() - t0
        assert elapsed < 12, f"watchdog 竞争导致会话楔死（耗时 {elapsed:.1f}s）——回归！"
        r = results[0]
        assert r["passed"] is False
        assert r["harness_errors"], "click 不存在的元素必须产生驱动失败"

    def test_no_race_session_completes_normally(self, tmp_path):
        """watchdog 拉远（无竞争）时，click 超时正常抛错、会话正常收尾。

        注：run_ac_checks 把 page 超时与 watchdog 绑成同一个 timeout_ms，
        想构造真正的无竞态场景必须直接用 sandboxed_browser 分开设值
        （watchdog=15s，click=3s）。
        """
        from harness.tools.sandboxed_browser import sandboxed_browser

        html = self._fixture_html(tmp_path)

        def session():
            with sandboxed_browser(timeout_ms=15000) as browser:
                context = browser.new_context()
                page = context.new_page()
                page.set_default_timeout(3000)
                page.goto(html.as_uri(), wait_until="domcontentloaded", timeout=3000)
                try:
                    page.click("#absent")
                except Exception:  # noqa: BLE001  预期：3s 超时抛错
                    pass
                browser.close()

        t0 = time.time()
        run_browser_session_isolated(session, 12.0)
        elapsed = time.time() - t0
        assert elapsed < 10, f"无竞争场景不应慢（耗时 {elapsed:.1f}s）——close 挂死回归！"
