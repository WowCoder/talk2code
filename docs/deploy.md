# Talk2Code 公网部署手册

面向 Ubuntu 22.04 LTS 服务器的生产部署操作文档。全文自包含：所有配置文件正文都写在文档里，  
不依赖仓库外的任何脚本。

> **路线**：Nginx（80/443，含泛域名）→ gunicorn(gthread) → Flask(:5001) → PostgreSQL+pgvector(:5434) / Redis(:6380)

---

## 0. 结论摘要

部署前的安全审查结论：**应用代码本身的加固质量好，风险集中在「部署材料」而不是「代码逻辑」。**

| 类别                      | 结论                                                                  |
| ----------------------- | ------------------------------------------------------------------- |
| 代码层（沙箱、鉴权、注入、路径穿越、SSRF） | ✅ 已核实到位，见 §1.3                                                      |
| 部署材料                    | 🔴 **缺失**：`deploy/` 整个目录被 `.gitignore` 忽略，从 GitHub clone 后拿不到任何部署脚本 |
| 依赖                      | 🔴 `requirements.txt` 不含 gunicorn，生产启动会失败或静默降级到开发服务器                |
| 中间件暴露                   | 🔴 数据库/缓存端口绑 `0.0.0.0` 直连公网，Redis 无密码、PG 弱口令                        |
| 限流                      | 🟠 可被伪造 `X-Forwarded-For` 绕过                                        |
| 进程托管                    | 🟠 用 `nohup` 启动，服务器重启后服务不恢复                                         |

**必须先做完 §1 的 P0/P1 修复再上线。** §2 的操作步骤已经内嵌了这些修复，照抄即可。

---

## 1. 部署前安全审查

### 1.1 风险清单

| #  | 级别     | 问题                                                      | 位置                                                                  |
| -- | ------ | ------------------------------------------------------- | ------------------------------------------------------------------- |
| 1  | **P0** | Redis / PostgreSQL 端口映射到公网；Redis 无密码，PG 口令为 `talk2code` | `docker-compose.yml:12-18`, `:31-35`                                |
| 2  | **P0** | `requirements.txt` 无 gunicorn → 生产起不来，或降级到 Flask 开发服务器  | `backend/requirements.txt`, `start.sh:90-96`, `deploy/deploy.sh:57` |
| 3  | **P1** | 限流取 XFF **最左段**（攻击者可控），可无限刷登录/生成接口                      | `backend/utils/rate_limiter.py:28-30` + `deploy/nginx.conf:25`      |
| 4  | **P1** | `deploy/` 被 gitignore，部署材料不在仓库里                         | `.gitignore:8`                                                      |
| 5  | **P1** | 预览沙箱放行 loopback，生成的页面可探测服务器本机其他服务                       | `backend/harness/tools/sandboxed_browser.py:56-63`                  |
| 6  | **P2** | 无全局安全响应头（无 HSTS / X-Frame-Options / CSP），可被 iframe 点击劫持 | `backend/factory.py:42-53`                                          |
| 7  | **P2** | JWT 前台有效期 24h，且改密/登出后旧 token 不失效                        | `backend/config.py:115-123`                                         |
| 8  | **P2** | `/api/market/users/<id>` 免登录返回用户名，可枚举用户名辅助撞库            | `backend/routes/market.py:469-500`                                  |
| 9  | **P2** | ✅ 已锁定：新增 `backend/requirements.lock`（用 Python 3.10 环境生成，钉到**本机已验证**的版本） | `backend/requirements.lock`                                          |
| 10 | **P2** | `DATABASE_URL` 为空时静默回退 SQLite，多 worker 下写锁竞争            | `backend/config.py:38-57`                                           |
| 11 | 🟡     | `.env` 明文存 LLM API Key（已被 gitignore，未入库），需控权限与分发范围      | `backend/.env:49`                                                   |

### 1.2 逐条修复

#### 【P0-1】中间件端口只监听回环 + 加密码

