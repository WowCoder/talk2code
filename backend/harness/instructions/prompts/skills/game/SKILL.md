---
name: game
description: 游戏类需求开发规范：启动流程、就绪状态、游戏循环、遮罩层状态管理
when_to_use: 用户需求是游戏（贪吃蛇、俄罗斯方块、2048、扫雷等）时启用
level: L1
triggers: [游戏, 贪吃蛇, 俄罗斯方块, 2048, 打地鼠, 扫雷, 小游戏, game, snake, tetris]
---

# 游戏开发规范

生成游戏时必须遵守以下规则，否则会导致「玩家无法启动」或「点击即死亡」等致命缺陷。

## P0 启动流程（最高优先级）

1. **开始界面必须初始可见** — 覆盖层（overlay / 遮罩 / 开始界面）在 HTML 里**不要默认写 `hidden` 类**；初始状态必须显示「开始游戏」按钮。正确做法：
   - HTML 里 overlay 不带 `hidden`，或
   - 脚本初始化时调用 `showOverlay()` / `overlay.classList.remove('hidden')`。
   - 反例（禁止）：`<div class="overlay hidden">` 且初始化时从不移除 hidden。

2. **就绪状态（ready）必须存在** — 点击「开始游戏」后，角色**保持静止**进入「就绪」状态，**首次方向输入才启动移动循环**。禁止「点击开始后角色立即自动移动」——这会导致角色在玩家来得及操作前撞墙死亡。
   - 正确：`startBtn 点击 → reset() + ready=true（角色静止）`；`首次 keydown/方向按钮 → 启动循环 + 设置方向`。
   - 反例：`start() 里直接 setInterval(tick)` 且无就绪等待。

3. **遮罩层状态切换要闭环** — `showOverlay()` 用于「开始/结束」界面，`hideOverlay()` 用于「游戏中」。确保初始态调用 showOverlay（开始界面），点击开始后 hideOverlay，游戏结束后 showOverlay。

## P1 游戏循环

- 用 `requestAnimationFrame`（时间步进）或 `setInterval` + 固定 tick 间隔（建议 100-180ms），速度随分数递增。
- 移动间隔不要过短（避免角色瞬间撞墙）；初始速度要给玩家反应时间。
- 方向切换禁止 180° 直接反向（如向右时不能立即向左）。

## P1 数据持久化

- localStorage 访问必须包 try/catch，失败时降级为内存对象，避免沙箱环境下抛 SecurityError 崩溃。
- 排行榜/最高分在页面加载时从存储读取并渲染。

## P1 自包含

- 禁止依赖外部 CDN（Tailwind、字体等），所有样式/脚本本地化，确保离线可运行。

## 通用（Canvas 游戏）

- canvas 用 `getContext('2d', { willReadFrequently: true })`（若游戏需要频繁读像素做碰撞/检测）。
- 键盘监听挂到 `document` 或 `window`（方向键/WASD/空格），并 `preventDefault()` 阻止页面滚动。
- 同时提供触屏/方向按钮作为移动端备选。
