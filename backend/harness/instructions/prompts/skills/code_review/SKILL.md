---
name: code_review
description: 前端代码审查技能——按本项目硬约束对生成的前端代码做专项审查
when_to_use: 用户要求审查 / review / 检查代码质量时使用；也可在交付前主动调用
level: L1
type: workflow
triggers: [审查, review, 代码质量, 检查代码]
---

# 前端代码审查技能（code_review）

你被调用执行一次**前端代码审查**。本项目的产出是纯前端应用
（原生 HTML/CSS/JS、file:// 预览、IIFE、无构建、不接后端），
因此审查必须针对这些**项目硬约束**做专项检查，而不是泛泛的通用代码质量。

## 执行步骤

1. **定位范围**：用 `list_files` 列出工作区文件；若 `target` 指定了文件/目录，仅审查该范围。
2. **读取代码**：用 `read_file` 逐个读取目标文件，理解结构与依赖。
3. **静态检查**：对 JS 用 `lint_js`、对 CSS 用 `lint_css`、对 HTML 用 `validate_html`，
   收集确定性错误（语法 / 未闭合标签 / 未定义引用）。
4. **项目约束合规检查**（本项目独有，优先级最高）：
   - **禁止 ES Module**：不得出现 `import` / `export` / `<script type="module">`；
     每个 JS 必须用 IIFE 包裹 `(function (global) { ... })(window)`，通过 `window.XXX` 暴露接口，
     HTML 用普通 `<script src="js/xxx.js"></script>` 按依赖顺序引入
   - **无外部 CDN / 远程资源**：样式与脚本本地化，不依赖网络加载
   - **相对路径**：所有资源引用必须是相对路径，保证 file:// 协议下可加载
   - **自包含交付**：无 npm / 构建工具 / 第三方库依赖
   - **入口文件**：必须存在 `index.html`
   - **数据持久化**：数据必须走 localStorage / IndexedDB，封装在 `js/storage.js`，
     禁止在业务代码里散落裸 `localStorage.getItem/setItem`
5. **五维专项审查**（按 `focus` 收敛，未指定则全做）：
   - **correctness** 正确性：事件绑定、逻辑分支、边界条件、异步竞态、初始化时机（DOM 就绪后绑定）
   - **security** 安全：禁止 `innerHTML` / `eval` / `document.write`；用户输入经
     `textContent` / `createElement` 插入；不硬编码密钥
   - **accessibility** 可访问性：交互元素有可访问名、语义标签、键盘可达、颜色对比度足够
   - **performance** 性能：避免布局抖动、超大同步循环、频繁 DOM 重建；同类事件优先事件委托
   - **maintainability** 可维护性：职责单一、命名清晰、IIFE 内模块划分合理、无大段重复、魔法值提取为具名常量
6. **汇总**：给每条问题定级（critical / major / minor），给出文件、问题描述与修改建议；
   输出 0-100 的质量分与一句话总结。

## 反幻觉约束

- 只基于**实际读取到的代码**与**工具返回结果**写 findings，禁止编造未发生的错误。
- 每条 finding 必须能定位到具体文件与（尽量）具体行/函数。
- 若某维度无法验证（如无可运行环境），标注「未验证」而非臆断。

## 输出格式

按 output_schema 返回：`summary` / `findings[]`（severity、category、file、issue、suggestion）/ `score`。
先给总结与分数，再按严重度从高到低列出 findings。合规类问题（ES Module / CDN / 绝对路径）一律记 critical。
