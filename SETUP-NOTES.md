# OMS 指纹在 Ubuntu 下的启用笔记

> 设备：MECHREVO WUJIE14 Pro 上的 **OMS/欧菲光指纹模块 `33a7:2388`**（固件 `ofilm_pc_000057`，bcdDevice 2.00）
> 方案完成日期：2026-10-10 ｜ 已打包为 deb 安装（含 GUI 管理器）

## 安装方式（已完成的改动）

**deb 包**：`dist/libfprint-2-omsmoc_1.94.100-1omsmoc4_amd64.deb`
（可复制到其他机器的共享目录）

```bash
sudo dpkg -i libfprint-2-omsmoc_1.94.100-1omsmoc4_amd64.deb
```

包内容：

| 路径 | 说明 |
|---|---|
| `/usr/local/lib/x86_64-linux-gnu/libfprint-2.so.*` | 打进 omsmoc 驱动的 libfprint 1.94.100（动态加载器优先于发行版库生效） |
| `/usr/bin/oms-fprint-manager` + `.desktop` | **GUI 管理器**（应用菜单搜"指纹"或"OMS"） |
| `/usr/sbin/oms-import` | 特权助手（GUI 使用，也可命令行）：`--list` / `--slot N=FINGER` / `--rename OLD=NEW` |
| `/etc/udev/rules.d/60-oms-fingerprint.rules` | 供电保持 + 用户访问（conffile，升级保留） |
| `/usr/share/doc/libfprint-2-omsmoc/` | README（安装后步骤）+ copyright |

安装后步骤（本机已完成）：

```bash
sudo pam-auth-update --enable fprintd     # 启用 PAM（登录/sudo 指纹，密码兜底）
sudo oms-import $USER --slot 0=left-index --slot 1=right-index   # 导入芯片已有模板
```

## GUI 功能（OMS 指纹管理器）

| 操作 | 说明 |
|---|---|
| **增**（新增指纹） | 选择手指 → 开始录入 → 按阶段提示按压/抬起（6 阶段进度条） |
| **删**（删除指纹） | 列表中选择删除（会同时删除芯片模板，有确认提示） |
| **改**（重命名） | 列表中选择重命名，更换手指归属（模板不动，无需重新录入） |
| **改**（芯片模板） | 「芯片模板…」查看槽位、关联/更换未关联的 Windows 时代模板（pkexec 授权） |
| 其他 | 支持重复录入检测提示（enroll-duplicate）、错误中文提示、刷新 |

> 技术实现：GTK4 + libadwaita（Python），指纹操作走 fprintd D-Bus（标准流程），
> 芯片模板/重命名通过 pkexec 调 `/usr/sbin/oms-import` 完成。

## 锁屏 / 登录界面修复（2026-10-12）

| 症状 | 修复 |
|---|---|
| 锁屏指纹按下无效 / 需回车 / 输密码卡约 10 秒 | `/etc/pam.d/kde` 覆盖：密码服务去掉 pam_fprintd（防止与 kde-fingerprint 双通道争抢设备） |
| 指纹匹配后需按一次回车才解锁 | `/etc/pam.d/kde-fingerprint` 覆盖：去掉阻塞认证的 pam_kwallet5（指纹无密码可取） |
| sudo / polkit 只有 1 次指纹机会 | max-tries 统一为 3（common-auth + pam-configs 持久化 + gdm-fingerprint 显式） |

- 重试规则：**按压未匹配**才会重试（每次认证最多 3 次）；**全程未按**则超时结束回落密码（锁屏 30s/次、sudo 与 polkit 10s/次）。
- 复现/迁移：配置文件存于仓库 `pam/`，可跑 `sudo pam/apply-pam-fixes.sh`；deb（omsmoc4 起）已内置。
- 排障方法存档：fprintd 加 `G_MESSAGES_DEBUG=all` drop-in、pam_fprintd 加 `debug` 参数、`pamtester <服务> <用户> authenticate` 分层测试（均已清理，需要时再加）。

## 源码与工具

- 驱动源码（社区逆向，LGPL-2.1）：`~/oms-fingerprint/`（BlueApple233/OMS-fingerprint-linux，commit fc0d55b）
- GUI 源码：`gui/oms-fprint-manager.py`（+ `gui/oms-fprint-manager.desktop`）
- **重新打包**：`deb/build-deb.sh`（自动按需重建 libfprint/oms-import，然后 `dpkg-deb --build`）
- 构建产物：`build/libfprint-build/`
- 硬件调试工具：`build/libfprint-build/examples/oms-smoke`
  - `oms-smoke info` / `list` / `verify 120 --slot 1` / `enroll 120` / `delete <slot>` / `clear`

## 常用命令

```bash
oms-fprint-manager                    # 打开 GUI
fprintd-list $(whoami)                # 查看已录入指纹
fprintd-verify -f any $(whoami)       # 验证任意指纹
sudo pam-auth-update --enable fprintd # 启用 PAM（已做）
sudo oms-import <user> --list         # 查看芯片槽位与关联（JSON）
sudo oms-import <user> --slot N=<手指> # 关联/更换槽位归属
sudo oms-import <user> --rename OLD=NEW # 重命名手指关联
```

## 设备注意事项 ⚠️

- **芯片模板是唯一不可再生数据**：GUI 删除 / `oms-smoke delete/clear` 前务必确认。
- 设备扛不住 USB autosuspend，必须保持 `power/control=on`（udev 规则已处理）。
- 不要手工发送不完整的 `USBC` 序列；日常使用 fprintd/GUI 不涉及。
- 固件卡死（bulk 超时）恢复：
  `python3 -c "import usb.core; usb.core.find(idVendor=0x33a7, idProduct=0x2388).reset()"`（需 python3-usb）
- Windows 里录入的模板 Linux/Windows 共用（匹配在芯片端完成）。

## 回退方法

```bash
sudo apt remove libfprint-2-omsmoc    # 移除库+GUI+udev规则+工具（fprintd 自动回到发行版 libfprint）
sudo pam-auth-update --disable fprintd    # 关闭 PAM 指纹（可选）
sudo rm -rf /var/lib/fprint/$USER   # 删除 fprintd 存储记录（可选）
```

## 适用性说明

- deb 面向 **Ubuntu 24.04+/26.04 amd64**（依赖名 `libglib2.0-0t64`、`libssl3t64`、`pkexec` 等为新版命名）。
- 任何带同款传感器（USB `33a7:2388`）的机器都可用；不同发行版依赖名可能不同，可 `--force-depends` 或改 control 依赖。
- 未来若上游 libfprint 合并了 omsmoc 驱动，直接 `apt remove` 本包即可回归系统原生支持。
