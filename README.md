# Atoms.dev Vibe Coding Demo

一个融合 Vibe Coding 实时代码生成交互风格的 AI 驱动代码生成平台 Demo。

## 核心定位

用户输入自然语言需求 → AI 多智能体协同处理 → 以 Vibe Coding 方式实时生成可运行的产品代码

## 技术栈

- **前端**: HTML5 + CSS3 + JavaScript + Tailwind CSS + CodeMirror
- **后端**: Python 3.8+ + Flask 2.0+
- **数据库**: SQLite
- **实时通信**: SSE (Server-Sent Events)
- **认证**: JWT
- **AI 模型**: 阿里云百炼（通义千问）+ LangChain

## 项目结构

```
atoms-demo/
├── backend/
│   ├── app.py              # Flask 主程序（API、AI 智能体、SSE）
│   ├── config.py           # 配置文件
│   ├── models.py           # 数据库模型（User, Requirement）
│   ├── utils.py            # 工具类（密码加密、SSE 消息）
│   ├── llm_client.py       # 基础 LLM 客户端（DashScope API）
│   ├── langchain_client.py # LangChain 封装的 LLM 客户端
│   └── requirements.txt    # Python 依赖
└── frontend/
    ├── login.html          # 登录/注册页
    ├── index.html          # 首页（需求输入、我的应用列表）
    └── detail.html         # 需求详情页（AI 对话 + 代码编辑器 + 持续对话）
```

## 快速开始

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置环境变量

**重要**：需要配置阿里云百炼 API Key 才能使用 AI 功能。

在 `backend/` 目录下创建 `.env` 文件（可参考 `.env.example` 模板）：

```bash
# 阿里云百炼大模型 API Key
DASHSCOPE_API_KEY=your_api_key_here

# 模型选择（可选）
DASHSCOPE_MODEL=qwen-plus
```

**获取 API Key**: 访问 [阿里云百炼控制台](https://bailian.console.aliyun.com/) 申请。

### 3. 启动服务

```bash
python app.py
```

服务启动后访问：http://localhost:5001/login.html

### 4. 测试账号

- 用户名：`test`
- 密码：`123456`

## 使用流程

1. **登录** - 使用测试账号或注册新账号
2. **输入需求** - 在首页输入框描述你的需求，或点击"我的应用"查看历史创建
   - 示例：`开发一个待办清单 App，支持增删改查`
   - 示例：`做一个计算器应用`
   - 示例：`创建一个笔记应用`
3. **查看生成** - 进入详情页后：
   - **左侧**: 观看 AI 多智能体（研究员→产品经理→架构师→工程师）协同讨论
   - **右侧**: 实时查看代码生成（支持代码/预览 TAB 切换）
4. **持续对话** - 生成完成后，可在左侧 AI 对话面板底部输入框继续与 AI 对话
5. **预览与下载** - 切换到"预览"TAB 实时查看效果，或复制/下载代码文件

## 核心功能

### 用户系统
- 用户注册/登录（JWT 认证）
- 登录状态持久化
- 未登录拦截

### 我的应用
- 查看用户创建的所有应用列表
- 显示应用状态（处理中/生成中/已完成/失败）
- 点击快速跳转到应用详情

### AI 多智能体协同
1. **研究员**: 分析市场需求和可行性
2. **产品经理**: 拆解功能清单和交互逻辑
3. **架构师**: 设计技术架构和数据结构
4. **工程师**: 流式生成可运行代码

### 持续对话
- 会话历史持久化到数据库
- 支持多轮对话，AI 记住上下文
- 刷新页面不丢失对话记录

### 代码编辑器
- CodeMirror 语法高亮
- 多文件切换（HTML/CSS/JS）
- 实时预览（iframe 沙箱隔离）
- 复制/下载功能

### 数据持久化
- SQLite 存储用户数据
- 对话历史完整保存
- 代码文件完整保存
- 刷新页面数据恢复

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/register | POST | 用户注册 |
| /api/login | POST | 用户登录 |
| /api/requirements | POST | 创建需求 |
| /api/requirements | GET | 获取需求列表 |
| /api/requirements/<id> | GET | 获取需求详情 |
| /api/requirements/<id>/chat | POST | 发送对话消息（持续对话） |
| /api/sse/<id> | GET | SSE 实时推送连接 |

## 支持的应用类型

- **待办清单 App** (输入包含"待办"、"todo"或"清单")
- **计算器 App** (输入包含"计算器"或"计算")
- **笔记 App** (输入包含"笔记"或"备忘录")
- **通用应用** (其他需求)

## 界面预览

### 首页
- 需求输入框
- "我的应用"入口（查看历史创建）

### 详情页
- 左右分栏布局
- 左侧：AI 对话面板（支持持续对话输入框）
- 右侧：代码/预览 TAB 切换
  - 代码 TAB：文件标签、复制/下载按钮
  - 预览 TAB：实时预览效果（沙箱隔离）

## 注意事项

1. 这是一个 Demo 项目，AI 智能体逻辑为预设模板
2. 实际生产环境需要接入真实 AI 模型（如 Claude API）
3. JWT 密钥请生产环境使用环境变量配置
4. 建议使用现代浏览器（Chrome/Edge/Safari）
