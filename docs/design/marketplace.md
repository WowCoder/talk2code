# 创意市集（Marketplace）设计说明

一句话：把「一键发布」产生的站点，在**作者显式同意**的前提下聚合到一个公开可逛的列表，
用去重 + 加权 + 时间衰减的热度排序，让陌生人能看见、能点赞，并在他想参与的那个瞬间被
引导去注册。

## 1. 目标与非目标

### 目标

1. **接上闭环**：发布之后有「被看见」的下一站，给创作者回访理由。
2. **匿名可逛**：未登录也能浏览市集、点开作品（与已发布站点免登录直开保持一致）。
3. **热度可信**：排序不能被刷新页面刷出来。
4. **轻引导**：在访客「想做点什么却做不了」的那一刻才出现注册入口，不抢戏。

### 非目标（明确不做）

- 私信、通知中心、动态信息流、积分/等级体系
- 实时刷新、WebSocket 推送
- 站点内嵌交互组件（见 §2 的 origin 边界）
- 完整举报系统（首版只做「隐藏」+ 人工处理）

## 2. 架构约束（先讲清楚为什么这样切）

### 2.1 origin 边界：互动只能落在主站

已发布站点运行在 `<slug>.<PUBLISH_APEX>`，是**独立 origin**；主站的 JWT 靠
httpOnly cookie 下发，且 `JWT_COOKIE_DOMAIN` 必须为空（cookie host-only）—— 这是
子域隔离的唯一依赖，由 `tests/unit/test_publish_host_isolation.py` 守卫，不可回退。

推论：在发布站页面里调用主站的点赞接口属于跨站带凭证请求，会被浏览器拒绝。
**因此发布站内不放置任何交互 UI，只放一个跳转回主站的 badge；点赞 / 留言 / 关注
全部发生在主站 `/market`。**

### 2.2 badge 注入：不进 content_hash，也不改落盘产物

`content_hash` 是「用户作品真实内容」的指纹，决定 `PublishedSite.version` 的递增
语义与落盘幂等。两条注入路径后果不同：

- **落盘时注入（否决）**：装饰参与 hash → 同一作品「加 / 不加 badge」算出两个 hash，
  版本语义崩塌；且 `LocalFSStore.put_bundle` 是幂等的（目录存在即零写入），作者事后
  改开关将**无法**重新装饰已存在的产物目录。
- **服务时注入（采用）**：磁盘产物与 hash 永远是用户原稿，装饰只在 HTTP 响应前拼一次。

服务时注入的额外收益：badge 开关**即时生效**（改开关不必重新发布）；并且能给复验
请求开一个豁免口（§6），让验收跑在纯净产物上。

代价是每个 `index.html` 请求多一次字符串拼接 —— 几 KB 级别，配合按
`(content_hash, badge_enabled)` 的进程内缓存可忽略。

### 2.3 上架默认不上架（opt-in）

现状所有站点的 `visibility` 均为 `unlisted`，用户的心理预期是「我只是在分享一个链接」。
升级后若默认全部搬进公共市集，是对既有预期的背叛，且产物中可能包含私人内容。

**约束：`PublishedSite.market_visible` 默认 False，只有作者显式勾选才上架。
此约束不可回退，并配回归测试。**

### 2.4 上架不设复验门槛

`verify_status` 的 `degraded` 常常是平台自身复验环境异常导致的，用它卡上架等于
让作者为平台故障背锅。且既有约定明确：复验状态是负向信号，不暴露给终端用户。
**故 `market_visible` 与 `verify_status` 完全解耦。**

## 3. 数据模型

### 3.1 新增表

```
site_visit_dedup   去重访客账本
  id, site_id(FK published_sites.id), fingerprint(VARCHAR 32), day(DATE)
  UNIQUE(site_id, fingerprint, day)      -- 唯一索引即去重实现
  INDEX(site_id)

site_likes         点赞
  id, site_id, user_id, created_at
  UNIQUE(site_id, user_id)               -- 重复点赞靠 DB 兜底，不依赖前端禁用

site_comments      留言（Ship 2）
  id, site_id, user_id, body(VARCHAR 200), created_at, is_deleted

user_follows       关注（Ship 2）
  id, follower_id, followee_id, created_at
  UNIQUE(follower_id, followee_id)
```

