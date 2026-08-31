<p align="center">
  <img src="docs/images/logo.png" alt="Talk2Code Logo" width="120" />
</p>

<h1 align="center">Talk2Code</h1>

<p align="center">
  <b>用一句话，生成一个能跑的应用。</b>
</p>

<p align="center">
  输入自然语言需求 → AI 多智能体协同（需求分析 / 批量编码 / 真实浏览器验收）→ 实时产出可运行、可下载的产品代码。
</p>

<p align="center">
  <img src="docs/talk2code_pitch.gif" alt="Talk2Code 演示：一句话生成贪吃蛇小游戏" width="100%" />
</p>

<p align="center">
  🎬 <a href="https://github.com/WowCoder/talk2code/blob/main/docs/talk2code_pitch.mp4">观看高清视频（MP4）</a>
  &nbsp;·&nbsp; 📐 <a href="#架构一览">架构一览</a>
  &nbsp;·&nbsp; ⚡ <a href="#快速开始">3 步跑起来</a>
</p>

---

## 为什么不一样

多数「AI 写代码」工具止步于吐出一堆片段。Talk2Code 把<b>工程质量</b>当成一等公民：

| 能力 | 做法 |
|------|------|
| 🧠 多智能体分工 | LangGraph 编排 TeamLeader / Coder / Verify，职责分离而非一次生成了事 |
| ✅ 真实验收 | Verify 用 Playwright 在 headless Chromium 里逐条执行 AC，不是「看着像对的」 |
| 🛡️ 交付门禁 | critical 缺陷未清零不放行，转 `needs_user_input` 并附差异报告 |
| 🔗 跨文件契约 | 计划期声明 exports → 编码期契约注入 → 验收期闭合校验，杜绝「按钮静默失效」 |
| 🚫 写入即拦截 | PRE_WRITE Hook 零 LLM 成本拦掉 `type="module"` / 外部 CDN 等沙箱必炸写法 |
| 🔁 缺陷自动回炉 | 验收不通过按缺陷类别路由回 Coder 重构，架构类附根因卡片，修完再验 |
| 📊 回归纪律 | 固化 21 个回归任务（含贪吃蛇失败模式专项），改核心前后跑基线对照；失败经验入库 |

## 技术栈

- **前端**: Vue 3 + TypeScript + Vite
- **后端**: Python 3.11+ + Flask + LangGraph
- **数据库**: SQLite
- **实时通信**: SSE (Server-Sent Events)
- **认证**: JWT
- **AI 模型**: 兼容 OpenAI/Anthropic 接口协议，配置驱动切换
- **向量检索**: BGE-M3 混合检索

## 架构一览

<p align="center">
  <img src="docs/architecture.svg" alt="Talk2Code 架构流程图" width="100%" />
</p>

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
│   │   │   ├── skill_loader.py  #     声明式 Skill 加载（manifest.json 触发，knowledge/workflow 双类型）
│   │   │   ├── prompts/skills/  #     9 个技能：6 knowledge（generic/color/typography/accessibility/anti-ai-slop/game）
│   │   │   │                    #     + 3 workflow（scaffold 脚手架 / refactor 重构 / code_review 前端审查，可被 run_skill 调用组合）
│   │   │   └── nodes.py         #     LangGraph 节点（支持 Agent 委派）
│   │   ├── tools/                #   工具注册表（ToolHandler + @register_tool 装饰器）
│   │   │   └── skill_tools.py    #     run_skill：Agent 可调用/组合工作流技能的入口
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
| **TeamLeader** | Leon（技术负责人） | 需求分析 → 结构化 Plan → 复杂度分级（simple/standard） |
| **Coder** | Henry（开发工程师） | 批量创建文件 + 自适应迭代上限（文件数驱动），write_file 返回内容预览避免回读 |
| **Verify** | Catherine（质量工程师） | Playwright 真实浏览器 AC 逐条验收 → 快速通道（全部通过则跳过 LLM 评估） |

**两种复杂度 SOP**，由 TeamLeader 自动判断：

| 等级 | 触发条件 | 流程 |
|------|---------|------|
| 🟢 **simple** | 单个 HTML 页面、极简交互 | `TeamLeader` 轻量分析 → `Coder` 5 轮快速通道 → `run_preview` 验证 → 完成 |
| 🔵 **standard** | 多文件、交互式应用 | `TeamLeader` 完整 Plan + AC（DoD 程序化校验）→ 用户确认 → `Coder` 批量创建（文件数驱动轮数）→ `Verify` AC 逐条验收 → 按缺陷类别路由修复 → PASS / needs_user_input（交付门禁）|

