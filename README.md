# Talk2Code

一个 AI 驱动的一站式网站生成平台，用户输入自然语言需求 → AI 多智能体协同处理 → 实时生成可运行的产品代码。

## 技术栈

- **前端**: Vue 3 + TypeScript + Vite + Warm Soft 设计系统 (OKLch)
- **后端**: Python 3.11+ + Flask
- **数据库**: SQLite
- **实时通信**: SSE (Server-Sent Events)
- **认证**: JWT
- **AI 编排**: LangGraph + LangChain
- **AI 模型**: 兼容 OpenAI/Anthropic 接口协议，配置驱动切换

## 项目结构

```
talk2code/
├── backend/
│   ├── app.py                          # Flask 主程序（API 路由、SSE 推送）
│   ├── config.py                       # Pydantic 配置（数据库、JWT、SSE、LLM）
│   ├── requirements.txt                # Python 依赖
│   ├── .env.example                    # LLM 配置模板
│   ├── models/                         # SQLAlchemy 数据模型 + Schema
│   ├── llm/
│   │   └── client.py                   # 统一 LLM 客户端（OpenAI/Anthropic 双协议）
│   ├── harness/                        # Agent 运行时框架（6 层架构）
│   │   ├── runtime.py                  # ReAct 工具调用循环 — Agent 执行引擎
│   │   ├── graph.py                    # LangGraph 多节点编排工作流（v3）
│   │   ├── instructions/               # 提示词模板、节点函数、意图路由、角色执行、上下文组装/压缩
│   │   │   ├── prompts/                 #   统一 Prompt 管理（.md 文件 + load_prompt 工具函数）
│   │   │   ├── intent_router.py         #   前置意图分类 (QUICK/SEARCH/TASK/AMBIGUOUS)
│   │   │   ├── nodes.py                 #   LangGraph 节点函数（TeamLeader/PM/Architect/QA/Repair）
│   │   │   ├── file_coder.py            #   逐文件编码循环 + LGTM/LBTM 审查
│   │   │   ├── simple_coder.py          #   XS/S 简单编码节点
│   │   │   ├── summarize.py             #   整体代码审查节点 / SummarizeCode
│   │   │   ├── orchestrator.py          #   多角色编排引擎
│   │   │   ├── role_executor.py         #   单一角色执行器（含逐文件 QA）
│   │   │   ├── compactor.py             #   P0-P3 分层上下文压缩
│   │   │   ├── assembler.py             #   上下文组装（Skill + Craft 规则 + 记忆）
│   │   ├── tools/                      # 工具注册表、文件操作、代码生成/验证
│   │   ├── roles/                      # 多角色定义
│   │   │   ├── __init__.py              #   Role/RoleResult/RoleRegistry
│   │   │   └── definitions.py           #   5 角色 Prompt（从 prompts/*.md 加载）+ 路由表
│   │   ├── communication/               # 消息总线
│   │   │   └── __init__.py              #   AgentBus 四分支路由
│   │   ├── experience/                  # 经验池 (DEPRECATED → memory.py, 保留兼容)
│   │   ├── environment/                # 权限管理、沙箱执行
│   │   ├── state/                      # AgentState、WorkspaceFS、Git 版本化、记忆存储
│   │   │   ├── memory.py               #   MemoryManager — 统一记忆管理（BGE-M3 混合检索 + LLM 反思）
│   │   │   ├── memory_retriever.py      #   BGEM3Retriever — Dense+Sparse 混合检索 + 降级回退
│   │   │   ├── memory_store.py          #   MemoryStore (DEPRECATED → memory.py)
│   │   ├── constraints/                # Hook 管理器 + 约束检查（Craft 规则、安全、质量）
│   │   └── observability/              # 链路追踪、成本统计、SSE 事件上报、日志
│   ├── skills/                         # 统一技能 + 设计规则（渐进式披露：L0→L1→L2）
│   │   ├── __init__.py                  #   SkillLoader — 按任务特征匹配
│   │   ├── anti-ai-slop/SKILL.md        #   L0 始终注入：禁止 AI 刻板设计模式
│   │   ├── typography/SKILL.md          #   L1 UI 触发：排版字号层级规范
│   │   ├── color/SKILL.md               #   L1 UI 触发：色彩系统规则
│   │   ├── accessibility/SKILL.md       #   L2 表单触发：可访问性基础规则
│   │   └── generic/SKILL.md             #   通用前端开发技能模板
│   ├── agents/                         # 向后兼容重导出层
│   ├── services/                       # SSE 传输、任务调度、应用胶水层
│   ├── skills/                         # 可插拔应用模板（Skill 定义）
│   └── utils/                          # 限流、重试、安全、SSE 格式化
├── frontend-vue/                       # Vue 3 + TypeScript 前端
│   ├── src/
│   │   ├── components/                 # 组件（auth/common/detail/history/home/layout/settings）
│   │   ├── views/                      # 页面视图
│   │   ├── composables/                # 组合式函数（useApi/useSSE/useDarkMode/useToast）
│   │   ├── stores/                     # Pinia 状态管理（auth/requirement/settings）
│   │   └── router/                     # Vue Router 路由
│   └── dist/                           # 构建产物（由 Flask 托管）
├── start.sh                            # 一键启动脚本
└── openspec/                           # OpenSpec 规范驱动开发
```

