#!/usr/bin/env bash
# Talk2Code 生产部署脚本（Ubuntu 22.04 LTS）
#
# 用法: sudo bash deploy.sh <域名> [apex]
#   例: sudo bash deploy.sh app.example.com example.com
#   apex 省略时默认与域名相同（单域名部署）
#
# 本脚本与 deploy/ 下的 nginx 模板、环境变量样例同在版本控制内；
# docs/deploy.md 是它的等价文档（含安全审查结论与验收清单），二者需同步维护。
#
# 幂等性：可重复执行。不会删除已有代码、数据库卷或数据。
set -euo pipefail

APP_DIR=/opt/talk2code
REPO=https://github.com/WowCoder/talk2code.git
DOMAIN="${1:?用法: sudo bash deploy.sh <域名> [apex]}"
APEX="${2:-$DOMAIN}"
RUN_USER=talk2code
NGINX_SITE_CONF=/etc/nginx/sites-available/talk2code
PY=/usr/bin/python3.10
[ -x "$PY" ] || PY=/usr/bin/python3   # 兜底：系统默认 python3

log()  { echo "==> $*"; }
warn() { echo "    [注意] $*"; }

# ==================== 1/9 系统依赖 ====================
log "[1/9] 安装系统依赖（Python3.10 / Node20 / Nginx / Docker / Playwright 系统库）"
apt-get update -y
apt-get install -y software-properties-common curl ca-certificates gnupg lsof

# Ubuntu 22.04 官方源自带 Python 3.10.12，无需 deadsnakes PPA
apt-get install -y python3.10 python3.10-venv python3.10-dev python3-pip nginx git

# Docker（compose v2）。注意包名：Ubuntu 官方源里叫 docker-compose-v2，
# docker-compose-plugin 是 Docker 官方 apt 仓库的包名、在官方源中不存在 ——
# 两者混写会让整条 apt 命令因 "Unable to locate package" 失败，连 docker 都装不上。
apt-get install -y docker.io docker-compose-v2

# Node.js 20（前端构建需要）
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

# Playwright Chromium 运行所需的系统库
apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0

# ==================== 2/9 运行用户 ====================
log "[2/9] 准备运行用户 $RUN_USER"
if ! id -u "$RUN_USER" >/dev/null 2>&1; then
  useradd -r -m -s /bin/bash "$RUN_USER"
fi
usermod -aG docker "$RUN_USER"   # 允许该用户执行 docker compose（需重登生效）

# ==================== 3/9 获取代码（幂等，不删数据） ====================
log "[3/9] 获取代码到 $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  warn "目录已存在，执行 git pull（不删除任何本地数据）"
  git -C "$APP_DIR" pull --ff-only
else
  mkdir -p "$(dirname "$APP_DIR")"
  git clone "$REPO" "$APP_DIR"
fi
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

# ==================== 4/9 后端环境 ====================
log "[4/9] 后端 venv + Python 依赖 + Playwright 浏览器"
# ★ Playwright 浏览器内核：pip 装了 playwright **不等于**装了内核。缺内核时
#   服务端所有 `chromium launch` 直接失败 → AC 逐条验收 / 冒烟 / 截图**静默**
#   回退「直读工作区文件」（冒烟还会打印 available=True 的假绿，因为它是读本地
#   文件判的）→ 验收链路形同虚设，日志里只有一句「沙箱预览链路不可用」。
#   内核放 $APP_DIR 下（不依赖 HOME），并同步写进两个 systemd unit。
#   国内网络：走 npmmirror 镜像，playwright 官方 CDN 通常拉不动。
PW_BROWSERS="$APP_DIR/.pw-browsers"
PW_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright
sudo -u "$RUN_USER" -H env \
  PLAYWRIGHT_BROWSERS_PATH="$PW_BROWSERS" \
  PLAYWRIGHT_DOWNLOAD_HOST="$PW_DOWNLOAD_HOST" \
  bash -c "
  set -e
  cd '$APP_DIR/backend'
  [ -d venv ] || $PY -m venv venv
  source venv/bin/activate
  pip install -q --upgrade pip
  # torch 用 CPU 版（部署机无 GPU，不需要 PyPI 那 2–3 GB 的 nvidia-*/triton）；
  # 锁文件里也带了该 index 指令，这里显式给一份以覆盖回退 requirements.txt 的分支
  if [ -f requirements.lock ]; then
    pip install -q --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.lock
  else
    echo '[warn] requirements.lock 缺失，回退 requirements.txt（版本不可复现）'
    pip install -q --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
  fi
  playwright install chromium
