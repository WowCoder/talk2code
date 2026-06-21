# Talk2Code Harness 6 层架构改造 — 详细设计方案

> 基于 Addy Osmani "Agent Harness Engineering" 6 层架构模型
>
> 设计日期：2026-06-07

---

## 一、总览

### 1.1 当前架构请求-响应路径

一次完整的用户请求在当前系统中经历以下路径：

```
用户输入需求 (frontend/index.html)
  → POST /api/requirements (app.py:225)
    → TaskQueue.submit() (services/task_queue.py)
      → [ThreadPool 线程]
        → RequirementService.process_requirement() (services/requirement_service.py:89)
          → LangGraph workflow.stream() (agents/workflow.py)
            → planner_node() (agents/nodes.py:99)
              → LLMClient.chat() (llm/client.py:318)
              → 输出 Plan JSON 或 澄清问题
            → engineer_node() (agents/nodes.py:190)
              → LLMClient.chat() → 输出 Code JSON
              → 失败时: generate_fallback_code() (prompts.py:129)
          → SSE 推送: progress / dialogue / code / complete
  → 前端 SSE 接收渲染 (detail.html)
```

**关键特征**：
- Planner 和 Coder 各调用一次 LLM，单次生成全部代码
- LLM 没有工具可用，只能输出 JSON 文本
- 生成的代码无法运行验证，完全依赖 LLM 一次性正确
- 状态通过 LangGraph TypedDict 在内存中传递，无持久化检查点

### 1.2 6 层成熟度评分

| 层级 | 当前状态 | 评分 | 关键缺失 |
|:---:|---------|:---:|---------|
| **L1 指令层** | 静态 Prompt 模板 + Craft/Skill 字符串注入 | ★★★☆☆ | 动态上下文组装、指令版本管理 |
| **L2 工具层** | 无工具调用，LLM 单次 JSON 输出代码 | ★☆☆☆☆ | 工具注册表、file/code/web 工具、ReAct 循环 |
| **L3 环境层** | 仅前端 iframe 预览 + API 限流 | ★★☆☆☆ | 执行沙箱、权限审批、会话隔离 |
| **L4 状态层** | AgentState TypedDict + SQLite JSON 字段 | ★★★☆☆ | 文件系统、Git 版本化、跨会话记忆、断点恢复 |
| **L5 约束层** | Craft 规则建议注入 + Pydantic 校验 | ★★☆☆☆ | Hook 系统、自动化质量门禁、强制执行 |
| **L6 观测层** | 结构化日志 + SSE 进度 + 健康检查 | ★★☆☆☆ | 链路追踪、Token/成本统计、Session Replay |

### 1.3 改造后目标架构全景图

```
┌─────────────────────────────────────────────────────────────────┐
│                       Agent Harness Runtime                      │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ L1 指令层 (Instructions)                                    │  │
│  │ ContextAssembler → 动态组装 system prompt + craft + skill   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                ↓                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ L2 工具层 (Tools)                                           │  │
│  │ ToolRegistry ← file_tools / code_tools / web_tools          │  │
│  │ Agent Loop: Think → Tool Call → Observe → Think → Done     │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                ↓                                 │
│  ┌──────────────────────┬────────────────────────────────────┐  │
│  │ L3 环境层 (Env)       │ L5 约束层 (Constraints)            │  │
│  │ PermissionManager     │ HookManager                        │  │
│  │ SandboxExecutor       │ PreToolUse / PostToolUse hooks     │  │
│  │ SessionIsolator       │ Quality / Security / Craft gates   │  │
│  └──────────────────────┴────────────────────────────────────┘  │
│                                ↓                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ L4 状态层 (State)                                           │  │
│  │ WorkspaceFS ← GitRepo ← MemoryStore ← CheckpointManager    │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                ↓                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ L6 观测层 (Observability)                                   │  │
│  │ Tracer → CostTracker → SSEReporter → LogStore              │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.4 核心设计决策：为什么是 Multi-Agent 而不是单 ReAct Agent

**当前架构**：Planner + Coder 两个 Agent，各调用一次 LLM，单向传递 Plan JSON。

**改造后保持 Multi-Agent**：Planner + ReAct Coder。原因：

| 方案 | Planner 职责 | Coder 职责 | 适用性 |
|------|-------------|-----------|:---:|
| 单 ReAct Agent | 无结构化设计；在工具调用中边想边做 | 适合简单任务，复杂需求容易迷失方向 |
| **Planner + ReAct Coder（选择）** | 一次性推理：需求分析 → 结构化 Plan → 匹配 Skill → 必要时触发澄清或原型确认 | ReAct 工具循环：按 Plan 逐步实现 → 写文件 → 验证 → 修复 | 架构设计需要结构化思维，代码实现需要迭代验证，两者分开更高效 |
| 两个 ReAct Agent | Planner 也用工具（搜索、分析） | Coder 用工具（写文件、执行） | 过度设计，Planner 不需要工具 |

**Planner 的 4 种输出**（改造后）：

```
Planner 分析需求后：
  ├─ 需求模糊 → 触发澄清问题表单 (现有能力，保留)
  ├─ 需要确认视觉风格 → 触发设计原型确认 (新增！)
  ├─ 需求清晰 → 输出结构化 Plan → 交给 Coder
  └─ 需求超出能力范围 → 礼貌拒绝并说明原因 (新增)
```

### 1.5 改造原则

1. **渐进式**：每层独立演进，不破坏现有功能，任何阶段均可停止并交付
2. **可回滚**：新代码放在 `harness/` 目录，通过配置开关控制新旧实现切换
3. **保持可运行**：每个阶段结束时所有现有测试通过 + 新增测试覆盖
4. **棘轮原则 (The Ratchet)**：只在真实失败后添加约束，每条规则可追溯到具体事故

---

## 二、Layer 1 指令层 — 结构化增强

### 2.1 现状

**当前实现**（`backend/prompts.py`、`backend/craft_loader.py`、`backend/skill_loader.py`）：

- `PLANNER_PROMPT` 和 `ENGINEER_PROMPT` 是 LangChain `ChatPromptTemplate`，通过 `.format_messages()` 一次性填充变量
- Craft 规则通过 `{craft_rules}` 占位符以字符串拼接方式注入到 ENGINEER_PROMPT 的 system 消息末尾
- Skill 匹配（`match_skill()`）仅影响 planner_node 中的 `matched_skill` 字段标注，不实际改变提示词策略
- `CLAUDE.md` 仅用于指导 Claude Code CLI，不参与 Agent 运行时行为

**问题**：

1. **静态模板**：无论什么类型的需求，都使用同一套 Planner/Coder 提示词，只是替换 `{requirement}` 变量
2. **上下文膨胀**：Craft 规则无条件全部注入，无论当前需求是否需要（比如生成计算器时也注入无障碍规范）
3. **Skill 未充分集成**：Skill 匹配结果只作为标注，不改变 Coder 的提示词策略
4. **指令与代码耦合**：提示词模板和 fallback 代码生成器混在同一个 `prompts.py` 文件中（715 行）

### 2.2 改造内容

#### 2.2.1 动态上下文组装器 `ContextAssembler`

新增 `backend/harness/instructions/assembler.py`：

```python
class ContextAssembler:
    """根据需求类型动态组装 LLM 上下文"""

    def __init__(self, memory_store: MemoryStore = None):
        self.memory_store = memory_store

    def assemble(self, requirement: str, user_id: int, metadata: dict) -> AssembledContext:
        # 1. 加载通用 Skill（提供领域知识、易错点、检查清单）
        skill = self._load_generic_skill()

        # 2. 分析需求特征 → 按需选择 Craft 规则
        required_crafts = self._select_crafts(requirement)

        # 3. 检索长期记忆（用户偏好、项目背景）
        memories = ""
        if self.memory_store:
            recalled = self.memory_store.recall(user_id, requirement, top_k=5)
            if recalled:
                memories = "\n".join(
                    f"- [{m.memory_type}] {m.fact}"
                    for m in recalled
                )
                memories = f"\n\n## 用户偏好与项目背景\n{memories}"

        # 4. 按优先级组装：System Prompt > Skill 知识 > 长期记忆 > Craft 规则 > 需求
        return AssembledContext(
            system_prompt=self._build_system_prompt(),
            skill_instructions=skill.body if skill else "",
            long_term_memories=memories,
            craft_rules=load_craft_rules(required_crafts),
            user_prompt=requirement,
            metadata={"crafts": required_crafts, "memories_count": len(recalled) if recalled else 0}
        )

    def _load_generic_skill(self):
        """加载唯一的通用 Skill，提供前端开发最佳实践"""
        # 单一通用 Skill: skills/generic/SKILL.md
        # 包含: 通用前端开发规范、易错点、检查清单、浏览器存储方案选择指南
        return load_skill("generic")

    def _select_crafts(self, requirement) -> list[str]:
        """按需选择 Craft 规则"""
        selected = []
        features = self._analyze_features(requirement)
        if features.get('has_ui'):
            selected.extend(['typography', 'color'])
        if features.get('has_form'):
            selected.append('accessibility-baseline')
        if features.get('has_content'):
            selected.append('anti-ai-slop')
        return selected or get_default_craft_names()
```

#### 2.2.2 提示词模板引擎升级

从 `prompts.py` 迁移到 `backend/harness/instructions/prompts.py`：

- Prompt 模板与 Fallback 代码分离（Fallback 代码移到 `backend/harness/tools/code_fallback.py`）
- 按 Skill 提供差异化系统提示词（calculator / todo / note 各有不同的 System Prompt）
- 上下文长度监控：组装后的总 token 数超过阈值时自动裁剪低优先级内容

#### 2.2.3 上下文压缩机制（Context Compaction）

当前系统无压缩逻辑——初始生成时 `use_memory=False`（不带任何历史），后续对话时仅简单截断最近 10 条消息。引入工具调用循环后，消息历史会快速增长（每轮工具调用产生 2-4 条消息），必须有智能压缩机制。

**Token 预算模型**：

```
┌──────────────────────────────────────────────────────┐
│ 总预算：模型 context window 的 70%（如 80K → 56K）    │
│                                                      │
│ ┌──────────────────────────┐ 优先级 P0：永远保留       │
│ │ 固定指令层 (System Prompt) │  System Prompt + Skill 指令 + Craft 规则 │
│ │ 占用: ~2-5K tokens        │  这些是 Agent 的"行为准则"，压缩绝不能动  │
│ ├──────────────────────────┤                         │
│ │ 关键事实层                 │ 优先级 P1：压缩时保留    │
│ │ 占用: ~1-3K tokens        │  技术决策、文件清单、数据模型等结构化信息  │
│ ├──────────────────────────┤  由 Planner 输出，被标记为 important=true  │
│ │ 近期对话 (完整保留)        │                         │
│ │ 占用: 动态计算             │ 优先级 P2：滑动窗口保留   │
│ ├──────────────────────────┤  最近 N 轮对话完整保留    │
│ │ 历史摘要区                 │  N = 预算中剩余空间 / 平均每轮 tokens     │
│ │ 占用: ~500 tokens / 10轮  │                         │
│ └──────────────────────────┘ 优先级 P3：旧对话 → 摘要  │
│                              超出窗口的 → LLM 生成 1-2 句摘要替换原文    │
└──────────────────────────────────────────────────────┘
```

**压缩触发逻辑**：

```python
class ContextCompactor:
    COMPACTION_THRESHOLD = 0.85  # 上下文占用 > 85% 预算时触发压缩

    def maybe_compact(self, messages: list, budget: int) -> list:
        current_tokens = estimate_tokens(messages)
        if current_tokens < budget * self.COMPACTION_THRESHOLD:
            return messages  # 不需要压缩

        # 分层处理：
        # 1. P3 层：旧对话 → 摘要（最优先压缩）
        old_dialogues = select_old_dialogues(messages)
        summary = llm.summarize(old_dialogues)  # "用户要求添加搜索功能，Agent 修改了 script.js 并验证通过"
        messages = replace_with_summary(messages, old_dialogues, summary)

        # 2. 如果还不够 → P2 层：进一步缩减滑动窗口
        # 3. P1/P0 层：绝不压缩
        return messages
