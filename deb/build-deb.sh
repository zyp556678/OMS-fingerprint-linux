#!/bin/bash
#
# build-deb.sh — package the patched libfprint + omsmoc driver as a .deb
#
# The package ships:
#   /usr/local/lib/x86_64-linux-gnu/libfprint-2.so.2.0.0   (patched libfprint)
#   /usr/sbin/oms-import                                   (template import tool)
#   /etc/udev/rules.d/60-oms-fingerprint.rules             (power + access rule)
#   /usr/share/doc/libfprint-2-omsmoc/                     (docs, copyright)
#
# /usr/local shadows the distribution libfprint for dynamic-loader consumers
# (fprintd etc.) without conflicting with any distribution package, so it
# survives distribution upgrades and can be removed cleanly.
#
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)

BUILD_DIR="$REPO/build/libfprint-build"
LIBFPRINT_VER=1.94.100
PKGVER="${LIBFPRINT_VER}-1omsmoc3"
ARCH=$(dpkg --print-architecture)
PKG="$HERE/pkgroot"
OUT_DIR="$REPO/dist"
DEB="$OUT_DIR/libfprint-2-omsmoc_${PKGVER}_${ARCH}.deb"

LIB="$BUILD_DIR/libfprint/libfprint-2.so.2.0.0"
IMPORT="$REPO/helper/oms-import"

# ---------------------------------------------------------------- sources
if [ ! -f "$LIB" ]; then
    echo "==> libfprint is not built yet; running driver/setup.sh"
    ( cd "$REPO" && ./driver/setup.sh )
fi

if [ ! -x "$IMPORT" ]; then
    echo "==> building oms-import"
    ( cd "$REPO/helper" \
      && gcc -O2 -Wall -o oms-import oms-import.c \
             $(pkg-config --cflags --libs libfprint-2 glib-2.0) )
fi

# ---------------------------------------------------------------- staging
echo "==> staging package tree in $PKG"
rm -rf "$PKG"
mkdir -p "$PKG/DEBIAN" \
         "$PKG/usr/local/lib/x86_64-linux-gnu" \
         "$PKG/usr/bin" \
         "$PKG/usr/sbin" \
         "$PKG/usr/share/applications" \
         "$PKG/etc/udev/rules.d" \
         "$PKG/usr/share/doc/libfprint-2-omsmoc"

install -m 0644 "$LIB" "$PKG/usr/local/lib/x86_64-linux-gnu/libfprint-2.so.2.0.0"
ln -s libfprint-2.so.2.0.0 "$PKG/usr/local/lib/x86_64-linux-gnu/libfprint-2.so.2"
ln -s libfprint-2.so.2 "$PKG/usr/local/lib/x86_64-linux-gnu/libfprint-2.so"
install -m 0755 "$IMPORT" "$PKG/usr/sbin/oms-import"
install -m 0755 "$REPO/gui/oms-fprint-manager.py" "$PKG/usr/bin/oms-fprint-manager"
install -m 0644 "$REPO/gui/oms-fprint-manager.desktop" \
        "$PKG/usr/share/applications/oms-fprint-manager.desktop"

cat > "$PKG/etc/udev/rules.d/60-oms-fingerprint.rules" <<'EOF'
# OMS fingerprint module 33a7:2388: keep powered, disable persist, allow local user access
ACTION=="add|change", SUBSYSTEM=="usb", ATTR{idVendor}=="33a7", ATTR{idProduct}=="2388", TAG+="uaccess", ATTR{power/control}="on", ATTR{power/persist}="0"
EOF

# ---------------------------------------------------------------- docs
{
    cat <<'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: OMS-fingerprint-linux
Source: https://github.com/BlueApple233/OMS-fingerprint-linux

Files: *
Copyright: libfprint contributors
           omsmoc driver: BlueApple233 and contributors
License: LGPL-2.1-or-later
EOF
    sed 's/^$/./; s/^/ /' "$REPO/LICENSE"
} > "$PKG/usr/share/doc/libfprint-2-omsmoc/copyright"

cat > "$PKG/usr/share/doc/libfprint-2-omsmoc/README.Debian" <<'EOF'
libfprint-2-omsmoc — OMS Match-on-Chip 指纹驱动（33a7:2388）
================================================================

本包做了什么
------------
* 把带 "omsmoc" 驱动的 libfprint 1.94.100 安装到
  /usr/local/lib/x86_64-linux-gnu/
  动态加载器会优先使用 /usr/local，因此 fprintd 等程序自动使用它；
  发行版自带的 libfprint 包不受影响，系统升级也不会覆盖本包。
* 安装 udev 规则（设备保持供电 + 允许本地用户访问）：
  /etc/udev/rules.d/60-oms-fingerprint.rules
  （该芯片扛不住 USB autosuspend，必须保持 power/control=on）
