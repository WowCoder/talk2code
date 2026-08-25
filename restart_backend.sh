#!/bin/bash
# 重启 talk2code 后端（干净环境启动，规避 TRAE 注入的 PYTHONHOME/PYTHONPATH）
VENV=/Users/huahao/Desktop/code/claudecode/talk2code/venv/bin/python
LOG=/Users/huahao/Desktop/code/claudecode/talk2code/backend/logs/server.log

OLD_PID=$(lsof -ti :5001 2>/dev/null)
if [ -n "$OLD_PID" ]; then
  kill "$OLD_PID" 2>/dev/null
  sleep 2
fi

nohup env -u PYTHONHOME -u PYTHONPATH "$VENV" -c "
import os, sys, subprocess
for k in ('PYTHONHOME', 'PYTHONPATH'):
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

sleep 8
NEW_PID=$(lsof -ti :5001 2>/dev/null)
if [ -n "$NEW_PID" ]; then
  echo "RESTARTED old=$OLD_PID new=$NEW_PID"
  curl -s -m 5 http://127.0.0.1:5001/api/health | head -c 120
  echo ""
else
  echo "FAILED: port 5001 not listening"
  tail -5 "$LOG"
fi