```

**不可压缩内容清单**（P0/P1 优先级）：
- System Prompt、Skill 指令、Craft 规则
- Planner 输出的结构化 Plan（技术选型、数据模型、文件结构）
- 当前工作区文件清单
- 最近一次工具调用的结果（如果失败，必须保留错误信息）
- 用户的最新消息

#### 2.2.4 Craft/Skill 注入增强

- Craft 规则从"全部注入"改为 `_select_crafts()` 按需注入
- Skill 的 `body` 字段（SKILL.md 正文）注入到 Coder 的 system prompt 中，提供具体实现指导
- 指令版本号：每次修改提示词时记录版本，便于追踪效果变化

#### 2.2.5 设计偏好发现（扩展现有 clarify 流程）

**现状**：`integrate-open-design-patterns` 已实现交互式需求澄清——
`_is_vague_requirement()` → `_generate_clarify_questions()` → SSE `question-form` → 前端表单 → `POST /clarify` → 重新执行。但澄清问题是纯功能导向的（"做什么类型应用？"），不涉及视觉风格。

**改造**：在现有的 `_generate_clarify_questions()` 的 LLM prompt 中增加一个检测维度——如果需求涉及 UI/页面/界面，则额外生成 1 个视觉风格偏好的问题。

**LLM prompt 修改**（`_generate_clarify_questions()` 中）：

```python
prompt = f"""用户提出需求："{requirement}"
这个需求比较模糊。请生成 2-3 个关键澄清问题。

生成规则：
1. 第一个问题始终是功能类型 ("你想做什么类型的应用？")
2. 如果需求涉及 UI/页面/界面，第二个问题询问视觉风格偏好 (radio，选项如下)
3. 第三个问题根据需要补充功能细节

视觉风格问题固定为：
{{"id": "visual_style", "type": "radio",
  "label": "你偏好哪种视觉风格？",
  "options": [
    "极简白 — 白色背景，灰黑文字，大量留白，功能优先",
    "暖柔风格 — 暖色调、圆角卡片、柔和阴影 (默认)",
    "暗黑科技 — 深色背景、霓虹强调色、终端风格",
    "活泼多彩 — 明亮渐变、大色块、趣味性设计",
    "无偏好，自动选择"
  ]
}}

只返回 JSON 数组，不要其他文字。"""
```

**优势**：完全复用现有 clarify 基础设施（SSE 事件、前端表单组件、`/clarify` 端点），不新增任何端点或前端组件。只是扩展了问题生成 prompt。

**用户回答后的处理**：风格选择通过 `POST /clarify` 提交后，拼接为 `[用户补充说明]\n视觉风格偏好: xxx`，Plan 中包含该偏好，进而影响 Coder 的 System Prompt 中的设计约束。

### 2.3 改造后风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 上下文膨胀导致 LLM 推理质量下降 | 代码生成质量降低 | `_select_crafts()` 按需选择；监控上下文 token 数；超过阈值自动裁剪 |
| 提示词变更导致现有需求输出变化 | 回归问题 | 保留旧提示词作为 `fallback` 策略；通过配置开关 `PROMPT_VERSION` 切换 |
| Skill 指令与 Craft 规则冲突 | Agent 行为不一致 | 定义优先级规则：System Prompt > Skill 指令 > Craft 规则；冲突时以后者为准但记录警告 |

### 2.4 后续演进方向

- **指令版本管理和 A/B 测试**：记录每次提示词变更，支持 A/B 对比生成质量
- **用户自定义 Craft**：允许用户创建自己的设计规范文件
- **自动优化**：根据生成代码的质量反馈（用户接受/拒绝）自动调整提示词权重

---

## 三、Layer 2 工具层 — 核心改造

### 3.1 现状

**当前实现**（`backend/agents/nodes.py`、`backend/agents/workflow.py`、`backend/prompts.py`）：

当前 Agent 的"代码生成"流程：
```
Planner: LLM.chat() → 输出 JSON Plan
    ↓
Coder:   LLM.chat() → 输出 JSON Code files
    ↓
如果 JSON 解析失败 → generate_fallback_code() 硬编码模板
```

**关键问题**：

1. **无工具调用**：LLM 无法读文件、写文件、执行代码、搜索文档。它只能一次性输出 JSON 文本
2. **无迭代验证**：生成的代码无法运行验证。语法错误、逻辑 bug 只能靠 LLM 自身正确性
3. **无增量修改**：代码修改通过 `CODE_EDIT_SYSTEM_PROMPT` 让 LLM 输出 unified diff，但仍然是"一次性输出 diff 文本"
4. **工作流是线性的**：Planner → Coder → END，没有循环、没有工具调用的条件分支

当前 `LLMClient.chat()` 不支持 function calling —— 它只发送 `messages` 数组并接收文本 `content`。要让 Agent 使用工具，需要在 `llm/client.py` 中扩展对 tools 参数的支持。

### 3.2 改造内容

#### 3.2.1 工具注册表 `ToolRegistry`

新增 `backend/harness/tools/registry.py`：

```python
from dataclasses import dataclass, field
from typing import Callable, Any

@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict          # JSON Schema
    handler: Callable         # 实际执行函数
    permission: str = "read"  # read | write | execute
    max_retries: int = 1

class ToolRegistry:
    """工具注册表 —— 所有 Agent 可用工具的单一注册源"""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition):
        self._tools[tool.name] = tool

    def get_schemas(self) -> list[dict]:
        """生成 LLM function calling 格式的工具描述"""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters
                }
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, arguments: dict) -> ToolResult:
        """执行工具调用，返回结果"""
        tool = self._tools.get(name)
        if not tool:
            return ToolResult(error=f"未知工具: {name}")
        try:
            output = tool.handler(**arguments)
            return ToolResult(content=output)
        except Exception as e:
            return ToolResult(error=str(e))
```

#### 3.2.2 首批工具清单

新增 `backend/harness/tools/file_tools.py`：

| 工具名 | 权限 | 描述 |
|--------|:----:|------|
| `read_file` | read | 读取工作区中的文件内容 |
| `write_file` | write | 创建或覆盖工作区中的文件 |
| `list_files` | read | 列出工作区中的所有文件 |
| `delete_file` | write | 删除工作区中的文件 |

新增 `backend/harness/tools/code_tools.py`：

| 工具名 | 权限 | 描述 |
|--------|:----:|------|
| `execute_code` | execute | 在沙箱中执行 HTML 文件，返回渲染结果或控制台输出 |
| `validate_html` | read | 校验 HTML 语法有效性 |
| `lint_css` | read | 检查 CSS 语法错误 |
| `lint_js` | read | 检查 JavaScript 语法错误（使用 AST 解析） |

新增 `backend/harness/tools/web_tools.py`：

| 工具名 | 权限 | 描述 |
|--------|:----:|------|
| `search_docs` | read | 搜索 MDN/CanIUse 文档获取 API 兼容性信息 |
| `fetch_cdn_library` | read | 获取主流 CDN 库（Tailwind/React 等）的最新版本号和使用示例 |

#### 3.2.3 文件生成策略：去除 3 文件限制

**当前问题**：`ENGINEER_PROMPT` 明确要求"生成 3 个文件：index.html、style.css、script.js"，这限制了复杂应用的代码组织结构。

**改造方案**：

1. **文件数量和结构自由**：Agent 可以生成任意数量的文件，可以使用子目录组织代码

   ```
   允许的文件结构示例:
   index.html
   css/
     ├── style.css
     └── components.css
   js/
     ├── app.js
     ├── storage.js
     └── utils.js
   assets/
     └── icons.svg
   ```

2. **前端沙箱可运行性约束**（替代硬性的 3 文件限制）：
   - 必须有一个 `index.html` 作为入口（Hook 检查）
   - 所有资源使用相对路径引用
   - 可以使用 CDN 引入第三方库（Tailwind、图标库等），**不使用 npm/构建工具**
   - 不涉及后端 API 调用（纯前端应用）

3. **数据持久化方案**（用于替代后端，保证沙箱可运行）：

   | 存储方案 | 适用场景 | 容量 | 示例 |
   |---------|---------|:---:|------|
   | `localStorage` | 简单键值数据、用户设置 | 5-10MB | 待办事项列表、用户偏好 |
   | `IndexedDB` | 结构化数据、大量记录 | 50MB+ | 笔记应用、日历事件、数据管理 |
   | `Cache API` | 静态资源缓存、离线支持 | 视浏览器而定 | PWA 离线缓存 |
   | `sessionStorage` | 会话级临时数据 | 5-10MB | 表单草稿、临时状态 |

   **存储方案选择指南**（注入到 Coder System Prompt 中）：

   ```
   ## 数据持久化（纯前端方案，不涉及后端）
   根据应用的数据特点选择合适的浏览器存储方案：
   - localStorage (5-10MB): 简单键值数据、用户设置、少量列表数据
   - IndexedDB (50MB+): 大量结构化数据、需要索引/查询/排序的数据
   - Cache API: 静态资源缓存、离线支持
   - sessionStorage: 表单草稿、页面级临时状态
   原则：简单数据用 localStorage 封装 getItem/setItem，大量数据用 IndexedDB 封装增删改查接口。
   数据操作封装在独立的 js/storage.js 文件中，在应用启动时检查数据完整性。
   ```
   - 大量结构化数据使用 IndexedDB，封装增删改查接口
   - 数据操作封装在独立的 js/storage.js 文件中
   - 在应用启动时检查数据完整性，异常时提供降级方案
   ```

#### 3.2.4 LLM Function Calling 协议适配

改造 `backend/llm/client.py` 的 `LLMClient` 类：

```python
class LLMClient:
    def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_choice: str = "auto",
    ) -> LLMResponseWithTools:
        """
        支持 function calling 的聊天接口

        返回 LLMResponseWithTools:
          - content: 文本回复
          - tool_calls: [{"name": "...", "arguments": {...}}] 或 None
        """
        # OpenAI 协议: 请求中加入 "tools" 和 "tool_choice" 字段
        # Anthropic 协议: 请求中加入 "tools" 字段（格式略有差异）
```

**协议差异处理**：

| 功能 | OpenAI 协议 | Anthropic 协议 |
|------|------------|---------------|
| 工具描述 | `tools: [{type: "function", function: {...}}]` | `tools: [{name, description, input_schema}]` |
| 工具调用响应 | `choices[0].message.tool_calls` | `content[{type: "tool_use", ...}]` |
| 工具结果回传 | 放入 `messages[]`，role=`tool` | 放入 `messages[]`，role=`user`，content 为 `tool_result` 块 |
| tool_choice | 支持 `auto`/`none`/`required` | 不支持，通过 `tool_choice` 字段控制 |

在 `_request_openai()` 和 `_request_anthropic()` 中分别适配。

#### 3.2.5 Agent 多轮工具调用循环

新增 `backend/agents/tool_loop.py`：

