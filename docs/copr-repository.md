# COPR 软件源

设备装好 Fedora 后，用户态包（配置、固件、相机、触控、传感器、VA-API、调度器）可以通过 COPR 升级，不必每次手动下载 RPM 再 `rpm -Uvh`。

**当前状态：源还没建起来。** 发布链的 dry-run 已通过，真实发布从未执行。仓库 secrets 里没有 `COPR_LOGIN` 与 `COPR_TOKEN`，COPR 上的 `fft-infra` 账号和 `piano-fedora` 项目都不存在（`api_3/user` 与 `api_3/project` 均返回 404）。`piano-fedora-configs` 随包带出的 `.repo` 因此指向一个 404 地址，设备上 `dnf upgrade` 只会得到 `skip_if_unavailable=True` 下的静默跳过。下面写的是建成之后的流程，不是现在可用的能力。在设备上核对现状用 `scripts/verify-copr-device.py`，它的判定与本节一致。

**内核不在升级范围内。** `kernel-piano` 会发布进仓库供审计，但仓库文件用 `exclude=kernel-piano*` 排除它。用户态 `dnf upgrade` 更新不了 SunUEFI 从 ESP 引导的 `Image`；装上 COPR 的内核只会让 ESP 与 `/usr/lib/modules` 脱钩。内核更新跟着 release bundle 走。

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

   需要仓库 secrets `COPR_LOGIN` 与 `COPR_TOKEN`。脚本幂等创建项目 `fft-infra/piano-fedora`，按依赖顺序提交构建，轮询到全部 `succeeded` 才写 `copr-publication.json`。任一 `failed` 或 `canceled` 都让整个步骤失败。

## 启用仓库

`piano-fedora-configs` 已经带上 `/etc/yum.repos.d/piano-fedora-copr.repo`：

```
[copr:copr.fedorainfracloud.org:fft-infra:piano-fedora]
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
sudo dnf config-manager setopt copr:copr.fedorainfracloud.org:fft-infra:piano-fedora.enabled=0

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
