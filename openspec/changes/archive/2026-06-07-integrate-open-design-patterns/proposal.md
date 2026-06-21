# Integrate Open Design Patterns

## Why

Talk2Code 当前的代码生成质量受限于单一的结构化 JSON 输出格式和固定的 Prompt 模板，缺乏对设计质量（可访问性、反 AI 刻板模式、排版规范）的系统性约束。Open Design 项目在 Skill 化架构、Craft 质量规则、交互式需求澄清等方面有成熟模式可以直接借鉴，提升 Talk2Code 的生成质量和可扩展性。

## What Changes

### 1. 引入 Craft 设计质量规则层（高优先级）
- 从 Open Design 的 `craft/` 中引入 `anti-ai-slop.md`、`accessibility-baseline.md`、`typography.md`、`color.md` 四个通用规则
- 在 `ENGINEER_PROMPT` 中注入对应的 Craft 规则，作为代码生成的质量约束
- 这些规则与具体应用类型无关，适用于所有代码生成场景

### 2. 重构 Fallback 模板为可插拔 Skill（中优先级）
- 将当前 `prompts.py` 中硬编码的 4 种应用模板（Todo/Calculator/Note/Calendar）抽取为独立的 Markdown Skill 文件
- 每个 Skill 包含：元数据（名称、触发词、场景）、设计约束、代码模板
- Planner 节点根据用户需求自动匹配 Skill，找不到匹配时使用通用模板
- **BREAKING**: `prompts.py` 中的 `_generate_todo_app_code()` 等内部函数移除，改为从 Skill 文件加载

### 3. 增加交互式需求澄清阶段（中优先级）
- 借鉴 Open Design 的 Discovery 模式，在 Planner 之前插入一个可选的澄清步骤
- 当用户需求过于模糊时（少于 20 字 或 缺少关键要素），Planner 生成结构化的问题表单
- 前端渲染问题表单，用户回答后补充到需求上下文中再继续生成
- 类似 Open Design 的 `<question-form>` → 交互式表单 → 锁定需求的流程

### 4. 标准化代码输出为 Artifact 协议（低优先级）
- 将当前的 `[{filename, content}]` JSON 格式升级为 Artifact 协议
- 增加 metadata 字段：`type`（text/html）、`title`、`identifier`
- 前端解析 Artifact 块而不是纯 JSON 数组
- 为未来支持多文件类型（图片、视频）打基础

### 5. 预览沙箱安全增强（低优先级）
- 借鉴 Open Design 的 iframe sandbox 策略
- 添加更严格的内容安全策略、导航拦截、postMessage 通信隔离
- 当前项目的 iframe 预览已有基础沙箱，但可以进一步强化

## Capabilities

### New Capabilities
- `craft-quality-rules`: 通用设计质量规则层，在代码生成 Prompt 中注入 anti-ai-slop、可访问性、排版、颜色规范
- `skill-based-templates`: 可插拔的 Skill 模板系统，将硬编码的应用类型替换为 Markdown Skill 文件，支持 Planner 自动匹配
- `interactive-discovery`: 交互式需求澄清流程，模糊需求时生成问题表单，用户补充后再继续

### Modified Capabilities
<!-- 无现有 spec 需要修改，这是 OpenSpec 的第一个 change -->

## Impact

**修改的文件：**
- `backend/prompts.py` — 注入 Craft 规则到 ENGINEER_PROMPT；重构 fallback 函数为 Skill 加载器
- `backend/agents/nodes.py` — Planner 增加模糊需求检测和问题表单生成；Engineer 增加 Craft 规则注入
- `backend/config.py` — 增加 Skill 目录配置项
- `frontend/detail.html` — 增加问题表单渲染组件
- 新增 `skills/` 目录 — 存放 Skill Markdown 文件
- 新增 `craft/` 目录 — 存放 Craft 规则文件

**不修改的部分：**
- `llm/client.py` — LLM 客户端接口不变
- `app.py` 路由层 — SSE 端点、认证逻辑不变
- `services/` — SSE 管理器、任务队列不变
- `models.py` — 数据库模型不变
