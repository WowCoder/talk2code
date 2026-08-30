将以下验收条件翻译为 Playwright DOM 操作序列。

## 可用 CSS 选择器（从实际代码中提取）
{selector_text}

## 验收条件
{ac_text}

## 翻译规则
- 每个步骤的 action 必须是: type | click | select | press | wait | assert_exists | assert_visible | assert_text | assert_count | assert_value | assert_canvas_change
- selector 必须从"可用 CSS 选择器"中选择，或从 AC 描述中合理推断
- type 需要 value 字段；只用于 input/textarea，禁止对 canvas/普通元素使用
- press 需要 key 字段（如 ArrowUp/ArrowDown/Enter/Space）：键盘交互（游戏方向键、快捷键）必须用 press，禁止用 type 模拟
- wait 需要 ms 字段（默认 500）
- assert_text 需要 contains 字段
- assert_count 需要 min_count 字段
- assert_value 需要 value 字段
- assert_canvas_change 需要 wait_ms 字段（默认 2000）：验证 canvas 画面随操作变化（游戏移动/动画）
- 游戏类 AC 的标准模式：click 开始按钮 → wait 800ms → press 方向键 → wait 500ms → assert_canvas_change(wait_ms=1500)
- 【重要·贪吃蛇等方向键游戏】press 方向键必须**轮转且不连续反向**：蛇类游戏里方向键连续 180° 掉头（如 ArrowUp 后立即 ArrowDown）会让蛇瞬间自撞死亡，画面立即静止 → assert_canvas_change 必然误判。
  生成按键序列时必须遵循：
  - 不要出现互为相反的两键相邻（Up 后 Down / Left 后 Right）
  - 方向变化走 90° 轮转（如 Up → Right → Down → Left，或 Up → Left → Down → Right）
  - 每步 wait 用 **500~600ms**（不是 1500ms）：蛇通常 150ms/格移动，1500ms 会让蛇走 10 格、
    很快撞墙死亡，画面静止后断言必然误判。短 wait（3~4 格移动）既证明画面在动又不会撞死
  - 推荐固定模式：`ArrowUp → ArrowRight → ArrowDown`（共 3 键、顺时针 270°），每键后 wait 500ms
  - 若验证「吃食物得分」：可在开始后 wait 800ms 让蛇自动移动，然后 press 一次方向键转向 +
    wait 800ms，再 assert_canvas_change 或 assert_exists 分数元素，不要长时间按键
- 【重要】游戏类 AC 严禁断言精确分数文本（如 contains="10"），因为蛇吃到食物需要时间和多次操作，分数值不确定。
  验证"得分/吃食物"改为：assert_canvas_change 验证画面变化（蛇移动/变长），或 assert_exists 验证分数元素存在即可，不要断言具体数字。
- 【重要】分享类 AC：游戏可能设计为"有成绩才能分享"（score=0 时提示"先玩一局"）。这是合理设计，不要断言 toast 必须包含"复制"等特定文本。
  验证"分享"改为：assert_exists/assert_visible 验证分享按钮存在，点击后 assert_exists/assert_visible 验证有反馈提示（toast）出现即可，不断言具体文案。

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
