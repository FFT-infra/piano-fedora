# COPR 软件源

设备装好 Fedora 后，用户态包（配置、固件、相机、触控、传感器、VA-API、调度器）可以通过 COPR 升级，不必每次手动下载 RPM 再 `rpm -Uvh`。

**当前状态：9 个自有包已发布，用户态可以 `dnf upgrade` 升级。** 项目是 `xinyang26/piano-fedora`，`fedora-44-aarch64` 仓库开着，仓库里现在有全部 9 个二进制包：`alsa-xiaomi-piano`、`iio-sensor-proxy-ssc`、`libssc`、`piano-camera`、`piano-fedora-configs`、`sm8750-video-accel`、`uperf-linux`、`xiaomi-piano-firmware`、`xiaomi-piano-thp`。核对方法见下面的启用仓库一节，或在设备上跑 `python3 scripts/verify-copr-device.py`。

`kernel-piano` 出现在仓库里是为了让版本号可审计；`.repo` 用 `exclude=kernel-piano*` 把它挡在升级之外。

内核更新跟着 release bundle 走。切换条件写在这里，方便日后复查：上游把 `simpleinit.static.uefi.json` 的 `Image.efi` 配置改成 `enabled: true` 且 `PianoProductCore.c` 开始调 `PianoProductOsController` 时，或者引导链引入 boot counter 之后，就能迁移到 `kernel-install` / UKI，把内核纳入设备侧升级。

## 为什么是 COPR 重建，而不是上传二进制

Fedora 官方 COPR 实例没有启用二进制 RPM 上传。运行中实例的 API 描述里没有 `/build/create/rpm-upload`，只有 `/build/create/upload`（SRPM）。源码里这条路由由 `DIRECT_RPM_UPLOAD` 控制，默认关闭，公开实例没打开。`copr-cli` 2.4 也没有 `uploadrpm` 子命令。

所以发布链改成：矩阵构建同时产出 SRPM，COPR 在干净的 `fedora-44-aarch64` chroot 里从 SRPM 重建。仓库因此能从源码复现，而不是一堆来路不明的二进制。

## 发布流程（维护者）

发布走 `publish-copr.yml`，需要仓库 secrets `COPR_LOGIN` 与 `COPR_TOKEN`。先在 `main` 上跑一次带 `build_piano_rpms=true` 的 `host-check` 拿到 `piano-rpms` artifact，再用它的 run id 触发 `publish-copr.yml`：

```bash
gh workflow run publish-copr.yml --ref main \
  -f rpms_run_id=<host-check run> -f kernel_run_id=<同一个内核 run> -f execute=false
```

`execute=false` 是 dry-run：核对 run 状态、每个 RPM 与 SRPM 的大小和摘要、清单里的包是否齐全。通过后把 `execute` 换成 `true` 真实提交。脚本幂等创建项目，按依赖顺序提交构建，轮询到全部 `succeeded` 才写 `copr-publication.json`。

CI 的 RPM 矩阵、COPR 的重建产物与 bundle 里的 RPM 版本号相同但构建环境不同，不是逐字节同一份。要完全一致的二进制就用 bundle 里的 RPM。

## 启用仓库

`piano-fedora-configs` 已经带上 `/etc/yum.repos.d/piano-fedora-copr.repo`：

```
[copr:copr.fedorainfracloud.org:xinyang26:piano-fedora]
gpgcheck=1
repo_gpgcheck=0
exclude=kernel-piano*
```

装好系统后直接：

```bash
sudo dnf makecache
sudo dnf upgrade --refresh
```

`kernel-piano` 被排除，所以升级只动用户态包。

## 核对与回滚

```bash
# 确认仓库和签名可用
dnf repolist --enabled | grep piano
rpm -q gpg-pubkey --qf '%{SUMMARY}\n' | grep -i copr

# 确认内核不会被这条源替换
dnf list --showduplicates kernel-piano

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

## 边界

- COPR 只覆盖用户态包。ESP、`boot.img`、内核、initramfs 和 F2FS 根镜像仍要走 release bundle 和安装文档。
- 设备的 `.repo` 只指向项目自己的 COPR。Hyprland 档在构建期另加第三方源
  `lionheartp/Hyprland`（Fedora 44 官方仓库没有 Hyprland 全家），仓库以
  `--repofrompath` 临时加入并校验公钥，不写进镜像的持久配置。四档里只有
  Hyprland 用这个源，其余三档只连官方仓库；细节见[桌面档](desktops.md)。
- 重建产物和 CI 矩阵产物不是逐字节同一份。版本号相同，构建环境不同。要完全一致的二进制就用 bundle 里的 RPM，不要用 COPR。
- 设备目前没有默认路由，只有 USB NCM 的 `192.168.77.0/30`。接上 Wi-Fi 之前 `dnf` 连不上 COPR。这是设备侧的网络配置，不是仓库的问题。