"
# 真拉起一次内核才算装好（`playwright install` 只下载、不校验能否启动）。
# 这一步不过就直接终止：宁可部署失败，也不要带着「验收永远假绿」上线。
sudo -u "$RUN_USER" -H env PLAYWRIGHT_BROWSERS_PATH="$PW_BROWSERS" \
  "$APP_DIR/backend/venv/bin/python" - <<'PY' \
  || { echo "Playwright 内核无法启动（验收链路会静默降级），终止"; exit 1; }
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    _b = p.chromium.launch(headless=True)
    print("    chromium 启动正常:", _b.version)
    _b.close()
PY
# requirements.txt 已含 gunicorn；这里只做确认，避免静默降级到开发服务器
sudo -u "$RUN_USER" -H "$APP_DIR/backend/venv/bin/gunicorn" --version >/dev/null \
  || { echo "gunicorn 安装失败，终止"; exit 1; }

# ==================== 5/9 预下载模型 ====================
log "[5/9] 预下载 BGE-M3 模型（避免线上首次检索卡 1-2 分钟）"
# ★ 必须显式给 HF_ENDPOINT：默认端点 huggingface.co 在国内机房是 `Network is
#   unreachable`，而 huggingface_hub 会对**每个文件**重试 5 次（1/2/4/8/8s 退避）
#   —— 实测这一步能拖 20 分钟以上，最后超时失败还会因 set -e 把整个部署带走
#   （前几步都成功了，却停在模型预热上）。镜像端点已同时写进两个 systemd unit，
#   这里保持一致；可用 HF_ENDPOINT=... 覆盖。
#   失败不再终止：模型已缓存时它只是校验一遍；即使没下成，运行时会用同一个
#   HF_ENDPOINT 自动补齐，属于「慢一点」而非「坏掉」。
sudo -u "$RUN_USER" -H env \
  HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}" \
  HF_HUB_ENABLE_HF_TRANSFER="${HF_HUB_ENABLE_HF_TRANSFER:-1}" \
  bash -c "
  cd '$APP_DIR/backend' && source venv/bin/activate
  python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3', device='cpu')\"
