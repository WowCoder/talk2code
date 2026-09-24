将以下验收条件翻译为 Playwright DOM 操作序列。

## 可用 CSS 选择器（从实际代码中提取）
{selector_text}

## 验收条件
{ac_text}

## 渲染方式（harness 检测实际代码得出的事实，不要自行猜测）
{render_info}

## 断言选型规则（硬性，违反即判废）
- 实现**有** canvas → 画面类断言用 `assert_canvas_change`
- 实现**没有** canvas（DOM / div / table 渲染）→ 画面类断言必须用 `assert_dom_change`，
  **严禁**使用 `assert_canvas_change`（页面无 canvas 时它只会判「不适用」，拿不到任何信号）
- 元素是否存在一律用 `assert_exists` / `assert_visible`

## 翻译规则
- 每个步骤的 action 必须是: type | click | select | press | wait | assert_exists | assert_visible | assert_text | assert_count | assert_value | assert_canvas_change | assert_dom_change
- selector 必须从"可用 CSS 选择器"中选择，或从 AC 描述中合理推断
- type 需要 value 字段；只用于 input/textarea，禁止对 canvas/普通元素使用
- press 需要 key 字段（如 ArrowUp/ArrowDown/Enter/Space）：键盘交互（游戏方向键、快捷键）必须用 press，禁止用 type 模拟
- wait 需要 ms 字段（默认 500）
- assert_text 需要 contains 字段
- assert_count 需要 min_count 字段
- assert_value 需要 value 字段
- assert_canvas_change 需要 wait_ms 字段（默认 2000）：验证 canvas 像素随操作变化
- assert_dom_change 需要 selector 字段（观察范围，如游戏棋盘容器；省略则观察整个 body）+ wait_ms 字段（默认 1500）。
  语义：自本条 AC 开始执行到现在，该容器内是否发生过 DOM 变化（子节点增删、属性、文本）。
  这是 DOM 实现游戏「棋盘是否随操作更新」的标准断言
- 游戏类 AC 的标准模式：click 开始按钮 → wait 800ms → press 方向键 → wait 500ms →
  **画面断言**（按上面的选型规则在 assert_canvas_change / assert_dom_change 之间二选一）
- 【重要·方向键游戏】press 方向键必须**轮转且不连续反向**：蛇类游戏里方向键连续 180° 掉头
  （如 ArrowUp 后立即 ArrowDown）会让蛇瞬间自撞死亡，画面立即静止 → 画面断言必然误判。
  生成按键序列时必须遵循：
  - 不要出现互为相反的两键相邻（Up 后 Down / Left 后 Right）
  - 方向变化走 90° 轮转（如 Up → Right → Down → Left，或 Up → Left → Down → Right）
  - 每步 wait 用 **500~600ms**（不是 1500ms）：避免角色一路走到边界死亡后画面静止，导致断言误判
  - 推荐固定模式：`ArrowUp → ArrowRight → ArrowDown`（共 3 键、顺时针 270°），每键后 wait 500ms
- 【重要】严禁断言精确分数文本（如 contains="10"），得分依赖随机局面、取值不确定。
  验证"得分"改为：assert_exists 验证分数元素存在，必要时配合画面断言证明玩局确实在进行
- 【重要】不要断言概率性结果：
  - 「相同数字合并」这类依赖随机局面的 AC，不要写死"按一次方向键就必须合并"——实测固定序列
    连续多局都可能一次都不触发。应该：连续按 3~4 个不同方向（每步 wait 500ms），
    最后用画面断言 + 分数元素存在收口，把「合并是否真的发生」交给评估器结合代码判断
  - 「达到 X 数值才胜利」这类目标，黑盒操作通常需要成百上千步才能到达，
    **不要生成"狂按几百次"的荒谬脚本**。改为断言其可观察的前置行为，
    必要时用 assert_exists 验证胜利层元素存在
- 【重要】分享类 AC：游戏可能设计为"有成绩才能分享"（score=0 时提示"先玩一局"）。这是合理设计，
  不要断言 toast 必须包含"复制"等特定文本。验证"分享"改为：点击后 assert_exists / assert_visible
  验证有反馈提示出现即可，不断言具体文案。

## 验收契约（必读·决定你翻出的脚本是否被信任）

harness 对每条 AC 会归一到四个信号，前端据此展示验收状态：

- `passed`：真正通过（无产品失败、无脚本错误、前提成立）——你追求的目标。
- `fail`：可信的产品断言失败（选择器点到了、操作执行了，但结果不对）。
- `compromised`：**失败不可信**——脚本没跑成（点击步骤超时 / 选择器找不到）却仍有断言失败。
  此时失败是幽灵，会误导修复环节去改不存在的问题。宁可让它 `unverified`，也不要 `compromised`。
- `unverified`：断言前提不成立（如页面无 canvas 却断言 canvas 变化）；既非通过也非失败。
- `not_applicable`：该断言对当前实现不适用，不纳入验收。

如何避免 `compromised` / `harness_errors`（这直接决定验收质量）：

1. **selector 只能从本节「可用 CSS 选择器」里选，或严格从 AC 描述推断**。瞎猜选择器
   （如 `#nonexistent`）必然驱动失败 → 落入 `compromised`，既浪费一轮修复，又会在多数 AC
   验不了时触发整份脚本缓存作废重译。
2. 每一步都必须是「能在真实页面上完成」的操作。游戏 / 动画类先用 wait 留出渲染时间，
   再断言画面变化（见上方 断言选型规则）；断言前确保前置操作真的发生。
3. 一条 AC 拿不准能不能黑盒验证（依赖随机局面、需成百上千步），就不要硬写断言——
   退化成 `assert_exists` 证明关键元素存在即可，交给评估器结合代码判断。

缓存与重译：命中 `ac_hash`（AC 文本 + 渲染方式）时本批脚本会被锁定复用，防止每轮漂移；
但如果你翻出的脚本大量 `compromised` / `unverified`，harness 会作废缓存按当前代码重译。
所以**第一版就要翻对**，不要指望下一轮替你修。

## 输出格式
只返回 JSON 数组，不要其他文字:
```json
[
  {{
    "ac_id": "AC-1",
    "label": "...",
    "steps": [
      {{"action": "type", "selector": "#input", "value": "测试文字"}},
      {{"action": "click", "selector": "#add-btn"}},
      {{"action": "wait", "ms": 500}},
      {{"action": "assert_exists", "selector": ".result-item", "label": "新项目出现在列表中"}}
    ]
  }}
]
```
