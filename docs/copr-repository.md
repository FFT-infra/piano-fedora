# COPR 软件源

设备装好 Fedora 后，内核、固件、相机、触控、传感器、电源模式与图形栈全部通过 COPR `xinyang26/piano-fedora`（`fedora-45-aarch64`）在线升级，不必手工下载安装 RPM。

## 默认维护的 19 个包

- **矩阵包（14 个）**：`alsa-xiaomi-piano`、`iio-sensor-proxy-ssc`、`kernel-piano`、`libssc`、`piano-bluez`、`piano-camera`、`piano-fedora-configs`、`piano-fingerprint`、`piano-libfprint`、`piano-libwacom`、`piano-pipewire-gstreamer`、`sm8750-video-accel`、`xiaomi-piano-firmware`、`xiaomi-piano-thp`
- **设备运行时（3 个）**：`piano-runtime`、`piano-crosvm`、`librevm`
- **图形栈（2 组）**：`mesa-piano-*`、`ffmpeg-piano`

### 启用与日常操作

系统默认已预置 `/etc/yum.repos.d/piano-fedora-copr.repo`，装机后直接更新：

```sh
sudo dnf makecache
sudo dnf upgrade --refresh
```

日常检查与回滚：

```sh
dnf repolist --enabled | grep piano                        # 确认源启用
rpm -q gpg-pubkey --qf '%{SUMMARY}\n' | grep -i copr       # 确认公钥
dnf list --showduplicates kernel-piano                     # 查询内核可用版本
cat /var/lib/piano/kernel-esp/*.json                       # 查看上次内核 ESP 更新审计
sudo dnf downgrade --repo fedora piano-fedora-configs      # 单个包回退 Fedora 原版
```

## 内核在线升级与回滚细节

`kernel-piano` 故意不声明 `Provides: kernel` / `kernel-core`，使其按普通包原地替换（若作为 installonly 包，同一编译期模块目录会产生文件冲突）。

事务末尾的 `%posttrans` 会运行 `depmod -a <release>`、用新内核重建 initramfs，并同步 `/usr/lib/piano/board.dtb` 与新载荷到 ESP：

- **BLS 布局**（带 `systemd-boot`）：写 `/linux/<release>/` 与 `/loader/entries/linux-<release>.conf`，仅保留当前、更新前与正在运行的内核条目，不动非 piano 启动项。
- **UKI 布局**：重打包含 `.linux` / `.initrd` / `.dtb` / `.cmdline` / `.osrel` / `.uname` 的 PE 文件（先写 `.new` 并核对六个节区，再备份旧文件为 `.bak` 后原子替换 `sunuefi.efi`）。

写 ESP 前任一步出错即中止且不修改 ESP。

`kernel-piano` 7.3.3-12 之前的板级 DTB 没有 SoC 降频映射，内核过热时不降频。系统还跑在这种设备树上时（升级后没重启、ESP 没写成、从上一代启动项启动），`piano-thermal-guard` 把 CPU 与 GPU 上限压在省电档，`tuned.service` 不启动，电源模式开关不可用；重启进新设备树后恢复。

因为旧模块目录在 RPM 升级时已被移除，`dnf history undo` 或仅还原 `.bak` 都不足以让旧内核完整工作，回滚须先降级包：

```sh
sudo dnf downgrade kernel-piano
# 若 ESP 为单 UKI 布局：
sudo cp /boot/efi/EFI/Linux/sunuefi.efi.bak /boot/efi/EFI/Linux/sunuefi.efi
```

## 图形栈改名规则

为了避免 Fedora 官方 `mesa` / `ffmpeg-free` 随时升小版本把补丁包覆盖回去（又不想锁死单向 `Epoch: 1`），图形栈效仿 RPM Fusion 对齐命名：

- `mesa*` → `mesa-piano*`（含 `mesa-piano-libGL`、`mesa-piano-libEGL`、`mesa-piano-libgbm`、`mesa-piano-dri-drivers`、`mesa-piano-vulkan-drivers`、`mesa-piano-filesystem`）
- `ffmpeg-free` / `libavcodec-free` 等 8 个 → `ffmpeg-piano` / `libavcodec-piano` 等

