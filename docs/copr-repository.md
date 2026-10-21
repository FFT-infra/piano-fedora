# COPR 软件源

设备装好 Fedora 后，用户态包（配置、固件、相机、触控、传感器、VA-API、调度器）可以通过 COPR 升级，不必每次手动下载 RPM 再 `rpm -Uvh`。

**当前状态：项目已建，9 个自有包全部发布。** `xinyang26/piano-fedora` 已经在 COPR 上，`fedora-44-aarch64` 仓库开着，仓库 secrets 的 `COPR_LOGIN` 与 `COPR_TOKEN` 也配好了。2026-10-21 的发布 run 39573927054 把 10 个包（9 个自有包加 `kernel-piano`）提交进构建队列并全部成功，设备仓库的 repodata 里有全部 9 个二进制包：`alsa-xiaomi-piano`、`iio-sensor-proxy-ssc`、`libssc`、`piano-camera`、`piano-fedora-configs`、`sm8750-video-accel`、`uperf-linux`、`xiaomi-piano-firmware`、`xiaomi-piano-thp`。

上一轮的两个失败都已定位并修掉：

- `sm8750-video-accel`（build 11181429）的 `meson.build:12:6` 报 `Dependency "egl" not found`：spec 漏了 `egl`、`glesv2`、`gbm` 三个 BuildRequires。补进 spec 后构建成功。
- `uperf-linux`（build 11181697）的 `CMakeLists.txt:2` 报 `Could not find the compiler specified in the environment variable CXX: g++`：spec 只声明了 `gcc`，上游 `project()` 同时启用 C 与 CXX。补 `gcc-c++` 后这一轮成功。

`kernel-piano` 这一轮也构建成功并发进了设备仓库，但**内核不在设备侧升级范围内**。原因在引导路径上：SunUEFI 只读 ESP 的 `/EFI/Piano/stable/boot.img`（`PianoEspBootSource.c` 里的固定路径），`PianoProductCore.c` 也只调 `PianoRawLinuxLoadStable`，没有走 `Image.efi` + `PianoProductOsController` 那条路。用户态 `dnf upgrade` 只改 `/usr/lib/modules`，ESP 上的 `boot.img` 不动，装上 COPR 的内核就是让两者脱钩——一台起不来的机器，而且 SunUEFI 没有 boot counter，新内核失败不会自动回滚。

`kernel-piano` 出现在仓库里是为了让版本号可审计；`.repo` 用 `exclude=kernel-piano*` 把它挡在升级之外。

内核更新跟着 release bundle 走。切换条件写在这里，方便日后复查：上游把 `simpleinit.static.uefi.json` 的 `Image.efi` 配置改成 `enabled: true` 且 `PianoProductCore.c` 开始调 `PianoProductOsController` 时，或者引导链引入 boot counter 之后，就能迁移到 `kernel-install` / UKI，把内核纳入设备侧升级。

## 为什么是 COPR 重建，而不是上传二进制

Fedora 官方 COPR 实例没有启用二进制 RPM 上传。运行中实例的 API 描述里没有 `/build/create/rpm-upload`，只有 `/build/create/upload`（SRPM）。源码里这条路由由 `DIRECT_RPM_UPLOAD` 控制，默认关闭，公开实例没打开。`copr-cli` 2.4 也没有 `uploadrpm` 子命令。

所以发布链改成：矩阵构建同时产出 SRPM，COPR 在干净的 `fedora-44-aarch64` chroot 里从 SRPM 重建。仓库因此能从源码复现，而不是一堆来路不明的二进制。

## 发布流程

1. 在 `main` 上触发 RPM 矩阵：

   ```bash
   gh workflow run host-check.yml --ref main \
     -f build_piano_rpms=true \
     -f kernel_run_id=<成功的 build-kernel run>
   ```

   等到 `piano-rpms` artifact 产出。它同时包含二进制 RPM、`srpms/` 和
   `piano-rpms-manifest.json`。

2. 先 dry-run 校验，不上传：

   ```bash
   gh workflow run publish-copr.yml --ref main \
     -f rpms_run_id=<上一步的 host-check run> \
     -f kernel_run_id=<同一个内核 run> \
     -f execute=false
   ```

   校验内容：run 必须是成功的 host-check，每个 RPM 与 SRPM 的大小和 SHA256
   必须与 manifest 一致，`scripts/publish-copr.py` 清单里的包一个都不能少，
   manifest 里的内核 run 与 SunUEFI commit 必须存在，且与调用者声明的一致。
   任一项不符就失败。

3. 真实发布：

   ```bash
   gh workflow run publish-copr.yml --ref main \
     -f rpms_run_id=<同一个 run> \
     -f kernel_run_id=<同一个内核 run> \
     -f execute=true
   ```

   需要仓库 secrets `COPR_LOGIN` 与 `COPR_TOKEN`（已配置）。脚本幂等创建项目 `xinyang26/piano-fedora`，按依赖顺序提交构建，轮询到全部 `succeeded` 才写 `copr-publication.json`。任一 `failed` 或 `canceled` 都让整个步骤失败。

   脚本对已存在的项目不会重复创建，构建 id 从 `Created builds:` 行解析；解析不到就报错而不是当作空集合放行。API token 有效 180 天，过期后重新生成并更新 secret。

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
- 重建产物和 CI 矩阵产物不是逐字节同一份。版本号相同，构建环境不同。要完全一致的二进制就用 bundle 里的 RPM，不要用 COPR。
- 设备目前没有默认路由，只有 USB NCM 的 `192.168.77.0/30`。接上 Wi-Fi 之前 `dnf` 连不上 COPR。这是设备侧的网络配置，不是仓库的问题。
