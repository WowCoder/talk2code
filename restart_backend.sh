#!/bin/bash
# 重启 talk2code 后端（干净环境启动）
# 1. 规避 TRAE 注入的 PYTHONHOME/PYTHONPATH
# 2. 清除代理变量：LLM 端点（apihub.agnes-ai.com）可直连，无需本地代理。
#    本机代理端口会漂移（如 51322 → 61412），进程启动后继承的旧代理失效会导致
#    所有 LLM 请求 ProxyError: Connection refused，需求必然跑失败。
# 3. 只识别 LISTEN 状态的进程（避免把前端的 ESTABLISHED 连接误判为旧后端），
#    并等待端口真正释放后再启动新进程（优雅退出约需 5s）。
VENV=/Users/huahao/Desktop/code/claudecode/talk2code/venv/bin/python
LOG=/Users/huahao/Desktop/code/claudecode/talk2code/backend/logs/server.log

list_listener() { lsof -tiTCP:5001 -sTCP:LISTEN 2>/dev/null | head -1; }

OLD_PID=$(list_listener)
if [ -n "$OLD_PID" ]; then
  kill "$OLD_PID" 2>/dev/null
  # 等待端口释放，最多 15 秒
  for _ in $(seq 1 15); do
    [ -z "$(list_listener)" ] && break
    sleep 1
  done
  # 仍未释放则强杀
  if [ -n "$(list_listener)" ]; then
    kill -9 "$OLD_PID" 2>/dev/null
    sleep 2
  fi
fi

nohup env -u PYTHONHOME -u PYTHONPATH \
         -u HTTP_PROXY -u HTTPS_PROXY -u http_proxy -u https_proxy \
         -u ALL_PROXY -u all_proxy "$VENV" -c "
import os, sys, subprocess
for k in ('PYTHONHOME', 'PYTHONPATH', 'HTTP_PROXY', 'HTTPS_PROXY',
          'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
    os.environ.pop(k, None)
os.setsid()
log_fd = os.open('$LOG', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(log_fd, 1); os.dup2(log_fd, 2)
devnull = os.open('/dev/null', os.O_RDONLY); os.dup2(devnull, 0)
os.chdir('/Users/huahao/Desktop/code/claudecode/talk2code/backend')
sys.path.insert(0, '/Users/huahao/Desktop/code/claudecode/talk2code/backend')
subprocess.Popen(['$VENV', 'app.py'])
" > /dev/null 2>&1 &
disown

# 等待新进程就绪（最多 25 秒）
for _ in $(seq 1 25); do
  sleep 1
  if curl -s -m 2 http://127.0.0.1:5001/api/health > /dev/null 2>&1; then
    break
  fi
done

NEW_PID=$(list_listener)
if [ -n "$NEW_PID" ]; then
  echo "RESTARTED old=$OLD_PID new=$NEW_PID"
  curl -s -m 5 http://127.0.0.1:5001/api/health | head -c 120
  echo ""
else
  echo "FAILED: port 5001 not listening"
  tail -5 "$LOG"
fi