本地包均带 `Provides`/`Obsoletes`/`Conflicts` 接管官方包请求。如果设备装过未改名前的官方图形栈，执行一次切换：

```sh
sudo dnf install --allowerasing mesa-piano-vulkan-drivers mesa-piano-libGL \
  mesa-piano-libEGL ffmpeg-piano
```

## 可选的 extras 软件源

`xinyang26/piano-fedora-extras` 提供打过桌面补丁的 opt-in 包，默认关闭。不启用它时，桌面保持 Fedora 原版包。

| 包名 | 说明 |
| --- | --- |
| `gnome-settings-daemon-piano` | 改名重建，带原厂自动亮度曲线、键盘背光热插拔与启动锁屏同步补丁 |
| `kwin` | `Release` 带 `.piano1`，修复退出 HDR/广色域后背光不回落 |
| `gtk3` | `Release` 带 `.piano1`，修复 X11 应用下的 XI2 手写笔工具原子查询 |
| `mutter` | `Release` 带 `.piano1`，增加空 KMS gamma LUT 与取消触摸 grab 守卫 |
| `gdm` | `Release` 带 `.piano1`，登录前等待 KMS connector 就绪（仅 GNOME） |
| `gnome-control-center` | `Release` 带 `.piano1`，指纹录入前等待 `fprintd` 验证动作结束（仅 GNOME） |
| `piano-gnome-extras` | noarch meta，拉入 GSD/GTK3/mutter 并写入 `20-piano-ambient` 开启自动亮度 |
| `piano-kde-extras` | noarch meta，拉入 KWin 与 GTK3 |

启用并安装（`priority=1` 确保同名包优先于 Fedora 官方更高小版本；升级原有同名包后再装 meta）：

```sh
sudo dnf copr enable -y xinyang26/piano-fedora-extras
sudo dnf config-manager setopt \
  'copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras'.priority=1
sudo dnf upgrade kwin gtk3 mutter gdm gnome-control-center
sudo dnf install --allowerasing piano-gnome-extras   # KDE 用户改为 piano-kde-extras
sudo dconf update
```

注意设了 `priority=1` 后，Fedora 官方对 `kwin`/`gtk3`/`mutter`/`gdm`/`gnome-control-center` 的上游更新会被遮住，需等 extras 按新 SRPM 重建。卸载回退：

```sh
sudo dnf remove piano-gnome-extras piano-kde-extras
sudo dnf config-manager setopt \
  'copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras'.enabled=0
sudo dconf update
sudo dnf distro-sync --repo fedora
# 若要还原原版 GSD：
sudo dnf swap gnome-settings-daemon-piano gnome-settings-daemon --repo fedora
```

## 维护者发布与验证

Fedora COPR 公开实例未开启二进制 RPM 直接上传（无 `DIRECT_RPM_UPLOAD`），因此 CI 矩阵同时生成 SRPM，由 `publish-copr.yml` 提交到 COPR 在 `fedora-45-aarch64` chroot 内从源码重建（缺 chroot 时先自动 `copr-cli modify --chroot` 补齐）。因此 COPR 包与 bundle 内的 RPM 版本一致但并非逐字节同一二进制。

触发发布先跑一次 dry-run（`execute=false`）校验 run 状态、清单摘要及 `-piano` 命名，通过再置为 `true`：

```sh
gh workflow run publish-copr.yml --ref main \
  -f rpms_run_id=<host-check run> -f kernel_run_id=<kernel run> \
  -f mesa_run_id=<build-mesa run> -f ffmpeg_run_id=<host-check run> \
  -f execute=false
```

在平板上验证仓库状态：`python3 scripts/verify-copr-device.py`（检查 19 个包齐全、图形栈已切 `mesa-piano-*`/`ffmpeg-piano`、无 `exclude=kernel-piano`），加 `--install-test` 确认 `dnf upgrade --assumeno` 可选到内核，加 `--extras` 检查 extras 源包状态。