改 `docker-compose.yml`：

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    restart: unless-stopped
    ports:
      - "127.0.0.1:5434:5432"        # 关键：绑回环，否则公网直连
    environment:
      POSTGRES_USER: talk2code
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: talk2code
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports:
      - "127.0.0.1:6380:6379"        # 同上
    command: >
      sh -c "redis-server --appendonly yes
      --requirepass \"$$REDIS_PASSWORD\"
      --maxmemory 256mb --maxmemory-policy allkeys-lru"
    volumes:
      - redisdata:/data
```

在 `docker-compose.yml` **同目录**建 `.env`（compose 会自动读取，且已被仓库 gitignore）：

```bash
umask 077
cat > /opt/talk2code/.env <<EOF
POSTGRES_PASSWORD=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
REDIS_PASSWORD=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
EOF
chmod 600 /opt/talk2code/.env
```

对应地，`backend/.env` 的 `DATABASE_URL` / `REDIS_URL` 要用同一套口令：  
`REDIS_URL=redis://:<REDIS_PASSWORD>@127.0.0.1:6380/0`。

> 只有首次 `up` 时 `POSTGRES_PASSWORD` 才会写进数据卷；之后改 `.env` 不会生效，  
> 需要进容器 `ALTER USER` 或重建卷。

#### 【P0-2】补 gunicorn 依赖，禁止静默降级

`backend/requirements.txt` 末尾加一行：

```
gunicorn>=21.2.0
```

`start.sh` 的回退分支改成硬失败，不留"降级到开发服务器"的口子：

```bash
    if command -v gunicorn &>/dev/null; then
        cd "$BACKEND_DIR"
        gunicorn -w 2 -k gthread --threads 8 --timeout 120 --bind 127.0.0.1:"$PORT" app:app
    else
        err "gunicorn 未安装（pip install gunicorn），拒绝以开发服务器对外服务"
        exit 1
    fi
```

#### 【P1-3】限流取真实客户端 IP

`backend/utils/rate_limiter.py:28-30` 现在取的是 XFF 最左段，而 nginx 用  
`$proxy_add_x_forwarded_for` 把真实 IP **追加到末尾** —— 两者叠加后，限流键完全由请求方控制。

最省事且可靠的修法：让 nginx **覆盖**而不是追加。把 `deploy/nginx.conf:25` 改成：

```nginx
proxy_set_header X-Forwarded-For $remote_addr;
```

这样 Flask 侧取到的一定是 nginx 看到的对端 IP，攻击者无法伪造。

#### 【P1-4】部署材料纳入版本控制

`.gitignore:8` 的 `deploy/` 会连同 nginx 配置、systemd unit、`.env.prod.example` 一起忽略掉。  
二选一：

- 保留 `deploy/` 被忽略（当前行为）：本文 §2 已经把所有配置正文写全，按文档手工落地即可；
- 或 `git add -f deploy/` 放行部署目录。

#### 【P1-5】预览沙箱的 loopback 放行面

`build_chromium_args()` 为了保住 `127.0.0.1` 预览链路，把 `127.0.0.1;localhost;<local>` 写进了  
`--proxy-bypass-list`。外网出口已经被 `--host-resolver-rules` + 黑洞代理封死，**但本机端口没有封**。  
在公网服务器上，这意味着用户生成的页面可以发起对 `127.0.0.1:5001 / 5434 / 6380` 的请求。

缓解措施（部署侧即可完成，无需改代码）：

1. PostgreSQL / Redis 只监听回环（见 P0-1），且 **必须设密码** —— 这是主要防线；
2. 用防火墙禁止容器/应用进程对外发起非必要连接；
3. 预览 iframe 跑在 `CSP: sandbox` 不透明源里，不带主站 cookie，读不到响应体 —— 风险等级因此从  
   「可窃取数据」降为「可做端口探测」。

#### 【P2 各项】统一加安全响应头

在 `backend/factory.py` 的 `CORS(...)` 之后追加：

```python
@app.after_request
def _security_headers(resp):
    resp.headers.setdefault('X-Frame-Options', 'DENY')
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    resp.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    if request.is_secure:
        resp.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return resp
```

### 1.2.1 其它 P2 的处理建议

