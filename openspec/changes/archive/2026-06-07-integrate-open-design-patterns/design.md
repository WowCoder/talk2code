# Design: Integrate Open Design Patterns

## Context

Talk2Code 当前架构：用户输入需求 → Planner（LLM 分析）→ Coder（LLM 生成代码）→ SSE 推送前端。代码生成 Prompt 是固定模板，Fallback 模板硬编码在 Python 中，缺乏质量约束层。

Open Design 的核心模式值得借鉴但**不是照搬**：
- Open Design 是有状态 Daemon + Child Process 架构，Talk2Code 是 Flask + LangGraph
- Open Design 面向通用设计生成，Talk2Code 面向代码生成（HTML/CSS/JS 应用）
- 借鉴的是 **Prompt 工程模式** 和 **可扩展架构**，技术实现保持独立

## Goals / Non-Goals

**Goals:**
1. 在代码生成 Prompt 中注入通用设计质量规则（Craft 层），提升生成代码质量
2. 将硬编码的 4 种应用模板重构为可插拔的 Markdown Skill 文件
3. 为模糊需求增加交互式澄清环节，减少无效生成
4. 标准化代码输出格式为 Artifact 协议

**Non-Goals:**
- **不**引入 daemon/child-process 架构（保持 Flask + LangGraph）
- **不**支持多 Agent CLI 切换（保持 LLM API 直调）
- **不**引入图片/视频/PPT 生成能力
- **不**引入 Design System 系统（当前 4 种模板场景不匹配）
- **不**改变前端 SPA 架构（不引入 Next.js）

## Decisions

### 1. Craft 规则层：Prompt 注入而非 Post-Linting

**选择**: 将 Craft 规则作为 System Prompt 的一部分注入，而非生成后 Lint。

**理由**: Open Design 两者都有（Craft 注入 + `lint-artifact.ts` 后检查），但 Talk2Code 的 LLM 调用次数有限（2 次：Planner + Coder），额外 Lint 调用成本高。注入方式简单直接，LLM 在生成时就遵守规则。

**Craft 规则来源**: 从 Open Design `craft/` 目录中精选 4 个规则文件，翻译为中文，适配到代码生成场景：
- `anti-ai-slop.md` → 避免 AI 生成的刻板设计（如过度圆角、紫色渐变、无意义的 SVG 图标）
- `accessibility-baseline.md` → 基础可访问性（颜色对比度、键盘导航、ARIA 标签、focus 样式）
- `typography.md` → 排版规范（行高、字体层级、最大行宽）
- `color.md` → 色彩系统（OKLch 色板、语义色、暗色模式考虑）

**注入方式**: 在 `ENGINEER_PROMPT` 的 System Prompt 末尾追加，与现有约束并列，保持独立可开关。

### 2. Skill 系统：Markdown 文件 + YAML Frontmatter

**选择**: 每个应用类型一个 `skills/<name>/SKILL.md`，包含 YAML 元数据和 Markdown 模板。

**文件结构**:
```
backend/
└── skills/
    ├── todo/
    │   └── SKILL.md          # 待办清单 Skill
    ├── calculator/
    │   └── SKILL.md          # 计算器 Skill
    ├── note/
    │   └── SKILL.md          # 笔记 Skill
    ├── calendar/
    │   └── SKILL.md          # 日历 Skill
    └── generic/
        └── SKILL.md          # 通用 Skill
```

**SKILL.md 格式**:
```yaml
---
name: todo-app
description: 待办清单应用
triggers: [待办, todo, 清单, 任务, task]
craft_requires: [anti-ai-slop, accessibility-baseline, typography]
---

## 代码模板
<!-- 定义该应用类型的默认代码结构和约束 -->
...
```

**匹配逻辑**: `get_skill(requirement_text)` 函数遍历 `skills/` 目录，解析 YAML 元数据，用 `triggers` 关键词匹配用户需求。匹配不到时 fallback 到 `generic/SKILL.md`。

### 3. 交互式澄清：Planner 节点内嵌

**选择**: 在 Planner 节点开头增加需求评估 + 可选的问题表单生成，通过 SSE 推送到前端。

**流程**:
```
用户需求 → Planner 评估
  ├─ 需求明确 (>30字 且 含功能关键词) → 正常执行 Planner + Coder
  └─ 需求模糊 (<30字 或 无功能关键词) → 生成问题表单
      └─ SSE event: question-form → 前端渲染表单
          └─ 用户提交 → 补充到需求上下文 → 重新执行 Planner
```

**问题表单协议**:
```json
{
  "type": "question-form",
  "questions": [
    {"id": "q1", "type": "radio", "label": "应用类型", "options": ["工具类", "展示类", "游戏类"]},
    {"id": "q2", "type": "text", "label": "核心功能是什么？"}
  ]
}
```

### 4. Artifact 协议：兼容现有 JSON 格式

**选择**: 保持 `[{filename, content}]` 格式，增加可选的 metadata 字段。

**新格式**:
```json
{
  "artifact": {
    "type": "text/html",
    "title": "待办清单应用",
    "identifier": "todo-app-v1"
  },
  "files": [
    {"filename": "index.html", "content": "..."},
    {"filename": "style.css", "content": "..."},
    {"filename": "script.js", "content": "..."}
  ]
}
```

前端优先解析新格式，fallback 到旧格式（向后兼容）。

## Risks / Trade-offs

- **[Prompt 膨胀风险]** Craft 规则注入增加 System Prompt 长度 ~2-3KB，可能增加 token 消耗。→ 对模型做了可配置开关，按需启用。
- **[Skill 匹配不准]** 简单的关键词匹配可能误判。→ 引入 LLM 辅助匹配（在 Planner 中完成），关键词仅用于快速路由。
- **[问题表单循环]** 用户可能在澄清阶段反复回答。→ 最多 1 轮澄清，超过后强制进入生成。
- **[Fallback 模板质量]** 抽取为 Skill 后，模板维护从 Python 代码改为 Markdown，可能降低修改灵活性。→ 模板 JSON 保持独立，Skill 仅声明引用关系。

## Migration Plan

1. **Phase 1（无破坏性）**: 新增 Craft 规则文件和 Prompt 注入逻辑，不改变现有行为
2. **Phase 2（无破坏性）**: 新增 Skill 文件，同时保留旧的 Python 函数，两套并行验证
3. **Phase 3（破坏性）**: 移除旧的硬编码函数，切换到 Skill 系统
4. **回滚**: 每个 Phase 独立可回滚，通过 Feature Flag（配置开关）控制

## Open Questions

1. Craft 规则是否需要对 Planner 节点也注入？（当前方案仅注入 Coder）
2. Skill 匹配是否使用 LLM 还是纯关键词？（倾向 LLM 辅助，但需评估延迟）
3. 问题表单是否需要持久化到数据库？（当前方案仅前端暂存）