```python
class ToolCallLoop:
    """Agent 工具调用循环 —— ReAct 模式"""

    MAX_ITERATIONS = 10  # 防止无限循环

    def run(self, state: AgentState, tools: ToolRegistry) -> AgentState:
        client = get_client()

        for iteration in range(self.MAX_ITERATIONS):
            # 1. 调用 LLM，传入工具描述
            response = client.chat_with_tools(
                messages=self._build_messages(state),
                tools=tools.get_schemas(),
            )

            # 2. 如果没有工具调用 → Agent 认为任务完成
            if not response.tool_calls:
                state["current_step"] = "task_complete"
                state["dialogue_history"].append({
                    "role": "agent", "name": "Coder",
                    "content": response.content or "任务完成"
                })
                break

            # 3. 执行所有工具调用
            for tc in response.tool_calls:
                result = tools.execute(tc["name"], tc["arguments"])
                state["dialogue_history"].append({
                    "role": "tool_call",
                    "name": tc["name"],
                    "content": tc["arguments"],
                    "result": result.content if result.success else result.error
                })

            # 4. 将工具结果反馈给 LLM，进入下一轮
            if iteration >= self.MAX_ITERATIONS - 1:
                state["current_step"] = "max_iterations"
                break

        return state
```

**循环终止条件**：
1. LLM 返回无 tool_calls 的响应（自然完成）
2. 达到最大迭代次数（10 轮）
3. LLM 主动调用一个隐式的 `task_complete` 标记
4. 连续 3 轮工具调用无实质进展（防死循环）

#### 3.2.6 LangGraph 工作流改造

改造 `backend/agents/workflow.py`：

```
当前：planner → coder → END (带 coder 重试边)

改造后：
  planner → tool_coder → tool_executor ←─┐
                              │           │ (还有工具调用待执行)
                              ↓           │
                         route_tools ─────┘
                              │
                              ↓ (无工具调用 / 任务完成)
                            END
```

```python
def create_workflow_v2() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("planner", planner_node)
    workflow.add_node("tool_coder", tool_coder_node)     # 调用 LLM with tools
    workflow.add_node("tool_executor", tool_executor_node) # 执行工具调用

    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "tool_coder")

    # tool_coder → tool_executor (如果有 tool_calls) / END (如果完成)
    workflow.add_conditional_edges(
        "tool_coder",
        should_use_tools,   # 检查 response 中是否有 tool_calls
        {"execute": "tool_executor", "done": END}
    )
    # tool_executor → tool_coder (循环回去)
    workflow.add_edge("tool_executor", "tool_coder")

    return workflow.compile()
```

#### 3.2.7 前端 SSE 事件扩展

新增 SSE 事件类型，让用户实时看到 Agent 在做什么：

```
event: tool_call
data: {"tool_name": "write_file", "arguments": {"filename": "index.html", ...}}

event: tool_result
data: {"tool_name": "write_file", "success": true, "summary": "已写入 index.html (120 行)"}

event: tool_result
data: {"tool_name": "execute_code", "success": false, "error": "JavaScript 语法错误：第 45 行缺少分号"}

event: thinking
data: {"content": "发现 JS 语法错误，正在修复..."}
```

**前端改造**（`detail.html`）：
- 对话面板新增"工具调用"消息类型，展示工具图标 + 名称 + 执行结果
- 代码面板实时更新文件树（工具写入的文件立即反映到文件树）

#### 3.2.8 生成后继续对话与代码修改

首次生成完成后，用户可以通过 `/chat` 端点继续对话修改代码。与首次生成不同，后续修改**不需要重新跑 Planner**，直接复用 Coder 的 ReAct 工具调用循环。

**流程对比**：

```
首次生成                           后续修改
──────────                        ──────────
POST /api/requirements             POST /api/requirements/<id>/chat
  → Planner (分析需求 + Plan)        → 跳过 Planner（设计阶段已完成）
  → Coder ReAct 循环                 → 直接进入 Coder ReAct 循环：
    (从零生成代码)                      1. 加载 WorkspaceFS 现有代码
  → 任务完成                                (已有完整文件和 Git 历史)
                                        2. 加载 dialogue_history（上下文压缩）
                                        3. 用户消息 + 代码 + 上下文 → LLM
                                        4. LLM → tool_calls:
                                           read_file(需要修改的文件)
                                           write_file(修改内容)
                                           execute_code(验证修改结果)
                                        5. Hook 检查 → SSE 推送
                                        6. Git commit(增量修改)
                                        7. 更新 DB code_files
```

**与首次生成的区别**：

| 维度 | 首次生成 | 后续修改 |
|------|---------|---------|
| Planner | ✅ 执行 | ❌ 跳过 |
| Coder 工作目录 | WorkspaceFS.init() 空目录 | 已有文件，增量修改 |
| 上下文 | 仅 Plan + 需求描述 | 已有完整 dialogue_history（需压缩） |
| 工具策略 | 从零 write_file 所有文件 | read_file 了解现状 → write_file 修改特定文件 |
| 约束检查 | 全部 Hook 执行 | 只对修改过的文件执行 Hook（节省时间） |
| 设计风格确认 | 可能需要 | 不需要（首次已确定） |
| Git | init + 首次 commit | 在已有 repo 上增量 commit |

**API 改造**（`POST /api/requirements/<id>/chat`）：

```python
# 现有 chat 端点逻辑过于复杂（手动拼上下文、格式化 diff prompt）
# 改造后简化为：创建 Coder 工具循环，由 Agent 自己决定改哪些文件

@app.route('/api/requirements/<int:req_id>/chat', methods=['POST'])
def chat_with_requirement(req_id):
    user_id = get_jwt_identity()
    user_message = request.get_json().get('message')

    # 1. 双重校验身份 + 加载状态
    requirement = db.query(Requirement).filter_by(id=req_id, user_id=user_id).first()
    workspace = WorkspaceFS(user_id, req_id)
    workspace.init(requirement.code_files)
    git = GitVersioning(workspace)

    # 2. 保存用户消息到 dialogue_history
    requirement.dialogue_history.append({
        'role': 'user', 'content': user_message,
        'timestamp': get_current_timestamp()
    })

    # 3. 上下文压缩（对话历史可能很长）
    context = ContextCompactor().maybe_compact(
        requirement.dialogue_history,
        budget=estimate_budget()
    )

    # 4. 进入 Coder ReAct 循环（与首次生成共用同一个 ToolCallLoop）
    state = AgentState(
        requirement_content=requirement.content,
        dialogue_history=context,
        code_files=requirement.code_files,
        ...
    )
    loop = ToolCallLoop(workspace, git, tools, hooks)
    final_state = loop.run(state)

    # 5. 保存结果
    requirement.code_files = workspace.snapshot()
    requirement.dialogue_history = final_state['dialogue_history']
    db.commit()

    return jsonify({'code_files': requirement.code_files, ...})
```

**关键设计**：首次生成和后续修改**共用一个 `ToolCallLoop`**，区别仅在于初始状态不同（空目录 vs 已有代码）。这避免了维护两套代码生成逻辑。

### 3.3 改造后风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 工具调用增加多轮 LLM 请求，单次需求耗时从 10s → 60s+ | 用户体验下降 | 设置最大 10 轮限制；前端展示每一步工具调用，让等待可感知；提供"快速模式"（跳过工具调用，回退到单次生成） |
| LLM 滥用工具（无限循环写入/执行） | 资源耗尽、安全问题 | 最大 10 轮硬性限制；权限分级（execute 需用户确认）；连续 3 轮无进展自动终止 |
| 要求 LLM 模型支持 function calling | 部分模型不可用 | 在 `LLMClient` 中检测，不支持 tools 的模型自动降级到旧版单次生成模式 |
| 工具调用出错导致 Agent 行为退化 | 代码生成失败率上升 | 每个工具 1 次自动重试；工具失败 3 次后降级到 fallback 模板 |

### 3.4 后续演进方向

- **MCP (Model Context Protocol) 集成**：将工具注册表对接到 MCP Server，让社区工具可直接使用
- **用户自定义工具**：允许用户在 `tools/` 目录下添加自定义工具脚本
- **工具组合编排**：定义"工具链"（如修改代码后自动 lint + 执行），减少重复的工具调用模式

---

## 四、Layer 3 环境层 — 安全边界

### 4.1 现状

**当前实现**：

- **前端 iframe 沙箱**（`detail.html`）：代码预览通过 `sandbox="allow-scripts allow-same-origin"` 的 iframe 实现，有 CSP 限制
- **API 限流**（`utils/rate_limiter.py`）：Flask-Limiter 按端点 + 用户身份限流
- **无 Agent 执行沙箱**：LLM 生成的代码不会在服务端实际运行
- **无权限审批**：所有 API 操作对登录用户完全放行

**问题**：

1. 引入工具层后，Agent 可以 `write_file`、`execute_code`，需要安全边界
2. 前端 iframe 沙箱不足以防护服务端的代码执行
3. 缺少用户对 Agent 工具调用的可见性和控制权

### 4.2 改造内容

#### 4.2.1 工具权限分级模型

新增 `backend/harness/environment/permissions.py`：

| 级别 | 名称 | 典型工具 | 审批要求 |
|:---:|------|---------|---------|
| **0** | 只读 | `read_file`, `list_files`, `search_docs`, `validate_html`, `lint_css`, `lint_js`, `fetch_cdn_library` | 自动放行 |
| **1** | 写入 | `write_file`, `delete_file` | 首次请求时用户一次性授权；后续自动放行 |
| **2** | 执行 | `execute_code` | 每次调用都需要用户审批 |

```python
class PermissionManager:
    """工具调用权限管理器"""

    def check(self, requirement_id: str, tool_name: str) -> PermissionResult:
        level = self._get_level(tool_name)
        if level == 0:
            return PermissionResult.ALLOW
        if level == 1:
            return self._check_write_permission(requirement_id)
        if level == 2:
            return PermissionResult.NEEDS_APPROVAL  # 每次都需要

    def grant(self, requirement_id: str, level: int):
        """用户授权后记录"""
        # 写入 Redis/内存，有效期到会话结束
```

#### 4.2.2 权限审批前后端流程

**后端新增 SSE 事件**：

```
event: permission_request
data: {"tool_name": "execute_code", "arguments": {...}, "reason": "Agent 请求执行 index.html 验证代码正确性"}
```

**前端审批 UI**（`detail.html`）：
- 在对话面板中插入"权限确认"卡片：显示工具名称、参数、原因
- 两个按钮：`[允许]` `[拒绝]`
- 30 秒超时自动拒绝

**后端接收审批**：

新增 `POST /api/requirements/<id>/permission` 端点，接受 `{tool_call_id, decision}` 并挂起/恢复 Agent 执行。

#### 4.2.3 代码执行沙箱

新增 `backend/harness/environment/sandbox.py`：

```python
class SandboxExecutor:
    """
    HTML/CSS/JS 代码执行沙箱

    方案：subprocess 调用 headless Chrome (puppeteer) 或使用 PyMiniRacer (轻量 JS 引擎)
    初期采用 subprocess + Node.js 的简化方案
    """

    def execute(self, files: dict[str, str]) -> SandboxResult:
        # 1. 创建临时目录 /tmp/talk2code/{requirement_id}/{run_id}/
        # 2. 写入所有文件
        # 3. subprocess 调用 node 运行 JS 语法检查
        # 4. 对于 HTML：使用 html5lib 校验
        # 5. 资源限制：30s 超时，50MB 内存限制
        # 6. 清理临时目录
        pass
```

**安全措施**：
- 每次执行在独立临时目录
- 进程级超时和内存限制（`resource` 模块 + `subprocess.timeout`）
- 禁止网络访问（沙箱内无网络权限）
- 禁止文件系统写入超出临时目录

#### 4.2.4 用户会话隔离

- 每个 `requirement_id` 对应独立的 `workspace/` 目录
- 每个 `requirement_id` 的 Agent 实例和 LLM 会话内存完全隔离
- `TaskQueue` 中同一 `requirement_id` 不允许并发执行

