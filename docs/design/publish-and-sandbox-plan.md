# 实施计划：运行时沙箱加固 + 一键发布

> 上游设计：[publish-and-sandbox.md](./publish-and-sandbox.md)
> 纪律：**RED → GREEN → REFACTOR**。测试之前写的业务代码一律删除重写。
> 每个任务 2–5 分钟。计划变了就回头改本文档，不悄悄改。

## 0. 前置：测试基线

```bash
cd backend
python -m pytest tests/unit -q
```

**先跑一遍记下红绿分布基线**。若基线本身有红，停工先搞清楚 —— 不能把新改动和既存失败混在一起。

隔离工作区：当前 `main` 上有 7 个未提交文件（上一轮记忆系统改动），
开 worktree 会把它们留在原分支。等 Leon 授权「提交或 stash」后再开隔离分支。

---

## Ship A — 运行时加固（P0，先做）

> 目标：服务端验证浏览器默认零外部出口，且 127.0.0.1 的预览链路不受影响。

### 任务 A1（RED）：断言 Chromium 启动参数
- 文件路径：`backend/tests/unit/test_sandboxed_browser.py`（新建）
- 写测试：
  ```python
  def test_chromium_args_deny_all_egress():
      args = build_chromium_args(allow_hosts=())
      assert any("MAP * ~NOTFOUND" in a for a in args)
      assert any("--proxy-server=" in a for a in args)
  ```
- 验证：跑 pytest 应**失败**（ModuleNotFoundError），这就是 RED。

### 任务 A2（GREEN）：实现参数构造器
- 文件路径：`backend/harness/tools/sandboxed_browser.py`（新建）
- 实现 `build_chromium_args(allow_hosts: tuple[str, ...]) -> list[str]`，返回：
  ```
  --host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost
  --proxy-server=http://127.0.0.1:9
  --proxy-bypass-list=127.0.0.1;localhost;<local>
  --no-first-run --no-default-browser-check
  --disable-background-networking --disable-component-update
  ```
  > 注意 resolver 里 **`EXCLUDE 127.0.0.1` 不可省**：`MAP *` 的 `*` 会匹配数字 IP，
  > 否则预览 URL（`http://127.0.0.1:5001`）被解析成 NOTFOUND → 静默假绿。
  > 这条由 A9b 集成测试守住。
- 同时加一条**反向断言**到 A1 的测试里：结果中**不得出现** `<-loopback>`。
  那个 token 是减法语义（去掉隐式 loopback 绕行），传了会把 127.0.0.1 预览请求
  一起送进黑洞代理，触发 `preview_runner` 的 iframe 失败降级分支 → 静默假绿。
- 验证：`pytest tests/unit/test_sandboxed_browser.py -q` 转绿。

### 任务 A3（RED）：端口白名单必须能放行
- 同一文件加测试：`build_chromium_args(allow_hosts=("198.18.0.1",))` 时，
  resolver rules 里应出现 `EXCLUDE 198.18.0.1`，且不出现 `NOTFOUND` 兜底覆盖它。
- 验证：先红。

### 任务 A4（GREEN）：支持 allow_hosts
- 修改 `build_chromium_args`：为空时 `MAP * ~NOTFOUND, EXCLUDE localhost`；
  非空时拼接 `, EXCLUDE <host>`。
- 验证：A1 + A3 双绿。

### 任务 A5（RED）：上下文管理器必定关闭浏览器
- 测试：构造假 launcher（记录 `close()` 调用次数），断言
  `with sandboxed_browser(launcher=fake): pass` 之后 `fake.closed == 1`；
  再断言块内抛异常时**仍然**等于 1（`try/finally`）。
- 验证：先红。

### 任务 A6（GREEN）：`sandboxed_browser` 上下文管理器
- 同文件实现：
  ```python
  @contextmanager
  def sandboxed_browser(*, launcher=None, allow_hosts=(), timeout_ms=30_000):
  ```
  `launcher` 缺省用 `playwright.sync_api.sync_playwright`。块退出必 `browser.close()`。
- 验证：`pytest tests/unit/test_sandboxed_browser.py -q` 全绿。

### 任务 A7（RED→GREEN）：wall-clock watchdog
- 测试：假 launcher 的 `new_page` 睡 200ms，`sandboxed_browser(timeout_ms=50)`
  必须在约 75ms 内被中断并抛 `BrowserWatchdogTimeout`。
- 实现：起一个 `threading.Timer` 后台线程，超时调用 `browser.close()`；
  主线程 block 在 `threading.Event`，被醒来则 raise。
- 验证：绿，且**整份测试文件耗时不超过 1 秒**（别写真睡眠测试）。

