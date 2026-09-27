"""AC 脚本「同一元素上多点交互」修复的回归测试（需求 199）。

背景：验收把一条**实现完全正确**的五子棋需求判成
「存在未解决的关键缺陷」（AC-1 / AC-2 假 critical）。根因有两层：

1. Playwright 的 `click` 默认点元素**中心**。棋盘 / canvas / 网格这类元素上
   多条 click 会全部压在同一个像素上，**只有第一条生效** —— 要点两下切两次
   回合的 AC 只生效一次，要连下十子成五的 AC 实际只落一子。
   → 修复：click 支持 `at` 比例坐标 / `offset` 像素偏移落点。

2. 选择器提取只扫静态 HTML 的 `id=""` / `class=""`，而棋盘交叉点 `.cell` 是
   JS `createElement` 生成的 → 候选里没有它，翻译器只能写容器 `.board`。
   → 修复：提取器补上 JS 运行时类名与 CSS 中定义的选择器。
"""

from harness.instructions.nodes import _extract_selector_hints
from harness.tools.preview_runner import _click_position


class _FakeEl:
    def __init__(self, box):
        self._box = box

    def bounding_box(self):
        return self._box


class _FakeDoc:
    def __init__(self, el=None):
        self._el = el

    def query_selector(self, sel):
        return self._el


class TestClickPosition:
    """click 落点解析（比例 → 像素）"""

    def test_no_hint_returns_none(self):
        """未指定落点 → None，保持原有的默认中心点击行为"""
        doc = _FakeDoc(_FakeEl({"x": 0, "y": 0, "width": 600, "height": 600}))
        assert _click_position(doc, ".board", {}) is None

    def test_ratio_maps_to_pixels(self):
        doc = _FakeDoc(_FakeEl({"x": 10, "y": 10, "width": 600, "height": 400}))
        assert _click_position(doc, ".board", {"at": [0.5, 0.25]}) == {
            "x": 300.0,
            "y": 100.0,
        }

    def test_two_ratios_give_different_points(self):
        """核心回归：同一元素上两个不同比例必须落到不同像素"""
        doc = _FakeDoc(_FakeEl({"x": 0, "y": 0, "width": 600, "height": 600}))
        p1 = _click_position(doc, ".board", {"at": [0.25, 0.25]})
        p2 = _click_position(doc, ".board", {"at": [0.75, 0.75]})
        assert p1 != p2
        assert p1 == {"x": 150.0, "y": 150.0}
        assert p2 == {"x": 450.0, "y": 450.0}

    def test_ratio_is_clamped_inside_element(self):
        """比例越界须夹进元素内部：正好压在边界会被 Playwright 判 outside viewport"""
        doc = _FakeDoc(_FakeEl({"x": 0, "y": 0, "width": 100, "height": 100}))
        assert _click_position(doc, ".b", {"at": [0, 0]}) == {"x": 1.0, "y": 1.0}
        assert _click_position(doc, ".b", {"at": [1, 1]}) == {"x": 99.0, "y": 99.0}
        assert _click_position(doc, ".b", {"at": [-3, 0.5]}) == {"x": 1.0, "y": 50.0}

    def test_offset_uses_pixels(self):
        doc = _FakeDoc(_FakeEl({"x": 0, "y": 0, "width": 100, "height": 100}))
        assert _click_position(doc, ".b", {"offset": {"x": 12, "y": 34}}) == {
            "x": 12.0,
            "y": 34.0,
        }

    def test_missing_element_returns_none(self):
        """元素不存在 → None（交由调用方走默认 click，并如实记 harness 错误）"""
        assert _click_position(_FakeDoc(None), ".nope", {"at": [0.5, 0.5]}) is None

    def test_zero_box_returns_none(self):
        doc = _FakeDoc(_FakeEl({"x": 0, "y": 0, "width": 0, "height": 0}))
        assert _click_position(doc, ".b", {"at": [0.5, 0.5]}) is None

    def test_malformed_ratio_returns_none(self):
        doc = _FakeDoc(_FakeEl({"x": 0, "y": 0, "width": 100, "height": 100}))
        assert _click_position(doc, ".b", {"at": ["a", "b"]}) is None
        assert _click_position(doc, ".b", {"at": [0.5]}) is None


class TestSelectorHints:
    """选择器提取：动态生成的元素必须进入候选"""

    CODE = """
    <div class="board" id="board"><div id="turn-label"></div></div>
    <script>
      var cell = document.createElement('button');
      cell.className = 'cell stone-host';
      el.classList.add('is-black');
      var grid = document.createElement('div');
      grid.className = 'board-grid';
    </script>
    <style>
    .cell { width: 20px; }
    .board-grid { display: grid; }
    #win-overlay { position: fixed; }
    </style>
    """

    def test_static_hints_from_html_attrs(self):
        static, _ = _extract_selector_hints(self.CODE)
        assert "#board" in static
        assert ".board" in static
        assert "#turn-label" in static

    def test_dynamic_hints_include_generated_cell(self):
        """核心回归：.cell 只在 JS/CSS 里出现，必须进入 dynamic 候选。

        需求 199 就是漏了这一步：翻译器手里只有 `.board`，只能写
        `click .board`（点中心），多点交互全部失效。
        """
        _, dynamic = _extract_selector_hints(self.CODE)
        assert ".cell" in dynamic
        assert ".board-grid" in dynamic
        assert ".stone-host" in dynamic  # className 多类名拆分
        assert ".is-black" in dynamic  # classList.add
        assert "#win-overlay" in dynamic  # CSS 中的 id 选择器

    def test_dynamic_excludes_static_duplicates(self):
        _, dynamic = _extract_selector_hints(self.CODE)
        assert ".board" not in dynamic

    def test_no_duplicates(self):
        static, dynamic = _extract_selector_hints(self.CODE)
        assert len(static) == len(set(static))
        assert len(dynamic) == len(set(dynamic))

    def test_order_is_stable(self):
        """顺序必须稳定（原实现用 set → 每轮提示词都可能不同，不利于第一版翻对）"""
        a = _extract_selector_hints(self.CODE)
        b = _extract_selector_hints(self.CODE)
        assert a == b

    def test_empty_code_is_safe(self):
        assert _extract_selector_hints("") == ([], [])