### 3.2 `PublishedSite` 新增列

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `market_visible` | Boolean | **False** | 是否上架市集（opt-in） |
| `market_listed_at` | DateTime | NULL | 上架时间，热度的时间基准 |
| `badge_enabled` | Boolean | True | 发布站是否注入来源 badge |
| `author_note` | String(120) | '' | 市集卡片上的一句话介绍，缺省回落到 `title` |

### 3.3 访客指纹

```
fingerprint = sha256(HASH_SALT + ip + user_agent + day)[:32]
```

- **只存 hash，不存 IP/UA 明文**（隐私与合规）。
- `day` 参与计算，使账本天然按天分桶、可定期清理 30 天前数据。
- 去重粒度是「访客-天」：同一人隔天再来算两次，这是刻意的（近似活跃度，而非永久身份）。

## 4. 热度算法

```
heat = (uniq_visitors × 1.0 + likes × 3.0 + comments × 2.0) / (hours_since_listed + 2) ^ 1.5
```

- `uniq_visitors` = `COUNT(DISTINCT fingerprint)`，**不是 `view_count`**。
  现有 `view_count` 在 Host 路由里每个 `index.html` 请求 +1，刷新即 +1，直接当热度
  会被作者自己刷穿。
- 权重：主动表达 > 被动曝光（点赞 3 > 留言 2 > 浏览 1）。
- 衰减指数取 1.5（Hacker News 用 1.8）；本平台内容量小，1.5 让新作品有更长的窗口期。
- **对外只暴露取整后的 `heat`，不暴露 `view_count`，也不暴露公式。**
  可刷的数字露出来就等同于邀请人刷。
- 热度是**查询时计算的派生量**，不落列、不做定时任务。数据量在万级以内足够；
  真到瓶颈再加 `heat_score` 列 + 后台刷新。

