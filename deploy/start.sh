#!/bin/bash
# 仓眼 · 本地启动脚本（调试用）
# 用法：bash start.sh
cd /home/dwb/cangyan || exit 1

# 精确匹配，避免 pkill 误杀包含 "monitor.py" 的其它命令
for p in $(pgrep -f '^python3 -u monitor.py$'); do
    kill "$p" 2>/dev/null
done
sleep 1

: > run.log
QT_QPA_PLATFORM=offscreen setsid nohup python3 -u monitor.py >> run.log 2>&1 < /dev/null &
sleep 3
echo "已启动，PID=$(pgrep -f '^python3 -u monitor.py$' | tr '\n' ' ')"