- **#7 JWT 24h**：公网建议缩到 8h 以下，并引入 `jti` + 吊销表；当前改密/登出后旧 token 仍有效。
- **#8 用户名枚举**：`/api/market/users/<id>` 改为只返回聚合计数，或加 `@jwt_required()`。
- **#9 依赖锁定**：✅ 已做。`backend/requirements.lock` 由 `pip-compile` 在 **Python 3.10 容器**中生成
  （服务器版本 3.10.12），并用**本机已验证的版本**做约束。
  **为什么不直接解析「最新」**：`requirements.txt` 全是 `>=`，裸解析会装到 `sentence-transformers 6.1.0`
  / `transformers 5.19.0` / `numpy 2.2.6`，而本机跑通 1267 个测试的组合是 `2.7.0` / `4.57.6` / `1.26.4`
  —— 跨了 4 个与 1 个大版本，BGE-M3 检索链路有真实破坏风险。锁文件的基准必须是「验证过的环境」。
  `torch` 用 **CPU 版**（`2.13.0+cpu`，来自 `--extra-index-url https://download.pytorch.org/whl/cpu`）：
  PyPI 的 Linux `torch` 会拖 15 个 `nvidia-*` 加 `triton`（约 2–3 GB），部署机无 GPU 用不上。
  该 index 指令已写进锁文件，`pip install -r requirements.lock` 会自动读取。
- **#10 SQLite 回退**：把「`APP_DEBUG=false` + `DATABASE_URL` 为空」加入  
  `factory.py::check_production_security()` 的 `fatal_issues`。

### 1.3 已核实到位（无需处理）

这些是审查中逐条验证过的加固点，供放心：

| 面             | 证据                                                                                                                                                                                                                |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **沙箱外网出口**    | `sandboxed_browser.py:65-68` 双重封禁：`--host-resolver-rules=MAP * ~NOTFOUND` + 黑洞代理 `--proxy-server=http://127.0.0.1:9`；`preview_runner.py` 5 处 launch 全走该入口，无裸 `chromium.launch`，无 `--allow-file-access-from-files` |
| **预览 XSS**    | `routes/preview.py:223` 返回 `CSP: sandbox allow-scripts allow-forms`（不透明源，取不到主站 cookie）；前端 `PreviewFrame.vue` 同步带 `sandbox` 属性                                                                                     |
| **路径穿越**      | `harness/state/workspace.py:39-56` 分 `..` 段判断 + `is_relative_to` 双校验；`services/publish/store.py:27-36` 同样 resolve + 前缀校验；`published_site.py` 再挡一次 `is_internal_path`                                              |
| **越权 / IDOR** | 40+ 个 `/api/requirements/*` 均带 `@jwt_required()` 且查询内联 `Requirement.user_id == current_user_id`；`/api/pt/` 用 HMAC 能力令牌，不存在 token 走 query param                                                                    |
| **SSE 鉴权**    | `routes/requirements.py:998-1010` 有 `@jwt_required()` + 需求归属校验（仅限流被 exempt，鉴权完整）                                                                                                                                  |
| **SSRF**      | 无任何接口接受用户 URL 后由服务端请求（`web_tools.py` 为静态字典）                                                                                                                                                                       |
| **注入**        | 业务查询全部 ORM 化；`models.py:563,577`、`memory_retriever.py:516` 的 f-string SQL 插值均为代码内常量/内部 int ID                                                                                                                     |
| **调试模式**      | `app.py:21` 与 `factory.py:303` 均硬编码 `debug=False`                                                                                                                                                                 |
| **发布站隔离**     | `factory.py:65-69` cookie 为 host-only，未设 Domain，发布子域拿不到主站登录态                                                                                                                                                      |
| **命令执行**      | 全仓无 `subprocess` / `os.system` / `eval` / `exec`                                                                                                                                                                  |

---

## 2. 部署操作步骤

### 前置条件

- Ubuntu 22.04 LTS，≥ 2C4G（Playwright + BGE-M3 常驻约 2-3GB 内存）
- 域名已完成 ICP 备案（大陆节点）并解析到本机
- 安全组/防火墙仅放行 80、443

### 步骤 1：系统依赖