* 安装图形界面「OMS 指纹管理器」（应用菜单里搜索“指纹”即可找到，
  或命令行运行 oms-fprint-manager）：
    - 查看/录入/删除指纹（增、删）
    - 重命名指纹关联（改）
    - 查看芯片模板槽位、关联/更换归属（改，不重新录入）
* 安装 /usr/sbin/oms-import：GUI 用的特权助手（也可以命令行使用），
  用于把芯片里已存在的模板（例如 Windows 时期录入的指纹）导入
  fprintd 存储，从而无需重新录入。

安装后的典型步骤
----------------
1. 启用 PAM 指纹认证：
     sudo pam-auth-update --enable fprintd
2. 打开「OMS 指纹管理器」：
     - 新增指纹：选择手指 → 开始录入 → 按提示按压/抬起
     - 删除：列表中选择「删除」按钮
     - 改名/换手指：列表中选择「重命名」按钮
     - 芯片已有模板（Windows 录入的）：点「芯片模板…」关联到手指
3. 命令行等价操作：
     fprintd-enroll / fprintd-list / fprintd-verify -f any
     sudo oms-import $USER --slot 0=left-index --slot 1=right-thumb
     sudo oms-import $USER --rename 2=7
     sudo oms-import $USER --list

注意事项
--------
* 芯片模板是唯一不可再生数据，删除（GUI/fprintd/oms-smoke clear）
  前请确认；删除后需要重新录入。
* 设备卡死（suspend/resume 后 bulk 超时）可通过 USB 端口复位恢复：
     python3 -c "import usb.core; usb.core.find(idVendor=0x33a7, idProduct=0x2388).reset()"
  （需要 python3-usb，即 Recommends 里的包）
* 卸载：sudo apt remove libfprint-2-omsmoc
  卸载后 fprintd 自动回到发行版 libfprint（指纹功能消失，系统其余部分不变）。

来源
----
驱动源码：https://github.com/BlueApple233/OMS-fingerprint-linux (LGPL-2.1)
打包脚本：见源码目录 deb/build-deb.sh
EOF

# ---------------------------------------------------------------- scripts
cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e

if command -v ldconfig >/dev/null 2>&1; then
    ldconfig
fi

if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules 2>/dev/null || true
    udevadm trigger --action=change --subsystem-match=usb \
        --attr-match=idVendor=33a7 --attr-match=idProduct=2388 2>/dev/null || true
fi

# Make a running daemon pick up the newly installed library right away.
if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart fprintd.service 2>/dev/null || true
fi

exit 0
EOF

cat > "$PKG/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e

if command -v ldconfig >/dev/null 2>&1; then
    ldconfig
fi

# Stop using the (now removed) library immediately, fall back to /usr/lib.
if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart fprintd.service 2>/dev/null || true
fi

exit 0
EOF

chmod 0755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/postrm"
echo "/etc/udev/rules.d/60-oms-fingerprint.rules" > "$PKG/DEBIAN/conffiles"

# ---------------------------------------------------------------- control
INSTALLED_SIZE=$(du -sk "$PKG" | cut -f1)

cat > "$PKG/DEBIAN/control" <<EOF
Package: libfprint-2-omsmoc
Version: $PKGVER
Architecture: $ARCH
Maintainer: zyp556678 <taitanyunluo2@126.com>
Installed-Size: $INSTALLED_SIZE
Depends: libc6 (>= 2.34), libglib2.0-0t64, libgusb2a, libgudev-1.0-0, libusb-1.0-0, libpixman-1-0, libssl3t64, python3, python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, pkexec
Recommends: fprintd, python3-usb
Section: libs
Priority: optional
Description: libfprint with the OMS match-on-chip fingerprint driver (33a7:2388)
 This package ships libfprint $LIBFPRINT_VER built with the "omsmoc"
 driver for the OMS/OFilm match-on-chip fingerprint reader
 (USB 33a7:2388, e.g. in the MECHREVO WUJIE14 Pro).
 .
 The library is installed into /usr/local, where the dynamic loader
 prefers it over the distribution libfprint. The distribution
 packages are left untouched and system upgrades keep working.
 .
 Also installs a udev rule (the reader cannot survive USB autosuspend),
 the "OMS Fingerprint Manager" GUI (list/enroll/delete/rename
 fingerprints and re-map on-chip templates) and oms-import(8), its
 privileged helper.
EOF

# ---------------------------------------------------------------- build
mkdir -p "$OUT_DIR"
dpkg-deb --root-owner-group --build "$PKG" "$DEB"

echo
echo "==> built: $DEB"
echo
dpkg-deb --info "$DEB" | sed -n '1,20p'
echo
dpkg-deb --contents "$DEB" | awk '{print $1, $6, $7, $8}'