" || warn "模型预热未成功（HF_ENDPOINT=${HF_ENDPOINT:-https://hf-mirror.com}）；运行时会自行补齐，继续部署"

# ==================== 6/9 前端构建 ====================
log "[6/9] 前端构建"
sudo -u "$RUN_USER" -H bash -c "cd '$APP_DIR/frontend-vue' && npm install --silent && npm run build"

# 构建产物自检：整站不得依赖任何外部 origin。
# 历史上 index.html 外链了 fonts.googleapis.com —— 那是**阻塞渲染的跨域样式表**，
# 在网络到不了 Google 的环境（大陆最常见）下浏览器会卡到超时，首屏长时间白屏。
# 字体现已自托管（public/fonts/*.woff2 + src/assets/styles/fonts.css，由
# `npm run fonts` 生成；只含拉丁两族，中文走系统字体栈），这里做几道断言，
# 避免以后又悄悄引回外链、或漏掉字体。
DIST="$APP_DIR/frontend-vue/dist"
if grep -qE '(href|src)="https?://' "$DIST/index.html"; then
  echo "构建产物仍在引用外部资源，首屏会被外部网络阻塞，终止："
  grep -oE '(href|src)="https?://[^"]*"' "$DIST/index.html" | sort -u
  exit 1
fi
FONT_N=$(find "$DIST/fonts" -name '*.woff2' 2>/dev/null | wc -l | tr -d ' ') || FONT_N=0
FONT_N=${FONT_N:-0}
if [ "$FONT_N" -lt 1 ]; then
  echo "自托管字体缺失（dist/fonts 里没有 woff2），终止。"
  echo "补齐：cd $APP_DIR/frontend-vue && sudo -u $RUN_USER npm run fonts"
  exit 1
fi
# 字体文件数与 CSS 里引用到的 /fonts/*.woff2 必须一一对应：
#   多了 → 改过字体族配置后残留的孤儿文件（白占仓库与磁盘，还会被部署上去）
#   少了 → CSS 引用了不存在的文件，浏览器静默不加载，字形回退且无任何报错
#
# ⚠️ 两处坑（实测各踩过一次）：
#   ① 不能按 `url('/fonts/xxx')` 匹配 —— Vite 压缩会**把 url() 里的引号去掉**，
#      产物里是 `url(/fonts/xxx.woff2)`，写死单引号会永远匹配 0 条；
#   ② 不能直接 `VAR=$(grep ... | wc -l)` —— grep 无命中返回非 0，在
#      `set -e -o pipefail` 下**赋值就失败、脚本无任何提示地退出**
#      （表现：前端刚构建完，7/8/9 步全被跳过）。所以统一兜 `|| VAR=0`。
FONT_REF=$(grep -ohE "/fonts/[A-Za-z0-9._-]+\.woff2" "$DIST"/assets/*.css 2>/dev/null \
  | sort -u | wc -l | tr -d ' ') || FONT_REF=0
FONT_REF=${FONT_REF:-0}
if [ "$FONT_N" != "$FONT_REF" ]; then
  echo "字体文件数（$FONT_N）与 CSS 引用数（$FONT_REF）不一致，终止。"
  echo "重新生成：cd $APP_DIR/frontend-vue && sudo -u $RUN_USER npm run fonts && npm run build"
  exit 1
fi
log "  产物自检通过：无外部 origin 引用；自托管字体 $FONT_N 个（与 CSS 引用数一致）"

# ★ 构建完成后必须让 nginx 换一批 worker。
#   nginx 的 open_file_cache 会把**打开的文件描述符**缓存住（见 conf.d/talk2code-tuning.conf）：
#   构建产物是换 inode 落盘的，旧 fd 仍指向旧 inode —— 部署后 nginx 会继续下发旧文件。
#   index.html 已在配置里单独 `open_file_cache off`（它是唯一「文件名不变、内容会变」
#   的文件，被缓存住会导致整站白屏）；这里再 reload 一次，把 favicon.png 之类
#   稳定文件名的一并刷新。reload 不中断连接。
systemctl reload nginx 2>/dev/null || true

# ==================== 7/9 中间件（随机口令，只监听回环） ====================
log "[7/9] 启动 PostgreSQL + Redis"
cd "$APP_DIR"
if [ ! -f .env ]; then
  cat > .env <<EOF
# 由 deploy.sh 生成，勿提交（.gitignore 已忽略）
POSTGRES_PASSWORD=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')
REDIS_PASSWORD=$(python3 -c 'import secrets;print(secrets.token_urlsafe(24))')
EOF
  chmod 600 .env
  echo "    已生成随机口令写入 $APP_DIR/.env"
else
  warn ".env 已存在，沿用其中口令（不覆盖）"
fi
# shellcheck disable=SC1091
set -a; . "$APP_DIR/.env"; set +a
docker compose up -d
sleep 5
docker compose ps

# ==================== 8/9 生产 .env + systemd ====================
log "[8/9] 写入 backend/.env 并注册 systemd 服务"

ENV_SRC="$APP_DIR/deploy/.env.prod.example"
ENV_DST="$APP_DIR/backend/.env"
if [ ! -f "$ENV_SRC" ]; then
  echo "缺少 $ENV_SRC（deploy/ 未随代码分发？）。请按 docs/deploy.md 步骤 7 手工创建 $ENV_DST"
  exit 1
fi

JWT_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
mkdir -p "$APP_DIR/backend/logs"

if [ -f "$ENV_DST" ]; then
  warn "backend/.env 已存在，保留原文件（如需重置请先手工备份并删除）"
else
  cp "$ENV_SRC" "$ENV_DST"
  # 占位符 → 实际值（token_urlsafe 只含 A-Za-z0-9_- ，无需转义）
  sed -i \
    -e "s#__POSTGRES_PASSWORD__#${POSTGRES_PASSWORD}#g" \
    -e "s#__REDIS_PASSWORD__#${REDIS_PASSWORD}#g" \
    -e "s#__JWT_SECRET__#${JWT_SECRET}#g" \
    -e "s#__DOMAIN__#${DOMAIN}#g" \
    -e "s#__APEX__#${APEX}#g" \
    "$ENV_DST"
  warn "LLM_API_KEY 仍是占位符，必须手工填入真实 Key 后重启服务"
fi
chmod 600 "$ENV_DST"
chown "$RUN_USER:$RUN_USER" "$ENV_DST"
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR/backend/logs"

cat > /etc/systemd/system/talk2code.service <<EOF
[Unit]
Description=Talk2Code backend (gunicorn)
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR/backend
Environment="PYTHONPATH=$APP_DIR/backend"
Environment="PLAYWRIGHT_BROWSERS_PATH=$APP_DIR/.pw-browsers"
Environment="PYTHONUNBUFFERED=1"
ExecStart=$APP_DIR/backend/venv/bin/gunicorn \\
  -w 2 -k gthread --threads 8 \\
  --timeout 120 --graceful-timeout 30 --keep-alive 5 \\
  --access-logfile $APP_DIR/backend/logs/gunicorn-access.log \\
  --error-logfile $APP_DIR/backend/logs/gunicorn-error.log \\
  --bind 127.0.0.1:5001 app:app
Restart=always
RestartSec=5
KillSignal=SIGTERM

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/talk2code-celery.service <<EOF
[Unit]
Description=Talk2Code celery worker
After=network.target talk2code.service

[Service]
Type=simple
User=$RUN_USER
Group=$RUN_USER
WorkingDirectory=$APP_DIR/backend
Environment="PYTHONPATH=$APP_DIR/backend"
Environment="PLAYWRIGHT_BROWSERS_PATH=$APP_DIR/.pw-browsers"
ExecStart=$APP_DIR/backend/venv/bin/celery \\
  -A celery_app.celery_app worker --loglevel=info --concurrency=1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now talk2code talk2code-celery
sleep 3
systemctl --no-pager --lines=0 status talk2code talk2code-celery || true

# 健康检查（失败不中断，交由后续人工确认）
if curl -sf -m 5 http://127.0.0.1:5001/api/health >/dev/null; then
  echo "    /api/health 正常"
else
  warn "健康检查未通过，先看日志：journalctl -u talk2code -n 50"
fi

# ==================== 9/9 Nginx + SSL ====================
log "[9/9] 配置 Nginx"

# ★ 开启 gzip（幂等）。Ubuntu 自带的 /etc/nginx/nginx.conf 把 gzip_types 整段
#   注释掉了，只压 text/html —— 实际后果是所有 JS/CSS/JSON **原样下发**，
#   实测前端资源可省 60%+（codemirror.js 834KB → 277KB），弱网下体感差别明显。
#   注意：不得把 text/event-stream 加进去，SSE 进度推送被压缩会破坏流式即时性。
if ! grep -qE '^[[:space:]]*gzip_types' /etc/nginx/nginx.conf; then
  sed -i -E 's/^([[:space:]]*)#[[:space:]]*(gzip_(vary|proxied|comp_level|buffers|http_version|types)\b)/\1\2/' /etc/nginx/nginx.conf
  grep -qE '^[[:space:]]*gzip_min_length' /etc/nginx/nginx.conf \
    || sed -i -E 's/^([[:space:]]*gzip on;)/\1\n\tgzip_min_length 256;/' /etc/nginx/nginx.conf
  echo "    已开启 gzip（text/css、application/javascript、application/json 等）"
else
  echo "    gzip 已开启，跳过"
fi

# ★ 全局调优（幂等）：open_file_cache 复用静态文件的 stat 与 fd；并定义带
#   $request_time 的日志格式 —— 默认 combined 格式没有任何时间字段，事后无法
#   区分「服务端慢」与「用户网络慢」，排查首屏慢时只能靠猜。
if [ -f "$APP_DIR/deploy/nginx-tuning.conf" ]; then
  cp "$APP_DIR/deploy/nginx-tuning.conf" /etc/nginx/conf.d/talk2code-tuning.conf
  echo "    已安装 conf.d/talk2code-tuning.conf"
fi

# ★ 渲染 + 裁剪抽成函数：第 9 步要跑**两遍** —— 一遍在 certbot 之前，一遍在它之后。
#   第二遍的理由：certbot 的 nginx 插件每次「部署证书」都会往配置里塞自己的东西
#   （server 级的 `if ($host = …) return 301`、给证书行加 `# managed by Certbot`）。
#   其中 server 级 if 会抢在 location 之前生效，把 http://www.<DOMAIN> 先跳成
#   https://www.<DOMAIN> —— 「一跳归一」就退化成两跳。渲染是幂等的，再跑一遍即可把
#   它冲掉，让线上内容始终 == 模板内容（唯一事实源）。
render_nginx_conf() {
  sed -e "s#<DOMAIN>#${DOMAIN}#g" \
      -e "s#<APEX_REGEX>#$(echo "$APEX" | sed 's#\.#\\\\.#g')#g" \
      -e "s#<APEX>#${APEX}#g" \
      "$APP_DIR/deploy/nginx.conf" > "$NGINX_SITE_CONF"
  # ★ 依赖证书的段落按证书是否**齐备**裁剪 —— 否则 nginx -t 会因 ssl_certificate
  #   加载不到文件而**直接失败**，连主站都起不来。三对标记 + 一个锚点：
  #     apex-ssl / apex-http-redirect / www-https：同一判据 —— apex 证书 + certbot 的
  #       TLS 参数文件（options-ssl-nginx.conf、ssl-dhparams.pem 是 include 的目标，
  #       缺了照样起不来）
  #     「已发布站点 → EOF」：泛域名证书
  #   顺序不能反：最后那个的删除范围是「锚点 → EOF」，放前面会把别的块一起吞掉。
  #   证书签好后重跑本脚本，被裁掉的块会自动加回。
  #   ⚠️ 匹配一律允许行首空白（`^[[:space:]]*#`）：apex-ssl 那对标记写在 server 块
  #      内部、天然带缩进。实测踩过按行首 `^#` 锚的版本 —— 匹配不上，那一段根本没被
  #      删掉，而 `nginx -t` 照样通过（它只校验"配置能不能加载"，不校验"该删的删没删"）。
  #
  # ★ 主站的 443 为什么写在模板里、而不是像以前那样留给 certbot 追加：
  #   certbot 只在「签发/续期」那一次动配置，证书没到期时它直接跳过，而第 9 步是
  #   **整文件覆盖** —— 以前 443 只存在于线上，重跑一次部署就把主站变回只监听 80：
  #   :443 上没有默认 server（没人写 default_server），nginx 取第一个 :443 块（www 的
  #   跳转块）当默认，`Host: <DOMAIN>` 于是被 301 到它自己 → https 无限循环；而末尾
  #   两条验收都还会打 ✓（301 响应本身就是 HTTP/2）。现在配置全部由模板产出，
  #   certbot 只负责签证书。
  APEX_READY=0
  if [ -f "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" ] \
     && [ -f /etc/letsencrypt/options-ssl-nginx.conf ] \
     && [ -f /etc/letsencrypt/ssl-dhparams.pem ]; then
    APEX_READY=1
    echo "    apex 证书与 TLS 参数齐备，保留主站 443 与两个跳转块"
  else
    sed -i '/^[[:space:]]*# >>> apex-ssl-start/,/^[[:space:]]*# <<< apex-ssl-end/d' "$NGINX_SITE_CONF"
    sed -i '/^[[:space:]]*# >>> apex-http-redirect-start/,/^[[:space:]]*# <<< apex-http-redirect-end/d' "$NGINX_SITE_CONF"
    sed -i '/^[[:space:]]*# >>> www-https-start/,/^[[:space:]]*# <<< www-https-end/d' "$NGINX_SITE_CONF"
    warn "apex 证书或 certbot 的 TLS 参数文件缺失，本次未启用 HTTPS（主站只在 :80 上服务）"
    warn "签好证书后重跑本脚本即自动启用：certbot --nginx -d $DOMAIN -d www.$DOMAIN"
  fi
  if [ ! -f "/etc/letsencrypt/live/wildcard.${APEX}/fullchain.pem" ]; then
    sed -i '/^[[:space:]]*# ==================== 已发布站点/,$d' "$NGINX_SITE_CONF"
    warn "未检测到泛域名证书 /etc/letsencrypt/live/wildcard.${APEX}/，本次省略发布站块"
  fi

  # ★ 兜底断言：保留了 443 段却 grep 不到主站的 `listen 443 ssl`，说明标记区间被改坏
  #   或模板回退成了旧版 —— 部署上去就是 https 无限 301，而旧版两条验收都发现不了。
  if [ "$APEX_READY" = "1" ] \
     && ! grep -qE '^[[:space:]]*listen[[:space:]]+443[[:space:]]+ssl' "$NGINX_SITE_CONF"; then
    warn "★ 主站没有 443 监听（apex-ssl 标记区间被改坏？）—— 现在部署上去会让 https 无限 301"
  fi

  # ★ 给 443 的 listen 补 http2（幂等）。nginx 1.18 没有 `http2 on;` 这种写法，http2
  #   只能写在 listen 上、而且是 **socket 级**参数 —— 丢了就掉回 HTTP/1.1（每域名 6
  #   条并发连接，一页十几个静态资源要排队）。模板里的 443 都自带 http2，这一步只兜
  #   首次部署时 certbot 自己插进来的那行 `listen 443 ssl;`。
  sed -i -E 's#^([[:space:]]*listen[[:space:]]+443[[:space:]]+ssl)([[:space:]]*;)#\1 http2\2#' "$NGINX_SITE_CONF"
}

if [ -f "$APP_DIR/deploy/nginx.conf" ]; then
  render_nginx_conf
  ln -sf "$NGINX_SITE_CONF" /etc/nginx/sites-enabled/talk2code
  rm -f /etc/nginx/sites-enabled/default
  if nginx -t; then
    # 用 restart 而非 reload：实测过一次 reload 后 `nginx -T` 已是新内容、
    # 线上行为却还是旧的（同名响应头未更新），restart 后才生效。
    systemctl restart nginx
  else
    warn "nginx 配置校验失败，请检查 $NGINX_SITE_CONF"
  fi
else
  warn "未找到 deploy/nginx.conf，跳过（见 docs/deploy.md 步骤 9）"
fi

log "申请 SSL 证书（需域名已解析且备案通过；失败不中断）"
apt-get install -y certbot python3-certbot-nginx
# 一次把 apex 与 www 都签进去：www 的 https 跳转块要复用它，只签 apex 的话
# https://www.<DOMAIN> 会因证书不匹配而报错（浏览器不给跳过）。
certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos -m "admin@${DOMAIN}" \
  || warn "certbot 未成功（常见原因：域名未解析 / 备案未完成）。备案后手动跑：certbot --nginx -d $DOMAIN -d www.$DOMAIN"

# ★ certbot 的 nginx 插件每次「部署证书」都会改配置（往 :80 块里插 server 级
#   `if ($host = …) return 301`、给证书行加 `# managed by Certbot`）。server 级 if 会
#   抢在 location 之前生效，于是 http://www.<DOMAIN> 先跳 https://www.<DOMAIN>、
#   再由 www 的块跳第二次 —— 「一跳归一」退化成两跳。渲染是幂等的，这里再跑一遍把它的
#   改动冲掉，让线上内容 == 模板内容。渲染后必须 restart 才生效；万一二次渲染过不了
#   nginx -t，就退回 certbot 那次的结果（线上仍然可用）。
if [ -f "$APP_DIR/deploy/nginx.conf" ]; then
  cp -p "$NGINX_SITE_CONF" "$NGINX_SITE_CONF.certbot-result"
  render_nginx_conf
  if nginx -t >/dev/null 2>&1; then
    systemctl restart nginx 2>/dev/null || true
    echo "    ✓ certbot 的改动已渲染掉，线上内容 == 模板内容"
    rm -f "$NGINX_SITE_CONF.certbot-result"
  else
    cp -p "$NGINX_SITE_CONF.certbot-result" "$NGINX_SITE_CONF"
    rm -f "$NGINX_SITE_CONF.certbot-result"
    warn "certbot 之后的二次渲染没通过 nginx -t，已回退到 certbot 那次的结果"
  fi
fi

# ---- 实测验收：协议版本与 www 跳转 ----
# 只校验配置文件内容是没用的 —— 实测踩过 `systemctl reload nginx` 后 `nginx -T`
# 已是新内容、线上行为却还是旧的。这里一律打到线上实际响应上。
systemctl restart nginx 2>/dev/null || true
sleep 1
if command -v curl >/dev/null; then
  if [ "$APEX_READY" = "1" ]; then
    # ★ 先断言首页真的返回 200，再谈协议版本。主站丢了 443 时，请求会落到 :443 的默认
    #   server（www 跳转块）上 → 301 到它自己；而那个 301 响应本身就是 HTTP/2，只测协议
    #   版本会判成「正常」（历史事故），所以必须先看状态码。
    HTTPS_CODE=$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' "https://$DOMAIN/" 2>/dev/null || echo "?")
    case "$HTTPS_CODE" in
      200*) echo "    ✓ 主站 https 返回 200" ;;
      *) warn "★ 主站 https 没返回 200（实测：$HTTPS_CODE）—— 若是 301 到自身即死循环，检查主站 443 块是否还在" ;;
    esac

    VER=$(curl -sS -o /dev/null -w '%{http_version}' "https://$DOMAIN/" 2>/dev/null || echo "?")
    if [ "$VER" = "2" ]; then echo "    ✓ 主站协议 HTTP/2"
    else warn "主站协议是 HTTP${VER}（期望 2）—— 静态资源会受每域名 6 连接限制"; fi

    WWW=$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' "https://www.$DOMAIN/" 2>/dev/null || echo "?")
    case "$WWW" in
      301*"https://$DOMAIN/"*) echo "    ✓ www.$DOMAIN 已 301 到 $DOMAIN" ;;
      *) warn "www.$DOMAIN 未按预期 301（实测：$WWW）—— 检查 apex 证书与 www 的 server 块" ;;
    esac

    # ★ www 的 http 必须**一跳**直达 https://<DOMAIN>。certbot 部署证书时会往 :80 块里插
    #   server 级 `if ($host = www…) return 301 https://www…`，那会让它先跳 https://www
    #   再跳一次（两跳，缓存与收录都吃亏）—— 二次渲染就是为了冲掉它，这里是它的守卫。
    WWW_HTTP=$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' "http://www.$DOMAIN/" 2>/dev/null || echo "?")
    case "$WWW_HTTP" in
      301*"https://$DOMAIN/"*) echo "    ✓ http://www.$DOMAIN 一跳直达 https://$DOMAIN" ;;
      *) warn "http://www.$DOMAIN 不是一跳直达 apex（实测：$WWW_HTTP）—— certbot 又往 :80 块里插了 server 级 if？" ;;
    esac
  else
    warn "本次未启用 HTTPS（证书或 TLS 参数缺失），跳过 https 验收；主站当前只在 :80 上服务"
  fi
fi

# ==================== 完成 ====================
cat <<EOF

==> 部署脚本执行完毕。

必须手工完成的四件事：
  1) 在 $APP_DIR/backend/.env 填入真实 LLM_API_KEY，然后：
       systemctl restart talk2code talk2code-celery
  2) 创建后台管理员与邀请码（注册强制要求邀请码）：
       cd $APP_DIR/backend && sudo -u $RUN_USER venv/bin/python manage.py admin create-user --username ops
       cd $APP_DIR/backend && sudo -u $RUN_USER venv/bin/python manage.py invite create --count 20
  3) 若要启用「一键发布」（*.$APEX）：签泛域名证书后重跑本脚本，发布站
     nginx 块会自动加回（未签证书时该块被主动省略，否则 nginx -t 过不了）。
       certbot certonly --manual --preferred-challenges dns --cert-name wildcard.$APEX -d '*.$APEX'
     按提示把 TXT 值加到 _acme-challenge.$APEX，验证后即可删该记录。
     通配符**不支持 HTTP 验证**，只能走 DNS-01。另外：手动签发的证书**不会
     自动续期**（certbot 无法无人值守地重放 DNS 挑战），90 天后需重跑一次；
     要自动续期请装 DNS 插件并配好服务商 API 凭据。
  4) ★ 跑一条真实需求，确认日志里**没有**「沙箱预览链路不可用」——有就说明验收
     在静默降级（见 docs/deploy.md 的「验收链路自检」）：
       journalctl -u talk2code-celery -n 200 | grep -c "沙箱预览链路不可用"   # 期望 0

验收：curl -sI https://$DOMAIN/ ；文档：docs/deploy.md
EOF