### 4.3 改造后风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 沙箱逃逸（Node.js/Puppeteer 漏洞） | 服务端安全风险 | 使用最小权限原则；进程级隔离而非容器级；监控异常进程行为 |
| 权限审批打断体验 | 用户需要频繁确认，体验差 | Level 1 写入权限只在首次请求时确认；Level 2 执行权限可配置"本次会话自动允许" |
| 权限审批超时 | Agent 等待超时后行为不确定 | 30s 超时默认拒绝；Agent loop 中处理 `permission_denied` 作为工具失败 |

### 4.4 后续演进方向

- **容器级沙箱**：Docker / gVisor 提供更强隔离
- **网络策略精细化**：按域名白名单控制沙箱的外网访问
- **执行预算**：按用户配额限制代码执行总次数

---

## 五、Layer 4 状态层 — 状态外置

### 5.1 现状

**当前实现**（`backend/agents/state.py`、`backend/models/models.py`、`backend/llm/client.py`）：

```python
# AgentState: LangGraph 运行时内存状态
class AgentState(TypedDict):
    requirement_id: int
    requirement_content: str
    plan: Optional[dict]
    current_step: str
    code_files: Optional[List[dict]]     # [{filename, content}]
    dialogue_history: List[dict]         # 对话记录
    retry_count: int
    error: Optional[str]
    metadata: dict

# Requirement 表持久化
dialogue_history = Column(JSON)  # 对话历史序列化为 JSON
code_files = Column(JSON)        # 代码文件序列化为 JSON
```

**问题**：

1. **无文件系统**：代码文件仅存在于 `AgentState` 内存和数据库 JSON 字段中，没有真实的文件系统表示。工具层需要可读写的文件
2. **无版本化**：每次代码修改直接覆盖，无法回滚到之前版本
3. **无跨会话记忆**：`LLMClient` 的 `_messages` 是进程内内存，服务重启后丢失。用户偏好、常用技术栈等信息无法跨需求复用
4. **无断点恢复**：LangGraph 工作流执行中断（进程崩溃、超时）后，所有中间状态丢失，无法从断点恢复

### 5.2 改造内容

#### 5.2.1 运行时文件系统 `WorkspaceFS`

新增 `backend/harness/state/workspace.py`：

```python
import os
import shutil
from pathlib import Path

class WorkspaceFS:
    """
    每个需求一个独立工作目录，三层隔离保证安全：
    Layer 1: 路径包含 user_id → 物理隔离不同用户
    Layer 2: _validate() 拒绝路径穿越 → 限制在工作目录内
    Layer 3: TaskQueue 并发控制 → 同一 requirement_id 只允许一个线程
    """

    BASE_DIR = Path("/tmp/talk2code/workspaces")

    def __init__(self, user_id: int, requirement_id: int):
        self.user_id = user_id
        self.req_id = requirement_id
        self.path = self.BASE_DIR / str(user_id) / str(requirement_id)

    def _validate(self, filename: str):
        """防止路径穿越：拒绝 ../ 上级目录和绝对路径"""
        if '..' in filename or filename.startswith('/'):
            raise PermissionError(f"非法文件路径: {filename}")
        full_path = (self.path / filename).resolve()
        if not str(full_path).startswith(str(self.path.resolve())):
            raise PermissionError(f"禁止访问工作目录外的文件: {filename}")

    def init(self, code_files: list[dict] = None):
        self.path.mkdir(parents=True, exist_ok=True)
        if code_files:
            for f in code_files:
                self._validate(f["filename"])
                (self.path / f["filename"]).write_text(f["content"], encoding="utf-8")

    def read(self, filename: str) -> str:
        self._validate(filename)
        return (self.path / filename).read_text(encoding="utf-8")

    def write(self, filename: str, content: str):
        self._validate(filename)
        (self.path / filename).parent.mkdir(parents=True, exist_ok=True)
        (self.path / filename).write_text(content, encoding="utf-8")

    def list(self) -> list[str]:
        files = []
        for f in self.path.rglob("*"):
            if f.is_file() and '.git' not in f.parts:
                files.append(str(f.relative_to(self.path)))
        return files

    def delete(self, filename: str):
        self._validate(filename)
        (self.path / filename).unlink()

    def snapshot(self) -> list[dict]:
        return [
            {"filename": str(f.relative_to(self.path)), "content": f.read_text(encoding="utf-8")}
            for f in self.path.rglob("*") if f.is_file() and '.git' not in f.parts
        ]

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)
```

**工具层对接**：`file_tools.py` 中的文件操作工具直接操作 `WorkspaceFS`，每次调用都经过 `_validate()` 安全检查。

#### 5.2.2 Git 版本化

新增 `backend/harness/state/versioning.py`：

```python
import subprocess
from pathlib import Path

class GitVersioning:
    """每次代码变更自动 commit，支持 diff 和回滚"""

    def __init__(self, workspace: WorkspaceFS):
        self.workspace = workspace

    def init(self):
        """初始化 git 仓库"""
        subprocess.run(["git", "init"], cwd=self.workspace.path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Talk2Code Agent"],
                       cwd=self.workspace.path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "agent@talk2code.local"],
                       cwd=self.workspace.path, capture_output=True)

    def commit(self, message: str) -> str:
        """暂存所有变更并提交，返回 commit hash"""
        subprocess.run(["git", "add", "-A"], cwd=self.workspace.path)
        result = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=self.workspace.path, capture_output=True, text=True
        )
        # 提取 commit hash
        return self._get_head()

    def log(self, max_count: int = 20) -> list[dict]:
        """获取提交历史"""
        result = subprocess.run(
            ["git", "log", f"-{max_count}", "--format=%H|%s|%ai"],
            cwd=self.workspace.path, capture_output=True, text=True
        )
        return [
            {"hash": parts[0], "message": parts[1], "time": parts[2]}
            for line in result.stdout.strip().split("\n") if line
            for parts in [line.split("|", 2)]
        ]

    def rollback(self, commit_hash: str) -> bool:
        """回滚到指定 commit"""
        result = subprocess.run(
            ["git", "reset", "--hard", commit_hash],
            cwd=self.workspace.path, capture_output=True
        )
        return result.returncode == 0

    def _get_head(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.workspace.path, capture_output=True, text=True
        )
        return result.stdout.strip()
```

**提交时机**：
- 每次 `write_file` 工具调用成功后自动 commit，消息为 `"[tool] write_file: {filename}"`
- 工具调用循环结束时 commit，消息为 `"[agent] task complete"`
- `chat` 接口的 diff 应用后 commit，消息为 `"[user] chat modification #{n}"`

#### 5.2.3 跨会话记忆存储

新增 `backend/harness/state/memory_store.py`。

**什么值得存成长期记忆 —— 判断规则**：

```python
# 应该存（长期记忆）                      # 不应存（会话内即可，压缩掉）
──────────────────────────────────     ──────────────────────────────
用户："我习惯用 React + TypeScript"     单次对话中的具体实现细节
用户："这个项目是电商后台管理系统"        某次代码生成的中间 bug 修复过程
用户拒绝方案A，选择了方案B               某轮工具调用的中间结果
Agent 发现该用户的API Key限制某些模型    单次执行的 trace/span 数据
用户："颜色方案用暖色系"                 "帮我把按钮改成蓝色"（已反映在代码中）
Agent 连续3次在同一个Hook上失败           一次性任务的状态快照
```

**判断实现 — LLM 驱动的记忆提取**：

> 关键词匹配太脆弱（"我要一个管理后台"不会匹配到任何关键词）。直接让 LLM 判读对话记录，输出值得长期记忆的事实。

```python
class MemoryStore:
    def extract_memories(self, dialogue_context: list[dict]) -> list[MemoryItem]:
        """
        每次 Agent 任务完成后，用 LLM 扫描本轮对话，
        提取值得长期记忆的内容。在 ON_TASK_COMPLETE Hook 中触发。

        输入: 本轮对话记录（最近 5-10 轮）
        输出: [{fact, memory_type, importance, reason}]
        """
        prompt = f"""以下是用户与 AI 编程助手的对话记录。请判断：对话中有哪些信息值得作为"长期记忆"保存？

值得记住的信息（重要性 0.7-1.0）：
1. 用户偏好: 明确表达的技术/设计偏好 ("我想用React", "颜色用暖色系")
2. 项目背景: 项目目标、领域、约束 ("这是电商后台", "需要支持移动端")
3. 重要决策: 用户确认的设计/技术决策 ("选择了方案B")
4. 错误经验: 用户纠正过的错误方案 ("上次JS eval有问题，不要用")

不应记住的信息：
- 单次任务的执行细节 ("生成了15行代码")
- 已在代码文件中体现的信息
- LLM 能从对话上下文自行推理出的信息

对话记录：
{self._format_dialogues(dialogue_context)}

返回 JSON 数组，每条: {{"fact": "一句话描述", "type": "user_preference|domain_knowledge|agent_lesson|user_feedback", "importance": 0.0-1.0, "reason": "为什么值得记住"}}
如果没有值得长期记忆的内容，返回空数组 []。"""

        response = client.chat(prompt, max_tokens=500)
        return self._parse_memory_response(response.content)
```

**调用时机**：在 HookManager 的 `ON_TASK_COMPLETE` Hook 中触发，每次任务结束后自动扫描本轮对话提取记忆。

**长期记忆检索 —— 两阶段方案**：

```
阶段1 (记忆 ≤ 10条): LLM 直接判读

  已存记忆列表 + 当前需求 → 发给 LLM：
  "以下是用户的偏好和历史信息。请判断哪些与当前需求相关，返回相关记忆的ID列表。
   只返回真正相关的，不相关的不返回。"
  → LLM 筛选出 0-5 条 → 注入 Coder system prompt
  → 额外成本: ~200 tokens/次，零额外基础设施

阶段2 (记忆 > 10条): embedding 初筛 + LLM 精排

  1. text-embedding-3-small 将所有记忆向量化 (成本 $0.02/1M tokens)
  2. 对当前需求做 embedding，取 top-K=10 条相似记忆
  3. 将 10 条候选 + 需求发给 LLM 精排
  → 成本: embedding $0.00004 + LLM ~200 tokens
  → 需要: 新增一个 embedding API 调用
```

**记忆生命周期管理**：

```
创建 ──→ 活跃使用 ──→ 衰减 ──→ 归档/删除
 │           │           │           │
 │     每次被 recall()   │      importance < 0.1
 │     时 importance     │      且 30天未访问
 │     小幅增加 (+0.05)   │
 │                       │
 │              时间衰减: importance *= 0.95^(days/7)
 │              (每7天不访问，重要性衰减5%)
```

```python
class MemoryStore:
    def remember(self, fact: str, memory_type: str, importance: float):
        """写入新记忆，处理冲突和重复"""
        existing = self._find_similar(fact, threshold=0.7)
        if existing:
            if self._is_contradiction(existing, fact):
                # 冲突：用户偏好改变了 → 新信息覆盖旧
                existing.fact = fact
                existing.importance = max(importance, existing.importance + 0.1)
            else:
                # 重复/强化：提高重要性
                existing.importance = min(1.0, existing.importance + 0.05)
                existing.last_accessed_at = func.now()
        else:
            db.add(AgentMemory(
                user_id=self.user_id, fact=fact, memory_type=memory_type,
                importance=importance, created_at=func.now()
            ))

    def recall(self, query: str, top_k: int = 5) -> list[dict]:
        """
        两阶段检索:
        - 记忆 ≤ 10条: LLM直接判读筛选
        - 记忆 > 10条: embedding初筛(cosine top-10) → LLM精排(top_k)
        返回: [{fact, importance, memory_type}, ...]

        重要：每次 recall 成功命中后，更新 last_accessed_at 和 importance，
        防止高价值记忆因"未被访问"而随时间衰减被误删。
        """
        all_memories = self._get_active_memories()  # importance > 0.1
        if len(all_memories) <= 10:
            results = self._llm_filter(all_memories, query, top_k)
        else:
            candidates = self._embedding_search(query, all_memories, limit=10)
            results = self._llm_filter(candidates, query, top_k)

        # 标记访问：防止高价值记忆被衰减误删
        for item in results:
            mem = db.query(AgentMemory).filter_by(id=item.id).first()
            if mem:
                mem.last_accessed_at = func.now()
                mem.access_count += 1
                # 每次被成功 recall，重要性小幅提升（上限 1.0）
                mem.importance = min(1.0, mem.importance + 0.02)
        db.commit()

        return results

    def decay(self):
        """后台定时任务: 每天执行一次"""
        for m in self._get_all():
            days = (func.now() - m.last_accessed_at).days
            m.importance *= 0.95 ** max(days / 7, 1)
        # 清理 importance < 0.1 且 30天未访问的记忆
        self._cleanup(min_importance=0.1, max_idle_days=30)
```

