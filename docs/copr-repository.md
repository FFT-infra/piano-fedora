# COPR 软件源

设备装好 Fedora 后，配置、固件、相机、触控、传感器、VA-API、调度器和内核都可以通过 COPR 升级，不必每次手动下载 RPM 再 `rpm -Uvh`。

**当前状态：10 个包已发布，全部可以 `dnf upgrade` 升级，内核在内。** 项目是 `xinyang26/piano-fedora`，`fedora-44-aarch64` 仓库开着，仓库里有全部 10 个包名：`alsa-xiaomi-piano`、`iio-sensor-proxy-ssc`、`kernel-piano`、`libssc`、`piano-camera`、`piano-fedora-configs`、`sm8750-video-accel`、`uperf-linux`、`xiaomi-piano-firmware`、`xiaomi-piano-thp`。核对方法见下面的启用仓库一节，或在设备上跑 `python3 scripts/verify-copr-device.py`。

**内核怎么升级。** 固件只读 ESP 的 `/EFI/Piano/stable/boot.img`（`PianoEspBootSource.c` 里的固定路径），用户态 `dnf upgrade` 本身只改 `/usr/lib/modules`，两者本来是脱钩的。`kernel-piano` 的 `%posttrans` 把这条链接上：升级事务收尾时它先 `depmod -a <release>`，再用刚装上的内核重建 initramfs，最后把新内核、新 initramfs 和 ESP 上原有的 `board.dtb` 重新打成 Android boot v2 容器，原子替换 `boot.img`。重启之后固件读到的是新内核。

不需要重编固件，依据在固件自己的代码里：`PianoRawLinuxLoadStable()` 把 `boot.img` 自身的 sha256 存进 `AcceptedSha`，固件不要求这份文件由谁签发，只要求它能被解析成 Android boot v2 容器。

`board.dtb` 用的是 ESP 上那份，不是内核包里的 `/boot/dtb/qcom/sm8750-xiaomi-piano.dtb`。两者不是同一个文件：ESP 上那份叠加过板级 dtbo，内核包里的只是单板 DTB。

**失败边界。** 任何一步失败，`boot.img` 保持原样，设备继续用上一个内核启动。写入顺序是：先打 `.boot.img.new` 并 fsync，逐段解包比对通过，把旧文件复制成 `boot.img.bak`，最后 `os.replace` 换上去。

**实机记录。** `7.2.9-1` → `7.2.9-3` 在平板上走过一次：事务收尾时 initramfs 重建（6 个早期模块）、ESP 的 `Image`、`initramfs`、`boot.img` 三份都换掉并各留一份 `.bak`，审计记录写到 `/var/lib/piano/kernel-esp/<release>.json`。重启后 `uname -r` 是升级后的 `7.2.9` release，`graphical.target` 与 GDM 起来，`systemctl --failed` 为空，`v4l2loopback` 从新模块树的 `updates/` 加载，`/dev/video40`、`/dev/video41` 在。

同一条路径也从 COPR 走过一次 `dnf reinstall kernel-piano`（版本不变，等于把下载、校验、`%posttrans` 整条重跑）：事务成功，审计记录里 `changed` 三项全为 `false`，`boot.img` 摘要没变——同样的输入重打出的容器逐字节相同，所以重复触发不会重写 43 MB。

挂起与唤醒各测了两轮，都能回到桌面，`systemctl --failed` 保持为空。挂起路径上每次会打 4 条 `qdss already disabled/unprepared` 的 clk 警告（`amba_pm_runtime_suspend` 对 coresight qdss 重复 disable），不影响唤醒，没有对应的故障现象。

manifest 的名字带 NVRA：`kernel-piano` 是 installonly，升级时新旧版本同时安装，固定的 `/usr/share/piano/kernel-manifest.json` 会让两个包争同一路径，rpm 报 `file ... conflicts with file from package` 并中止整个事务。`%posttrans` 读的是自己那一版，并把文件名传给 `piano-kernel-esp-update`，盘上有多份 manifest 时工具不猜。

已经装好但起不来的内核没有自动回滚。上游没有 boot counter（`PianoNvJournal` 是现成的双槽 NV journal，只是没有人往里写尝试计数），回滚是手工的：

```bash
# 恢复上一个容器，重启即回到旧内核
sudo cp /boot/efi/EFI/Piano/stable/boot.img.bak /boot/efi/EFI/Piano/stable/boot.img
sudo cp /boot/efi/EFI/Piano/stable/Image.bak /boot/efi/EFI/Piano/stable/Image
sudo cp /boot/efi/EFI/Piano/stable/initramfs.bak /boot/efi/EFI/Piano/stable/initramfs
```

`dnf history undo` 只回滚 RPM，不动 ESP，所以单跑它不会让设备回到旧内核。`kernel-piano` 提供 `kernel-core`，旧模块树与 `/boot/vmlinuz-*` 在 `installonly_limit` 之内会保留下来，但把它们重新装进 ESP 需要再走一次打包。

`Image.efi` 那条路仍然帮不上忙。`PianoProductOsController.c` 里定义了 `\EFI\Piano\stable\Image.efi` + `piano.dtb` + `initramfs.cpio` 的加载路径，但模块没有绑定到产品调用链，而且它对三份载荷做编译期固定哈希与长度校验，接上之后换内核反而必须重编固件。

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
```

装好系统后直接：

```bash
sudo dnf makecache
sudo dnf upgrade --refresh
```

内核升级会重写 ESP 的 `boot.img`，所以升级前先确认电量充足，别在升级事务中间断电。

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

## 边界

- COPR 覆盖全部 10 个包，包括内核。ESP 上的 `boot.img` 由 `kernel-piano` 的 `%posttrans` 维护，但 `board.dtb` 不换——它来自固件的板级叠加，升级路径只复用 ESP 上已有那份。
- 设备的 `.repo` 只指向项目自己的 COPR。Hyprland 档在构建期另加第三方源
  `lionheartp/Hyprland`（Fedora 44 官方仓库没有 Hyprland 全家），仓库以
  `--repofrompath` 临时加入并校验公钥，不写进镜像的持久配置。四档里只有
  Hyprland 用这个源，其余三档只连官方仓库；细节见[桌面档](desktops.md)。
- 重建产物和 CI 矩阵产物不是逐字节同一份。版本号相同，构建环境不同。要完全一致的二进制就用 bundle 里的 RPM，不要用 COPR。
- 设备目前没有默认路由，只有 USB NCM 的 `192.168.77.0/30`。接上 Wi-Fi 之前 `dnf` 连不上 COPR。这是设备侧的网络配置，不是仓库的问题。

## 设备侧验证

`verify-copr-device.py` 在平板上跑：只读模式核对仓库里的 10 个包名齐全、公钥可下载、`.repo` 里没有 `exclude=kernel-piano`、`dnf repoquery` 能查到内核包；`--install-test` 再跑一次 `dnf upgrade --assumeno`，确认升级计划里带着 `kernel-piano` 且仍然要求确认。
