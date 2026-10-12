#!/bin/bash
#
# apply-pam-fixes.sh — 让 OMS 指纹在 KDE 锁屏 / GDM 登录 / sudo / polkit 下可靠工作
#
# 应用的修复（详细背景见仓库 README「锁屏 / 登录界面」一节）：
#   1) /etc/pam.d/kde            锁屏"密码服务"去掉 pam_fprintd：
#        防止与 kde-fingerprint 服务双通道争抢指纹设备
#        （症状：锁屏指纹提示出现但按下无效 / 需回车 / 输密码卡约 10 秒）
#   2) /etc/pam.d/kde-fingerprint 指纹通道去掉 pam_kwallet5：
#        指纹没有密码，其密码提示会阻塞认证完成
#        （症状：指纹匹配后需再按一次回车才解锁）
#   3) 重试次数统一为 3 次：
#        - /etc/pam.d/common-auth（sudo/polkit 等）：Ubuntu 默认 max-tries=1 -> 3
#        - /usr/share/pam-configs/fprintd：持久化（未来 pam-auth-update 重新生成时保持 3 次）
#        - /etc/pam.d/gdm-fingerprint：显式 3 次
#
# 幂等，可重复执行。需要 root：
#   sudo pam/apply-pam-fixes.sh
#
set -euo pipefail

if [ "$(id -u)" != "0" ]; then
    echo "请用 root 运行（sudo pam/apply-pam-fixes.sh）" >&2
    exit 1
fi

HERE=$(cd "$(dirname "$0")" && pwd)

echo "== 1) 安装锁屏 PAM 覆盖文件 =="
install -m 0644 "$HERE/kde"             /etc/pam.d/kde
install -m 0644 "$HERE/kde-fingerprint" /etc/pam.d/kde-fingerprint
echo "   -> /etc/pam.d/kde, /etc/pam.d/kde-fingerprint"

echo "== 2) 重试次数 -> 3 =="
if [ -f /usr/share/pam-configs/fprintd ]; then
    sed -i 's/max-tries=1/max-tries=3/' /usr/share/pam-configs/fprintd
    echo "   -> /usr/share/pam-configs/fprintd"
fi
if [ -f /etc/pam.d/common-auth ]; then
    sed -i 's/pam_fprintd\.so max-tries=1 timeout=10/pam_fprintd.so max-tries=3 timeout=10/' /etc/pam.d/common-auth
    echo "   -> /etc/pam.d/common-auth"
fi
if [ -f /etc/pam.d/gdm-fingerprint ]; then
    grep -q 'pam_fprintd\.so max-tries=3' /etc/pam.d/gdm-fingerprint || \
        sed -i 's|^\(auth[[:space:]]\+required[[:space:]]\+pam_fprintd\.so\)\( debug\)\?$|\1 max-tries=3|' /etc/pam.d/gdm-fingerprint
    echo "   -> /etc/pam.d/gdm-fingerprint"
fi

echo
echo "== 结果 =="
grep -H "pam_fprintd" /etc/pam.d/kde-fingerprint /etc/pam.d/common-auth 2>/dev/null | sed 's/^/  /'
echo "完成。无需重启，下次认证即生效。"