**记忆的注入方式**（在 `ContextAssembler.assemble()` 中）：

```python
# 组装上下文时注入相关记忆
memories = memory_store.recall(user_id, requirement, top_k=5)
if memories:
    memory_text = "\n".join(
        f"- [{m.memory_type}] {m.fact} (重要性: {m.importance:.2f})"
        for m in memories
    )
    system_prompt += f"\n\n## 用户偏好与项目背景\n{memory_text}"
```

**数据库表**（在 `models/models.py` 中新增）：

```python
class AgentMemory(Base):
    __tablename__ = "agent_memories"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    requirement_id = Column(Integer, nullable=True)
    memory_type = Column(String(32))
    fact = Column(Text)
    importance = Column(Float, default=0.5)
    access_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())
    last_accessed_at = Column(DateTime)
```

#### 5.2.4 Checkpoint 断点恢复

改造 `backend/agents/state.py`：

```python
from dataclasses import dataclass
@dataclass
class Checkpoint:
    """工作流检查点"""
    id: str
    requirement_id: int
    node_name: str       # 当前节点: planner / tool_coder / tool_executor
    state: AgentState    # 完整状态快照
    created_at: str

class CheckpointManager:
    """持久化检查点，支持断点恢复"""

    def save(self, requirement_id: int, node_name: str, state: AgentState) -> str:
        """序列化状态到 SQLite"""
        checkpoint_json = json.dumps(state, default=str)
        # INSERT INTO agent_checkpoints ...

    def load(self, requirement_id: int) -> Checkpoint | None:
        """加载最近的检查点"""

    def resume(self, requirement_id: int) -> AgentState | None:
        """从检查点恢复状态，继续执行"""
```

**保存时机**：
- 每个 LangGraph 节点执行完成后自动保存检查点
- 工具调用循环中每 3 轮保存一次（减少 I/O）

**恢复机制**（在 `RequirementService.process_requirement()` 中）：
```python
# 处理开始前检查是否有未完成的检查点
checkpoint = checkpoint_manager.load(requirement_id)
if checkpoint and checkpoint.node_name != 'end':
    logger.info(f"从检查点恢复需求 {requirement_id}")
    initial_state = checkpoint.state  # 使用断点状态而非新建
```

### 5.3 改造后风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 工作目录磁盘占用增长 | 磁盘满导致服务不可用 | 定期清理 7 天前的工作目录；设置磁盘配额上限（每需求 100MB） |
| Git 操作在文件频繁变更时开销大 | Agent 循环变慢 | `write_file` 累积后批量 commit（最多延迟 3 次写入）；Git 操作异步执行 |
| 记忆污染/冲突 | 错误记忆影响后续生成 | 记忆重要性衰减；重要度低于阈值的自动清理；用户可手动管理记忆 |
| 检查点 I/O 开销 | 频繁序列化大状态影响性能 | 仅持久化关键字段；每 3 轮保存一次；异步写入 |

### 5.4 后续演进方向

- **向量库语义检索**：升级 `MemoryStore.recall()` 从关键词匹配到 embedding + 向量库 (ChromaDB/SQLite-vss)
- **记忆重要性衰减**：基于时间、访问频率、用户反馈自动调整重要性权重
- **多 Agent 共享状态**：多个并发 Agent 可通过共享的 MemoryStore 交换上下文

---

## 六、Layer 5 约束层 — Hook 系统

### 6.1 现状

**当前实现**：

- **Craft 规则注入**（`agents/nodes.py:204-209`）：`load_craft_rules()` 将 Markdown 规则作为字符串拼入系统提示词 `{craft_rules}` 占位符
- **Pydantic Schema 校验**（`models/schema.py`）：`CodeFile` 校验文件扩展名和内容，`CodeGenerationResponse` 校验文件唯一性
- **Fallback 兜底**（`agents/nodes.py:248,265-266`）：JSON 解析失败或 LLM 异常时使用硬编码模板
- **JSON 提取正则**（`agents/nodes.py:34-40`）：尝试从 LLM 响应中正则提取 JSON

**问题**：

1. **Craft 规则是"建议性"的**：只是注入到 Prompt 中让 LLM "遵守"，没有任何机制验证 LLM 是否真的遵守了
2. **无 Hook 系统**：无法在工具调用前后插入检查逻辑
3. **无自动化质量门禁**：生成的代码不经任何自动化检查（HTML 有效性、CSS 语法、JS 语法、XSS 风险）
4. **Pydantic 校验太弱**：只检查文件扩展名，不检查代码内容质量

### 6.2 改造内容

#### 6.2.1 Hook 管理器

新增 `backend/harness/constraints/hooks.py`：

```python
from enum import Enum
from dataclasses import dataclass

class HookPoint(Enum):
    PRE_TOOL_USE = "pre_tool_use"       # 工具调用前
    POST_TOOL_USE = "post_tool_use"     # 工具调用后
    PRE_LLM_CALL = "pre_llm_call"       # LLM 调用前
    POST_LLM_CALL = "post_llm_call"     # LLM 调用后
    ON_ERROR = "on_error"               # 发生错误时
    ON_TASK_COMPLETE = "on_task_complete"  # 任务完成时

@dataclass
class HookContext:
    requirement_id: int
    tool_name: str | None
    tool_args: dict | None
    tool_result: str | None
    state: dict

class HookManager:
    """
    Hook 管理器 —— 生命周期事件触发检查

    原则：成功静默，失败喧哗。
    Hook 检查通过时不返回任何信息（避免污染 Agent Context），
    失败时才将错误信息塞回 Agent Loop。
    """

    def __init__(self):
        self._hooks: dict[HookPoint, list[callable]] = {
            hp: [] for hp in HookPoint
        }

    def register(self, point: HookPoint, hook: callable):
        self._hooks[point].append(hook)

    def trigger(self, point: HookPoint, ctx: HookContext) -> list[str]:
        """触发指定生命周期的所有 Hook，返回失败信息列表（空列表表示全部通过）"""
        failures = []
        for hook in self._hooks[point]:
            try:
                result = hook(ctx)
                if result:  # 非空 = 检查失败
                    failures.append(result)
            except Exception as e:
                failures.append(f"Hook [{hook.__name__}] 异常: {e}")
        return failures
```

#### 6.2.2 首批 Hook 实现

新增 `backend/harness/constraints/quality.py`：

```python
def html_validity_hook(ctx: HookContext) -> str | None:
    """检查 HTML 语法有效性"""
    if ctx.tool_name == "write_file" and ctx.tool_args["filename"].endswith(".html"):
        try:
            from html5lib import parse
            parse(ctx.tool_args["content"])
        except Exception as e:
            return f"HTML 语法错误：{e}"

def css_lint_hook(ctx: HookContext) -> str | None:
    """检查 CSS 语法"""
    if ctx.tool_name == "write_file" and ctx.tool_args["filename"].endswith(".css"):
        # 使用 cssutils 或 tinycss2 校验
        pass

def js_syntax_hook(ctx: HookContext) -> str | None:
    """检查 JS 语法"""
    if ctx.tool_name == "write_file" and ctx.tool_args["filename"].endswith(".js"):
        import subprocess
        result = subprocess.run(
            ["node", "--check", "-"],
            input=ctx.tool_args["content"], capture_output=True, text=True
        )
        if result.returncode != 0:
            return f"JavaScript 语法错误：{result.stderr[:200]}"

def tailwind_classes_hook(ctx: HookContext) -> str | None:
    """检查是否使用了不存在的 Tailwind 类名"""

def required_files_hook(ctx: HookContext) -> str | None:
    """任务完成时检查是否生成了 index.html"""
    if "index.html" not in ctx.state.get("file_list", []):
        return "缺少必需的 index.html 文件"
```

新增 `backend/harness/constraints/craft_enforcer.py`：

```python
def craft_enforcer_factory(craft_name: str) -> callable:
    """
    将 Craft Markdown 规则转换为可执行的检查 Hook

    例如 craft/typography.md 中的规则：
      "标题和正文字号比例 >= 1.5"
      → 检查生成的 CSS 中 h1 font-size / p font-size >= 1.5
    """

def anti_ai_slop_hook(ctx: HookContext) -> str | None:
    """
    检查 AI 生成代码中的常见坏味道：
    - 无意义的 placeholder 文本 ("lorem ipsum")
    - 过度使用渐变色和阴影
    - 注释内容空洞 ("// TODO: implement")
    """
    slop_patterns = [
        r'lorem ipsum',
        r'TODO: implement',
        r'add your code here',
    ]
    import re
    content = ctx.tool_args.get("content", "")
    if content:
        for pattern in slop_patterns:
            if re.search(pattern, content, re.IGNORECASE):
                return f"检测到 AI 坏味道：匹配模式 '{pattern}'"
```

新增 `backend/harness/constraints/security.py`：

```python
def xss_hook(ctx: HookContext) -> str | None:
    """检查生成的代码中是否有 XSS 风险"""
    patterns = [
        (r'innerHTML\s*=', '使用 innerHTML 存在 XSS 风险，建议使用 textContent 或 createElement'),
        (r'document\.write\(', '使用 document.write() 存在 XSS 风险'),
        (r'eval\(', '使用 eval() 存在安全风险'),
        (r'src="data:text/html', 'data: URI 存在安全风险'),
    ]
    # ...
```

#### 6.2.3 Hook 结果反馈策略

**成功静默，失败喧哗**：

```python
# 在 ToolCallLoop 中的集成方式
failures = hook_manager.trigger(HookPoint.POST_TOOL_USE, ctx)
if failures:
    # 将失败信息作为工具调用结果的一部分反馈给 LLM
    for f in failures:
        state["dialogue_history"].append({
            "role": "system",
            "name": "Hook",
            "content": f"检查失败：{f}。请修复后重新尝试。"
        })
    # 标记上次工具调用为失败，触发 Agent 重新思考和修复
```

#### 6.2.4 约束失败时的恢复策略

| 失败次数 | 策略 |
|:---:|------|
| 第 1 次 | Hook 失败信息反馈给 LLM，让 Agent 自行修复 |
| 第 2 次 | 除了反馈失败信息，还注入简化的修复建议 |
| 第 3 次 | 跳过该 Hook，记录警告日志，允许继续（不阻塞任务完成） |

### 6.3 改造后风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| Hook 链性能开销 | 每个工具调用后都执行多个 Hook，增加延迟 | Hook 超时限制（每个 2s）；轻量 Hook 优先执行；结果缓存 |
| 误拦截影响体验 | JS/CSS 检查工具本身可能有 bug，导致正确代码被拒 | 3 次失败后自动放过；用户可手动跳过 |
| 规则冲突 | Craft 规则相互矛盾 | 定义优先级：Security > Quality > Craft；冲突时按优先级裁决 |

### 6.4 后续演进方向

- **可配置 Hook 链**：`.talk2code/hooks.yaml` 配置文件，用户可选择开启/关闭特定 Hook
- **社区 Hook 市场**：允许社区贡献和分享 Hook 规则
- **AI 自动生成检查规则**：分析用户拒绝/修改代码的模式，自动生成新的检查 Hook