```bash
sudo apt-get update -y
sudo apt-get install -y software-properties-common curl ca-certificates gnupg lsof

# Ubuntu 22.04 官方源自带 Python 3.10.12，无需 deadsnakes PPA
sudo apt-get install -y python3.10 python3.10-venv python3.10-dev python3-pip nginx git

# Node.js 20（前端构建）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo bash -
sudo apt-get install -y nodejs

# Playwright Chromium 运行所需系统库
sudo apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0

# Docker（仅用于跑 PostgreSQL / Redis）
# ⚠️ Ubuntu 官方源的包名是 docker-compose-v2；docker-compose-plugin 是 Docker
#    官方 apt 仓库的包名、在官方源里不存在 —— 混写会让整条命令因
#    "Unable to locate package" 失败，连 docker.io 都装不上。
sudo apt-get install -y docker.io docker-compose-v2
sudo usermod -aG docker "$USER"   # 重新登录后生效

# 腾讯云机器建议配内网镜像加速（拉 pgvector / redis 镜像用）
sudo mkdir -p /etc/docker
echo '{"registry-mirrors":["https://mirror.ccs.tencentyun.com"]}' | sudo tee /etc/docker/daemon.json
sudo systemctl restart docker
```

### 步骤 2：获取代码

**先判断服务器能否直连 GitHub。** 大陆节点常见 TLS 干扰，实测报
`GnuTLS recv error (-110): The TLS connection was non-properly terminated.`，
直连、`gitclone.com`（504）、`ghfast.top` 等镜像均不可用。最可靠的是从**开发机**打包上传：

```bash
# ① 在开发机（能访问 GitHub）
git bundle create /tmp/talk2code.bundle main
scp /tmp/talk2code.bundle root@<SERVER_IP>:/root/

# ② 在服务器
rm -rf /opt/talk2code
git clone -q -b main /root/talk2code.bundle /opt/talk2code
git -C /opt/talk2code remote set-url origin https://github.com/WowCoder/talk2code.git
```

若服务器能直连 GitHub，可简化为：

```bash
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/WowCoder/talk2code.git talk2code
```

> 后续更新同理：本地 `git bundle create /tmp/inc.bundle <新提交范围>` → `scp` →
> 服务器 `git pull /root/inc.bundle main`。


### 步骤 3：建运行用户

```bash
sudo useradd -r -m -s /bin/bash talk2code || true
sudo chown -R talk2code:talk2code /opt/talk2code
```

> 后续所有 `pip install` / `npm run build` / `playwright install` 都要以 `talk2code` 用户执行，  
> 否则浏览器缓存和产物目录会落到 root 名下，服务起来后读不到。

### 步骤 4：后端环境

**先把 pip / npm 源换成国内镜像**（实测默认源下载速率低至 24–36 KB/s）：

```bash
# pip：清华源（腾讯云内网源 mirrors.cloud.tencent.com 实测回源很慢）
sudo -u talk2code -H mkdir -p /home/talk2code/.pip
printf '[global]\nindex-url = https://pypi.tuna.tsinghua.edu.cn/simple\ntrusted-host = pypi.tuna.tsinghua.edu.cn\ntimeout = 120\n' \
  | sudo tee /home/talk2code/.pip/pip.conf >/dev/null
sudo chown -R talk2code:talk2code /home/talk2code/.pip

sudo -u talk2code -H npm config set registry https://registry.npmmirror.com
```

装依赖时 **torch 必须单独装**：锁文件里 `torch==2.13.0+cpu` 只存在于
`download.pytorch.org`，pip 会把它当成一个 index 去解析，并连带从该 index 拉
numpy / networkx 等依赖 —— 实测这些包会掉到 30 KB/s 并卡死。拆成三步即可：

