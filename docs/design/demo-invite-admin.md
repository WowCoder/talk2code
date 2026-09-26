# 演示模式 · 邀请码准入 · 运营后台 设计方案

> 状态：待评审（v1）
> 范围：R1 演示模式只读 / R2 演示帐号与需求转移 CLI / R3 注册邀请码 / R4 运营与审批后台
> 代码基线：Flask + JWT(httpOnly cookie) + SQLAlchemy，43 个路由，无 CLI、无后台、无邮件/短信基础设施

---

## 0. 结论先行

| 需求 | 核心决策 | 为什么不是别的做法 |
|---|---|---|
| R1 演示模式 | 演示登录签发 **带 `demo:true` claim 的 JWT**，后端 `before_request` **默认拒绝所有写方法**（白名单放行），前端只做 UX 置灰 | 只做前端隐藏 = 改一个 curl 就能写库。默认拒绝（而非黑名单）保证**以后新增写接口不会漏** |
| R2 演示帐号 | 演示帐号是 `users` 表里的**真实用户**，密码由 CLI 随机生成、只打印一次；`backend/manage.py` 提供 `demo transfer` | 不做数据副本：副本要维护同步逻辑，而后端拦截已能保证"打不进去" |
| R3 邀请码 | 单表 `invite_codes` 承载申请→审批→发放→使用全流程；**一次性 + 7 天有效**；注册时在同一事务内原子核销 | 双表（申请单 + 码）要维护同步；永久码等于公开注册 |
| R3 发放渠道 | **邮箱 SMTP**，手机号作为申请表单必填字段留存（人工联系/风控用），不接短信网关 | 短信需企业资质 + 签名报备 + 按条计费，且本地开发无法真发 |
| R4 后台 | 主应用内 `/api/admin/*` + `admin_users` 独立表 + Vue 后台页；管理员 token 走 **Authorization header**，与前台 cookie 登录态互不覆盖 | 独立服务要重复部署与鉴权；Flask-Admin 样式割裂且定制审批流受限 |
| R4 实时审批 | 后台列表 **15s 轮询**（低频、量小），审批操作即时生效；SSE 列为可选增强 | 内测期每天几十条申请，轮询成本近乎为零且无连接管理负担 |
| R4 指标 | 运营指标走 `/api/admin/metrics`（鉴权）；公开的 `/api/metrics` 保持纯技术指标不变 | 公开 Prometheus 端点不应暴露用户数、需求数等业务指标 |

---

## 1. 现状摸底（设计的事实依据）

### 1.1 鉴权现状

- JWT 由 `create_access_token(identity=str(user.id))` 签发，仅通过 httpOnly cookie 下发（`routes/auth.py:82`），`JWT_TOKEN_LOCATION = ['headers', 'cookies']`。
- 每个路由自己 `@jwt_required()` + `int(get_jwt_identity())`，共约 30 处鉴权点，**没有统一的写操作闸门**。
- `factory.py:83` 已有一个 `before_request` 钩子 `_set_rate_limit_identity()` 用 `verify_jwt_in_request(optional=True)` 解析身份 —— 演示守卫复用同一模式，注册在其**之后**（保证 `g.user_id` 已就绪）。

### 1.2 写接口清单（演示模式必须全部拦住的）

| 模块 | 写接口 | 演示模式 |
|---|---|---|
| requirements | `POST /api/requirements`、`PUT trash`、`PUT restore`、`DELETE`、`POST chat/clarify/confirm/cancel/resume`、`POST code`、`PUT code/all` | 全部拦截 |
| publish | `POST /api/publish`、`POST /api/publish/<slug>/unpublish` | 全部拦截 |
| market | `POST/DELETE like`、`PATCH /api/publish/<slug>/market`、`POST/DELETE cover`、`POST comment`、`DELETE comment`、`POST/DELETE follow` | 全部拦截 |
| auth | `POST register`、`POST login`、`POST logout` | **白名单放行**（演示→注册的转化路径必须通） |
| invite | `POST /api/invite/requests` | **白名单放行** |
| admin | `/api/admin/*` | 由 `admin_required` 独立鉴权，不进 demo 守卫 |

### 1.3 可复用的既有资产