---

## 七、Layer 6 观测层 — 可观测性

### 7.1 现状

**当前实现**：

- **结构化日志**（`utils/logger.py`）：`%(asctime)s | %(levelname)s | %(name)s | %(funcName)s:%(lineno)d | %(message)s` 格式，控制台 + 文件输出
- **SSE 进度推送**（`services/requirement_service.py:62-68`）：Planner 40%、Coder 80% 的粗粒度进度
- **健康检查**（`app.py:445-481`）：`/api/health` 详细检查（DB、LLM 配置、任务队列），`/live` 和 `/ready` K8s 探针
- **LLMResponse**（`llm/client.py:33-41`）：包含 `content`、`usage`、`finish_reason`，但 `usage` 始终为 `None`

**问题**：

1. **无链路追踪**：无法追踪一个请求在 Planner → Coder → Tool Loop 中各环节的耗时
2. **无 Token/成本统计**：`LLMResponse.usage` 字段定义了但从未填充，不记录每次调用的 Token 消耗
3. **SSE 事件不完整**：只有 progress/dialogue/code/complete，缺少工具调用和 LLM 思考过程
4. **无 Session Replay**：出现问题时无法回放完整的 Agent 执行过程来排查

### 7.2 改造内容

#### 7.2.1 链路追踪 `Tracer`

新增 `backend/harness/observability/tracer.py`：

```python
@dataclass
class Span:
    span_id: str
    parent_id: str | None
    name: str                # "planner_node" / "tool_coder" / "tool_call:write_file"
    start_time: float
    end_time: float | None
    status: str              # "running" / "success" / "error"
    metadata: dict           # {"tool_name": ..., "requirement_id": ..., "iteration": ...}
    error: str | None

@dataclass
class Trace:
    trace_id: str
    requirement_id: int
    user_id: int
    start_time: float
    end_time: float | None
    spans: list[Span]
    total_tokens: int
    total_cost: float

class Tracer:
    """链路追踪管理器"""

    def start_trace(self, requirement_id: int, user_id: int) -> Trace:
        """开始一次请求的全链路追踪"""

    def start_span(self, trace_id: str, name: str, parent_id: str = None) -> Span:
        """开始一个节点/工具调用的追踪"""

    def end_span(self, span_id: str, status: str = "success", error: str = None):
        """结束一个节点的追踪"""

    def end_trace(self, trace_id: str):
        """结束全链路追踪，持久化"""

    def get_trace(self, trace_id: str) -> Trace:
        """获取追踪详情"""

    def recent_traces(self, limit: int = 20) -> list[Trace]:
        """获取最近的追踪列表"""
```

**追踪点**：

| 节点 | 追踪内容 |
|------|---------|
| `planner_node` | 入参长度、耗时、是否触发澄清、Plan JSON 大小 |
| `tool_coder_node` | LLM 调用轮次、每轮耗时、是否调用工具 |
| `tool_executor_node` | 每个工具名、参数摘要、执行耗时、成功/失败 |
| `requirement_service` | 总耗时、数据库操作耗时、SSE 推送次数 |

#### 7.2.2 Token/成本统计

改造 `backend/llm/client.py`：

```python
class CostTracker:
    """Token 用量和成本统计"""

    # 各模型价格 (per 1M tokens)，从 LLM 配置读取或硬编码参考值
    PRICING = {
        "gpt-4o": {"input": 2.50, "output": 10.00},
        "qwen-plus": {"input": 0.50, "output": 2.00},
        "claude-opus-4-7": {"input": 15.00, "output": 75.00},
    }

    def __init__(self):
        self._usage: dict[str, Usage] = {}  # trace_id → Usage

    def record(self, trace_id: str, input_tokens: int, output_tokens: int, model: str):
        model_pricing = self.PRICING.get(model, {"input": 1.0, "output": 4.0})
        cost = (input_tokens / 1_000_000) * model_pricing["input"] + \
               (output_tokens / 1_000_000) * model_pricing["output"]
        # 累加到 trace

    def get_report(self, trace_id: str) -> CostReport:
        # 返回 {total_tokens, total_cost, by_model: {...}, by_agent: {...}}
```

**数据来源**：LLM API 响应中的 `usage` 字段（OpenAI: `response.usage.total_tokens`，Anthropic: `response.usage.input_tokens/output_tokens`）。

**展示位置**：
- 需求详情页底部状态栏：本次生成消耗 XXX tokens / $0.XXX
- 设置页新增"用量统计"：按天/周/月的 Token 和费用汇总

#### 7.2.3 SSE 事件体系统一

**完整 SSE 事件清单**：

| 事件名 | 来源层 | 含义 | 前端展示 |
|--------|:---:|------|---------|
| `progress` | L6 | Agent 进度 | 进度条 |
| `dialogue` | L2 | Agent 对话消息 | 对话面板气泡 |
| `code` | L2 | 代码文件推送 | 代码面板更新 |
| `question_form` | L1 | 需求澄清表单 | 表单弹窗 |
| **`tool_call`** (新) | L2 | Agent 调用工具 | 对话面板工具卡片 |
| **`tool_result`** (新) | L2 | 工具执行结果 | 工具卡片更新 |
| **`thinking`** (新) | L2 | Agent 思考过程 | 对话面板流式文本 |
| **`hook_check`** (新) | L5 | Hook 检查结果 | 对话面板状态标签 |
| `permission_request` | L3 | 权限审批请求 | 权限确认卡片 |
| `complete` | L6 | 任务完成 | 状态栏 + 通知 |
| `error` | L6 | 错误信息 | 错误提示 |
| **`trace_summary`** (新) | L6 | 追踪摘要（完成时发送） | 用量统计卡片 |

#### 7.2.3.1 Agent 执行过程可见性设计原则

**核心理念**：用户应该像使用 Claude Code 一样，实时看到 Agent 的每一步思考和操作——不是看到进度条从 0 跳到 100，而是看到 Agent 在"干什么"。

**哪些 Agent 内部状态应该暴露给用户**：

| Agent 内部状态 | 是否暴露 | 暴露方式 | 理由 |
|:---|:---:|------|------|
| LLM 的"思考"（text response before tool call） | ✅ 是 | SSE `thinking` 事件，流式推送到对话面板 | 用户需要知道 Agent 正在分析和决策 |
| 工具调用（名称 + 参数） | ✅ 是 | SSE `tool_call` 事件，对话面板显示工具卡片 | 用户需要知道 Agent 在做什么操作 |
| 工具执行结果（成功/失败 + 摘要） | ✅ 是 | SSE `tool_result` 事件，工具卡片更新状态 | 用户需要知道操作是否成功 |
| Hook 检查结果 | ✅ 是 | SSE `hook_check` 事件，对话面板状态标签 | 用户需要知道质量检查结果 |
| 完整的 LLM raw response | ❌ 否 | 仅记录到 Trace | 太长太技术化，污染对话面板 |
| 完整的工具调用参数（如 write_file 的全部内容） | ❌ 否 | 截断显示，完整内容在 Trace 中可查 | 污染对话面板，用户不需要看 500 行代码的参数 |
| Span/耗时数据 | ✅ 部份 | 执行详情面板（可折叠） | 用户关注时才展开 |
| Token/成本 | ✅ 部份 | trace_summary + 底部状态栏 | 用户关心成本 |

**"让人知道 Agent 在干活"的设计要求**：

1. **思考过程 (`thinking`)**: Agent 每次调用 LLM 后，先推送 LLM 的文本回复（"我来分析一下这个需求... 首先需要创建 HTML 结构..."），再推送工具调用。类似 Claude Code 的输出方式。

2. **工具调用必须可读**：
   - `write_file("index.html")` → 展示为 "📝 正在创建 index.html (120 行)"
   - `execute_code()` → 展示为 "▶ 正在运行代码验证..."
   - `lint_js("script.js")` → 展示为 "🔍 正在检查 script.js 语法..."
   - `read_file("style.css")` → 展示为 "📖 读取 style.css"

3. **工具结果必须及时**：
   - 成功："✅ index.html 写入成功 (120 行)"
   - 失败："❌ JavaScript 语法错误: 第 45 行 Unexpected token"
   - 然后 Agent 的思考接着流式输出："语法有问题，让我修复..."

4. **沉默超过 5 秒必须给信号**：
   - 如果 LLM 调用超过 5 秒还没返回，推送 `thinking` 心跳："正在思考中..."
   - 如果工具执行超过 3 秒还没完成，推送进度："正在执行代码验证..."

**与 Claude Code 的类比**：

```
Claude Code 行为                    Talk2Code 对等实现
─────────────────────────────      ─────────────────────
"Let me read the file..."          SSE thinking: "正在分析项目结构..."
[Read tool call]                   SSE tool_call: read_file("index.html")
[Tool result: file content]        SSE tool_result: ✓ index.html
"I found that..."                  SSE thinking: "发现 index.html 中..."
[Edit tool call]                   SSE tool_call: write_file("index.html")
[Tool result: success]             SSE tool_result: ✓ index.html 已更新
```

#### 7.2.4 前端观测面板

在 `detail.html` 底部新增可折叠的"执行详情"面板：

```
┌─────────────────────────────────────────┐
│ ▼ 执行详情                    耗时 45.2s │
├─────────────────────────────────────────┤
│ Planner           1.2s    ✓             │
│ Tool Coder                         38.5s│
│   ├ write_file     0.3s    ✓ index.html │
│   ├ write_file     0.2s    ✓ style.css  │
│   ├ execute_code   3.1s    ✗ JS 语法错误 │
│   ├ write_file     0.3s    ✓ script.js  │
│   └ execute_code   2.8s    ✓            │
│ Hook 检查           0.4s    ✓ (5/5 通过) │
├─────────────────────────────────────────┤
│ Token: 12,450  │  Cost: $0.042          │
│ Model: gpt-4o   │  Retries: 1           │
└─────────────────────────────────────────┘
```

#### 7.2.5 日志系统设计

**日志存储位置与分类**：

```
{项目根目录}/logs/
├── app.log           # 应用日志 (Flask请求、DB操作、认证)
├── agent.log         # Agent 执行日志 (Planner/Coder/ToolLoop 决策)
├── llm.log           # LLM 调用日志 (请求参数/响应/Token用量/耗时)
├── access.log        # HTTP 访问日志
└── archive/          # 历史归档 (超过30天自动压缩)
```

**日志轮转策略**：
- 按天轮转（每天午夜）
- 单文件最大 50MB，超过立即轮转
- `agent.log` 和 `llm.log` 保留 30 天，`app.log` 保留 90 天
- 归档使用 gzip 压缩

**日志格式**：

```
app.log:
  %(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s
  示例: 2026-06-07 14:32:01 | INFO     | app | chat_with_requirement:448 | 用户补充对话消息

agent.log:
  %(asctime)s | [%(trace_id)s] | %(node_name)-12s | %(event)-16s | %(message)s
  示例: 2026-06-07 14:32:05 | [t_abc123] | tool_coder   | tool_call        | write_file("index.html") 成功 (120行)

llm.log:
  %(asctime)s | [%(trace_id)s] | model=%(model)s | t_in=%(input_tokens)d | t_out=%(output_tokens)d | latency=%(latency).2fs | %(finish_reason)s
  示例: 2026-06-07 14:32:03 | [t_abc123] | model=qwen-plus | t_in=1250 | t_out=850 | latency=3.21s | stop
```

**配置项**（在 `config.py` 中）：