```bash
sudo -u talk2code -H bash -c '
cd /opt/talk2code/backend
python3.10 -m venv venv
source venv/bin/activate
pip install --upgrade pip

# ① torch：curl 直下 wheel（实测 4.8 MB/s），不让 pip 参与解析
curl -o /tmp/torch.whl "https://download.pytorch.org/whl/cpu/torch-2.13.0%2Bcpu-cp310-cp310-manylinux_2_28_x86_64.whl"
pip install --no-deps /tmp/torch.whl

# ② 其余依赖走清华源（剔除锁文件里的 pytorch index 与 torch 行）
grep -vE "^--extra-index-url|^torch==" requirements.lock > /tmp/req-rest.txt
pip install -r /tmp/req-rest.txt

# ③ 浏览器内核（走 npmmirror；playwright 官方 CDN 在大陆拉不动）
# ★ pip 装了 playwright ≠ 装了内核。缺内核时服务端所有 chromium launch 直接失败，
#   AC 逐条验收 / 冒烟 / 截图会**静默**回退「直读工作区文件」——冒烟甚至打印
#   available=True 的假绿（它是读本地文件判的），验收链路形同虚设。
#   内核落到 /opt/talk2code/.pw-browsers（不依赖 HOME），并在步骤 8 的
#   两个 systemd unit 里写 PLAYWRIGHT_BROWSERS_PATH 指向它。
export PLAYWRIGHT_BROWSERS_PATH=/opt/talk2code/.pw-browsers
PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright playwright install chromium
'

# ★ 光"装完"不算数：`playwright install` 只下载、不校验能否启动。
#   真拉起一次，不过就别往下走（宁可部署失败，也别带着"验收永远假绿"上线）。
sudo -u talk2code -H env PLAYWRIGHT_BROWSERS_PATH=/opt/talk2code/.pw-browsers \
  /opt/talk2code/backend/venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    print("chromium 启动正常:", b.version)
    b.close()
PY
```

> 校验：`backend/venv/bin/python -c "import torch;print(torch.__version__)"` → `2.13.0+cpu`

预下载 BGE-M3 模型（约 2.3GB，避免线上首次检索卡 1–2 分钟）。

**必须带 HF 镜像，并装 `hf_transfer`**：不加镜像在国内下不动；不加 `hf_transfer`
实测只有约 100 KB/s（2.3GB 要下数小时），加上后实测 8.7 MB/s。

```bash
sudo -u talk2code -H bash -c '
cd /opt/talk2code/backend && source venv/bin/activate
pip install hf_transfer
HF_ENDPOINT=https://hf-mirror.com HF_HUB_ENABLE_HF_TRANSFER=1 \
  python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer(\"BAAI/bge-m3\", device=\"cpu\")"
'
```

> 服务运行期同样需要 `HF_ENDPOINT`（缓存失效时会在线补下），已在 systemd unit 中
> 写入 `Environment="HF_ENDPOINT=https://hf-mirror.com"`（见步骤 8）。

### 步骤 5：前端构建

```bash
sudo -u talk2code -H bash -c '
cd /opt/talk2code/frontend-vue
npm ci --silent        # 有 package-lock.json 时用 ci：可复现、更快
npm run build
'
```

产物落在 `frontend-vue/dist`，由 Nginx 直接托管。前端所有请求走同源 `/api`（`useApi.ts:17`  
用相对路径 + `credentials: 'include'`），**不需要**配置任何 `VITE_API_BASE`。

### 步骤 6：数据库与缓存

先按 P0-1 改好 `docker-compose.yml`，然后：

```bash
cd /opt/talk2code
# 写 /opt/talk2code/.env（POSTGRES_PASSWORD / REDIS_PASSWORD，见 P0-1）
sudo docker compose up -d
sleep 5
sudo docker compose ps        # 两个都应是 healthy
```

**不要** 把 `DATABASE_URL` 留空 —— 那样会静默回退 SQLite，`gunicorn -w 2` 多进程下写锁竞争会导致  
任务状态错乱（P2-10）。

### 步骤 7：生产环境配置

```bash
sudo -u talk2code -H bash -c 'cp /opt/talk2code/deploy/.env.prod.example /opt/talk2code/backend/.env'
```

然后逐项确认 `backend/.env`（下列为**必须改**的项）：

| 变量               | 要求                                                                                                                          |
| ---------------- | --------------------------------------------------------------------------------------------------------------------------- |
| `JWT_SECRET_KEY` | 必须换成随机值：`python3 -c "import secrets;print(secrets.token_hex(32))"`。留默认值应用会**拒绝启动**（`factory.py:156`），别用 `APP_DEBUG=true` 绕过 |
| `LLM_API_KEY`    | 真实 Key。为空时非调试模式直接拒绝启动                                                                                                       |
| `DATABASE_URL`   | `postgresql+psycopg://talk2code:<POSTGRES_PASSWORD>@127.0.0.1:5434/talk2code`                                               |
| `REDIS_URL`      | `redis://:<REDIS_PASSWORD>@127.0.0.1:6380/0`                                                                                |
| `CORS_ORIGINS`   | `https://<你的域名>`，**不要留 `*`**（`supports_credentials=True` 时会抛错）                                                              |
| `PUBLISH_APEX`   | 你的 apex 域名。留空则「一键发布」拿不到可访问链接                                                                                                |
| `APP_DEBUG`      | 必须 `false`。置 true 会同时关掉 Secure Cookie、CORS 严格性与默认密钥阻断                                                                       |
| `APP_PORT`       | 保持 `5001`                                                                                                                   |