- `services/market/heat.py::compute_heat(uniq, likes, comments, hours)` —— 热度算法直接复用，后台"需求热度"不另造公式。
- `utils/db.py::get_db() / transactional_db()` —— 所有 DB 访问必须走它（CLAUDE.md 硬性约定）。
- `models.py::_add_column()` —— 新增列的迁移必须走它，**按 PG/SQLite 分别给 DDL**（踩过坑：`BOOLEAN DEFAULT 0` 在 PG 上是 DatatypeMismatch）。
- `routes/health.py::/api/metrics` —— Prometheus 文本端点，保持公开指标不变。

---

## 2. R1 演示模式（只读）

### 2.1 身份模型

演示模式**不是匿名游客**，而是"以演示帐号的身份登录，但被剥夺写权限"。

```
POST /api/demo/enter
  → 查 users.username == settings.DEMO_USERNAME
  → create_access_token(identity=str(demo.id), additional_claims={"demo": True, "type": "user"})
  → set_access_cookies(...)
  → {"user": {"id":..., "username":"演示帐号", "is_demo": true}}
```

这样设计的好处：**所有按 `user_id` 过滤的既有查询一行都不用改**。演示用户看到的"历史记录"就是演示帐号的需求列表，"需求详情"就是它自己的需求 —— 复用现有 `user_id == current_user_id` 过滤即可，不引入任何"公开数据"旁路。

> 关键约束：`/api/user/info` 增加 `is_demo` 字段，前端 store 据此渲染。演示 JWT 与普通 JWT 用同一密钥，`type` claim 区分用途（`"user"` / `"admin"`）。

### 2.2 后端强制：默认拒绝（deny by default）

新增 `utils/demo_guard.py`：

```python
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# 演示模式下仍然允许的写接口 —— 显式白名单，新增即需评审
DEMO_WRITE_ALLOWLIST = (
    "/api/login", "/api/logout", "/api/register",
    "/api/invite/requests",
    "/api/admin/",          # 后台由 admin_required 独立鉴权
)

@app.before_request
def _demo_write_guard():
    if request.method not in WRITE_METHODS:
        return
    if request.path.startswith(DEMO_WRITE_ALLOWLIST):
        return
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt()
    except Exception:
        return                      # 无 token：交给各路由自己的 @jwt_required 处理
    if claims and claims.get("demo"):
        return jsonify({"error": "演示模式为只读，不可修改数据", "code": "demo_readonly"}), 403
```

**为什么是"默认拒绝"而不是"给 30 个接口贴装饰器"**：贴装饰器是黑名单思维 —— 半年后有人加一个 `POST /api/requirements/<id>/fork`，忘了贴，演示账号就被写脏了，且没有任何告警。默认拒绝把这个失败模式反转成"新接口默认被拦，需要显式评审放行"，漏贴的表现是**功能不可用**（立刻被发现），而不是**数据被改**（可能几个月才发现）。

守卫的边界（明确不做什么）：
- 不改任何 GET 语义，读接口一律放行 —— 只读模式的定义就是"能看不能改"。
- 不负责鉴权，无 token 的请求直接交给路由层 `@jwt_required()`。
- 不拦 `OPTIONS`（CORS 预检）。

### 2.3 演示模式的退出与转化

- 顶部常驻 banner：`演示模式 · 只读` + `退出演示`（调 `/api/logout`）。
- 任何被拦的操作不弹"错误"，而是弹引导：`注册后即可创建自己的应用`（跳登录页注册 tab）。演示模式的目的是转化，不是劝退。

### 2.4 前端改造

| 位置 | 改动 |
|---|---|
| `stores/auth.ts` | 新增 `isDemo` ref；`initAuth()` 读 `/api/user/info` 的 `is_demo`；新增 `enterDemo()` |
| `components/auth/LoginForm.vue` | 底部加次要按钮「以演示模式进入」（`POST /api/demo/enter`） |
| `components/layout/AppNav.vue` / `App.vue` | 新增 `DemoBanner.vue`，`isDemo` 时常驻 |
| `views/HistoryView.vue` | 隐藏删除/回收站入口；新建按钮改为引导注册 |
| `views/DetailView.vue` | `DialogueInput` 禁用（占位文案"演示模式不可提问"）；隐藏发布/删除 |
| `views/HomeView.vue` | `RequirementInput` 禁用 + 引导注册 |
| `views/MarketView.vue` / `MarketDetailView.vue` | 点赞/留言/关注点击时 toast「演示模式不可点赞」，不发请求 |
| `views/SettingsView.vue` | 所有表单 `disabled` |

