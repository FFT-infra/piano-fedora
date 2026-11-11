# COPR 软件源

设备装好 Fedora 后，配置、固件、相机、触控、传感器、VA-API、调度器和内核都可以通过 COPR 升级，不必每次手动下载 RPM 再 `rpm -Uvh`。

**当前状态：10 个矩阵包加上 Mesa 与 FFmpeg 两组图形栈可以 `dnf upgrade` 升级，内核在内。** 项目是 `xinyang26/piano-fedora`，chroot 跟 `product.json` 的 releasever 走，现在是 `fedora-45-aarch64`。项目存在但缺这个 chroot 时，发布脚本会先 `copr-cli modify --chroot` 补上，否则 `copr-cli build` 会被 COPR 拒掉。仓库里有全部 10 个包：

`alsa-xiaomi-piano`、`iio-sensor-proxy-ssc`、`kernel-piano`、`libssc`、`piano-camera`、`piano-fedora-configs`、`sm8750-video-accel`、`uperf-linux`、`xiaomi-piano-firmware`、`xiaomi-piano-thp`。

核对方法见下面的[启用仓库](#启用仓库)一节，或在设备上跑 `python3 scripts/verify-copr-device.py`。

**内核怎么升级。** 固件只读 ESP 的 `/EFI/Piano/stable/boot.img`（`PianoEspBootSource.c` 里的固定路径）。用户态 `dnf upgrade` 本身只改 `/usr/lib/modules`，两者本来是脱钩的。`kernel-piano` 的 `%posttrans` 把这条链接上。升级事务收尾时，`%posttrans` 先跑 `depmod -a <release>`，再用刚装上的内核重建 initramfs。最后 `%posttrans` 把新内核、新 initramfs 和 ESP 上原有的 `board.dtb` 重新打成 Android boot v2 容器，原子替换 `boot.img`。重启之后固件读到的是新内核。

不需要重编固件，依据在固件自己的代码里。`PianoRawLinuxLoadStable()` 把 `boot.img` 自身的 sha256 存进 `AcceptedSha`。固件不要求这份文件由谁签发，只要求它能被解析成 Android boot v2 容器。

`board.dtb` 用的是 ESP 上那份，不是内核包里的 `/boot/dtb/qcom/sm8750-xiaomi-piano.dtb`。两者不是同一个文件。ESP 上那份叠加过板级 dtbo，内核包里的只是单板 DTB。

**失败边界。** 任何一步失败，`boot.img` 保持原样，设备继续用上一个内核启动。写入顺序是：先打 `.boot.img.new` 并 fsync，再逐段解包比对。比对通过后把旧文件复制成 `boot.img.bak`，最后用 `os.replace` 换上去。

**实机记录。** `7.2.9-1` → `7.2.9-3` 在平板上走过一次。事务收尾时重建 initramfs（6 个早期模块），ESP 的 `Image`、`initramfs`、`boot.img` 三份都换掉，各留一份 `.bak`，审计记录写到 `/var/lib/piano/kernel-esp/<release>.json`。重启后 `uname -r` 是升级后的 `7.2.9` release，`graphical.target` 与 GDM 起来，`systemctl --failed` 为空。`v4l2loopback` 从新模块树的 `updates/` 加载，`/dev/video40`、`/dev/video41` 都在。

同一条路径也从 COPR 走过一次 `dnf reinstall kernel-piano`。设备连上 Wi-Fi，包从 `copr:copr.fedorainfracloud.org:xinyang26:piano-fedora` 下载，公钥同源拉取并校验。事务成功，审计记录里 `changed` 三项全为 `false`，`boot.img` 摘要没变。同样的输入重打出的容器逐字节相同，所以重复触发不会重写 43 MB。

挂起与唤醒各测了两轮，都能回到桌面，`systemctl --failed` 保持为空。挂起路径上每次会打 4 条 `qdss already disabled/unprepared` 的 clk 警告（`amba_pm_runtime_suspend` 对 coresight qdss 重复 disable）。这条警告不影响唤醒，没有对应的故障现象。

manifest 的名字带 NVRA。`kernel-piano` 是 installonly，升级时新旧版本同时安装。固定的 `/usr/share/piano/kernel-manifest.json` 会让两个包争同一路径，rpm 报 `file ... conflicts with file from package` 并中止整个事务。`%posttrans` 读的是自己那一版，并把文件名传给 `piano-kernel-esp-update`。盘上有多份 manifest 时，工具不猜用哪一份。

已经装好但起不来的内核没有自动回滚。上游没有 boot counter（`PianoNvJournal` 是现成的双槽 NV journal，只是没有人往里写尝试计数），回滚只能手工做：

```bash
# 恢复上一个容器，重启即回到旧内核
sudo cp /boot/efi/EFI/Piano/stable/boot.img.bak /boot/efi/EFI/Piano/stable/boot.img
sudo cp /boot/efi/EFI/Piano/stable/Image.bak /boot/efi/EFI/Piano/stable/Image
sudo cp /boot/efi/EFI/Piano/stable/initramfs.bak /boot/efi/EFI/Piano/stable/initramfs
```

`dnf history undo` 只回滚 RPM，不动 ESP，所以单跑它不会让设备回到旧内核。`kernel-piano` 提供 `kernel-core`，旧模块树与 `/boot/vmlinuz-*` 在 `installonly_limit` 之内会保留下来。要把它们重新装进 ESP，得再走一次打包。

`Image.efi` 那条路仍然帮不上忙。`PianoProductOsController.c` 里定义了 `\EFI\Piano\stable\Image.efi` + `piano.dtb` + `initramfs.cpio` 的加载路径。这个模块没有绑定到产品调用链，而且它对三份载荷做编译期固定哈希与长度校验。接上它之后，换内核反而必须重编固件。

## 为什么是 COPR 重建，而不是上传二进制

Fedora 官方 COPR 实例没有启用二进制 RPM 上传。运行中实例的 API 描述里没有 `/build/create/rpm-upload`，只有 `/build/create/upload`（SRPM）。源码里这条路由由 `DIRECT_RPM_UPLOAD` 控制，默认关闭，公开实例没打开。`copr-cli` 2.4 也没有 `uploadrpm` 子命令。

发布链因此改成：矩阵构建同时产出 SRPM，COPR 在干净的 `fedora-44-aarch64` chroot 里从 SRPM 重建。仓库因此能从源码复现，不会留下一堆来路不明的二进制。

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

```
[copr:copr.fedorainfracloud.org:xinyang26:piano-fedora]
gpgcheck=1
repo_gpgcheck=0
```

装好系统后直接跑：

```bash
sudo dnf makecache
sudo dnf upgrade --refresh
```

内核升级会重写 ESP 的 `boot.img`。升级前先确认电量充足，不要在升级事务中间断电。

## 可选的 extras 软件源

默认镜像与上面的默认仓库都不带补丁版桌面组件。需要 GNOME 自动亮度、键盘背光热插拔或 KDE HDR 退出后背光恢复时，再单独启用 `xinyang26/piano-fedora-extras`。这个项目默认关闭，不装它时四档桌面仍用 Fedora 原版 GSD、KWin 与 GTK3。

| 包 | 内容 |
| --- | --- |
| `gnome-settings-daemon-piano` | 官方 GSD 改名重建，带自动亮度设备 profile 与键盘背光热插拔两个补丁 |
| `kwin` | 同名重建，`Release` 带 `.piano1`，带 HDR 背光恢复补丁 |
| `gtk3` | 同名重建，`Release` 带 `.piano1`，带 XI2 工具原子补丁 |
| `piano-gnome-extras` | noarch meta，拉入 GSD 与 GTK3，并写入 `20-piano-ambient` dconf drop-in |
| `piano-kde-extras` | noarch meta，拉入 KWin 与 GTK3 |

启用并安装：

```bash
sudo dnf copr enable -y xinyang26/piano-fedora-extras
sudo dnf config-manager setopt \
  'copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras'.priority=1
sudo dnf upgrade kwin gtk3
sudo dnf install --allowerasing piano-gnome-extras   # 或 piano-kde-extras
sudo dconf update
```

`priority=1` 让 extras 的数字小于 Fedora 仓库，DNF5 因此优先用 `.piano1` 的 KWin/GTK3，哪怕 Fedora 后来发了更高版本。代价是 Fedora 对这几个包的更新会被遮住，直到 extras 按新 SRPM 重编。手动 `dnf copr enable` 不会自动写这个优先级，要自己补上。meta 包只写裸 `Requires`，原版已装时依赖就算满足，所以装 meta 前先 `dnf upgrade kwin gtk3`，否则只装 meta 不换组件。

卸载回退：

```bash
sudo dnf remove piano-gnome-extras piano-kde-extras
sudo dnf config-manager setopt \
  'copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras'.enabled=0
sudo dconf update
sudo dnf distro-sync --repo fedora
```

`distro-sync` 把同名的 KWin/GTK3 换回 Fedora 版本。GSD 用 `sudo dnf swap gnome-settings-daemon-piano gnome-settings-daemon --repo fedora`。先加 `--assumeno` 看清楚要动的包。

这套包还没有跑过本轮的真机验收。自动亮度、键盘背光与 KDE HDR 的验收命令见 [桌面档](desktops.md#extras-可选组件)。

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

Fedora 的 mesa 与 ffmpeg 版本号一直在往上走，补丁版的版本号永远落后：`26.2.6-1.piano1` 排在 `26.2.7-1` 之下，`dnf upgrade` 会把补丁版换回官方版。

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

- COPR 覆盖全部 10 个包，包括内核。ESP 上的 `boot.img` 由 `kernel-piano` 的 `%posttrans` 维护，但 `board.dtb` 不换。它来自固件的板级叠加，升级路径只复用 ESP 上已有那份。
- 设备的 `.repo` 只指向项目自己的 COPR。Hyprland 档位在构建期另加第三方源
  `lionheartp/Hyprland`（Fedora 44 官方仓库没有 Hyprland 及其配套组件）。仓库以
  `--repofrompath` 临时加入并校验公钥，不写进镜像的持久配置。四档里只有
  Hyprland 用这个源，其余三档只连官方仓库。细节见[桌面档](desktops.md)。
- 重建产物和 CI 矩阵产物不是逐字节同一份。版本号相同，构建环境不同。要完全一致的二进制就用 bundle 里的 RPM，不要用 COPR。

## 设备侧验证

`verify-copr-device.py` 在平板上跑。只读模式核对：仓库里的 10 个矩阵包和 Mesa/FFmpeg 齐全、图形栈是改名后的 `mesa-piano-*` 与 `ffmpeg-piano` 且仓库里没有同名的 Fedora 包（避免升级时被官方包覆盖）、公钥可下载、`.repo` 里没有 `exclude=kernel-piano`、`dnf repoquery` 能查到内核包。`--install-test` 再跑一次 `dnf upgrade --assumeno`，确认升级计划里带着 `kernel-piano`，且仍然要求确认。

`--extras` 换到 extras 仓库：核对 GSD/KWin/GTK3 与两个 meta 包齐全、三个桌面包的 `Release` 含 `.piano1`、两个 meta 是 noarch、仓库里没有官方的 `gnome-settings-daemon`，报告写到 `copr-extras-device-check.json`。
