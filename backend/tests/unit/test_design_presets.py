# -*- coding: utf-8 -*-
"""成品设计模板（`.design/preset-*.css`）的守护测试。

为什么需要：这 5 个模板会被**播种进每一个工作区**，作为模型写样式时的起点。
如果其中一个出现括号不配平、引用了不存在的变量、或混入被明令禁止的色系，
受影响的不只是这个模板 —— 是此后所有生成任务的页面质量，而且表现是
"页面有点怪"这种极难归因的软症状。所以在源头加一道自动化校验。

同时校验 core skill 的引用与实际文件一一对应：core 里写了路径但文件不存在时，
模型会去 read 一个不存在的文件，白烧一轮迭代（这个问题真的发生过）。
"""
import re
from pathlib import Path

import pytest

# backend/ 目录（本文件位于 backend/tests/unit/）
_BACKEND = Path(__file__).resolve().parents[2]
_PRESET_DIR = _BACKEND / "assets" / "design"
_CORE_SKILL = _BACKEND / "harness" / "instructions" / "prompts" / "skills" / "core" / "SKILL.md"

# 被明令禁止的主色（AI 默认色）：indigo 系
_FORBIDDEN_HEX = ("#6366f1", "#4f46e5", "#818cf8", "#a5b4fc")


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*[\s\S]*?\*/", "", css)


def _presets():
    return sorted(_PRESET_DIR.glob("*.css"))


def test_presets_exist():
    files = _presets()
    assert files, f"未找到任何成品模板: {_PRESET_DIR}"
    # core skill 里承诺了这 5 类场景
    names = {f.name for f in files}
    expected = {
        "preset-game.css", "preset-dashboard.css", "preset-landing.css",
        "preset-crud.css", "preset-static.css",
    }
    assert expected <= names, f"缺少模板: {sorted(expected - names)}"


@pytest.mark.parametrize("path", _presets(), ids=lambda p: p.name)
def test_braces_balanced(path: Path):
    body = _strip_comments(path.read_text(encoding="utf-8"))
    assert body.count("{") == body.count("}"), (
        f"{path.name} 花括号不配平: {body.count('{')} 开 / {body.count('}')} 闭"
    )
    assert body.count("(") == body.count(")"), f"{path.name} 圆括号不配平"


@pytest.mark.parametrize("path", _presets(), ids=lambda p: p.name)
def test_no_dangling_css_variables(path: Path):
    body = _strip_comments(path.read_text(encoding="utf-8"))
    defined = set(re.findall(r"(--[\w-]+)\s*:", body))
    used = set(re.findall(r"var\(\s*(--[\w-]+)", body))
    dangling = used - defined
    assert not dangling, f"{path.name} 使用了未定义的变量: {sorted(dangling)}"


@pytest.mark.parametrize("path", _presets(), ids=lambda p: p.name)
def test_no_forbidden_brand_colors(path: Path):
    """core skill 明令避开 indigo；只在声明里检查，注释里说「避开 indigo」不算违规。"""
    body = _strip_comments(path.read_text(encoding="utf-8")).lower()
    hit = [h for h in _FORBIDDEN_HEX if h in body]
    assert not hit, f"{path.name} 混入了被禁的 indigo 色值: {hit}"


@pytest.mark.parametrize("path", _presets(), ids=lambda p: p.name)
def test_has_token_layers(path: Path):
    """每个模板都必须自带 token 层 —— 模型靠"只取变量"的禁令才可能做出统一视觉。"""
    body = _strip_comments(path.read_text(encoding="utf-8"))
    defined = set(re.findall(r"(--[\w-]+)\s*:", body))
    assert any(("brand" in v) or ("bg" in v) for v in defined), f"{path.name} 缺少颜色 token"
    assert any(v.startswith("--fs-") for v in defined), f"{path.name} 缺少字号 token"
    assert any(v.startswith("--sp-") for v in defined), f"{path.name} 缺少间距 token"
    assert any(v.startswith("--r-") for v in defined), f"{path.name} 缺少圆角 token"


def test_no_empty_declarations():
    for p in _presets():
        body = _strip_comments(p.read_text(encoding="utf-8"))
        assert ";;" not in body, f"{p.name} 存在连续分号"
        assert not re.search(r":\s*;", body), f"{p.name} 存在空属性值"


def test_core_skill_references_match_actual_files():
    """core 里写的模板路径必须真实存在 —— 否则模型会去读一个不存在的文件，白烧一轮。"""
    core = _CORE_SKILL.read_text(encoding="utf-8")
    referenced = set(re.findall(r"\.design/([\w.-]+)", core))
    actual = {p.name for p in _presets()}
    assert referenced, "core skill 未引用任何模板，成品模板机制形同虚设"
    assert referenced == actual, (
        f"引用与实际不一致 —— 悬空引用={sorted(referenced - actual)} "
        f"未被引用={sorted(actual - referenced)}"
    )


def test_presets_are_seeded_and_excluded_from_deliverables():
    """模板要能被模型读到（进工作区），但必须从交付列表里排除（不能污染用户产物）。"""
    import tempfile
    import shutil
    from harness.state.workspace import WorkspaceFS

    tmp = Path(tempfile.mkdtemp())
    try:
        ws = WorkspaceFS(user_id=9901, requirement_id=1, base_dir=tmp)
        ws.init([{"filename": "index.html", "content": "<h1>x</h1>"}])
        assert ws.list() == ["index.html"], f"交付列表被模板污染: {ws.list()}"
        seeded = sorted(p.name for p in (ws.path / ".design").glob("*.css"))
        assert len(seeded) == len(_presets()), f"播种数量不符: {seeded}"
        for name in seeded:
            assert len(ws.read(f".design/{name}")) > 500, f"{name} 内容异常短"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