> 前端置灰是**体验层**，不是安全层。安全性 100% 由 §2.2 后端守卫保证 —— 前端就算全删，演示帐号也写不进去。

---

## 3. R2 演示帐号与需求转移 CLI

### 3.1 帐号创建

- 配置新增 `DEMO_USERNAME: str = "demo"`。
- CLI `python manage.py demo init`：
  - 若用户不存在则创建，密码用 `secrets.token_urlsafe(20)` 生成，**只在终端打印一次**（同理 `demo rotate-password` 可轮换）。
  - 只存 bcrypt 哈希（复用 `utils/security.hash_password`），明文不落盘、不进日志。
- 演示帐号**与后台管理员完全无关**，它就是 `users` 表里的一行。

> 给你的凭据会在执行 `demo init` 时打印。这是唯一一次能看到明文的机会，请自行保存。

### 3.2 CLI：`backend/manage.py`（argparse，零新增依赖）

```
python manage.py demo init                       # 创建演示帐号并打印初始密码
python manage.py demo rotate-password            # 轮换演示帐号密码
python manage.py demo transfer --from <id|name> \
        [--requirement-id N | --all] \
        [--mode move|copy] [--dry-run] [--yes]
python manage.py invite create --count 10 --days 7   # 管理员应急批量发码
python manage.py admin create-user --username <u>    # 创建后台管理员
```

**`demo transfer` 的三条硬性约束**：

1. **默认 dry-run**：不带 `--yes` 时只打印影响清单（要迁哪些 requirement、哪些 published_site、多少 trace），不落库。带 `--yes` 才执行。
2. **单事务**：`with transactional_db()` 一次提交，中途异常全回滚，不留半迁移状态。
3. **幂等**：`--mode move` 重复执行时，源用户已无这些需求 → 影响 0 行，不报错。

### 3.3 转移的数据边界（表级清单）

这一步最容易做错：不是把所有 `user_id` 无脑 UPDATE 一遍。

| 表 | 是否随需求迁移 | 理由 |
|---|---|---|
| `requirements.user_id` | ✅ 迁 | 需求本体 |
| `agent_traces.user_id` | ✅ 迁（按 `requirement_id` 关联） | trace 是需求的产物，跟着走才对得上 |
| `published_sites.user_id` | ✅ 迁（按 `requirement_id` 关联） | 不迁的话市集详情页显示的作者仍是原用户 |
| `agent_memories` / `agent_memories_v2` / `agent_memory_vectors` | ❌ **不迁** | 记忆是个人私有资产，迁过去会污染演示帐号的记忆库，进而影响它后续生成质量 |
| `site_likes` / `site_comments` | ❌ 不迁 | 这两张表的 `user_id` 是**互动者**不是作者，改了等于把别人的点赞算到演示帐号头上 |
| `user_follows` | ❌ 不迁 | 社交关系不可转让 |

`--mode copy`（可选）：需求**复制**一份到演示帐号，原用户保留。`move` 为默认。

### 3.4 转移后的可见性

迁移进演示帐号的需求，其 `published_sites` 若 `market_visible=True`，会出现在公开市集且作者显示为"演示帐号"。这是预期行为（本来就是要拿来做展示的），但 CLI 在 dry-run 输出里会**显式列出受影响的市集站点**，避免误把私活搬到公开列表。

---

## 4. R3 注册邀请码

### 4.1 数据模型：单表 + 状态机

新增 `invite_codes`：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | PK | |
| `code` | String(32) unique, nullable | 审批通过时才生成 |
| `applicant_email` | String(255), index | 必填 |
| `applicant_phone` | String(32) | 必填（留存/风控，不用于发送） |
| `applicant_note` | String(500) | 用途说明 |
| `status` | String(16), index | `pending` → `issued` / `rejected`；`issued` → `used` / `expired` / `revoked` |
| `created_at` / `decided_at` / `expires_at` / `used_at` | DateTime | |
| `decided_by` | Integer, FK admin_users.id | |
| `used_by_user_id` | Integer, nullable | |
| `reject_reason` | String(200) | 拒绝理由会随邮件发给申请人 |
| `delivery_status` | String(16) | `pending` / `sent` / `failed`（见 §4.4） |