```bash
sudo chmod 600 /opt/talk2code/backend/.env
```

> 数据库表会在应用首次启动时由 `factory.py:133 → models.init_db()` 自动创建，  
> pgvector 扩展与 HNSW 索引也在同一函数里建，无需手工迁移。

### 步骤 8：进程托管（systemd）

`deploy/deploy.sh` 目前用 `nohup` 拉起 gunicorn/celery，服务器重启后不会自恢复。改为：

**`/etc/systemd/system/talk2code.service`**

```ini
[Unit]
Description=Talk2Code backend (gunicorn)
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=talk2code
Group=talk2code
WorkingDirectory=/opt/talk2code/backend
Environment="PYTHONPATH=/opt/talk2code/backend"
# 模型缓存失效时会在线补下，国内必须指向镜像
Environment="HF_ENDPOINT=https://hf-mirror.com"
# ★ Playwright 内核路径，必须与步骤 4 的安装路径一致，且两个 unit 都要写。
#   漏写 → 服务进程找不到内核 → 验收链路静默降级（见步骤 4 与「验收链路自检」）
Environment="PLAYWRIGHT_BROWSERS_PATH=/opt/talk2code/.pw-browsers"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/talk2code/backend/venv/bin/gunicorn \
  -w 2 -k gthread --threads 8 \
  --timeout 120 --graceful-timeout 30 --keep-alive 5 \
  --access-logfile /opt/talk2code/backend/logs/gunicorn-access.log \
  --error-logfile /opt/talk2code/backend/logs/gunicorn-error.log \
  --bind 127.0.0.1:5001 app:app
Restart=always
RestartSec=5
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/talk2code-celery.service`**

```ini
[Unit]
Description=Talk2Code celery worker
After=network.target talk2code.service

[Service]
Type=simple
User=talk2code
Group=talk2code
WorkingDirectory=/opt/talk2code/backend
Environment="PYTHONPATH=/opt/talk2code/backend"
ExecStart=/opt/talk2code/backend/venv/bin/celery \
  -A celery_app.celery_app worker --loglevel=info --concurrency=1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

启用：

```bash
sudo mkdir -p /opt/talk2code/backend/logs
sudo chown talk2code:talk2code /opt/talk2code/backend/logs
sudo systemctl daemon-reload
sudo systemctl enable --now talk2code talk2code-celery
sudo systemctl status talk2code talk2code-celery --no-pager
```

> 若不使用 Celery，在 `backend/.env` 设 `CELERY_ENABLED=false`，只启用 `talk2code.service` 即可。  
> 注意 `--bind 127.0.0.1:5001`：只让 Nginx 反代访问，不要暴露 5001 到公网。

### 步骤 9：Nginx

主站配置 `/etc/nginx/sites-available/talk2code`（替换 `<DOMAIN>` / `<APEX>` 为实际域名）：

```nginx
# ---------- 通用安全响应头 ----------
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;