### 任务 A8：替换 4 处 launch
- 文件路径：`backend/harness/tools/preview_runner.py`
- 逐个替换（保留原有 try/except 结构，只换 bootstrap 部分）：

  | 行号 | 原代码 | 改为 |
  |---|---|---|
  | 78-80 | `with sync_playwright() as p:` / `browser = p.chromium.launch(headless=True)` | `with sandboxed_browser(timeout_ms=timeout_ms) as browser:` |
  | 367-369 | 同上 | 同上 |
  | 568-569 | 同上 | 同上 |
  | 797-799 | 同上 | 同上 |

- 注意：原代码里 `browser.new_context()` 的位置不动；800 行附近的
  `result["logs"].append(f"[smoke] chromium 不可用…")` 保留，
  但要新增捕获新的 `BrowserWatchdogTimeout`。
- 验证：`grep -n "p.chromium.launch" harness/tools/preview_runner.py` 应**零命中**。

### 任务 A9（回归，必过）：本地预览仍然可达
- **这是最容易踩坏的一步**：加了黑洞代理后，如果 loopback 没 bypass 成功，
  `/api/pt/...` 的 `http://127.0.0.1:5001` 请求会全灭，验收会整片假绿/超时。
- 验证方式（二选一，都做更好）：
  1. `pytest tests/unit/test_preview_validation.py tests/unit/test_preview_runner_selector.py -q` 全绿
  2. 实跑一个真实需求：`cd eval && python run_eval.py --task t01`（或任一小任务），
     确认 AC 检查与冒烟结果和改动前一致

### 任务 A9b（RED→GREEN）：出口封锁自检，防「静默降级」
- **为什么必须有**：`preview_runner.py` 里 iframe 加载失败时会降级成「直读文件」，
  这本意是防假绿，但副作用是——如果沙箱参数写错把 loopback 也掐了，
  链路会**静默**退化而无人发现。系统层面表现为验收「变快且变绿」，非常危险。
- 断言：`sandboxed_browser` 里 page 能成功
  `fetch(settings 拼出来的预览 base)`（复用 `utils/preview_token.make_preview_url(absolute=True)`）
  并返回 200；同时 `fetch('http://example.com')` 必须失败。
- **一个必须同时成立**：loopback 可达 且 外网不可达。只测其中一半等于没测。
- 验证：绿。

### 任务 A10（真出口封锁，集成测试）
- 路径：`backend/tests/integration/test_sandbox_egress.py`（新建）
- 测试：`sandboxed_browser` 里 `page.goto("about:blank")` 后
  `page.evaluate("fetch('http://example.com').then(r=>'ok').catch(e=>'blocked')")`
  返回 `'blocked'`；对照组（不带沙箱参数的裸 launch）返回 `'ok'` 或直接被网络环境左右
  —— **关键断言是前者必须 blocked**。
- 标记 `@pytest.mark.slow`，不进默认 CI。
- 验证：`pytest tests/integration/test_sandbox_egress.py -q -m slow` 通过。

**Ship A 完成判定**：A1–A9 全绿 + A10 在装了 Chromium 的机器上通过。

---

## Ship B — 发布链路

> 顺序即依赖顺序。`bundle` / `store` / `slug` 三个原子件先于装配。

### 任务 B1（RED）：content_hash 稳定性
- `backend/tests/unit/test_publish_bundle.py`（新建）
- 断言：同一份 `{path: bytes}` 无论插入顺序如何，hash 相同；改一个字节 hash 变；
  路径名变了 hash 变。
- 验证：先红。

### 任务 B2（GREEN）：`build_bundle`
- `backend/services/publish/bundle.py`（新建）
- `build_bundle(files: dict[str, bytes]) -> tuple[str, dict[str, bytes]]`
  hash = `sha256` of `"\0".join(sorted(f"{rel}\0{sha256(content)}"))`
- 同时做**产物门禁**：违反 ENV-2（外链）/ ENV-6（悬空引用）直接抛 `BundleError`。
  复用 `harness/constraints/environment_contract.py` 里的
  `find_cdn_references` / `check_reference_closure`，**不要再写第二份**。
- 验证：B1 转绿。

### 任务 B3（RED→GREEN）：`LocalFSStore`
- `backend/tests/unit/test_publish_store.py` + `backend/services/publish/store.py`
- 接口三个方法：`put_bundle(hash, files) -> store_key` / `get(key) -> bytes` / `exists(hash) -> bool`
- 断言：put 幂等（第二次不写）；`get` 不存在的 key 抛 `KeyError`；
  路径不能逃出 `PUBLISH_STORE_DIR`（用 `..` 攻击串测）。
- 落盘布局：`{PUBLISH_STORE_DIR}/{hash[:2]}/{hash}/{relpath}`

### 任务 B4（RED→GREEN）：slug 生成
- `backend/tests/unit/test_publish_slug.py` + `backend/services/publish/slug.py`
- `new_slug() -> str`：12 字节 `secrets.token_bytes` → Crockford Base32 → **20 字符**
- 断言：长度 20；字符集 ⊂ Crockford 字母表（无 I/L/O/U）；两次调用不同。