### 代码质量验收系统

Verify 节点采用 **确定性证据优先 + LLM 增量判断** 的证据等级模型：

**L0 环境契约（写入时刻拦截）**：
- 运行环境硬约束单一事实源 `constraints/environment_contract.py`（禁止 ES Module/CDN、
  存储兜底、入口可见、引用闭合），渲染注入 TL/Coder prompt，程序化检查供 Hook/lint 复用
- PRE_WRITE Hook 零 LLM 成本拦截 `<script type="module">`、外部 CDN、import/export——
  file:// 沙箱必炸的代码在写入瞬间就被打回，不再等浏览器报错

**L3 交互式验收**：
- LLM 将每条验收条件翻译为 Playwright DOM 操作序列（type/click/assert_exists...）
- 在 headless Chromium 中逐条执行，收集 passed/failed/截图
- 全部 AC 通过 + preview 零错误 → **快速通道 PASS**（跳过 LLM 深度评估；
  UI/代码质量诚实标记为未评估，截图落盘 `.task/evaluator/screenshot.png`）
- 结果实时推送到前端 Spec 面板（AC 级别 ✅/❌）

**L2 深度评估**（AC 未全通过时触发）：
- 双视角 LLM 评估（功能正确性 + 代码/UI 质量），5 维度 1-10 分
- 确定性证据定下限：冒烟缺陷/浏览器错误/AC 失败是机器实测事实，LLM 无权推翻为 PASS
- 未通过时缺陷按类别路由：架构类（模块加载/CDN/文件缺失）携带根因卡片回 Coder 重构，
  局部语法类走小上下文定向修复

### 交付门禁

critical 缺陷未清零的需求不再自动放行为 finished_with_issues，而是转
**needs_user_input** 并附差异报告（未达成 AC 清单 + 关键缺陷明细）。
可用 `DELIVERY_GATE_STRICT=false` 关闭。Chat 人工修改路径同样有轻量闸门：
修改后自动跑一次冒烟，引入确定性缺陷则回滚本次修改并告知用户。

### 跨文件 API 契约（导出闭合）

多文件批量生成的最大风险是「A 文件调用了 B 文件没实现的方法」——页面不报错，
按钮静默失效（需求 124 事故：app.js 用了 utils.js 从没定义的 toast/copyText）。
三层确定性防护：

1. **计划期** `tl_analysis.md` 强制 tasks 声明 `exports`（每文件挂载到 window 的
   全局对象+方法清单）；`plan_validator` 校验被依赖的 js 未声明 exports 即打回。
2. **编码期** `build_api_contracts_section(plan)` 把 exports 渲染成「跨文件 API
   契约」注入 coder prompt——coder 只允许调用清单内方法，未声明能力必须在自己
   文件里实现。
3. **验收期** `check_cross_file_contract()`（确定性、零 LLM）解析各 JS 的实际
   导出与全项目引用，比对缺失；未定义的全局对象（如 `Game is not defined`）
   一并拦截。断裂属架构类缺陷，携根因卡片路由回 coder 重构；`classList` 动态
   类名与 CSS 无匹配则发警告。挂在 verify 冒烟 + task_complete 完成校验两道关。

### Plan DoD 校验

TeamLeader 产出的开发计划在进入 Coder 前经过程序化校验
（`constraints/plan_validator.py`）：文件引用闭合、每个任务有 purpose、
每条 AC 的 how_to_verify 含可操作动词、复杂度与文件数一致。
不合格打回 TL 重出最多 1 次；带病放行会记录弱 AC 清单供下游参考。

### 上下文效率优化

- **write_file 返回内容预览**：写入后返回前 80 行 + 尾 10 行，Agent 无需 read_file 验证
- **PRE_TOOL_USE Hook 真阻断**：写入后 2 轮内实际阻止对同一文件的回读
- **批量文件创建**：允许一次创建 2-3 个相关文件，消除"每次一个文件"的串行瓶颈
- **迭代上限文件数驱动**：3 文件 = 9 轮，5 文件 = 13 轮，按需分配不浪费

### 记忆系统

跨会话经验积累 — AI 会在任务前检索相关历史经验辅助编码，任务后自动总结关键模式供后续复用。
正负经验都沉淀：失败任务显式打 failure 标签、提高重要度，检索命中时以 ⚠️ 警示案例呈现，
避免同类缺陷重蹈覆辙。相似记忆定期由 LLM 合并去重。