server {
    listen 80;
    server_name <DOMAIN>;

    client_max_body_size 50m;

    # 前端静态产物
    location / {
        root /opt/talk2code/frontend-vue/dist;
        try_files $uri $uri/ /index.html;
    }

    # 带 hash 的构建产物可长缓存
    location /assets/ {
        root /opt/talk2code/frontend-vue/dist;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    # 后端 API + SSE 长连接
    # proxy_buffering off 是 SSE 的硬要求，否则流式输出被攒住
    location /api/ {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        # 覆盖而非追加：配合 TRUST_PROXY_HEADERS=true 得到不可伪造的客户端 IP
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";

        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        chunked_transfer_encoding on;
    }
}
```

**已发布站点（一键发布功能依赖此项）** `/etc/nginx/sites-available/talk2code-publish`：

```nginx
# 前置条件：
#   1) DNS 加 *. <APEX> 泛解析指向本机
#   2) 泛域名证书（云厂商免费证书通常不含通配符，用 acme.sh 走 DNS 验证申请）
server {
    listen 80;
    listen 443 ssl;
    server_name ~^(?<slug>[a-z0-9]+)\.<APEX_ESCAPED>$;

    ssl_certificate     /etc/letsencrypt/live/<APEX>/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/<APEX>/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5001;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
    }
}
```

> `<APEX_ESCAPED>` 是把域名点号转义后的形式，例如 `example\.com`。  
> 不配这一块，「一键发布」生成的链接将无法访问。

启用并校验：

```bash
sudo ln -sf /etc/nginx/sites-available/talk2code /etc/nginx/sites-enabled/talk2code
sudo ln -sf /etc/nginx/sites-available/talk2code-publish /etc/nginx/sites-enabled/talk2code-publish
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

### 步骤 10：SSL 证书

```bash
sudo apt-get install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <DOMAIN> -d www.<DOMAIN> \
  --non-interactive --agree-tos -m admin@<DOMAIN> --redirect
```

> ⚠️ **`server_name` 必须同时包含 www**（步骤 9 主站块已配）。certbot 申请多域名证书时，
> 只要有一个域名在 nginx 配置里找不到匹配的 `server_name`，它会判定安装失败并
> **回滚整个 443 部署** —— 证书签下来了，但 nginx 配置一点没改，表现就是「证书有了、
> HTTPS 却打不开」。修法：补全 `server_name` 后重跑同一条命令
> （`--keep-until-expiring` 会复用已签发的证书，不会重复申请）。

泛域名证书（发布站需要，走 DNS 验证）：

```bash
sudo certbot certonly --manual --preferred-challenges dns \
  -d "*.<APEX>" -d "<APEX>"
# 按提示添加 _acme-challenge TXT 记录，等待生效后回车
```

自动续期检查：

```bash
sudo systemctl status certbot.timer --no-pager
sudo certbot renew --dry-run
```

### 步骤 11：初始化账号

```bash
cd /opt/talk2code/backend && source venv/bin/activate

# 后台管理员
python manage.py admin create-user --username ops

# 邀请码（注册强制要求，见 routes/auth.py 与 services/invite.py）
python manage.py invite create --count 20

# 演示账号（可选；演示模式写操作默认被 utils/demo_guard.py 拦截）
python manage.py demo init
```

---

## 3. 验收清单

```bash
# 1) 健康检查（应返回 200）
curl -s -m 5 http://127.0.0.1:5001/api/health

# 2) 就绪探针（含数据库/缓存连通性）
curl -s -m 5 http://127.0.0.1:5001/api/health/ready

# 3) 公网 HTTPS
curl -sI https://<DOMAIN>/ | head -1          # 期望 200
curl -sI http://<DOMAIN>/ | head -1           # 期望 301 → https

# 4) 安全头
curl -sI https://<DOMAIN>/ | grep -iE "x-frame-options|referrer-policy|strict-transport"

# 5) 中间件不暴露公网（应连接失败/超时，不是 PONG）
nc -zv <公网IP> 6380 ; nc -zv <公网IP> 5434
```

浏览器侧：

1. 用邀请码注册 → 登录成功
2. 提交一条需求 → 详情页有 SSE 流式进度，代码区出文件
3. 预览标签页能渲染（说明 Playwright 沙箱正常）
4. 「一键发布」→ 拿到 `https://<slug>.<APEX>` 链接且可访问
5. 刷新详情页 → 进度条与文案能恢复（进度快照生效）
6. 后台 `/admin` → 轨迹列表、评测列表有数据

### 验收链路自检（必做）

平台的核心承诺是「AI 写的代码真的能跑」，而这条链路有**两处不会报错、只会静默降级**
的失效方式 —— 不查就会带着「验收永远假绿」上线（实测踩过，见下）。逐条核对：

