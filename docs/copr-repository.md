# COPR 软件源

设备装好 Fedora 后，配置、固件、相机、触控、传感器、VA-API、调度器和内核都可以通过 COPR 升级，不必每次手动下载 RPM 再 `rpm -Uvh`。

**当前状态：15 个矩阵包、3 个设备运行时包，加上 Mesa 与 FFmpeg 两组图形栈，共 20 个包可以 `dnf upgrade` 升级，内核在内。** 项目是 `xinyang26/piano-fedora`，chroot 跟 `product.json` 的 releasever 走，现在是 `fedora-45-aarch64`。项目存在但缺这个 chroot 时，发布脚本会先 `copr-cli modify --chroot` 补上，否则 `copr-cli build` 会被 COPR 拒掉。仓库里有全部 20 个包：

矩阵包：`alsa-xiaomi-piano`、`iio-sensor-proxy-ssc`、`kernel-piano`、`libssc`、`piano-bluez`、`piano-camera`、`piano-fedora-configs`、`piano-fingerprint`、`piano-libfprint`、`piano-libwacom`、`piano-pipewire-gstreamer`、`sm8750-video-accel`、`uperf-linux`、`xiaomi-piano-firmware`、`xiaomi-piano-thp`。

运行时包：`piano-runtime`、`piano-crosvm`、`librevm`。

图形栈：`mesa-piano-*` 与 `ffmpeg-piano`。