## 快速开始

### 1. 安装依赖

```bash
python -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
```

### 2. 配置 LLM

复制配置模板并填入你的 API Key：

```bash
cp backend/.env.example backend/.env
# 编辑 backend/.env，填入 LLM_API_KEY
```

支持两种协议，通过 `LLM_PROVIDER` 切换：

| 协议 | 适用服务商 |
|------|-----------|
| `openai_compatible` | DeepSeek、DashScope、OpenAI、智谱、月之暗面 等 |
| `anthropic_compatible` | Anthropic Claude 等 |

```bash
# OpenAI 兼容示例（DeepSeek）
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_API_KEY=your-api-key-here
```

### 3. 启动服务

**方式一：一键启动**

```bash
./start.sh
```

**方式二：手动启动**

```bash
# 构建前端
cd frontend-vue && npm install && npm run build && cd ..

# 启动后端
cd backend && python app.py
```

访问 http://localhost:5001

### 4. 测试账号

- 用户名：`test`
- 密码：`123456`

## 使用流程

1. **登录** - 使用测试账号或注册新账号
2. **输入需求** - 在首页输入框输入你的需求
   - 开发任务示例：`开发一个待办清单 App，支持增删改查`
   - 简单问答示例：`什么是 CSS Grid 布局？`（AI 直接回答，不触发代码生成）
   - 代码示例：`写一个冒泡排序函数`
3. **意图分流**（自动）- AI 判断用户意图：
   - 常识问答/代码解释 → 秒级直接回复
   - 开发任务 → 进入 LangGraph 多节点编排流水线（TeamLeader → 逐文件编码 → LGTM/LBTM 审查 → SummarizeCode）
   - 模糊需求 → 自动生成澄清问题表单
4. **查看生成** - 进入详情页：
   - **左侧**: AI 对话面板 — 观看 TeamLeader → PM → Architect → FrontendEngineer(逐文件编码) → QA → Summarize 全流程协同讨论
   - **右侧**: 代码/预览面板 — 文件树 + 代码编辑器 + 设备预览（桌面/平板/手机）
5. **持续对话** - 生成完成后，可在对话面板底部继续与 AI 交互，做增量修改或提问
6. **历史管理** - 在「历史对话」页面查看所有项目，支持搜索、状态筛选、回收站

## 核心功能

### 用户系统
- 用户注册/登录（JWT 认证）
- 登录状态持久化
- 未登录拦截

### AI 智能体协同