```python
LOG_DIR = "logs"                     # 项目根目录下的 logs/ 目录
LOG_LEVEL = "INFO"               # DEBUG/INFO/WARNING/ERROR
AGENT_LOG_RETENTION_DAYS = 30    # agent.log / llm.log 保留天数
APP_LOG_RETENTION_DAYS = 90      # app.log / access.log 保留天数
LOG_FILE_MAX_SIZE_MB = 50        # 单文件最大大小
```

**Prometheus 监控指标**（`/api/metrics` 端点）：

```
talk2code_requests_total{status="success|failed"}                   # 请求总数
talk2code_agent_duration_seconds{node="planner|coder|tool_exec"}    # 各节点耗时
talk2code_llm_tokens_total{model="...", direction="input|output"}   # Token 用量
talk2code_llm_latency_seconds{model="..."}                          # LLM 响应延迟
talk2code_tool_calls_total{tool="...", status="success|failed"}     # 工具调用次数
talk2code_hook_checks_total{status="pass|fail"}                     # Hook 检查结果
talk2code_active_sessions                                           # 活跃会话数
```

### 7.3 改造后风险

| 风险 | 影响 | 缓解措施 |
|------|------|---------|
| 追踪数据膨胀 | 数据库增长过快 | 定期清理 30 天前的 trace 数据；聚合存储 |
| 成本计算偏差 | LLM 价格变动或未配置价格导致计算不准 | 价格存储在配置文件中，可手动更新 |
| SSE 事件量暴增 | 工具调用多轮时事件频繁推送，前端渲染压力 | 事件合并：500ms 内的多个同类事件合并推送 |
| 日志文件磁盘占用 | 日志文件过大导致磁盘不足 | 按大小和时间双重轮转；归档压缩；配置保留天数 |

### 7.4 后续演进方向

- **OpenTelemetry 集成**：将 Trace/Span 导出到 OTLP 兼容的后端（Jaeger/Tempo）
- **异常检测告警**：基于历史 Trace 数据，自动检测异常（耗时突增、失败率异常）
- **Session Replay**：记录完整的 Agent 执行过程，支持回放排查问题

---

## 八、目录结构与模块职责

### 8.1 新目录结构总览

```
backend/
├── app.py                          # Flask 主程序（路由层，精简）
├── config.py                       # 配置管理（不变）
├── models/
│   ├── models.py                   # SQLAlchemy 模型（新增 AgentMemory, Trace 表）
│   └── schema.py                   # Pydantic 校验（不变）
│
├── harness/                        # 【新核心】Agent Harness 6 层实现
│   ├── __init__.py
│   │
│   ├── instructions/               # Layer 1: 指令层
│   │   ├── __init__.py
│   │   ├── assembler.py            #   ContextAssembler 动态上下文组装
│   │   ├── compactor.py            #   ContextCompactor 上下文压缩 【新增】
│   │   ├── prompts.py              #   提示词模板（Planner/Coder）
│   │   ├── craft_loader.py         #   Craft 设计质量规则加载
│   │   └── skill_loader.py         #   通用 Skill 加载（单一通用 Skill）
│   │
│   ├── tools/                      # Layer 2: 工具层 【新增核心】
│   │   ├── __init__.py
│   │   ├── registry.py             #   ToolRegistry 工具注册表
│   │   ├── file_tools.py           #   read_file / write_file / list_files / delete_file
│   │   ├── code_tools.py           #   execute_code / validate_html / lint_css / lint_js
│   │   ├── web_tools.py            #   search_docs / fetch_cdn_library
│   │   └── code_fallback.py        #   从原 prompts.py 迁移的 fallback 代码生成器
│   │
│   ├── environment/                # Layer 3: 环境层 【新增】
│   │   ├── __init__.py
│   │   ├── permissions.py          #   PermissionManager 权限分级
│   │   ├── sandbox.py              #   SandboxExecutor 代码执行沙箱
│   │   └── isolation.py            #   用户会话隔离
│   │
│   ├── state/                      # Layer 4: 状态层 【改造增强】
│   │   ├── __init__.py
│   │   ├── agent_state.py          #   从原 agents/state.py 迁移 + 增强
│   │   ├── workspace.py            #   WorkspaceFS 运行时文件系统 【新增】
│   │   ├── versioning.py           #   GitVersioning 版本控制 【新增】
│   │   ├── memory_store.py         #   MemoryStore 跨会话记忆 【新增】
│   │   └── checkpoint.py           #   CheckpointManager 断点恢复 【新增】
│   │
│   ├── constraints/                # Layer 5: 约束层 【新增】
│   │   ├── __init__.py
│   │   ├── hooks.py                #   HookManager Hook 生命周期管理
│   │   ├── quality.py              #   代码质量检查 Hook
│   │   ├── craft_enforcer.py       #   Craft 规则强制执行 Hook
│   │   └── security.py             #   安全检查 Hook
│   │
│   └── observability/              # Layer 6: 观测层 【新增】
│       ├── __init__.py
│       ├── tracer.py               #   Tracer 链路追踪
│       ├── cost.py                 #   CostTracker 成本统计
│       ├── sse_reporter.py         #   SSE 事件统一管理
│       └── logger.py               #   从 utils/logger.py 迁移
│
├── agents/                         # LangGraph 智能体（改造）
│   ├── __init__.py
│   ├── nodes.py                    #   planner_node + tool_coder_node
│   ├── tool_loop.py                #   【新增】ToolCallLoop 工具调用循环
│   └── workflow.py                 #   新的图结构（含工具节点和执行器）
│
├── llm/
│   ├── __init__.py
│   └── client.py                   #   扩展：chat_with_tools() 支持 function calling
│
├── services/                       # 服务层（精简）
│   ├── sse_manager.py              #   SSE 连接管理（不变）
│   ├── task_queue.py               #   任务队列（不变）
│   └── requirement_service.py      #   集成 harness 层（主要改造）
│
├── utils/                          # 工具函数（精简）
│   ├── security.py                 #   bcrypt 密码哈希（不变）
│   ├── retry.py                    #   重试工具（不变）
│   ├── rate_limiter.py             #   限流配置（不变）
│   └── time_utils.py               #   时间工具（不变）
│
├── craft/                          # 设计质量规则 Markdown（不变：typography/color/accessibility-baseline/anti-ai-slop）
├── skills/                         # 单一通用 Skill（仅保留 generic/SKILL.md，删除 todo/calculator/note/calendar）
├── routes/                         # 路由模块（预留）
└── tests/                          # 测试（新增 harness 测试 + 改造现有测试）
    ├── unit/
    │   ├── test_tool_registry.py
    │   ├── test_hooks.py
    │   ├── test_permissions.py
    │   └── ...
    ├── functional/
    └── integration/
```

### 8.2 模块间依赖关系图

```
┌──────────┐
│  app.py  │ (路由 + SSE 端点)
└────┬─────┘
     ↓
┌──────────────────┐
│ services/         │
│ requirement_service│ (编排层)
└────┬─────────────┘
     ↓
┌──────────────────────────────────────────────────┐
│ agents/                                           │
│  workflow.py ──→ nodes.py ──→ tool_loop.py       │
│                      │              │              │
│                      ↓              ↓              │
│              llm/client.py   harness/tools/       │
│              (LLM 调用)      (工具执行)            │
└──────────────────────────────────────────────────┘
     │                    │              │
     ↓                    ↓              ↓
┌─────────────────────────────────────────────────────────┐
│ harness/                                                 │
│                                                          │
│ instructions/  ← 被 agents/nodes.py 调用（组装上下文）    │
│ tools/         ← 被 agents/tool_loop.py 调用（执行工具）  │
│ environment/   ← 被 agents/tool_loop.py 调用（权限检查）  │
│ state/         ← 被 tools/ 和 agents/ 调用（读写状态）    │
│ constraints/   ← 被 agents/tool_loop.py 触发（Hook 执行） │
│ observability/ ← 被所有层调用（追踪记录）                 │
└─────────────────────────────────────────────────────────┘
```

**依赖规则**：
1. `harness/` 内各子包之间通过接口通信，不直接导入实现类
2. `agents/` 依赖 `harness/` 和 `llm/`，不依赖 `services/`
3. `services/` 依赖 `agents/` 和 `harness/`，不依赖 `app.py`
4. `app.py` 只依赖 `services/` 和 `models/`

### 8.3 旧代码清理清单

本次为完整重构，不保留向后兼容层。以下旧文件/模块直接删除或替换：

| 旧文件 | 处理方式 |
|--------|---------|
| `backend/prompts.py` | 删除，内容迁移到 `harness/instructions/prompts.py` + `harness/tools/code_fallback.py` |
| `backend/craft_loader.py` | 删除，迁移到 `harness/instructions/craft_loader.py` |
| `backend/skill_loader.py` | 删除，Skills 定位改变，重写为单一通用 Skill |
| `backend/agents/state.py` | 删除，迁移到 `harness/state/agent_state.py` |
| `backend/agents/nodes.py` | 重写，planner_node + tool_coder_node |
| `backend/agents/workflow.py` | 重写，新图结构（planner → tool_coder ↔ tool_executor） |
| `backend/services/requirement_service.py` | 重写，集成 harness 层 |
| `backend/utils/logger.py` | 删除，迁移到 `harness/observability/logger.py` |
| `backend/skills/*/SKILL.md` | 删除现有 5 个特定 Skill，替换为单一通用 Skill `skills/generic/SKILL.md` |
| `backend/skills/*/template.json` | 全部删除，Agent 用工具动态生成代码，不再需要硬编码模板 |

---

## 九、数据流与接口契约

### 9.1 一次完整请求的 6 层时序图

```
用户         前端              Flask            TaskQueue      Requirement    LangGraph     LLMClient     ToolRegistry  HookManager   WorkspaceFS   GitRepo     Tracer
 │            │                 │                 │             Service       Workflow       │              │             │             │            │           │
 │ 输入需求   │                 │                 │              │              │             │              │             │             │            │           │
 │──────────→│                 │                 │              │              │             │              │             │             │            │           │
 │           │ POST /api/req   │                 │              │              │             │              │             │             │            │           │
 │           │────────────────→│                 │              │              │             │              │             │             │            │           │
 │           │                 │ submit(task)    │              │              │             │              │             │             │            │           │
 │           │                 │────────────────→│              │              │             │              │             │             │            │           │
 │           │                 │                 │ [线程启动]    │              │             │              │             │             │            │           │
 │           │                 │                 │─────────────→│              │             │              │             │             │            │           │
 │           │                 │                 │              │              │             │             L1: ContextAssembler.assemble()              │           │
 │           │                 │                 │              │              │             │              │             │             │            │           │
 │           │                 │                 │              │ stream()     │             │              │             │             │            │           │
 │           │                 │                 │              │─────────────→│             │              │             │             │            │           │
 │           │                 │                 │              │              │ planner_node│             │              │             │            │           │
 │           │                 │                 │              │              │────────────→│             │              │             │            │           │
 │           │                 │                 │              │              │             │ chat()      │              │             │            │           │
 │           │                 │                 │              │              │             │────────────→│              │             │            │ L6: Trace │
 │           │                 │                 │              │              │             │←─────────── │              │             │            │ (planner) │
 │           │                 │                 │              │              │←─────────── │             │              │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │              │ tool_coder_node           │              │             │            │           │
 │           │                 │                 │              │              │────────────→│             │              │             │            │           │
 │           │                 │                 │              │              │             │ chat_with_tools()         │             │            │           │
 │           │                 │                 │              │              │             │────────────→│              │             │            │           │
 │           │                 │                 │              │              │             │←─────────── │              │             │            │           │
 │           │                 │                 │              │              │←─────────── │ tool_calls  │              │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │     [工具调用循环开始]        │             │              │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │   SSE: tool_call│              │              │             │             │              │             │            │           │
 │           │←────────────────│←─────────────── │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │              │ L3: PermissionManager.check()                 │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │              │ L5: HookManager.trigger(PRE_TOOL_USE)         │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │              │ tool_executor_node        │              │             │            │           │
 │           │                 │                 │              │              │─────────────────────────→│              │             │            │           │
 │           │                 │                 │              │              │             │             │ execute()    │             │            │           │
 │           │                 │                 │              │              │             │             │────────────→│ WorkspaceFS │            │           │
 │           │                 │                 │              │              │             │             │             │.write()     │            │           │
 │           │                 │                 │              │              │             │             │             │────────────→│            │           │
 │           │                 │                 │              │              │             │             │             │             │ git.commit │           │
 │           │                 │                 │              │              │             │             │             │             │───────────→│           │
 │           │                 │                 │              │              │             │             │←─────────── │             │←─────────── │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │              │ L5: HookManager.trigger(POST_TOOL_USE)        │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │   SSE: tool_result             │              │             │             │              │             │            │           │
 │           │←────────────────│←────────────── │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │ [循环直到 LLM 返回无 tool_calls]             │              │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │                 │              │              │ L5: HookManager.trigger(ON_TASK_COMPLETE)     │             │            │           │
 │           │                 │                 │              │              │             │             │              │             │            │           │
 │           │                 │   SSE: trace_summary + complete│              │             │             │              │             │            │           │
 │           │←────────────────│←────────────── │              │              │             │             │              │             │            │           │
```

