---
name: color
description: 色彩系统：色板结构、主色纪律、语义色、深色主题
when_to_use: 任务涉及配色方案、主题风格、暗色模式，或用户明确提到了颜色/风格/主题
level: L1
triggers: [ui, page, app, 界面, 页面, 网站, 应用, 设计, 视觉, 布局, 样式, 颜色, 风格, 主题, 暗色, 配色]
---

# 色彩系统规则

生成 Web 应用时必须遵守以下色彩规则。

## 色板结构

一个协调的 UI 色彩系统包含四层：

| 层级 | 占比 | Tailwind 映射 |
|------|------|-------------|
| 中性色 | 70-90% | gray/slate/zinc 系列 |
| 主色（一个） | 5-10% | blue/emerald/rose 等选择一个 |
| 语义色 | 0-5% | green(成功) / red(危险) / yellow(警告) |
| 特效色 | <1% | 渐变、发光（极少使用） |

## 主色纪律

- **每个屏幕最多使用 2 个主色元素**：典型配对：一个标签/徽章 + 一个主按钮。或一个导航激活态 + 一个 CTA
- 链接算主色消耗；如果同屏已有 CTA，链接降级为下划线灰色
- Hover/Focus 环也消耗主色配额

## Tailwind 语义色推荐

- 成功状态：`bg-green-50 text-green-700 border-green-200`
- 警告状态：`bg-yellow-50 text-yellow-700 border-yellow-200`
- 错误状态：`bg-red-50 text-red-700 border-red-200`
- 信息状态：`bg-blue-50 text-blue-700 border-blue-200`

## 深色主题

如果应用支持暗色模式：
- 背景不使用纯黑 `#000`，使用 `gray-950` 或 `#0f0f0f`
- 文字不使用纯白 `#fff`，使用 `gray-100` 或 `#f0f0f0`
- 暗色表面使用半透明白色边框 `rgba(255,255,255,0.08)` 代替纯色边框

## 命名原则

- 按用途命名，不按颜色值命名
- 好的：`--color-primary`, `--color-danger`
- 不好的：`--blue-500`, `--red-600`（锁定主题，难以切换）
- 在 `style.css` 的 `:root` 中定义全局颜色变量
