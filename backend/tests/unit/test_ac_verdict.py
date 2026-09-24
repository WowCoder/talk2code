# -*- coding: utf-8 -*-
"""AC 判定结果的契约测试（run_ac_checks 的 passed / unverified / compromised）

前两条是**回归守卫**：P1（harness_errors 阻断 passed）已在 preview_runner.py:592
实现但当时没有测试，这里把它钉死，防止后续重构悄悄退回 `len(failures) == 0`。
第三条 compromised 是 P2.5 的新行为。

为什么放在 unit 而不放 integration：测的是 run_ac_checks 单个函数的判定契约，
不碰 app / DB。为了跑得快，timeout_ms 一律压到 3 秒。
"""
import pytest

from harness.tools.preview_runner import run_ac_checks


_FIXTURE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>fixture</title></head>
<body>
  <button id="go">开始</button>
  <div id="out">初始</div>
  <script>
    document.getElementById('go').addEventListener('click', function () {
      document.getElementById('out').textContent = '已点击';
    });
  </script>
</body>
</html>
"""


@pytest.fixture(scope="module")
def fixture_html():
    """落 fixture 到项目内 tmp/ 而不是 pytest 的 tmp_path。

    原因：本沙箱环境下 pytest 创建 basetemp 目录会被拦截（EEXIST / PermissionError），
    任何依赖 tmp_path / tmp_path_factory 的用例都跑不起来。
    """
    from pathlib import Path

    out_dir = Path(__file__).resolve().parents[3] / "tmp" / "ac_verdict_fixture"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "index.html"
    p.write_text(_FIXTURE_HTML, encoding="utf-8")
    return p


def _run(html_path, steps, ac_id="AC-1"):
    return run_ac_checks(
        html_path,
        [{"ac_id": ac_id, "label": "测试用 AC", "steps": steps}],
        timeout_ms=3000,
        preview_url=None,  # 直读 file://，不依赖预览服务
    )


def test_harness_errors_block_passed(fixture_html):
    """脚本一步都驱动不起来时，绝不能判 passed。

    背景（req 145）：AC-1/AC-4 带着 harness_errors 拿到了 passed=True ——
    断言没跑所以 failures 为空，而旧判定只看 failures。
    """
    results = _run(fixture_html, [{"action": "click", "selector": "#does-not-exist"}])
    r = results[0]
    assert r["harness_errors"], "前置条件：点击不存在的元素应产生脚本驱动失败"
    assert r["failures"] == [], "前置条件：本例不应有产品断言失败"
    assert r["passed"] is False, "脚本没跑成却判通过 = 假绿"


def test_not_applicable_marks_unverified(fixture_html):
    """断言前提不成立（页面无 canvas 却断言 canvas 变化）→ 未验证，也不是通过。"""
    results = _run(
        fixture_html,
        [{"action": "assert_canvas_change", "label": "画面应变化"}],
    )
    r = results[0]
    assert r["not_applicable"], "无 canvas 页面应记为断言前提不成立"
    assert r["unverified"] is True
    assert r["passed"] is False, "未验证绝不等于通过"
    assert r["harness_errors"] == [], "N/A 不应混进脚本驱动失败（req 147）"


def test_compromised_flag_when_both_fail(fixture_html):
    """P2.5：脚本没跑成 + 断言失败 ⇒ 这条失败不可信，必须标记。

    点击步骤超时后，后续断言是在「没被点过的页面」上跑的，
    它的失败是幽灵，不能和可信失败同等对待。
    """
    results = _run(
        fixture_html,
        [
            {"action": "click", "selector": "#does-not-exist"},
            {"action": "assert_exists", "selector": "#definitely-absent", "label": "结果项"},
        ],
    )
    r = results[0]
    assert r["harness_errors"], "前置条件：应有脚本驱动失败"
    assert r["failures"], "前置条件：应有产品断言失败"
    assert r["compromised"] is True
