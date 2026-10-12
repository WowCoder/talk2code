#!/usr/bin/env bash
# Talk2Code 生产部署脚本（Ubuntu 22.04 LTS）
#
# 用法: sudo bash deploy.sh <域名> [apex]
#   例: sudo bash deploy.sh app.example.com example.com
#   apex 省略时默认与域名相同（单域名部署）
#
# ⚠️ 本文件位于 deploy/，而 deploy/ 被仓库 .gitignore 忽略 —— 从 GitHub clone
#    的机器上不存在本脚本。请从本地 rsync 过去，或直接按 docs/deploy.md 手工执行
#    （文档与本脚本等价，且包含安全审查结论与验收清单）。
#
# 幂等性：可重复执行。不会删除已有代码、数据库卷或数据。
set -euo pipefail

APP_DIR=/opt/talk2code
REPO=https://github.com/WowCoder/talk2code.git
DOMAIN="${1:?用法: sudo bash deploy.sh <域名> [apex]}"
APEX="${2:-$DOMAIN}"
RUN_USER=talk2code
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

# Docker（用 compose v2 插件，docker-compose v1 已停止维护）
apt-get install -y docker.io docker-compose-plugin

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
sudo -u "$RUN_USER" -H bash -c "
  set -e
  cd '$APP_DIR/backend'
  [ -d venv ] || $PY -m venv venv
  source venv/bin/activate
  pip install -q --upgrade pip
  if [ -f requirements.lock ]; then
    pip install -q -r requirements.lock
  else
    echo '[warn] requirements.lock 缺失，回退 requirements.txt（版本不可复现）'
    pip install -q -r requirements.txt
  fi
  playwright install chromium
"
# requirements.txt 已含 gunicorn；这里只做确认，避免静默降级到开发服务器
sudo -u "$RUN_USER" -H "$APP_DIR/backend/venv/bin/gunicorn" --version >/dev/null \
  || { echo "gunicorn 安装失败，终止"; exit 1; }

# ==================== 5/9 预下载模型 ====================
log "[5/9] 预下载 BGE-M3 模型（避免线上首次检索卡 1-2 分钟）"
sudo -u "$RUN_USER" -H bash -c "
  cd '$APP_DIR/backend' && source venv/bin/activate
  python -c \"from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3', device='cpu')\"
"

# ==================== 6/9 前端构建 ====================
log "[6/9] 前端构建"
sudo -u "$RUN_USER" -H bash -c "cd '$APP_DIR/frontend-vue' && npm install --silent && npm run build"

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
if [ -f "$APP_DIR/deploy/nginx.conf" ]; then
  sed -e "s#<DOMAIN>#${DOMAIN}#g" \
      -e "s#<APEX_REGEX>#$(echo "$APEX" | sed 's#\.#\\\\.#g')#g" \
      -e "s#<APEX>#${APEX}#g" \
      "$APP_DIR/deploy/nginx.conf" > /etc/nginx/sites-available/talk2code
  ln -sf /etc/nginx/sites-available/talk2code /etc/nginx/sites-enabled/talk2code
  rm -f /etc/nginx/sites-enabled/default
  if nginx -t; then
    systemctl reload nginx
  else
    warn "nginx 配置校验失败，请检查 /etc/nginx/sites-available/talk2code"
  fi
else
  warn "未找到 deploy/nginx.conf，跳过（见 docs/deploy.md 步骤 9）"
fi

log "申请 SSL 证书（需域名已解析且备案通过；失败不中断）"
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m "admin@${DOMAIN}" \
  || warn "certbot 未成功（常见原因：域名未解析 / 备案未完成）。备案后手动跑：certbot --nginx -d $DOMAIN"

# ==================== 完成 ====================
cat <<EOF

==> 部署脚本执行完毕。

必须手工完成的三件事：
  1) 在 $APP_DIR/backend/.env 填入真实 LLM_API_KEY，然后：
       systemctl restart talk2code talk2code-celery
  2) 创建后台管理员与邀请码（注册强制要求邀请码）：
       cd $APP_DIR/backend && sudo -u $RUN_USER venv/bin/python manage.py admin create-user --username ops
       cd $APP_DIR/backend && sudo -u $RUN_USER venv/bin/python manage.py invite create --count 20
  3) 若需「一键发布」到 *.$APEX，先申请泛域名证书并在 nginx 确认已包含该 server 块。

验收：curl -sI https://$DOMAIN/ ；文档：docs/deploy.md
EOF