状态机：

```
                 ┌── approve ──▶ issued ──use──▶ used
pending ─────────┤                  │
                 └── reject ───▶ rejected    └──过期──▶ expired
                                               └──管理员吊销──▶ revoked
```

单表而非「申请单 + 邀请码」两表：两表需要维护同步与外键，且"一个申请对应一个码"是本阶段的真实约束，拆表是过度设计。

### 4.2 注册校验（并发安全）

`POST /api/register` 新增必填 `invite_code`，在**同一个 `transactional_db()` 事务**内完成：

```python
# 1. 校验码可用（行级锁，防并发双用）
updated = db.execute(
    update(InviteCode)
    .where(InviteCode.code == code, InviteCode.status == "issued", InviteCode.expires_at > now)
    .values(status="used", used_at=now)
).rowcount
if updated != 1:
    raise RegisterError("邀请码无效或已使用")   # 事务回滚
# 2. 同一事务内创建 user
db.add(User(...)); db.flush()
db.execute(update(InviteCode).where(...).values(used_by_user_id=new_user.id))
```

**并发防护靠 `UPDATE ... WHERE status='issued'` 的 rowcount 检查，不靠"先查再插"** —— 先查再插在并发下必然漏判（`site_visit_dedup` 已经踩过一次这个坑，用唯一约束解决的；这里用行锁 + rowcount，语义等价）。

错误文案统一为「邀请码无效或已使用」，**不区分**"不存在/已用/已过期" —— 区分了就等于给攻击者一个邀请码枚举探针。

### 4.3 申请入口与弹窗

- `RegisterForm.vue` 新增必填输入框「邀请码」，下方加链接「没有邀请码？申请一个」→ 打开 `InviteRequestDialog.vue`（邮箱 + 手机号 + 用途说明）。
- `POST /api/invite/requests`：
  - 限流：IP 维度 + 邮箱维度（复用 `utils/rate_limiter.py`）。
  - 幂等：同一邮箱 24h 内已有 `pending` → 返回「已收到申请，请等待审批」，**不泄露该邮箱是否已有 issued 码**。
  - 提交成功后弹窗变为「已提交，审批通过后邀请码将发送到你的邮箱」。

### 4.4 邮件发放与失败降级

新增 `services/notify/email.py`（SMTP，配置见 §7）。

**核心设计：发送失败不能让审批失败。**

```
审批通过 → DB 落 status=issued + code 生成 + delivery_status=pending
        → 调 SMTP 发送
        → 成功：delivery_status=sent
        → 失败：delivery_status=failed，审批仍然成功，后台可「重新发送」
```

理由：SMTP 是外部依赖，必然有不可用的时候。如果发信失败就回滚审批，管理员会遇到"点了通过但没生效"的黑洞，且没有任何重试入口。把发信从审批事务里摘出去，审批永远成功，发信可独立重试 —— 这是失败隔离，不是偷懒。

**降级开关**：`SMTP_ENABLED=false` 时（本地开发 / 无 SMTP 环境）不发信，`delivery_status` 记为 `skipped`，后台详情页**直接展示邀请码明文**供管理员手动复制。这样本地开发能完整走通审批流程，不阻塞。

### 4.5 码空间与防枚举

- 码格式：`T2C-` + Crockford Base32 12 字符（去掉 `I/L/O/U` 等易混字符）→ 约 60 bit 熵，不可暴力枚举。
- 一次性 + 7 天有效期（可配）。
- 注册接口失败也计入限流。
- 管理员可 `revoke` 已发出但未使用的码（疑似泄露时）。

---

## 5. R4 运营与审批后台

### 5.1 管理员身份与双登录态隔离

新增 `admin_users` 表（**独立于 `users`**）：`id / username / password_hash / role / created_at / last_login_at`。

**为什么独立表而不给 `users` 加 `is_admin` 列**：`users` 表是用户侧身份，给它加权限位意味着任何一个注册用户都是潜在的提权面（一个 `UPDATE users SET is_admin=1` 的注入或越权就能拿到后台）。物理隔离后，后台权限与用户体系零交集。