核对方法见下面的[启用仓库](#启用仓库)一节，或在设备上跑 `python3 scripts/verify-copr-device.py`。

**内核怎么升级。** [SunUEFI](https://github.com/slwyts/Project-SunUEFI) 固件从 ESP 的 `/EFI/Linux/sunuefi.efi` 加载 Linux，`PianoProductCore.c` 用 `PianoApplicationRunFile` 直接打开这个文件，没有编译期哈希或 Android boot v2 解析。用户态 `dnf upgrade` 只改 `/usr/lib/modules`，两者本来脱钩。`kernel-piano` 的 `%posttrans` 把这条链接上。事务收尾时，`%posttrans` 先跑 `depmod -a <release>`，再用刚装上的内核重建 initramfs。最后用 `systemd-ukify` 把新内核、新 initramfs、ESP 里现有 UKI 的 `.dtb` 与 `.cmdline`、新的 `.osrel` 与 `.uname` 组装成新的 `/EFI/Linux/sunuefi.efi`。重启之后固件读到的是新内核。

不需要重编固件。固件只按固定路径加载一个 PE 文件，开机校验交给 `systemd-stub` 与内核。换内核不改固件，也不改 ESP 上的路径名。

`.dtb` 与 `.cmdline` 沿用 ESP 里现有 UKI 的节区，不是内核包里的 `/boot/dtb/qcom/sm8750-xiaomi-piano.dtb`。板级 overlay 已经在打包 bundle 时合成进那份 DTB。在线升级只复用现有 DTB，换 overlay 要换整个 bundle。

**失败边界。** 任何一步失败，旧 UKI 保持原样，设备继续用上一个内核启动。写入顺序是：先写 `.sunuefi.efi.new` 并 fsync，重新读出六个语义节区与预期比对；通过后把旧文件复制成 `sunuefi.efi.bak`，最后用 `os.replace` 换上去。PE 时间戳每次不同，判断文件有没有变只看 `.linux`、`.initrd`、`.dtb`、`.cmdline`、`.osrel`、`.uname` 六段内容。

**运行与验证记录。** UKI 升级由 `%posttrans` 重建 initramfs，写出 `sunuefi.efi.bak` 并原子替换 `sunuefi.efi`；重启后进入桌面，`uname -r` 与 UKI 内部 `.uname` 严格一致。升级审计信息同步写入 `/var/lib/piano/kernel-esp/<release>.json`。

挂起与唤醒测试中系统可正常恢复会话，`systemctl --failed` 保持为空。挂起路径打出 4 条 `qdss already disabled/unprepared` 时钟警告（`amba_pm_runtime_suspend` 重复 disable coresight qdss），属于无害内核日志，不影响唤醒与使用。

manifest 的名字带 NVRA，`%posttrans` 读自己那一版并把文件名传给 `piano-kernel-esp-update`；盘上留下多份时，工具不猜用哪一份。

`kernel-piano` 不提供 `kernel` / `kernel-core` 这类名字。DNF 的 installonlypkgs 按名字或 provides 匹配，提供它们会把新的 RPM Release 当成“再装一份内核”。模块树目录只由编译期 release 决定（`7.3.3-piano-sunuefi-cachy-optimize`），两个 Release 指向同一批文件，rpm 在安装阶段报 `file ... conflicts with file from package` 并中止整个事务。去掉这些 provides 后，`dnf upgrade` 按普通包替换本包。

已经装好但起不来的内核没有自动回滚。上游没有 boot counter（`PianoNvJournal` 是现成的双槽 NV journal，只是没有人往里写尝试计数），回滚只能手工做：

```bash
# 恢复上一个 UKI，重启即回到旧内核
sudo cp /boot/efi/EFI/Linux/sunuefi.efi.bak /boot/efi/EFI/Linux/sunuefi.efi
```

`dnf history undo` 只回滚 RPM，不动 ESP，所以单跑它不会让设备回到旧内核。内核按普通包升级，旧模块树在事务里被新包替换，`.bak` 里的旧内核没有可用的模块树。回滚要先 `dnf downgrade kernel-piano` 把旧模块树装回来，再把 `.bak` 复制回 ESP 重打。

## 为什么是 COPR 重建，而不是上传二进制

Fedora 官方 COPR 实例没有启用二进制 RPM 上传。运行中实例的 API 描述里没有 `/build/create/rpm-upload`，只有 `/build/create/upload`（SRPM）。源码里这条路由由 `DIRECT_RPM_UPLOAD` 控制，默认关闭，公开实例没打开。`copr-cli` 2.4 也没有 `uploadrpm` 子命令。

发布链因此改成：矩阵构建同时产出 SRPM，COPR 在干净的 `fedora-45-aarch64` chroot 里从 SRPM 重建。仓库因此能从源码复现，不会留下一堆来路不明的二进制。

## 发布流程（维护者）

发布走 `publish-copr.yml`，需要仓库 secrets `COPR_LOGIN` 与 `COPR_TOKEN`。先在 `main` 上跑一次带 `build_piano_rpms=true` 的 `host-check`，拿到 `piano-rpms` artifact；图形栈走 `build-mesa.yml` 与 `build-ffmpeg.yml`。再用这些 run id 触发 `publish-copr.yml`：

```bash
gh workflow run publish-copr.yml --ref main \
  -f rpms_run_id=<host-check run> -f kernel_run_id=<同一个内核 run> \
  -f mesa_run_id=<build-mesa run> -f ffmpeg_run_id=<host-check run> \
  -f execute=false
```

`execute=false` 是 dry-run：核对 run 状态、每个 RPM 与 SRPM 的大小和摘要、清单里的包是否齐全，以及源包名与二进制包名。Mesa 的源包名是 `mesa-piano`；FFmpeg 的配方把变体放在 `pkg_suffix` 里，源包仍叫 `ffmpeg`，所以按 release 认这一版。两者的二进制包必须全部带 `-piano`。通过后把 `execute` 换成 `true` 真实提交。脚本幂等创建项目，按依赖顺序提交构建，轮询到全部 `succeeded` 才写 `copr-publication.json`。

CI 的 RPM 矩阵、COPR 的重建产物与 bundle 里的 RPM 版本号相同，但构建环境不同，不是逐字节同一份。要完全一致的二进制，就用 bundle 里的 RPM。

## 启用仓库

`piano-fedora-configs` 已经带上 `/etc/yum.repos.d/piano-fedora-copr.repo`：

```ini
[copr:copr.fedorainfracloud.org:xinyang26:piano-fedora]
gpgcheck=1
repo_gpgcheck=0
```

装好系统后直接跑：

```bash
sudo dnf makecache
sudo dnf upgrade --refresh
```

内核升级会重写 ESP 的 `/EFI/Linux/sunuefi.efi`。升级前先确认电量充足，不要在升级事务中间断电。

## 可选的 extras 软件源

默认镜像与上面的默认仓库都不带补丁版桌面组件。需要 GNOME 自动亮度、键盘背光热插拔、GNOME 触摸崩溃修复、GNOME 登录前等 DRM connector 或 KDE HDR 退出后背光恢复时，再单独启用 `xinyang26/piano-fedora-extras`。这个项目默认关闭，不装它时五档桌面仍用 Fedora 原版 GSD、KWin、GTK3、mutter 与 gdm。

| 包 | 内容 |
| --- | --- |
| `gnome-settings-daemon-piano` | 官方 GSD 改名重建，带自动亮度设备 profile、键盘背光热插拔与启动时锁屏状态同步三个补丁 |
| `kwin` | 同名重建，`Release` 带 `.piano1`，带 HDR 背光恢复补丁 |
| `gtk3` | 同名重建，`Release` 带 `.piano1`，带 XI2 工具原子补丁 |
| `mutter` | 同名重建，`Release` 带 `.piano1`，带空 KMS gamma LUT 与取消的触摸 grab 两个守卫补丁 |
| `gdm` | 同名重建，`Release` 带 `.piano1`，登录前等原生 KMS connector；只对 GNOME 档有意义 |
| `gnome-control-center` | 同名重建，`Release` 带 `.piano1`，指纹录入前等 `fprintd` 的验证动作结束；只对 GNOME 档有意义 |
| `piano-gnome-extras` | noarch meta，拉入 GSD、GTK3 与 mutter，并写入 `20-piano-ambient` dconf drop-in |
| `piano-kde-extras` | noarch meta，拉入 KWin 与 GTK3 |

启用并安装：

```bash
sudo dnf copr enable -y xinyang26/piano-fedora-extras
sudo dnf config-manager setopt \
  'copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras'.priority=1
sudo dnf upgrade kwin gtk3 mutter gdm
sudo dnf install --allowerasing piano-gnome-extras   # 或 piano-kde-extras
sudo dconf update
```

`priority=1` 让 extras 的数字小于 Fedora 仓库，DNF5 因此优先用 `.piano1` 的 KWin、GTK3、mutter 与 gdm，哪怕 Fedora 后来发了更高版本。代价是 Fedora 对这几个包的更新会被遮住，直到 extras 按新 SRPM 重编。gdm 是登录管理器，mutter 是 GNOME 合成器，这两条被遮住的更新比 KWin/GTK3 更值得盯。手动 `dnf copr enable` 不会自动写这个优先级，要自己补上。

meta 包只写裸 `Requires`，原版已装时依赖就算满足，所以装 meta 前先 `dnf upgrade kwin gtk3 mutter gdm`，否则只装 meta 不换组件。gdm 不进 meta 的硬依赖：在非 GNOME 档装 `piano-gnome-extras` 不该顺带拉进一个登录管理器，GNOME 用户手动升级它。

卸载回退：

```bash
sudo dnf remove piano-gnome-extras piano-kde-extras
sudo dnf config-manager setopt \
  'copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras'.enabled=0
sudo dconf update
sudo dnf distro-sync --repo fedora
```

`distro-sync` 把同名的 KWin、GTK3、mutter 与 gdm 换回 Fedora 版本。GSD 用 `sudo dnf swap gnome-settings-daemon-piano gnome-settings-daemon --repo fedora`。先加 `--assumeno` 看清楚要动的包。

增量安装、KDE HDR 与自动亮度均已纳入支持，结果见[桌面档](desktops.md#extras-可选组件)与[已知问题](known-issues.md#hdr)。`mutter` 与 `gdm` 作为 opt-in extras 提供：GSD 正常加载 ambient profile。触摸手势与键盘背光需要外设配件配合，作为后续验收项推进。

## 核对与回滚

```bash
# 确认仓库和签名可用
dnf repolist --enabled | grep piano
rpm -q gpg-pubkey --qf '%{SUMMARY}\n' | grep -i copr

# 确认内核在这条源里、升级会带上它
dnf list --showduplicates kernel-piano
dnf upgrade --assumeno | grep kernel-piano

# 看上次内核升级做了什么（重建的 initramfs 摘要、模块数、容器摘要）
cat /var/lib/piano/kernel-esp/*.json

# 停用仓库
sudo dnf config-manager setopt copr:copr.fedorainfracloud.org:xinyang26:piano-fedora.enabled=0

# 回滚单个包到发行版版本
sudo dnf downgrade --repo fedora piano-fedora-configs
```

移除仓库：

```bash
sudo rm /etc/yum.repos.d/piano-fedora-copr.repo
sudo dnf makecache
```

## 图形栈为什么换包名

Fedora 的 mesa 与 ffmpeg 版本号一直在往上走，补丁版的版本号永远落后：本地包是 `26.2.7-1.piano1`，官方一升到 `26.2.8-1` 就排到它上面，`dnf upgrade` 会把补丁版换回官方版。

抬 Epoch 能压住，但 epoch 是单向的。`1:` 一旦发出去就回不到 `0:`，等 A830v1 的 chip id 进了上游、要回到 Fedora 的包时，每台设备都得手工 `dnf distro-sync`。所以改成 RPM Fusion 对同两个包的做法：换包名。

| 上游包 | 本地包 |
| --- | --- |
| `mesa`、`mesa-libGL`、`mesa-libEGL`、`mesa-libgbm`、`mesa-dri-drivers`、`mesa-vulkan-drivers`、`mesa-filesystem` | `mesa-piano`、`mesa-piano-libGL`、`mesa-piano-libEGL`、`mesa-piano-libgbm`、`mesa-piano-dri-drivers`、`mesa-piano-vulkan-drivers`、`mesa-piano-filesystem` |
| `ffmpeg-free`、`libavcodec-free` 等 8 个 | `ffmpeg-piano`、`libavcodec-piano` 等 8 个 |

每个本地子包声明 `Provides: <上游名> = <版本>-<发布号>`、`Obsoletes: <上游名> < <版本>-<发布号>` 和 `Conflicts: <上游名>`。依赖上游名字的包照旧能被满足；按名字提的安装请求（comps 组、桌面 environment 的成员）落到本地包上，`Obsoletes` 那一行就是为它写的——dnf 解析明确的包名只认名字，`Provides` 压不过同名的官方包；两个版本不会同时装进一台设备。上游版本号超过本地包时 `Obsoletes` 的区间盖不住，构建会直接报冲突，需要跟着新 SRPM 重做。

已经装着 Fedora 图形栈的设备要换一次名字，`dnf upgrade` 不会主动安装新名字的包：

```sh
sudo dnf install --allowerasing mesa-piano-vulkan-drivers mesa-piano-libGL \
  mesa-piano-libEGL ffmpeg-piano
```

从带这套包的新镜像装起的设备不需要这一步。

## 边界

- COPR 覆盖全部 20 个包（包括内核）：
  - ESP 上的 `/EFI/Linux/sunuefi.efi` 由 `%posttrans` 维护，`.dtb` 节区沿用现有 UKI；
  - 板级 overlay 不在这条升级路径上更新。
- 设备的 `.repo` 只指向项目自己的 COPR。Hyprland 与 Denial 两档在构建期另加
  第三方源：Hyprland 用 `lionheartp/Hyprland` COPR，Denial 用官方
  `packages.denialwm.org` 的 Fedora 44 仓库（Fedora 45 官方仓库没有这两个桌面）。
  仓库都以 `--repofrompath` 临时加入并校验公钥，不写进镜像的持久配置。其余三档
  只连官方仓库。细节见[桌面档](desktops.md)。
- 重建产物和 CI 矩阵产物环境不同，版本号相同但非逐字节一致：
  - 需要完全一致二进制时直接使用 bundle 内的 RPM，不使用 COPR。

## 设备侧验证

`verify-copr-device.py` 在平板上跑。只读模式核对：仓库里的 15 个矩阵包、3 个运行时包和 Mesa/FFmpeg 齐全、图形栈是改名后的 `mesa-piano-*` 与 `ffmpeg-piano` 且仓库里没有同名的 Fedora 包（避免升级时被官方包覆盖）、公钥可下载、`.repo` 里没有 `exclude=kernel-piano`、`dnf repoquery` 能查到内核包。`--install-test` 再跑一次 `dnf upgrade --assumeno`，确认升级计划里带着 `kernel-piano`，且仍然要求确认。

`--extras` 换到 extras 仓库：核对 GSD/KWin/GTK3/mutter/gdm/gnome-control-center 与两个 meta 包齐全、六个桌面包的 `Release` 含 `.piano1`、两个 meta 是 noarch、仓库里没有官方的 `gnome-settings-daemon`，报告写到 `copr-extras-device-check.json`。