### 学习闭环

`eval/tasks/tasks.yaml` 里固化的 21 个回归任务，覆盖的都是**历史上真实踩过的坑**，
而不是凑数的样例：

- `t21` 贪吃蛇 —— 曾连续七次失败的失败模式专项
- `ENV-3` —— `file://` 下 ES Module 被 CORS 拦截（需求 #115 的直接死因）
- `ENV-2` —— 无网络沙箱里 CDN 必挂（#110–116 反复踩坑）

改 harness 核心时前后各跑一次基线做对照，用数据判断「这次改动到底更好还是更差」，
而不是靠感觉。

最近一次全量基线（2026-08-31）：20/21 通过，唯一未通过项定位为上游模型端点读超时，
非架构缺陷。完整报告见 `eval/results/baseline_20260831_113741.md`。

标准命令（在 `backend/` 目录下执行）：

  ```bash
  env -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
      -u ALL_PROXY -u all_proxy PYTHONPATH=. \
      ../venv/bin/python ../eval/run_eval.py --no-preview
  ```

  - 虚拟环境在**仓库根目录** `venv/`（不是 `backend/venv/`）；
  - eval 是独立进程，必须显式清掉系统代理变量，否则会继承代理、导致 LLM 请求 ProxyError（同生产环境 req #134 根因）；
  - `--no-preview` 跳过 Playwright 浏览器验收（仅跑 file/结构/内容断言），速度快、无 429 限流风险；
  - 完整链路（含浏览器预览）去掉该开关即可，但耗时 30–60 分钟且 agnes 端点有 429 限流风险。
  - 预览验证在 headless Chromium 中**真实加载生成页面**，捕获 `pageerror` / `console_error` / `request_failed` 等运行时错误（含跨文件导出未定义导致的崩溃），与静态结构审计互补，构成「静态 + 运行时」双保险质量信号。
- trace 覆盖全流程：编码迭代 / verify 评估 / defect_repair 修复均有 span 可归因
- `logs/llm_traffic.log` 为 JSON Lines 结构化格式，按天轮转保留 7 天

### 快速问答

简单技术问题走快速通道，直接回答不进入编码流水线，节省 Token 和响应时间。

## 注意事项

1. 项目仍在迭代中，欢迎 Issue / PR 一同共创
2. 需要在 `.env` 中配置 `LLM_API_KEY` 才能使用 AI 功能
3. **安全配置（必读）**：
   - 生产环境必须配置强随机 `JWT_SECRET_KEY`（`python -c "import secrets; print(secrets.token_urlsafe(48))"`）。
     使用默认密钥且服务对外可达（非 debug 或监听 `0.0.0.0`）时，应用会**拒绝启动**；
     仅限无法配置密钥的隔离演示环境可显式设 `ALLOW_INSECURE_SECRETS=true` 豁免。
   - 默认开启 API 限流（`DISABLE_RATE_LIMIT=false`）；测试套件由 conftest 自行注入关闭。
   - 未部署在可信反向代理之后时，请勿开启 `TRUST_PROXY_HEADERS=true`（否则限流 IP 可被伪造）。
   - 登录令牌只通过 httpOnly cookie 下发，前端不存储 token。
4. 预览能力 URL 默认使用同源相对路径；若前后端不同域部署，配置 `PREVIEW_PUBLIC_BASE_URL`
   指向预览入口（如 `https://preview.example.com`）。
5. 建议使用现代浏览器（Chrome/Edge/Safari）
6. 前端开发模式：`cd frontend-vue && npm run dev` 启动 Vite 热更新开发服务器
7. README 中出现的 `test / 123456` 仅为本地演示账号，生产环境请删除或替换
8. **冷启动兼容性**：部分 OpenAI 兼容端点（如 agnes）强制要求 messages 中至少有一条 `user` 消息，
   否则首轮请求返回 400（`No user query found in messages.`）。`harness/runtime.py` 的
   `_build_messages()` 在对话历史为空（冷启动）时自动补一条 user 消息，生产环境因需求本就
   作为 user 消息进入历史而 no-op，零副作用。
9. **LLM 流量诊断**：`logs/llm_traffic.log`（仓库根目录，非 `backend/logs`）为 JSON Lines 格式，
   按天轮转保留 7 天，每条含完整请求/响应体，可用于定位 400/500 等端点校验问题。