基于 **LangGraph** 实现的多节点工作流编排，前置意图分类器做四路分流：

```
用户输入
  │
  └─ IntentRouter (前置分流)
       ├─ QUICK     → LLM 直接回答（常识问答/代码解释/问候）
       ├─ SEARCH    → 增强回答 + 时效性提示
       ├─ AMBIGUOUS → 澄清表单，引导用户补充信息
       └─ TASK      → 进入生成流水线
                         │
                    TeamLeader (需求分析 + 任务分解)
                      输出: features, complexity[XS|S|M|L], tasks[], interfaces, implementation_order
                         │
                    [复杂度路由]
                    ───┴───
                   │       │
              XS/S        M/L
               │           │
          simple_coder    pm (ProductManager — PRD)
          (单文件          │
           ToolCallLoop)  architect (Architect — 架构设计)
               │           │
               │      file_by_file_coder (逐文件编码循环)
               │           │  对每个文件:
               │           │    ├─ 构建 CodingContext (设计+任务+已完成文件+接口契约)
               │           │    ├─ ToolCallLoop 生成代码 (≤5 轮/文件)
               │           │    └─ CodeReview (LGTM/LBTM, 6 维度审查)
               │           │       LBTM → 重写 (最多 3 次)
               │           │
               │      qa_reviewer (QA — 逐文件代码审查)
               │           │
               │       ┌──[pass]──→ summarize (整体审查/SummarizeCode)
               │       │               │
               │       │           ┌──[pass]──→ END ✅
               │       │           │
               │       └──[fail]──→ repair ──→ qa_reviewer (修复循环, ≤3 轮)
               │
               └──→ END ✅

    全程: Hook 失败反馈 → LLM 上下文中注入验证错误
          ContextCompactor P0-P3 分层压缩 → 防上下文溢出
          MemoryManager (BGE-M3 Hybrid + LLM 反思) → few-shot 注入 + 经验积累
```

| 组件 | 职责 |
|------|------|
| **IntentRouter** | 前置意图分类（QUICK/SEARCH/TASK/AMBIGUOUS），轻量 LLM 调用（~200 tokens），非开发请求直接返回 |
| **TeamLeader** | 需求分析 + 复杂度评估 + **任务分解**（文件级 tasks[] + 接口契约 interfaces + 依赖排序 implementation_order） |
| **ProductManager** | 需求分析、PRD 生成（功能清单、数据模型、交互流程），M/L 流程第一步 |
| **Architect** | 技术选型、组件树设计、数据流设计、文件结构规划，M/L 流程第二步 |
| **FrontendEngineer** | **两种模式**：XS/S 简单模式（simple_coder），M/L 逐文件编码模式（file_by_file_coder）+ LGTM/LBTM 审查 |
| **CodeReviewer** | **逐文件 LGTM/LBTM 审查**（6 维度：需求实现、逻辑正确、接口遵循、功能完整、依赖正确、代码质量） |
| **QAReviewer** | 多文件整体代码审查：5 维度评分 + 问题识别 + 修复建议 |
| **Summarize** | **整体代码审查**：跨文件调用流、功能遗漏、边界情况、代码一致性 |
| **Repair** | **定向修复**：接收 QA/Summarize 问题反馈，调用 ToolCallLoop 修复特定文件 |
| **MemoryManager** | 记忆管理器：BGE-M3 混合检索（Dense+Sparse）+ LLM 3 问反思（reflection/lesson/pattern），任务完成后自动学习，持久化到 SQLite，越用越好 |

**复杂度自适应行为**：

