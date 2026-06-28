#!/usr/bin/env bash
set -e

# Talk2Code 启动脚本
# 用法: ./start.sh [dev|prod]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="$SCRIPT_DIR/venv"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend-vue"
ENV_FILE="$BACKEND_DIR/.env"
MODE="${1:-dev}"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $1"; }
err()  { echo -e "${RED}[ERR]${NC}   $1"; }

# ---- 1. 检查/创建虚拟环境 ----
if [ ! -d "$VENV_DIR" ]; then
    log "创建虚拟环境..."
    python3 -m venv "$VENV_DIR"
fi

log "激活虚拟环境"
source "$VENV_DIR/bin/activate"

# ---- 2. 安装依赖 ----
log "检查依赖..."
pip install -q -r "$BACKEND_DIR/requirements.txt" 2>/dev/null
log "依赖就绪"

# ---- 3. 检查 .env 配置 ----
if [ ! -f "$ENV_FILE" ]; then
    warn ".env 文件不存在，从 .env.example 创建"
    if [ -f "$BACKEND_DIR/.env.example" ]; then
        cp "$BACKEND_DIR/.env.example" "$ENV_FILE"
        log "已创建 $ENV_FILE，如需使用 LLM 请编辑填入 API Key"
    else
        touch "$ENV_FILE"
    fi
fi

# ---- 4. 检查端口 ----
PORT=5001
if lsof -i ":$PORT" &>/dev/null; then
    warn "端口 $PORT 已被占用，尝试终止已有进程..."
    lsof -ti ":$PORT" | xargs kill -9 2>/dev/null || true
    sleep 1
fi

# ---- 5. 构建前端 ----
log "构建 Vue 前端..."

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    log "安装前端依赖..."
    cd "$FRONTEND_DIR"
    npm install --silent 2>/dev/null
fi

cd "$FRONTEND_DIR"
if [ "$MODE" = "dev" ]; then
    log "开发模式跳过前端构建 (启动前端请运行: cd frontend-vue && npm run dev)"

    if [ ! -f "$FRONTEND_DIR/dist/index.html" ]; then
        warn "Vue dist 不存在，请运行: cd frontend-vue && npm run build"
    fi
else
    log "编译 Vue 前端..."
    npm run build --silent 2>&1
    log "前端构建完成"
fi

# ---- 6. 启动 ----
log "启动 Talk2Code 服务..."

if [ "$MODE" = "dev" ]; then
    # 开发模式：debug 输出
    export FLASK_ENV=development
    cd "$BACKEND_DIR"
    python app.py
elif [ "$MODE" = "prod" ]; then
    # 生产模式：使用 gunicorn
    if command -v gunicorn &>/dev/null; then
        cd "$BACKEND_DIR"
        gunicorn -w 2 -b "0.0.0.0:$PORT" app:app
    else
        warn "gunicorn 未安装，使用 Flask 内置服务器"
        cd "$BACKEND_DIR"
        python app.py
    fi
else
    err "未知模式: $MODE (可选: dev, prod)"
    exit 1
fi
