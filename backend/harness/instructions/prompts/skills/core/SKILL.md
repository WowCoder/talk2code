---
name: core
description: 常驻基线（generic + anti-ai-slop 合并）：技术栈硬约束、视觉基线、反 AI 刻板模式、易错点与提交检查
when_to_use: 任何前端开发任务都应启用。本 skill 是 always 类，占常驻名额
level: L0
always: true
triggers: []
---

# 常驻基线

本 skill 由 generic（通用前端纪律）与 anti-ai-slop（反刻板模式）合并精简而来，
保留全部硬约束，删去冗余解释，以便只占 1 个注入名额。

## 一、技术栈与交付（硬约束）

1. **纯前端**，无后端；数据用 localStorage / IndexedDB 持久化
2. **原生 HTML/CSS/JS**；禁止外部 CDN、禁止 npm / 构建工具
3. **禁止 ES Module**（`import` / `export` / `<script type="module">`）—— 预览用
   `file://` 协议加载，ES Module 会被 CORS 拦截，导致**所有 JS 不执行**。
   正确做法：每个 JS 文件用 IIFE 包裹 `(function (global) { ... })(window)`，
   通过 `window.XXX` 暴露接口，HTML 里用普通 `<script src="js/xxx.js"></script>`
   按依赖顺序引入
4. 必须有 `index.html` 作为入口；所有资源用相对路径
5. 文件按 `css/`、`js/`、`assets/` 组织，允许子目录

## 二、视觉基线（必须遵守）

1. 写样式前先 `read_file` 工作区里的成品模板（已在 `.design/` 下备好，共 5 个）：
   - `.design/preset-static.css` —— 个人名片 / 名片页 / 个人主页 / 作品集 / 博客文章 / 关于页
   - `.design/preset-landing.css` —— 官网 / 落地页 / 产品介绍页 / 定价表
   - `.design/preset-dashboard.css` —— 数据看板 / 图表 / 统计页
   - `.design/preset-crud.css` —— 待办 / 清单 / 表单 / 列表 / 增删改查
   - `.design/preset-game.css` —— 棋盘 / 休闲小游戏（五子棋、贪吃蛇、2048 等）

   **选最贴近的一个作为样式起点再改，不要从零写 CSS。**
   纯计算工具等确实用不到模板的场景可跳过，但仍须遵守下面 2~3 条。
2. 颜色只能取模板里定义的 CSS 变量（`--brand` / `--surface` / `--text` 等），
   禁止自己拍新的 hex；需要渐变背景时只允许用模板里的 `--grad-*`。
3. 字号 / 间距 / 圆角只能取模板定义的档位（`--fs-*` / `--sp-*` / `--r-*`）

## 三、反 AI 刻板模式（P0）

1. 不用 indigo / purple 系作主色（`#6366f1`、`#8b5cf6`、`#a855f7` 是 AI 默认色的标志）
2. 不用紫蓝渐变 Hero；纯色配好排版胜过万能渐变
3. 不用 emoji（✨🚀🎯⚡🔥💡）充当功能图标，用 SVG 或纯文字
4. 避免"大圆角卡片 + 彩色左边框"的 AI 仪表盘模板
5. 不编造指标（"10× 更快"、"99.9% 可用性"），用真实数据或标注为示例
6. 不用 lorem ipsum、"功能一/二/三"占位，写具体中文描述
7. 卡片圆角控制在 `rounded-lg` / `rounded-xl`，不滥用 `rounded-3xl` / `rounded-full`

## 四、常见易错点

- `innerHTML` 有 XSS 风险 → 用 `textContent` 或 `createElement`
- 禁止 `eval()` 与 `document.write`
- 数据操作封装在独立的 `js/storage.js`

## 五、提交前检查

- [ ] `index.html` 入口存在
- [ ] 无 `innerHTML` / `eval` / `document.write`
- [ ] 无 ES Module，用 IIFE + 普通 script
- [ ] 无外部 CDN
- [ ] 数据已通过 localStorage / IndexedDB 持久化
- [ ] JS / CSS 语法检查通过
