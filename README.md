# OMS 指纹模块 Linux 驱动 — Ubuntu 打包 & 图形管理器

让 **OMS/欧菲光 Match-on-Chip 指纹模块（USB `33a7:2388`）** 在 Linux 上可用的完整方案：

- 🔧 **驱动**：基于 [BlueApple233/OMS-fingerprint-linux](https://github.com/BlueApple233/OMS-fingerprint-linux) 逆向实现的 libfprint `omsmoc` 驱动（已真机验证）
- 📦 **一键 deb**：`libfprint-2-omsmoc` —— 驱动 + 图形管理器 + 工具，适用 Ubuntu 24.04+ / 26.04（amd64）
- 🖥️ **图形管理器**：录入 / 删除 / 重命名指纹、查看与关联芯片内已有模板（Windows 时期录入的也能直接复用）
- 🛠️ **oms-import 工具**：芯片模板 ↔ fprintd 存储映射管理（命令行，GUI 的后端）
- ⚡ **udev 规则**：保持设备供电（该芯片无法承受 USB autosuspend）+ 本地用户访问

> 适用机型（带同款模块即可）：机械革命 无界14 Pro（MECHREVO WUJIE14 Pro）、神舟/迈本等使用 `33a7:2388` 的机型。
> 上游驱动源码与逆向文档（RE 笔记、协议细节、umockdev 测试）见 [docs/upstream-README.md](docs/upstream-README.md)。

---

## 一、安装（推荐：deb）

从 [Releases](../../releases) 或 `dist/` 下载 `libfprint-2-omsmoc_*.deb`，然后：

```bash
sudo dpkg -i libfprint-2-omsmoc_1.94.100-1omsmoc3_amd64.deb
sudo pam-auth-update --enable fprintd     # 启用 PAM 指纹认证（登录/sudo，密码兜底保留）
```

打开 **「OMS 指纹管理器」**（应用菜单搜索"指纹"，或终端运行 `oms-fprint-manager`）：

1. **新增指纹** → 选手指 → 按提示按压/抬起（6 阶段）；
2. 芯片里已有旧模板（如 Windows 时期录入）→ 点「**芯片模板…**」关联到手指，**无需重新录入**；
3. 也可以随时**重命名**（更换归属）或**删除**。

### 安装包做了什么

| 路径 | 说明 |
|---|---|
| `/usr/local/lib/x86_64-linux-gnu/libfprint-2.so.*` | 打进 omsmoc 驱动的 libfprint 1.94.100（动态加载器优先于发行版库） |
| `/usr/bin/oms-fprint-manager` | 图形管理器（GTK4/libadwaita，中文界面） |
| `/usr/sbin/oms-import` | 特权助手（芯片模板列出/导入/重命名） |
| `/etc/udev/rules.d/60-oms-fingerprint.rules` | 供电保持 + 用户访问（conffile） |

**设计要点**：驱动库影子安装到 `/usr/local`，**不与发行版 `libfprint-2-2` 冲突**，系统升级不受影响；卸载后 `fprintd` 自动回退到发行版库。

---

## 二、图形管理器（增 / 删 / 改）

| 操作 | 说明 |
|---|---|
| **增** 新增指纹 | 选择手指 → 开始录入 → 中文阶段提示 + 进度条；重复录入芯片会查重提示 |
| **删** 删除指纹 | 列表内删除按钮（确认框），同时删除芯片内模板 ⚠️ 不可恢复 |
| **改** 重命名 | 更换手指归属，**模板不动、无需重新录入** |
| **改** 芯片模板 | 查看槽位（0–15）、关联未归属模板、更换已归属模板；读取/写入需一次授权 |
| 其他 | 设备状态显示、错误中文提示、一键刷新 |

---

## 三、锁屏 / 登录界面（KDE / GDM）与重试

本仓库的 deb 会随包安装以下 PAM 集成修复（真机排障所得）；手动安装时可运行 `sudo pam/apply-pam-fixes.sh`：

| 症状 | 原因 | 修复 |
|---|---|---|
| 锁屏指纹提示出现但按下无效 / 需回车 / 输密码卡约 10 秒 | KDE 锁屏并发运行 `kde-fingerprint` 与 `kde`（密码）两套 PAM 事务，`common-auth` 里的 pam_fprintd 使二者争抢设备 | `/etc/pam.d/kde` 覆盖文件：密码服务不再加载 pam_fprintd，指纹只走 KDE 官方通道 |
| 指纹匹配后仍需按一次回车才解锁 | `kde-fingerprint` 认证链里的 `pam_kwallet5` 在等密码提示（指纹没有密码可取），阻塞了事务完成 | `/etc/pam.d/kde-fingerprint` 覆盖文件：去掉该阻塞模块（钱包会话阶段不受影响） |
| sudo / polkit 授权框只有 1 次指纹机会 | Ubuntu 默认配置 `max-tries=1` | 统一为 **3 次**（common-auth + pam-configs 持久化 + GDM 显式 3 次） |

> 重试规则：**按了但没匹配上**才会重试（每次认证最多 3 次）；**完全没有按压**则超时结束并回落到密码（锁屏 30 秒/次、sudo 与 polkit 10 秒/次）。

---

## 四、命令行工具

```bash
# fprintd 标准命令
fprintd-list $(whoami)                 # 查看已录入指纹
fprintd-verify -f any $(whoami)        # 验证任意指纹
fprintd-enroll                         # 录入新指纹（终端交互）

# oms-import：芯片模板管理（需要 root）
sudo oms-import $USER --list                          # JSON：芯片槽位 + 关联
sudo oms-import $USER --slot 0=left-index --slot 1=right-thumb
sudo oms-import $USER --rename left-index=left-thumb  # 重命名（也可用数字 1..10）
```

手指名：`left-thumb / left-index / left-middle / left-ring / left-little / right-thumb / right-index / right-middle / right-ring / right-little`（或数字 `1..10`）。

---

## 五、从源码构建

在 Ubuntu 24.04+ / 26.04 上：

```bash
# 1) 构建依赖
sudo apt install meson ninja-build curl libglib2.0-dev libgusb-dev libusb-1.0-0-dev \
    libcairo2-dev libpixman-1-dev libnss3-dev libssl-dev libgudev-1.0-dev \
    gobject-introspection libgirepository1.0-dev umockdev libumockdev-dev python3-usb \
    python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 pkexec

# 2) 拉取 libfprint + omsmoc 驱动并构建（含无硬件回放测试）
driver/setup.sh --test

# 3) 重新打包 deb（会按需重建 libfprint/oms-import）
deb/build-deb.sh
# 产物：dist/libfprint-2-omsmoc_<版本>_amd64.deb
```

> 说明：`deb/build-deb.sh` 编译 `helper/oms-import.c` 需要 `pkg-config` 能找到 `libfprint-2`
> （即系统已安装本 deb，或安装了 libfprint 开发包）。

---

## 六、工作原理（简述）

- **匹配在芯片端完成**（Match-on-Chip）：芯片闪存最多存 16 枚模板，主机不接触指纹图像/特征；
- 协议为明文 `USBC` 传输头 + `EF01` 帧（批量端点），命令：`0x34` 序列号 / `0x1d,0x1f` 模板表 / `0x32` 识别·录入 / `0x31` 写入 / `0x0c` 删除 / `0x30` 取消；
- **模板 ↔ 用户/手指** 的关联存放在 fprintd 存储（`/var/lib/fprint/<用户>/omsmoc/<设备ID>/<手指编号>`），本仓库的 `oms-import` 负责读写这份关联；
- 匹配阈值/算法均由芯片固件决定，Linux 端只负责协议与状态机。

---

## 七、常见问题

<details>
<summary><b>设备卡死（suspend/resume 后报错、bulk 超时）</b></summary>

该芯片扛不住 USB autosuspend（本包已装 udev 规则保持 `power/control=on`）。
若已卡死，用 USB 端口复位恢复：

```bash
python3 -c "import usb.core; usb.core.find(idVendor=0x33a7, idProduct=0x2388).reset()"
```
（需要 `python3-usb`；若枚举成 `1234:abce` 引导模式，需厂商烧录工具恢复，芯片内模板会丢失）
</details>

<details>
<summary><b>删除/清空的风险</b></summary>

模板是芯片内唯一不可再生数据：删除后只能重新录入。GUI 删除按钮与 `oms-import` 都会真实操作芯片，请确认后再执行。
</details>

<details>
<summary><b>Windows 录入的指纹能用吗？</b></summary>

能。模板存在芯片里、跨系统共用；用「芯片模板…」把槽位关联到手指即可（`sudo oms-import --list` 查看槽位）。
</details>

<details>
<summary><b>卸载 / 回退</b></summary>

```bash
sudo apt remove libfprint-2-omsmoc        # 移除库+GUI+规则+工具，fprintd 自动回退发行版库
sudo pam-auth-update --disable fprintd    # 关闭 PAM 指纹（可选）
sudo rm -rf /var/lib/fprint/$USER         # 清理 fprintd 存储记录（可选）
```
</details>

---

## 八、仓库结构

```
├── README.md                 # 本文件
├── docs/upstream-README.md   # 上游项目原始 README（驱动源码/逆向文档入口）
├── SETUP-NOTES.md            # 本方案的完整安装笔记（含回退步骤）
├── driver/                   # 上游驱动源码、测试、上游补丁（原样保留）
├── aur/                      # 上游 Arch 包（原样保留）
├── gui/                      # 图形管理器（GTK4/libadwaita，Python）
├── helper/oms-import.c       # 特权助手源码（芯片模板管理）
├── pam/                      # PAM 集成修复（锁屏/登录/重试；含 apply-pam-fixes.sh）
├── deb/build-deb.sh          # deb 打包脚本
├── dist/                     # 预构建 deb（Releases 同款）
└── LICENSE                   # LGPL-2.1
```

---

## 九、来源与致谢

- **驱动逆向实现**：[BlueApple233/OMS-fingerprint-linux](https://github.com/BlueApple233/OMS-fingerprint-linux)（LGPL-2.1）——`omsmoc` 驱动、协议分析、umockdev 回放测试均来自该项目；
- 本仓库在其基础上增加：Ubuntu deb 打包、图形管理器、`oms-import` 模板管理工具、文档；
- 上游尚未合并入 libfprint 主线：本方案采用「影子安装」方式让 fprintd 用上该驱动，待上游合并后可平滑移除。

**License**：[LGPL-2.1-or-later](LICENSE)（与上游一致）。