登录态隔离方案：

| | 前台用户 | 后台管理员 |
|---|---|---|
| token 载体 | httpOnly cookie（`access_token_cookie`） | **Authorization: Bearer header** |
| claim | `{"type": "user", "demo"?: bool}` | `{"type": "admin"}` |
| 前端存储 | 不存（cookie 自动带） | `sessionStorage` |
| 有效期 | 24h | **2h** |

**为什么后台走 header 而不是 cookie**：flask-jwt-extended 的 cookie 名是全局配置（`JWT_ACCESS_COOKIE_NAME`），无法按 token 动态改。若管理员也写同一个 cookie，前台登录态和后台登录态会**互相覆盖**（登录后台 → 前台掉线）。走 header 则两套登录态天然并存。

代价是 token 存 `sessionStorage` 有 XSS 面。缓解：① 有效期 2h；② 后台页面**只渲染结构化数据，绝不渲染任何用户生成内容**（不 innerHTML 需求内容、不加载 AI 生成的代码），XSS 面被结构性消除；③ 后台全部接口另经 `admin_required` 校验 `type == "admin"` claim。

```python
def admin_required(fn):
    @wraps(fn)
    @jwt_required()
    def _wrapped(*a, **kw):
        if get_jwt().get("type") != "admin":
            return jsonify({"error": "需要管理员权限"}), 403
        return fn(*a, **kw)
    return _wrapped
```

### 5.2 邀请码审批

| 接口 | 说明 |
|---|---|
| `GET /api/admin/invites?status=pending&page=1` | 列表，按 `created_at` 倒序 |
| `POST /api/admin/invites/<id>/approve` | 生成码 → 发信（见 §4.4）→ 返回含码明文 |
| `POST /api/admin/invites/<id>/reject` | body 带 `reason`，随拒信发出 |
| `POST /api/admin/invites/<id>/resend` | 仅当 `delivery_status != sent` |
| `POST /api/admin/invites/<id>/revoke` | 已发出未使用的码吊销 |

**实时性**：后台列表 15s 轮询（页面可见时才轮询，`document.visibilityState` 判断），新申请到达时顶部计数高亮。审批操作本身是同步的，点下去立即生效。

> 若后续申请量上来再换 SSE（`services/sse_manager.py` 已有基础设施），但当前阶段轮询是正确选择 —— SSE 要新增连接管理、重连、消息幂等，而这里的数据量 15s 延迟完全无感。

### 5.3 指标看板 `GET /api/admin/metrics`

**A. 用户**

| 指标 | 口径 |
|---|---|
| 总用户数 | `COUNT(users)`（排除演示帐号） |
| 今日新增 / 7 日新增 | `create_time >= today/7d` |
| 7 日活跃用户 | 7 日内有 `requirements.create_time` 的 distinct user_id |
| 注册转化 | 已使用邀请码数 / 已发出邀请码数 |

**B. 需求**

| 指标 | 口径 |
|---|---|
| 总需求数 / 今日 / 7 日 | `COUNT(requirements WHERE is_deleted=false)` |
| 状态分布 | `finished / failed / processing / pending` 计数 |
| **完成率** | `finished / (finished + failed)` —— 这是产品质量的北极星指标 |
| 失败率与失败原因 Top | `error_message` 聚合取前 5 |

**C. 发布与市集**

| 指标 | 口径 |
|---|---|
| 已发布站点数 | `COUNT(published_sites)` |
| 上架市集数 | `WHERE market_visible = true` |
| 市集总浏览/点赞/留言 | `SUM(view_count)` / `COUNT(site_likes)` / `COUNT(site_comments WHERE is_deleted=false)` |

**D. 需求热度榜（Top 20）**

- 已上架站点：直接复用 `compute_heat(uniq_visitors, likes, comments, hours_since_listed)`，**不另造公式** —— 后台热度必须和市集前台显示的数字一致，否则会出现"后台说 87、前台显示 52"的信任崩塌。
- 未发布需求：无市集信号，热度记为 `-`（不编造数字）。另给"需求活跃度"内部指标：对话轮数、代码文件数、`update_time` 新鲜度。

