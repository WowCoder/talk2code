#!/usr/bin/env bash
# Talk2Code 腾讯云生产部署一键脚本（Ubuntu 22.04 LTS 轻量应用服务器）
# 用法: sudo bash deploy.sh your.domain.com
set -e

APP_DIR=/opt/talk2code
REPO=https://github.com/WowCoder/talk2code.git
DOMAIN="${1:-your.domain.com}"

echo "==> [1/7] 安装系统依赖（Python3.11 / Node20 / Nginx / Docker / Playwright 系统库）"
apt-get update -y
apt-get install -y software-properties-common curl ca-certificates gnupg
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update -y
apt-get install -y python3.11 python3.11-venv python3.11-dev python3-pip nginx docker.io docker-compose git
# Node.js 20（前端构建需要）
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
# Playwright Chromium 运行所需的系统库
apt-get install -y libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
  libdbus-1-3 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 \
  libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0

echo "==> [2/7] 克隆代码"
rm -rf "$APP_DIR"
git clone "$REPO" "$APP_DIR"
cd "$APP_DIR"

echo "==> [3/7] 后端 venv + Python 依赖 + Playwright 浏览器"
cd "$APP_DIR/backend"
python3.11 -m venv venv
# shellcheck disable=SC1091
source venv/bin/activate
pip install -q -r requirements.txt
playwright install --with-deps chromium

echo "==> [4/7] 预下载 BGE-M3 模型（避免线上首次检索卡 1-2 分钟）"
python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3', device='cpu')"

echo "==> [5/7] 前端构建"
cd "$APP_DIR/frontend-vue"
npm install --silent
npm run build

echo "==> [6/7] 启动数据库（Docker: PostgreSQL + Redis，宿主端口 5434 / 6380）"
cd "$APP_DIR"
docker compose up -d
sleep 5

echo "==> [7/7] 写入生产 .env 并启动后端（gunicorn gthread + celery worker）"
cp "$APP_DIR/backend/.env.prod.example" "$APP_DIR/backend/.env"
sed -i "s#your.domain.com#$DOMAIN#g" "$APP_DIR/backend/.env"
mkdir -p "$APP_DIR/backend/logs"
cd "$APP_DIR/backend"
# shellcheck disable=SC1091
source venv/bin/activate
nohup gunicorn -w 2 -k gthread --threads 8 --timeout 120 --bind 127.0.0.1:5001 app:app \
  > logs/gunicorn.log 2>&1 &
nohup celery -A celery_app.celery_app worker --loglevel=info --concurrency=1 \
  > logs/celery.log 2>&1 &

echo "==> [8/8] 申请免费 SSL 证书（certbot，需域名已解析且备案通过；失败不中断脚本）"
# Let's Encrypt 经 80 端口回源校验，因此必须: ① 域名 A 记录已指向本机 ② 大陆节点已完成 ICP 备案
# 备案通过前跑此步通常会失败，属预期；届时手动补: sudo certbot --nginx -d $DOMAIN
if command -v certbot >/dev/null 2>&1; then
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@"$DOMAIN" \
    || echo "    [跳过] certbot 未成功（常见原因: 域名未解析 / 备案未完成）。备案通过后手动跑: sudo certbot --nginx -d $DOMAIN"
else
  apt-get install -y certbot python3-certbot-nginx
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos -m admin@"$DOMAIN" \
    || echo "    [跳过] certbot 未成功（常见原因: 域名未解析 / 备案未完成）。备案通过后手动跑: sudo certbot --nginx -d $DOMAIN"
fi

echo "==> 部署脚本完成。"
echo "    下一步："
echo "    1) 把 deploy/nginx.conf 放到 /etc/nginx/sites-enabled/talk2code（已替换域名为 $DOMAIN）"
echo "    2) sudo nginx -t && sudo systemctl restart nginx"
echo "    3) 备案通过 + 域名解析后，确认 HTTPS 生效: 打开 https://$DOMAIN"
echo "    4) 在 backend/.env 填真实 LLM_API_KEY，重启 gunicorn/celery 生效"
