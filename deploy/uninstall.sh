#!/bin/bash
# 仓眼 · 取消开机自启（恢复成需要手工启动）
set -uo pipefail

sudo systemctl stop cangyan.service 2>/dev/null || true
sudo systemctl disable cangyan.service 2>/dev/null || true
sudo rm -f /etc/systemd/system/cangyan.service
sudo systemctl daemon-reload
echo "已取消 cangyan 自启。GDM 自动登录未改动，如需关闭请编辑 /etc/gdm3/custom.conf。"
