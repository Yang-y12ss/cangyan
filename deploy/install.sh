#!/bin/bash
# 仓眼 · 开机自启安装脚本（在板子上用 sudo 运行）
#
#   sudo bash deploy/install.sh
#
# 做四件事：
#   1. 把 cangyan.service 装到 /etc/systemd/system/，按当前用户和目录自动改写路径
#   2. 打开 GDM 自动登录（否则上电停在登录界面，服务等不到图形会话）
#   3. daemon-reload + enable + restart
#   4. 打印自检结果

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# 兼容两种用法：普通用户直接跑（脚本内部用 sudo），或 root 用 sudo bash 跑
RUN_USER="${SUDO_USER:-$(id -un)}"
RUN_UID="$(id -u "${RUN_USER}")"
UNIT_SRC="${APP_DIR}/deploy/cangyan.service"
UNIT_DST="/etc/systemd/system/cangyan.service"

if [ ! -f "${UNIT_SRC}" ]; then
    echo "找不到 ${UNIT_SRC}"
    exit 1
fi

echo "==> 应用目录：${APP_DIR}"
echo "==> 运行用户：${RUN_USER} (uid=${RUN_UID})"

# ---------- 1. 安装 unit ----------
sed -e "s#^User=.*#User=${RUN_USER}#" \
    -e "s#^Group=.*#Group=${RUN_USER}#" \
    -e "s#/home/dwb/cangyan#${APP_DIR}#g" \
    "${UNIT_SRC}" | sudo tee "${UNIT_DST}" > /dev/null
chmod +x "${APP_DIR}/run.sh" "${APP_DIR}/monitor.py"
echo "==> 已写入 ${UNIT_DST}"

# ---------- 2. GDM 自动登录 ----------
GDM_CONF="/etc/gdm3/custom.conf"
if [ -f "${GDM_CONF}" ]; then
    sudo cp -n "${GDM_CONF}" "${GDM_CONF}.bak.$(date +%s)" || true
    sudo python3 - "${GDM_CONF}" "${RUN_USER}" <<'PY'
import re, sys
path, user = sys.argv[1], sys.argv[2]
s = open(path, encoding="utf-8").read()
if "[daemon]" not in s:
    s += "\n[daemon]\n"
s = re.sub(r"^#?\s*AutomaticLoginEnable\s*=.*$", "", s, flags=re.M)
s = re.sub(r"^#?\s*AutomaticLogin\s*=.*$", "", s, flags=re.M)
s = s.replace("[daemon]", "[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin=%s" % user, 1)
s = re.sub(r"\n{3,}", "\n\n", s)
open(path, "w", encoding="utf-8").write(s)
print("==> 已开启 GDM 自动登录：%s" % user)
PY
else
    echo "!! 没有 ${GDM_CONF}，跳过自动登录设置（需要手工让板子开机直接进桌面）"
fi

# ---------- 3. 启用服务 ----------
sudo systemctl daemon-reload
sudo systemctl enable cangyan.service
sudo systemctl restart cangyan.service
sleep 2

# ---------- 4. 自检 ----------
echo
echo "==> 服务状态"
sudo systemctl --no-pager --lines=15 status cangyan.service || true
echo
echo "==> 接口自检"
curl -s --max-time 3 "http://127.0.0.1:8080/state" || echo "(还没起来，看上一步日志)"
echo
echo "完成。常用命令："
echo "  journalctl -u cangyan -f        看实时日志"
echo "  sudo systemctl restart cangyan  重启"
echo "  sudo bash deploy/uninstall.sh   取消自启"