### 任务 B5：数据模型
- `backend/models/models.py` 末尾追加 `PublishedBundle` / `PublishedSite`
  （字段见设计文档 §4，`requirement_id` **不加外键**）
- `backend/models/__init__.py` 导出这两个类
- 验证：`python -c "from models import PublishedSite, PublishedBundle; print('ok')"`
- **警告**：项目只靠 `init_db()` 的 `create_all`，**不会给已存在的表补列**。
  字段一次定死，之后加列要自己补 ALTER。

### 任务 B6（RED）：PublishService 幂等语义
- `backend/tests/unit/test_publish_service.py`
- 三条断言：
  1. 首次 publish → 产生 bundle + site，`version == 1`
  2. **内容没变**再 publish → `store.exists` 不重复写、`version` 仍为 1、`slug` 不变
  3. 内容改了再 publish → 新 bundle、`version == 2`、`slug` **仍然不变**
- 存根用内存版 store（不碰磁盘）。
- 验证：先红。

### 任务 B7（GREEN）：`PublishService`
- `backend/services/publish/service.py`
- `publish(requirement, user_id, visibility="unlisted") -> PublishedSite`
  流程：取 `code_files` → `build_bundle` → 幂等判断 → upsert site
- 依赖注入 `store` 参数（默认 `LocalFSStore()`），便于 B6 用内存替身。
- 验证：B6 转绿。

### 任务 B8（RED→GREEN）：路由层
- `backend/routes/publish.py`（新建），注册到 `backend/routes/__init__.py`
  （先看现有注册方式，保持一致）
- `POST /api/publish`（JWT） / `POST /api/publish/<slug>/unpublish` /
  `GET /api/publish/<slug>/info`
- 断言：未登录 401；**别人的 slug 不能 unpublish**（403/404）；
  发布他人 requirement_id 返回 404（不泄漏存在性 —— 沿用
  `routes/preview.py:207` 那套「不存在就说不存在」的处理）
- 参考 `backend/tests/unit/test_security.py` 既有风格。

### 任务 B9（RED→GREEN）：Host 路由（站点本体）
- `backend/routes/published_site.py`
- 从 `Host` 头解析 `{slug}.{PUBLISH_APEX}` → 查 `published_sites` → 从 store 取文件返回
- **响应头三条**：`X-Content-Type-Options: nosniff`；
  `visibility == "unlisted"` 时加 `X-Robots-Tag: noindex`；
  **绝不加 `Content-Security-Policy: sandbox`**（我们要的就是能持久化）
- 断言：未知 slug → 404；路径穿越 `../../etc/passwd` → 403
- 配置：`backend/config.py` 新增 `PUBLISH_APEX`（默认空=关闭）、`PUBLISH_STORE_DIR`

### 任务 B10：前端
- `frontend-vue/src/components/detail/` 下加发布按钮与结果卡片
  （具体文件名先确认现有组件约定再定）
- 展示：短链 URL + 一键复制；二次发布显示「已更新 v2」
- 验证：`npm run build` 通过 + 手工点一次

### 任务 B11：本地端到端
- 无 wildcard DNS 时用 nip.io 验证：
  `http://<slug>.127.0.0.1.nip.io:5001/`
- 断言：页面正常渲染（不是空白）；**localStorage 可写**（这是预览环境做不到的地方，
  必须实测到，而不是假设）
- 这条验证是整个 Ship B 的收尾判据。

---

## Ship C — 发布后复验

> 理由：预览是 opaque origin，发布后放开 same-origin，两环境不等价。

### 任务 C1（RED）：publish 触发复验
- 断言：`PublishService.publish(..., verify=stub)` 被调用后，
  stub 收到的是**已发布 URL**（含 slug 和 apex），不是预览 URL。

### 任务 C2（GREEN）：接真实复验
- 对 `${slug}.${apex}` 跑 `run_universal_smoke` + `run_ac_checks`
  （AC 脚本缓存在 `.task/ac_scripts.json`，沿用 `nodes.py:1191-1207` 的读取逻辑）
- 结果写回 `published_sites`：`verified_at` / `verify_status`（ok / degraded）
- 失败不得阻止发布成功，但要标 `degraded` 并在前端明示

### 任务 C3：端到端确认
- 造一个**只在 same-origin 下才正常**的用例（用 localStorage 存最高分），
  断言它在预览环境 **失败**、在发布环境 **通过** —— 这条测试是把
  「为什么要做 Ship C」钉死在代码里，防止将来有人删掉复验。

---

## 收尾

- 全量：`cd backend && python -m pytest tests/unit -q` 必须**不差于** §0 记的基线
- `eval` 回归：`cd eval && python run_eval.py`（21 题），确认通过率不掉
- 未提交前不碰 git；等 Leon 明确指令再决定分支去向
