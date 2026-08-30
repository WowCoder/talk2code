---
name: refactor
description: 前端代码重构技能——将现有代码重构成项目规范结构，功能不变
when_to_use: 代码经过多轮迭代后结构混乱、存在 ES Module 残留、重复代码或命名不一时使用
level: L1
type: workflow
triggers: [重构, refactor, 整理代码, 拆分文件, 模块化]
---

# 前端代码重构技能（refactor）

把现有前端代码重构成符合本项目规范的结构。**核心原则：重构不改变功能**，
改动后必须通过语法检查与冒烟验证。

## 重构目标（goals，未指定则按顺序全做）

1. **iife**：把裸全局脚本或残留的 ES Module（`import` / `export` / `<script type="module">`）
   改写成 `(function (global) { ... })(window)` + `window.XXX` 暴露接口，
   HTML 改为普通 `<script>` 按依赖顺序引入。
2. **module_split**：单文件过大时按职责拆分（如 app.js → storage.js / render.js / logic.js），
   同步更新 index.html 的 `<script>` 顺序。
3. **naming**：统一命名规范（语义化、风格统一、无拼音缩写、无 a1/b2 这类无意义命名）。
4. **dedupe**：消除重复代码，抽取公共函数；重复的 DOM 构建逻辑提取为工厂函数。
5. **const_extract**：魔法数字 / 硬编码字符串提取为具名常量，集中管理。
6. **storage_encap**：散落在业务代码中的裸 `localStorage.getItem/setItem` 收敛到
   `js/storage.js` 封装。

## 执行步骤

1. 用 `read_file` + `list_files` 读取目标代码，**先完整理解再动手**。
2. 根据 `goals` 逐项检查并记录需要改动的位置。
3. 用 `edit_file` / `write_file` 实施改造；**每次改动只做一件事**，保持可回滚。
4. 重构后验证：
   - 用 `lint_js` / `lint_css` / `validate_html` 确认语法无误；
   - 检查 index.html 的 `<script>` 顺序与引用是否与新结构一致；
   - 功能行为前后一致（不改变现有交互逻辑）。
5. 汇报改动文件与摘要。

## 反幻觉与安全约束

- 只重构**实际读取到**的代码，不改变现有功能行为。
- 重构过程禁止引入 `innerHTML` / `eval`，保持无 XSS。
- 若目标含 ES Module 但改不动（如无法安全拆分的第三方代码），标注原因而非强行改造。
