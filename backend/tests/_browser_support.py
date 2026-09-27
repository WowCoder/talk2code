# -*- coding: utf-8 -*-
"""浏览器可用性判定（多个测试文件共用）。

**必须真启动一次 Chromium 才算可用**，只判 `import playwright` 不够：
CI 会 `pip install -r requirements.txt`（其中含 playwright 包），但从不执行
`playwright install chromium`——此时 import 成功而 launch 失败，护栏会把
"包在浏览器不在"误判成可用，用例照样红。

判定结果缓存在模块级：多个测试文件 import 本模块时只探活一次。
"""
import functools

import pytest


@functools.lru_cache(maxsize=1)
def chromium_launchable() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            p.chromium.launch(timeout=10000).close()
        return True
    except Exception:  # noqa: BLE001  缺依赖/缺浏览器/启动失败一律视为不可用
        return False


requires_chromium = pytest.mark.skipif(
    not chromium_launchable(),
    reason="需要可启动的 Chromium（pip install playwright && playwright install chromium）",
)
