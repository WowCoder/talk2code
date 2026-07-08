---
name: typography
description: 排版规范：字号层级、行高、字距、行宽、字体配对
when_to_use: 任务要求设计页面布局、文字排版，或用户提到了样式/美观/视觉等要求
level: L1
triggers: [ui, page, app, 界面, 页面, 网站, 应用, 设计, 视觉, 布局, 样式, 排版, 字体, 好看, 美观]
---

# 排版规范

生成 Web 应用时必须遵守以下排版规则。

## 字号层级

| 角色 | Tailwind 类 | 字号 |
|------|-----------|------|
| 主标题 (H1) | text-3xl ~ text-5xl | 30-48px |
| 副标题 (H2) | text-2xl ~ text-3xl | 24-30px |
| 小标题 (H3) | text-xl | 20px |
| 正文 | text-base | 16px |
| 辅助文本 | text-sm | 14px |
| 说明文字 | text-xs | 12px |

- 整个应用最多使用 5-6 个字号层级
- 标题使用 `font-bold` 或 `font-semibold`

## 行高

| 文本类型 | Tailwind 类 | 行高 |
|----------|-----------|------|
| 标题 (≥30px) | leading-tight | 1.25 |
| 正文 (16px) | leading-relaxed | 1.625 |
| 辅助文本 (≤14px) | leading-normal | 1.5 |

## 字距

- 英文全大写文本（如 BUTTON LABEL）必须加 `tracking-wider` 或 `tracking-widest`（0.05em+）
- 正文使用默认字距，不额外设置
- 大标题 (≥text-3xl) 可选 `tracking-tight`（-0.02em）

## 行宽

- 正文容器设置 `max-w-prose` 或 `max-w-3xl`，限制在 65-75 字符/行
- 不使用的 `text-justify`（两端对齐在 Web 上产生不规则间距）

## 字体

- 最多使用 2 个字体族（一个标题字体 + 一个正文字体，或一个可变字体）
- 使用系统字体栈作为 Fallback：`-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`
- 不建议在功能性应用中使用 Google Fonts CDN（延迟加载影响体验），优先使用系统字体

## 三种字重体系

大多数优秀 UI 恰好使用 3 个字重：
- **正文**：`font-normal` (400) — 大部分内容
- **强调**：`font-medium` (500) — UI 标签、导航
- **标题**：`font-semibold` 或 `font-bold` (600-700) — 标题、按钮