## 5. 后端接口

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/market/sites` | 公开 | 列表。`sort=hot\|new`、`page`、`page_size`，返回卡片字段 + `heat` + `liked`（登录时） |
| GET | `/api/market/sites/<slug>` | 公开 | 详情。仅 `market_visible=True` 可见，否则 404 |
| POST | `/api/market/sites/<slug>/like` | 登录 | 点赞。给自己作品点赞 → **400**（不静默成功） |
| DELETE | `/api/market/sites/<slug>/like` | 登录 | 取消点赞 |
| PATCH | `/api/publish/<slug>/market` | 登录 + 本人 | 上架 / 撤下 + `author_note` + `badge_enabled` |

要点：

- 列表端点挂在**主站 origin**，不享受 `is_published_site_request` 的限流豁免，需挂独立限流档位。
- 「未登录访问需登录的接口」统一返回 401，前端据此处置引导；不做「请先注册」这类负向文案。
- 与既有约定一致：未命中不返回 404 噪音——详情页对未上架站点仍返回 404（这是资源语义，
  不是首屏必然触发的轮询），列表不返回任何不存在资源的信息。

### 5.1 访客去重落点

去重**不通过前端上报**（发布站无法跨 origin 调主站 API），而是在
`routes/published_site.py` 的 Host 路由里，与现有 `view_count` 自增同一个事务中完成：
页面级请求 → 计算指纹 → 插入 `site_visit_dedup` → 唯一键冲突则忽略。
`view_count` 语义保持不变（发布面板仍在用）。

## 6. badge 注入方案

新增 `services/publish/decorate.py`：

```
render_badge(html: bytes, *, market_url: str) -> bytes
```

- 在 `routes/published_site.py` 返回入口 HTML 之前调用；落盘产物不受影响（§2.2）。
- 形态：右下角小胶囊，`position: fixed`、`z-index: 2147483000`、默认 `opacity: .72`、
  hover 变实；文案「用 Talk2Code 做的 · 逛逛市集 →」；`pointer-events` 仅限自身，
  不铺满视口、不拦截页面点击。
- 体积 ~1.5 KB：内联 CSS 与文案，**不引任何外部资源**（无 CDN、无外链 JS）。
- **兜底**：找不到 `</body>`（含大小写与属性变体）或解析异常 → 原样返回，
  **绝不因装饰导致站点打不开**。
- 开关：`settings.PUBLISH_BADGE_ENABLED`（全局，默认 True）× `site.badge_enabled`
  （作者，默认 True），两者都开才注入。
- 缓存：模块级 dict，key = `(content_hash, badge_enabled)`，上限 200 条，超限清空。
- ⚠️ badge 指向主站的外链是 `ENV-2`（禁外链）门禁的**唯一例外**：门禁作用于
  `build_bundle()` 阶段的产物，装饰发生在服务阶段、不参与契约检查。必须写进注释。
- ⚠️ **复验豁免**：发布后复验以内部头 `X-T2C-Verify: 1` 请求站点，Host 路由识别后
  跳过装饰。理由有二：① 复验要验的是**用户作品本身**，平台装饰不在验收范围；
  ② badge 浮在右下角，可能遮挡 AC 脚本要点击的元素，造成假红。

## 7. 前端

- 新增路由 `/market`，`meta.requiresAuth = false`（首个公开路由）。
- 新增 `views/MarketView.vue` 与 `components/market/`：`SiteCard.vue`、`HeatBadge.vue`、
  `AuthNudge.vue`（内联注册/登录小表单）。
- 卡片缩略图：Ship 1 用 **slug 派生的稳定色块**（同一作品颜色恒定），不接真截图。
  真截图需 Chromium + 异步任务 + 失败重试，会把首版周期翻倍，放到 Ship 3。
- 发布面板新增「同步到市集」勾选项（默认不勾）+ 一句话介绍输入 + badge 开关。

### 7.1 注册引导的时机与话术

- 游客可自由浏览、点开作品；点赞按钮**可点**。
- 用 localStorage 计 `market_guest_actions`，**第 3 次**互动才出现 `AuthNudge`：
  第一次就弹是打扰；到第三次说明他真的想参与。登录成功后计数清空。
- `AuthNudge` 内联两框注册表单，**原地完成、原地重试刚才那个动作**，不跳页、不丢上下文。
- 话术一律正向：**「登录就能点赞」**，不做「未登录无法操作」这类负向提示。

## 8. 安全与滥用

1. 留言：敏感词过滤 + ≤200 字 + 独立限流档位 + **不允许匿名留言**（匿名区没有可追责主体）。
2. 点赞：唯一索引兜底，前端禁用只是体验优化；给自己的作品点赞返回 400。
3. 指纹：加盐、只存 hash、按天分桶、定期清理。
4. **上架站点保留 `X-Robots-Tag: noindex`**：市集内容被搜索引擎索引会放大合规风险，
   `unlisted` 的语义不做变更。
5. 列表端点是公开的，必须挂限流；不得误用发布站豁免逻辑。

## 9. 分期

| Ship | 内容 |
|---|---|
| **Ship 1** | 数据模型 + 热度算法 + 公开列表 + 点赞 + badge 注入 + 注册引导气泡 |
| **Ship 2** | 留言 + 关注/粉丝 |
| **Ship 3** | 标签筛选、作者主页、周榜、真截图缩略图 |

## 10. 测试清单（Ship 1 必过）

- `market_visible` 默认 False；未上架站点**不出现**在 `/api/market/sites`（不可回退约束）。
- 未登录可访问列表与详情；点赞返回 401。
- 同一用户对同一站点重复点赞只记一次。
- 给自己作品点赞返回 400，而非静默成功。
- 同一访客同日多次请求只产生一行去重记录；换 UA/换日各记一行。
- 热度排序：点赞权重高于浏览；时间衰减生效（新作品可在同等信号下排到前面）。
- 响应体中**不含** `view_count`、`verify_status`（沿用既有 UI 暴露边界约定）。
- 落盘产物与 `content_hash` 不含 badge；服务响应才带 badge。
- badge 关闭时不注入；带 `X-T2C-Verify: 1` 的请求不注入。
- 入口 HTML 找不到 `</body>` 时原样返回，站点仍 200（不因装饰失败）。
- 上架站点仍带 `X-Robots-Tag: noindex`。
- 热度响应体不含 `view_count`、`verify_status`（沿用既有 UI 暴露边界约定）。
