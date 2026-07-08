---
name: accessibility
description: 可访问性基础规则：对比度、键盘导航、语义化 HTML、ARIA 标签
when_to_use: 任务包含表单、输入框、按钮等交互组件，或需要用户输入/填写/提交的场景
level: L2
triggers: [form, 表单, 输入, 登录, 注册, 设置, 填写, 提交, 按钮, 输入框, 搜索, checkbox, radio]
---

# 可访问性基础规则

生成代码时必须遵守以下最小可访问性要求。

## 颜色对比度

| 文本类型 | 最低对比度 |
|----------|----------|
| 正文（≤16px） | 4.5:1 |
| 大文本（>18px 或 14px 加粗） | 3:1 |
| UI 组件与相邻表面 | 3:1 |

- 正文在白色背景上必须使用 `text-gray-900` 或 `text-gray-800`，禁止 `text-gray-400` 以下
- 彩色按钮上文字必须用 `text-white` 配合足够深的背景色（如 `bg-blue-600`）

## 键盘导航

- 所有交互元素（按钮、链接、输入框）必须可通过 Tab 到达
- `:focus-visible` 必须有明显的焦点环样式（`ring-2 ring-offset-2`）
- 表单控件必须有对应的 `<label>` 标签，使用 `for` 属性关联

## 语义化 HTML

- 使用 `<header>`、`<main>`、`<nav>`、`<section>` 语义标签
- 标题层级不跳级：h1 → h2 → h3，不跳过 h2 直接到 h3
- 图片使用 `alt` 属性，装饰性图片使用 `alt=""`
- 列表使用 `<ul>/<ol>/<li>`，不用 `<div>` 模拟

## ARIA

- 自定义交互组件（如开关、弹窗、标签页）必须设置适当的 `role` 和 `aria-*` 属性
- 弹窗必须有 `role="dialog"` 和 `aria-modal="true"`
- 图标按钮必须有 `aria-label` 描述其功能
