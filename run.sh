#!/bin/bash
# 仓眼 · 启动包装脚本
#
# 为什么需要这个脚本而不是 systemd 里直接写 python3 monitor.py：
#   检测程序要开窗口显示画面，必须带齐 DISPLAY / XDG_RUNTIME_DIR / WAYLAND_DISPLAY
#   三个环境变量，否则窗口上不了屏、程序会退出。
#   而且上电时图形会话往往还没就绪，需要等 wayland socket 出现。

set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION_USER="$(id -un)"
UID_NUM="$(id -u)"

# 最多等 90 秒，直到 dwb 的 wayland socket 就绪（GDM 自动登录需要时间）
for _ in $(seq 1 90); do
    if [ -S "/run/user/${UID_NUM}/wayland-0" ]; then
        break
    fi
    sleep 1
done

if [ ! -S "/run/user/${UID_NUM}/wayland-0" ]; then
    echo "[run.sh] 警告：wayland-0 未出现，仍尝试启动（画面可能上不了屏）"
fi

export DISPLAY="${DISPLAY:-:0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/${UID_NUM}}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export PYTHONUNBUFFERED=1

cd "${APP_DIR}" || exit 1

echo "[run.sh] user=${SESSION_USER} uid=${UID_NUM} dir=${APP_DIR} $(date '+%F %T')"
exec python3 -u "${APP_DIR}/monitor.py"