| 维度 | XS（极简） | S（标准） | M（多功能） | L（复杂） |
|------|-----------|----------|------------|----------|
| 典型场景 | 个人主页、计数器 | 待办清单、番茄钟 | 任务看板、博客 | 电商、后台系统 |
| LangGraph 路径 | TL → simple_coder | TL → simple_coder | TL → PM → Arch → FileCoder → QA → Summarize | TL → PM → Arch → FileCoder → QA → Summarize |
| 编码模式 | 单文件 ToolCallLoop | 单文件 ToolCallLoop | 逐文件 + LGTM/LBTM 审查 | 逐文件 + LGTM/LBTM 审查 |
| 每文件迭代 | 5 轮 | 15 轮 | ≤5 轮 | ≤5 轮 |
| 审查重试 | 无 | 无 | 3 次/文件 | 3 次/文件 |
| QA 修复循环 | 无 | 无 | ≤3 轮 | ≤3 轮 |
| 整体审查 | 无 | 无 | SummarizeCode | SummarizeCode |

**设计原则**：
- 简单需求快速产出（XS/S 单节点），复杂需求多节点协作保证质量（M/L）
- 所有路由逻辑内置于 LangGraph conditional edges，不依赖外部硬编码分支
- 逐文件编码 + CodeReview：每个文件生成后自动 LGTM/LBTM 审查，不通过则重写
- Hook 失败结果实时注入 LLM 上下文，Agent 能"看到"自己的验证错误并主动修复
- ContextCompactor P0-P3 分层压缩：长对话自动压缩旧消息，防止上下文溢出
- 每次成功/失败自动存入 MemoryManager（BGE-M3 混合检索 + LLM 反思），后续相似需求注入 few-shot 示例 + 历史教训
- 所有角色的行为规则写在 `prompts/*.md` 中，改行为 = 改 Markdown 文件，无需改代码

### Harness 框架

Agent 运行时，统一管理 AI 智能体全生命周期。

```
IntentRouter → TeamLeader → LangGraph 多节点编排
                ├─ XS/S: simple_coder
                └─ M/L: pm → architect → file_by_file_coder → qa → summarize
                           ↑ fail                                  ↓ fail
                           └────────── repair ─────────────────────┘
                └─ MemoryManager (BGE-M3 + LLM 反思, 全程注入)
                └─ ContextCompactor P0-P3 (全程压缩)
```

| 层级 | 模块 | 职责 |
|------|------|------|
| 0 - Routing | `intent_router.py` | 前置意图分类，四路分流 |
| 1 - Instructions | 提示词/角色/节点 | 8 个 LangGraph 节点 + 5 角色定义 + 逐文件编码 + LGTM/LBTM 审查 + SummarizeCode + 上下文压缩 |
| 2 - Tools | 工具注册/代码生成 | 工具注册表、read/write/edit_file、lint/validate/preview |
| 3 - Environment | 权限/沙箱 | 权限控制、沙箱安全执行 |
| 4 - State | 状态/工作区/记忆 | AgentState（含 tasks/interfaces/implementation_order）、WorkspaceFS、Git 版本化、Checkpoint |
| 5 - Constraints | Hook/检查 | Hook 管理器 + Craft 规则/安全/质量 + Hook 失败反馈链路 |
| 6 - Observability | 追踪/成本/上报 | 链路追踪、Token 成本统计、SSE 事件上报 |
| — | `state/memory.py` | BGE-M3 混合检索 + LLM 反思 (reflection/lesson/pattern) + 经验存储 + few-shot 注入 |
| — | `state/memory_retriever.py` | BGE-M3 Dense(1024维)+BM25 Sparse 混合检索 + TF-IDF 降级回退 |
| — | `experience/` | (DEPRECATED) TF-IDF 语义检索 + 经验存储 |

### 设计质量规则（Rules 层，渐进式披露）

规则按层级渐进注入，避免 Token 浪费：

| 层级 | 触发条件 | 规则 | Token 量 |
|------|---------|------|---------|
| **L0** | 始终注入 | anti-ai-slop（禁止 indigo 紫色、emoji 图标等 AI 刻板模式） | ~500 chars |
| **L1** | 需求含 UI 关键词 | typography（字号层级）+ color（色板结构） | ~2300 chars |
| **L2** | 需求含交互/表单关键词 | accessibility（对比度、键盘导航、语义HTML） | ~900 chars |
| **Skill** | 关键词匹配（空=通用） | 领域知识模板（如通用前端模式） | ~900 chars |