```bash
# ① Playwright 内核在不在 + 能不能真启动（缺内核 → 验收全走"直读文件"）
sudo -u talk2code env PLAYWRIGHT_BROWSERS_PATH=/opt/talk2code/.pw-browsers \
  /opt/talk2code/backend/venv/bin/python -c \
  "from playwright.sync_api import sync_playwright as S; p=S().start(); b=p.chromium.launch(headless=True); print('ok', b.version); b.close()"

# ② 预览载荷不能带 X-Frame-Options（带了 → file:// 宿主页的 iframe 根本不导航）
#    期望：只有 Content-Security-Policy，没有 X-Frame-Options
URL=$(cd /opt/talk2code/backend && sudo -u talk2code env PYTHONPATH=. \
      venv/bin/python -c "from utils.preview_token import make_preview_url as m; print(m(1,1,'index.html',absolute=True))")
curl -sI "$URL" | grep -iE "x-frame-options|content-security-policy"
#    对照：主站必须仍然是 SAMEORIGIN
curl -sI http://127.0.0.1:5001/api/health | grep -i x-frame-options

# ③ 跑一条真实需求后，日志里不该有降级标记
journalctl -u talk2code-celery -n 300 | grep -c "沙箱预览链路不可用"   # 期望 0
```

②的原因：服务端验收浏览器的宿主页是 `file://` 临时文件（见
`preview_runner._make_sandbox_wrapper_uri`），与预览地址**跨源**。预览响应一旦带上
`X-Frame-Options: SAMEORIGIN`，iframe 直接不导航 → `frame.url` 为空 → 命中
`_frame_load_failure()` 的「页面未导航 (url=空)」→ AC 逐条验收 / 冒烟 / 截图**全部**
回退直读工作区文件，而冒烟还会输出 `available=True, page_loads=True`（它是读本地
文件判的）。预览载荷真正的隔离来自 `CSP: sandbox allow-scripts allow-forms`
（唯一 opaque origin，读不到 cookie / localStorage、够不到父页），故它必须豁免 XFO；
主站与 `<slug>.<APEX>` 已发布站点照常 `SAMEORIGIN`。守卫：
`backend/tests/unit/test_security_headers.py`。

> 另需确认 `PREVIEW_PUBLIC_BASE_URL` **留空**（同源反代部署的正确取值）：它只被
> 服务端验收浏览器消费，填上公网域名等于让验证浏览器绕自己的公网地址一圈。

---

## 4. 日常运维

```bash
# 重启（改后端代码后必须；debug=False 无 autoreload）
sudo systemctl restart talk2code talk2code-celery

# 日志
sudo journalctl -u talk2code -f
tail -f /opt/talk2code/backend/logs/gunicorn-error.log
tail -f /opt/talk2code/backend/logs/celery.log

# 更新代码
cd /opt/talk2code && sudo -u talk2code git pull
sudo -u talk2code -H bash -c 'cd /opt/talk2code/frontend-vue && npm install && npm run build'
sudo systemctl restart talk2code talk2code-celery
```

备份（PostgreSQL 与发布产物）：

```bash
sudo docker exec talk2code-postgres \
  pg_dump -U talk2code talk2code | gzip > /opt/backup/t2c-$(date +%F).sql.gz
tar czf /opt/backup/published-$(date +%F).tar.gz /opt/talk2code/backend/published
```

---

## 5. 残留风险与限制

1. **预览沙箱可探测本机端口**（P1-5，已缓解未消除）。彻底封禁需要改 `build_chromium_args()`  
   的 loopback bypass 策略，会牵动 5 处预览链路，建议单独立项。
2. **无资源配额上限**。单个用户可以反复提交需求消耗 LLM 额度，目前只有频率限流（10/hour  
   创建 + 30/min 对话）。公网建议在上游加按用户的成本上限。
3. **JWT 无吊销机制**（P2-7）。泄露后只能等过期。
4. **依赖未锁定**（P2-9）。重装机器可能装出不同版本。
5. **`deploy/deploy.sh` 第 25 行有 `rm -rf "$APP_DIR"`**，会无提示删除 `/opt/talk2code`。  
   若改用脚本部署，务必先确认该目录无未提交数据。
6. **Celery worker 并发为 1**，多需求排队串行。负载升高时需要调 `--concurrency`。
