# Talk2Code

一个 AI 驱动的一站式网站生成平台，用户输入自然语言需求 → AI 多智能体协同处理 → 实时生成可运行的产品代码。

![首页](docs/images/index.png)

## 技术栈

- **前端**: Vue 3 + TypeScript + Vite
- **后端**: Python 3.11+ + Flask + LangGraph
- **数据库**: SQLite
- **实时通信**: SSE (Server-Sent Events)
- **认证**: JWT
- **AI 模型**: 兼容 OpenAI/Anthropic 接口协议，配置驱动切换
- **向量检索**: BGE-M3 混合检索

## 项目结构

```
talk2code/
├── backend/
│   ├── app.py                    # Flask 主程序（API 路由、SSE 推送）
│   ├── config.py                 # 配置管理
│   ├── models/                   # 数据模型
│   ├── llm/                      # LLM 统一客户端（OpenAI/Anthropic 双协议）
│   ├── harness/                  # Agent 运行时框架
│   │   ├── instructions/         #   LLM 指令与 Prompt 管理
│   │   │   ├── compactor.py     #     上下文压缩（支持 preserve 标记保护关键消息）
│   │   │   ├── skill_loader.py  #     声明式 Skill 加载（manifest.json 触发）
│   │   │   └── nodes.py         #     LangGraph 节点（支持 Agent 委派）
│   │   ├── tools/                #   工具注册表（ToolHandler + @register_tool 装饰器）
│   │   ├── state/                #   状态管理 / 工作区 / 记忆系统
│   │   ├── constraints/          #   Hook 与质量约束
│   │   ├── events.py             #   类型化事件模型（Pydantic）
│   │   ├── plugins/              #   插件系统（.talk2code-plugin/plugin.json）
│   │   └── observability/        #   追踪 / Token 成本 / SSE 上报
│   ├── services/                 # SSE 传输、任务队列
│   └── utils/                    # 限流、重试、安全工具
├── frontend-vue/                 # Vue 3 + TypeScript 前端
│   └── src/
│       ├── components/           # UI 组件
│       ├── views/                # 页面视图
│       ├── composables/          # 组合式函数
│       ├── stores/               # Pinia 状态管理
│       └── router/               # 路由配置
├── start.sh                      # 一键启动脚本
└── openspec/                     # OpenSpec 规范驱动开发
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

## 界面截图

### 首页 — 需求输入
![首页](docs/images/index.png)

### 详情页 — 代码 + 对话 + 预览
![详情页](docs/images/detail.png)

### 预览 — 浏览器实时运行
![预览](docs/images/detail_preview.png)

### 预览 — Spec
![预览](docs/images/detail_spec.png)

### 历史对话
![历史](docs/images/history.png)

## 核心功能

### AI 多智能体协同

基于 LangGraph v5 编排的 3 节点 Agent 工作流，前置意图分类器做智能分流。

| Agent | 角色 | 职责 |
|-------|------|------|
| **TeamLeader** | Leon（技术负责人） | 需求分析 → 结构化方案 → 任务拆分 |
| **Coder** | Henry（开发工程师） | 逐文件编码实现，自动选择编码策略 |
| **Verify** | Catherine（质量工程师） | 独立评估代码质量，浏览器真实执行验证 |

**支持三种工作模式**，由意图分类器智能路由：

| 模式 | 触发场景 | 流程 |
|------|---------|------|
| 🆕 **新需求** | 从零开始创建项目 | `TeamLeader` 需求分析 + 任务规划 → `Coder` 逐文件编码 + 自验证 → `Verify` 独立质量评估。不通过则自动修复重试，直到达标 |
| ➕ **追加需求** | 在已有项目上增加功能 | 小改动走增量编辑直接完成；大改动自动升级为完整 SOP |
| 🐛 **BUG 修复** | 修复项目中的缺陷 | `Coder` 诊断定位根因 → 精确修复 → 浏览器验证。可选 QA 回归检查确保未引入新问题 |

### 记忆系统

跨会话经验积累 — AI 会在任务前检索相关历史经验辅助编码，任务后自动总结关键模式供后续复用，持续优化生成质量。

### 快速问答

简单技术问题走快速通道，直接回答不进入编码流水线，节省 Token 和响应时间。

## 注意事项

1. 项目仍在迭代中，欢迎 Issue / PR 一同共创
2. 需要在 `.env` 中配置 `LLM_API_KEY` 才能使用 AI 功能
3. 生产环境请配置 `JWT_SECRET_KEY`、`LLM_API_KEY` 等敏感信息
4. 建议使用现代浏览器（Chrome/Edge/Safari）
5. 前端开发模式：`cd frontend-vue && npm run dev` 启动 Vite 热更新开发服务器