以不同任务为例：
- "做一个计算器" → L0 + Skill = 1766 chars
- "做一个登录表单" → L0 + L2 + Skill = 2500 chars
- "做一个博客网站" → L0 + L1 + Skill = 3850 chars

### 可插拔应用模板（Skill 系统）

通过 `skills/` 目录下的 Markdown 文件定义应用类型，支持关键词自动匹配：

- 待办清单、计算器、笔记、日历、通用应用
- 新增应用类型只需添加 `skills/<name>/SKILL.md`，无需修改代码

### 交互式需求澄清

- 需求过短或缺少功能关键词时，AI 自动生成结构化问题表单
- 用户补充后重新进入生成流程，最多 1 轮澄清

### 增量编辑

- 生成完成后支持持续对话（Chat 模式），AI 通过 `edit_file` 工具做精确增量修改
- 基于 search-replace 协议，避免重写整个文件

### 需求回收站

- 软删除机制：删除的需求进入回收站，`30 天后`自动清理
- 支持恢复操作
- 历史页面可通过 Tab 切换查看正常需求 / 回收站

## 界面预览

### 登录页面
![登录页面](docs/images/login.png)

### 首页
![首页](docs/images/index.png)

### 需求详情页（代码视图）
![详情页](docs/images/detail.png)

### 需求详情页（预览视图）
![预览页](docs/images/detail_preview.png)

### 历史对话
![历史对话](docs/images/history.png)

### 设置
![设置](docs/images/settings.png)

**页面说明**:
- **登录页**: Warm Soft 暖色设计，登录/注册双 Tab 切换，测试账号提示
- **首页**: 自然语言输入框 + 示例需求快捷填入
- **详情页**: 左侧气泡式 AI 对话（TeamLeader → FrontendEngineer 协同），右侧代码/预览面板（文件树 + 代码编辑器），支持桌面/平板/手机设备预览
- **历史对话**: 项目列表 + 实时搜索 + 状态筛选 + 回收站 Tab
- **设置**: 个人资料/外观偏好/关于 分区设置

## 架构总览

```
用户输入 → IntentRouter (四分流)
  ├─ QUICK/SEARCH → 秒级回复
  └─ TASK → LangGraph 多节点编排 (v3)
              │
              TeamLeader (需求分析 + 任务分解)
              输出: tasks[], interfaces, implementation_order
              │
         [复杂度路由]
         ────┴────
        │         │
    XS/S         M/L
        │         │
   simple_coder  pm → architect → file_by_file_coder
    (单文件        │                  │
     ToolCallLoop) │          逐文件编码循环:
        │         │          每个文件 → CodeReview (LGTM/LBTM)
        │         │                        │
        │         │                   LBTM → 重写 ≤3次
        │         │
        │         qa_reviewer → summarize → END ✅
        │              ↑ fail      ↓ fail
        │              └── repair ──┘
        │
        END ✅

  全域: Hook 失败反馈 → LLM 自动修复
       ContextCompactor P0-P3 → 防溢出
       MemoryManager (BGE-M3 + LLM 反思) → few-shot + 教训注入
```


## 注意事项

1. 项目仍在迭代中，欢迎 Issue / PR 一同共创
2. 需要在 `.env` 中配置 `LLM_API_KEY` 才能使用 AI 功能
3. 生产环境请配置 `JWT_SECRET_KEY`、`LLM_API_KEY` 等敏感信息
4. 建议使用现代浏览器（Chrome/Edge/Safari）
5. LangGraph v3 多节点编排工作流支持条件路由和修复循环
6. 前端开发模式：`cd frontend-vue && npm run dev` 启动 Vite 热更新开发服务器