> 实现约定：热度榜只取 Top 20，对话轮数等 JSON 字段在 **Python 侧**（`len(dialogue_history)`）计算，不写 `json_array_length` SQL —— 该函数在 SQLite 与 PG 上语法不同，为 20 行数据引入方言分支不划算。

**E. 可观测性（技术侧）**

- 复用 `/api/health` 的组件状态（db / llm / task_queue / sandbox / tool_registry）。
- 新增：`uptime`、`requirements_processing`（存量）、LLM 成功率（`agent_traces` 的 `total_cost` / `duration_ms` / 失败计数）。
- **边界**：这些运营与业务指标一律走 `/api/admin/metrics`（需鉴权）。公开的 `/api/metrics` **保持原样不改** —— Prometheus 抓取端点通常是无鉴权的，往里塞用户数等于把运营数据公开。

### 5.4 前端

新增 `views/admin/AdminLoginView.vue` / `AdminInvitesView.vue` / `AdminMetricsView.vue`，路由 `/admin/login`、`/admin/invites`、`/admin/metrics`，`meta: { requiresAdmin: true }`。后台页不进主导航（避免普通用户看见入口）。

---

## 6. 数据模型变更总表

| 变更 | 类型 |
|---|---|
| `users` | 无结构变更（演示帐号就是普通用户） |
| `invite_codes` | **新表** |
| `admin_users` | **新表** |
| `requirements` / `published_sites` | 无结构变更 |

迁移走 `models.py::init_db()` 内的 `Base.metadata.create_all()`（新表自动建）；若后续要给既有表补列，必须走 `_add_column()` 并分别给 PG/SQLite 的 DDL。

---

## 7. 配置新增（`config.py` + `.env.example`）

```python
# 演示模式
DEMO_USERNAME: str = "demo"

# 邀请码
INVITE_CODE_TTL_DAYS: int = 7
INVITE_REQUEST_COOLDOWN_HOURS: int = 24
INVITE_CODE_PREFIX: str = "T2C-"

# 邮件（SMTP）
SMTP_ENABLED: bool = False
SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD / SMTP_FROM: str
SMTP_USE_TLS: bool = True

# 后台
ADMIN_TOKEN_EXPIRES_HOURS: int = 2
```

`SMTP_ENABLED=false` 是默认值 —— 保证不配置邮件也能完整跑通审批流（后台手动复制码），部署方按需开启。

---

## 8. API 清单（新增/变更）

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| POST | `/api/demo/enter` | 无 | 签发 demo JWT |
| POST | `/api/register` | 限流 | **变更**：新增必填 `invite_code` |
| GET | `/api/user/info` | JWT | **变更**：新增 `is_demo` |
| POST | `/api/invite/requests` | 限流 | 提交申请 |
| POST | `/api/admin/login` | 限流 | 返回 admin JWT（body，不进 cookie） |
| GET | `/api/admin/invites` | admin | 申请列表 |
| POST | `/api/admin/invites/<id>/approve\|reject\|resend\|revoke` | admin | 审批动作 |
| GET | `/api/admin/metrics` | admin | 运营 + 技术指标 |

---

## 9. 测试策略（TDD：先红后绿）

每个任务先写失败测试，确认红，再写实现。测试放 `backend/tests/unit/`。

| 测试文件 | 关键用例（RED 阶段必须失败） |
|---|---|
| `test_demo_guard.py` | ① demo token 发 `POST /api/requirements` → 403 `demo_readonly`；② demo token 发 `POST /api/login` → 放行；③ 普通 token 发同样的写 → 200（守卫不误伤）；④ **新增一个未在白名单的写接口时自动被拦**（用一个临时注册的 dummy 路由验证默认拒绝生效）；⑤ GET 一律放行 |
| `test_demo_login.py` | demo JWT 含 `demo=true`；`/api/user/info` 返回 `is_demo=true`；退出后失效 |
| `test_invite_code.py` | ① 无码注册 400；② 错误码 400 且文案不区分原因；③ 正确码注册成功且码变 `used`；④ **同一码并发注册两次，只有一个成功**（rowcount 防护）；⑤ 过期码 400 |
| `test_invite_request.py` | ① 24h 内重复邮箱返回幂等提示；② 限流生效；③ 缺邮箱/手机号 400 |
| `test_admin_auth.py` | ① 无 token → 401；② **user token 访问 `/api/admin/*` → 403**（越权防护）；③ admin token 访问前台写接口不受 demo 守卫影响 |
| `test_admin_metrics.py` | 指标口径：完成率 = finished/(finished+failed)；热度榜与前台 `compute_heat` 数值一致 |
| `test_manage_cli.py` | ① `demo transfer --dry-run` 不改库；② `--yes` 后 requirements/traces/published_sites 的 user_id 变更；③ **记忆表不被迁移**；④ 重复执行幂等 |