### 9.2 公共接口定义

`harness/` 对外暴露的统一入口 `backend/harness/__init__.py`：

```python
# Harness 对外公共接口

# L1 指令层
from harness.instructions.assembler import ContextAssembler, AssembledContext

# L2 工具层
from harness.tools.registry import ToolRegistry, ToolDefinition, ToolResult
from harness.tools.file_tools import register_file_tools
from harness.tools.code_tools import register_code_tools
from harness.tools.web_tools import register_web_tools

# L3 环境层
from harness.environment.permissions import PermissionManager, PermissionResult

# L4 状态层
from harness.state.workspace import WorkspaceFS
from harness.state.versioning import GitVersioning
from harness.state.memory_store import MemoryStore
from harness.state.checkpoint import CheckpointManager

# L5 约束层
from harness.constraints.hooks import HookManager, HookPoint, HookContext

# L6 观测层
from harness.observability.tracer import Tracer, Trace, Span

# 便捷初始化函数
def create_harness(requirement_id: int, user_id: int) -> Harness:
    """创建完整的 Harness 实例，初始化所有 6 层"""
    ...
```

### 9.3 SSE 事件协议完整版

```
事件流顺序（一次典型请求）:

1. progress         → 进度 0%，开始
2. dialogue         → Planner 分析完成
3. progress         → 进度 40%

4. [工具调用循环]
   ├─ thinking       → "开始生成代码..."
   ├─ tool_call      → write_file("index.html")
   ├─ tool_result    → ✓ index.html (120 行)
   ├─ tool_call      → write_file("style.css")
   ├─ tool_result    → ✓ style.css (45 行)
   ├─ tool_call      → write_file("script.js")
   ├─ tool_result    → ✓ script.js (80 行)
   ├─ tool_call      → execute_code()
   ├─ tool_result    → ✗ JS 错误: 第 45 行
   ├─ thinking       → "修复 JS 语法错误..."
   ├─ tool_call      → write_file("script.js")
   ├─ tool_result    → ✓ script.js (82 行)
   ├─ tool_call      → execute_code()
   ├─ tool_result    → ✓ 代码检查通过
   └─ thinking       → "所有文件已生成并验证通过"

5. hook_check        → 5/5 检查通过
6. progress          → 进度 100%
7. trace_summary     → Token: 12,450 | Cost: $0.042 | Time: 45.2s
8. complete          → 任务完成
```

---

## 十、实施路线图

### 10.1 阶段一：工具层 + 状态层（核心改造）

**目标**：让 Agent 拥有工具调用能力，代码迭代式生成，状态可持久化和恢复。

**任务清单**：

| # | 任务 | 预估工时 | 优先级 |
|:--|------|:---:|:---:|
| 1.1 | 创建 `harness/` 目录结构，定义公共接口 | 2h | P0 |
| 1.2 | 实现 `ToolRegistry` + `ToolDefinition` | 2h | P0 |
| 1.3 | 实现 `file_tools.py`（read/write/list/delete） | 3h | P0 |
| 1.4 | 实现 `WorkspaceFS` | 2h | P0 |
| 1.5 | 扩展 `LLMClient.chat_with_tools()`（OpenAI 协议） | 4h | P0 |
| 1.6 | 扩展 `LLMClient.chat_with_tools()`（Anthropic 协议） | 3h | P1 |
| 1.7 | 实现 `ToolCallLoop` | 4h | P0 |
| 1.8 | 改造 `agents/workflow.py` 新增工具节点 | 3h | P0 |
| 1.9 | 实现 `tool_coder_node` + `tool_executor_node` | 4h | P0 |
| 1.10 | 实现 `GitVersioning` | 2h | P1 |
| 1.11 | 实现 `MemoryStore` 基础版（关键词检索） | 3h | P1 |
| 1.12 | 实现 `CheckpointManager` | 2h | P1 |
| 1.13 | 前端 SSE 新增 `tool_call`/`tool_result` 事件 | 3h | P0 |
| 1.14 | 改造 `RequirementService` 集成 harness | 3h | P0 |
| 1.15 | 新增配置开关 `WORKFLOW_VERSION` | 1h | P0 |
| 1.16 | 编写 `harness/tools/` 单元测试 | 3h | P0 |
| 1.17 | 编写 `harness/state/` 单元测试 | 2h | P1 |
| 1.18 | 端到端集成测试 | 3h | P0 |

**预估总工时**：~49h (~6 个工作日)
**验收标准**：
- Agent 可以通过工具调用迭代式生成代码
- 代码文件写入 WorkspaceFS，Git 版本化
- 前端实时展示工具调用卡片
- 所有现有测试通过 + 新增测试覆盖率 > 80%

### 10.2 阶段二：环境层 + 约束层

**目标**：为工具调用加上安全边界，让 Craft 规则从"建议"变"强制"。

**任务清单**：

| # | 任务 | 预估工时 | 优先级 |
|:--|------|:---:|:---:|
| 2.1 | 实现 `PermissionManager` + 三级权限模型 | 3h | P0 |
| 2.2 | 实现 `PermissionManager` 前后端审批流程 | 4h | P0 |
| 2.3 | 实现 `SandboxExecutor`（Node.js subprocess） | 4h | P1 |
| 2.4 | 实现 `HookManager` + 生命周期 | 3h | P0 |
| 2.5 | 实现代码质量 Hook（HTML/CSS/JS 语法检查） | 3h | P0 |
| 2.6 | 实现 `craft_enforcer.py`（关键 Craft 规则） | 3h | P1 |
| 2.7 | 实现安全检查 Hook（XSS/eval 检测） | 2h | P1 |
| 2.8 | 前端权限审批 UI（确认卡片） | 3h | P0 |
| 2.9 | SSE 新增 `permission_request`/`hook_check` 事件 | 2h | P0 |
| 2.10 | 约束失败时的恢复策略实现 | 2h | P1 |
| 2.11 | 测试（Hook 单元测试 + 权限集成测试） | 4h | P0 |

**预估总工时**：~33h (~4 个工作日)
**验收标准**：
- Level 2 执行工具每次需要用户审批
- 代码写入后自动触发语法检查，错误反馈给 Agent
- Craft 规则的关键项被强制执行
- Agent 收到 Hook 失败后能自行修复（至少重试 1 次）

### 10.3 阶段三：观测层 + 指令层增强

**目标**：完整的可观测性体系，指令层动态上下文组装。

**任务清单**：

| # | 任务 | 预估工时 | 优先级 |
|:--|------|:---:|:---:|
| 3.1 | 实现 `Tracer` + `Trace`/`Span` 模型 | 4h | P0 |
| 3.2 | 实现 `CostTracker`（从 API 响应提取 usage） | 2h | P1 |
| 3.3 | 在各节点埋点（planner/coder/tool_call） | 3h | P0 |
| 3.4 | 实现 `ContextAssembler` 动态上下文组装 | 3h | P1 |
| 3.5 | 提示词模板按 Skill 差异化 | 2h | P2 |
| 3.6 | 前端观测面板（可折叠执行详情） | 4h | P0 |
| 3.7 | 前端 Token 用量展示 | 1h | P1 |
| 3.8 | SSE `trace_summary` 事件 | 1h | P0 |
| 3.9 | Trace 持久化 + 查询 API | 2h | P1 |
| 3.10 | 日志迁移到 `harness/observability/logger.py` | 1h | P2 |
| 3.11 | 测试（Tracer 测试 + ContextAssembler 测试） | 3h | P0 |

**预估总工时**：~26h (~3.5 个工作日)
**验收标准**：
- 每次请求生成完整 Trace，前端可查看执行详情
- Token 用量和成本在前端展示
- 上下文按需组装，Craft 规则按需选择

### 10.4 测试策略

| 阶段 | 测试层级 | 覆盖内容 |
|:---:|---------|---------|
| 一 | 单元测试 | ToolRegistry, 每个 tool handler, WorkspaceFS, ToolCallLoop 逻辑, chat_with_tools 请求/响应格式 |
| 一 | 集成测试 | 完整工作流（Planner → 多轮工具调用 → 完成），Git 版本化 + Checkpoint 恢复 |
| 二 | 单元测试 | PermissionManager 权限判定, HookManager 触发逻辑, 每个 Hook 的检查逻辑 |
| 二 | 集成测试 | 权限审批端到端流程，Hook 失败 → Agent 修复循环，SandboxExecutor 隔离性 |
| 三 | 单元测试 | Tracer span 创建/结束/序列化, CostTracker 计算逻辑, ContextAssembler 组装逻辑 |
| 三 | E2E 测试 | 前端观测面板渲染，完整 Trace 数据持久化和查询 |

**Mock 策略**（`tests/conftest.py` 扩展）：
- `mock_llm_client_with_tools`：模拟返回 tool_calls 或不返回 tool_calls 的 LLM 响应
- `mock_tool_registry`：提供可预测的工具执行结果
- `mock_workspace`：使用 `tmp_path` fixture 提供真实临时目录

---

## 附录

### A. 关键设计决策记录

| 决策 | 选项 | 选择 | 理由 |
|------|------|:---:|------|
| 工具描述格式 | OpenAI Function Calling vs 自定义 JSON 格式 | OpenAI format | 通用性最好，Anthropic 协议也可适配 |
| 代码执行沙箱 | subprocess Node.js vs Docker vs PyMiniRacer | subprocess Node.js | 初期最简单的方案，后续可升级到 Docker |
| Hook 失败反馈 | 中断任务 vs 反馈给 Agent 自行修复 vs 静默记录 | 反馈给 Agent 自行修复 | 符合棘轮原则，让 Agent 学习纠正 |
| 记忆存储 | SQLite vs ChromaDB vs Redis | SQLite | 无需额外依赖，初期关键词匹配够用 |
| Git 版本化 | 每次写入 commit vs 批量 commit | 每次写入 commit | 粒度高，回滚精确；后续可优化为批量 |

### B. 参考资料

- Addy Osmani, "Agent Harness Engineering", 2026-04. [原文](https://addyosmani.com/blog/agent-harness-engineering/) / [O'Reilly Radar 转载](https://www.oreilly.com/radar/agent-harness-engineering/)
- LangGraph 文档: [Tool Calling](https://langchain-ai.github.io/langgraph/how-tos/tool-calling/)
- OpenAI Function Calling: [API Reference](https://platform.openai.com/docs/guides/function-calling)
- Anthropic Tool Use: [API Reference](https://docs.anthropic.com/en/docs/build-with-claude/tool-use)