前端：`npm run build` 必须通过；`tests/unit/test_lint_js.py` 保持绿。

---

## 10. 实施计划（按 Ship，任务粒度 2-5 分钟）

### Ship 1 — 演示模式

1. `utils/demo_guard.py`：`WRITE_METHODS` / `DEMO_WRITE_ALLOWLIST` / 守卫函数（**先写测试**）
2. `factory.py` 注册 `before_request`（顺序在 `_set_rate_limit_identity` 之后）
3. `routes/auth.py` 新增 `/api/demo/enter`
4. `config.py` 加 `DEMO_USERNAME` + `.env.example`
5. 前端 `stores/auth.ts` 增加 `isDemo` + `enterDemo()`
6. `LoginForm.vue` 加演示入口按钮
7. `DemoBanner.vue` + `App.vue` 挂载
8. History / Detail / Home / Market / Settings 五处置灰与引导

### Ship 2 — 演示帐号与转移 CLI

9. `backend/manage.py` argparse 骨架 + `demo init` / `rotate-password`
10. `demo transfer`（dry-run / move / copy / 事务 / 幂等）
11. `invite create` / `admin create-user` 子命令

### Ship 3 — 邀请码

12. `models.py` 新增 `InviteCode` + `init_db()`
13. `services/invite.py`：码生成、校验、状态机
14. `routes/auth.py` 注册接口改造（原子核销）
15. `routes/invite.py` 申请接口 + 限流 + 幂等
16. `services/notify/email.py` + 失败降级
17. 前端 `RegisterForm.vue` 邀请码字段 + `InviteRequestDialog.vue`

### Ship 4 — 运营后台

18. `models.py` 新增 `AdminUser`
19. `utils/admin_guard.py` `admin_required`
20. `routes/admin.py`：登录 / 审批 / 指标
21. `services/admin_metrics.py` 指标聚合
22. 前端三个后台页 + 路由守卫

每个 Ship 结束跑 `cd backend && pytest` + `cd frontend-vue && npm run build`。

---

## 11. 风险与权衡

| 风险 | 影响 | 缓解 |
|---|---|---|
| 演示帐号密码泄露 → 任何人获得写权限 | 演示数据被改 | 20 位随机密码、可一键轮换、演示数据本身视为公开资产 |
| `before_request` 默认拒绝误伤合法写接口 | 功能不可用（**易发现**，属好的失败模式） | 白名单显式化 + 测试覆盖每个白名单项 |
| SMTP 不可用 | 邀请码发不出去 | 审批与发信解耦，后台手动复制 + 重发（§4.4） |
| 邀请码被转卖/扩散 | 准入失效 | 一次性 + 7 天有效 + 可吊销 + 码空间不可枚举 |
| 后台 token 存 sessionStorage 的 XSS | 管理员权限被盗 | 2h 有效期 + 后台页零用户内容渲染 |
| 迁移进演示帐号的站点意外公开 | 隐私泄露 | CLI dry-run 显式列出受影响的市集站点 |
| 演示模式降低注册意愿 | 转化下降 | 演示路径全程带注册引导，不做"免费替代品" |

---

## 12. 待确认的开放问题

1. **演示模式是否允许点赞等低风险互动？** 当前设计全部禁止。若希望保留"市集互动"的演示效果，可在白名单加 `POST /api/market/<slug>/like`，但需接受演示帐号的点赞数据被游客污染。
2. **邀请码是否需要配额制**（一个申请人发多个码）？当前设计一申请一码。
3. **后台是否需要操作审计日志**（谁在什么时候批准了谁）？当前 `decided_by` + `decided_at` 已覆盖基础追溯；是否需要独立审计表取决于合规要求。
4. 需求热度榜是否需要**导出 CSV**？
