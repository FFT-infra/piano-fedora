# 维护与验收

面向维护者：流水线从哪进入，产物之间怎么绑定，run id 怎么传，PR 什么时候算设备验收通过。只装系统的读者看[安装指南](installation.md)。

上游 pin 与跟进规则见[上游跟踪](upstream.md)，产物定义见[部署产物](artifacts.md)。

## 流水线入口

PR 分支里的 workflow 不等于 Actions 里能启动的入口。GitHub 只给默认分支 `main` 上已注册的 workflow 提供 `workflow_dispatch`。只在 PR 分支新增的 `build-piano-rpms.yml` 在分支状态下查不到 workflow_id。发布链因此统一从已注册的 `host-check` 启动，用 `workflow_call` 复用 RPM 矩阵构建。`build-rootfs` 再按 `host-check.yml` 解析同一次 run 的 `piano-rpms` artifact。

```text
host-check (build_piano_rpms=true, kernel_run_id=...)
  └─ build-piano-rpms.yml (workflow_call)
       └─ piano-rpms

build-rootfs (desktop=all, piano_rpms_run_id=host-check run)
  └─ 按 profile 展开矩阵，每档一棵 rootfs
  └─ /usr/lib/modules/<release> 由 kernel-piano RPM 安装

build-image
  └─ 核对 rootfs 模块树 == 封存内核 manifest

build-bundle
  └─ deploy-bundle/sunuefi_esp.img + boot_sunuefi.img
```

四步之间要显式传 run id，链路不会自己去找"最新的那个"：

| 步骤 | 必填输入 |
| --- | --- |
| build-rootfs | `piano_rpms_run_id`、`runtime_run_id`、`mesa_run_id`、`ffmpeg_run_id` |
| build-image | `kernel_run_id`、`runtime_run_id`、`rootfs_run_id`、`mesa_run_id`、`ffmpeg_run_id` |
| build-bundle | `root_image_run_id` |
| prepare-install | `bundle_run` |

每次 dispatch 都用 tag 或分支钉住 commit，不要用会移动的分支名。四档产物必须落在同一个 commit 上，`release.yml` 会逐档核对 run 的 `head_sha`，混进一个别的提交就拒绝发布。中途往分支上推任何 commit 都会让后续步骤落到新提交上，所以发布期间停在 tag 上跑。

发布验收必须用这一整条链的新产物，并从原始 GPT/UFS 布局重新分区。手工补进会话 RPM 的 rootfs 不算通过。

## Actions 材料

手动运行 `prepare-install` 是可选步骤：它把 root 镜像离线扩到指定容量并做读回校验。首装只求能刷上任意容量的设备时，跳过它，直接用 `deploy-bundle` 自带的 sparse，首启会自己长满分区。要跑时选择完整 deploy-bundle 的 run，首装固定 `android_slot=_a`。这一步不需要公钥：SSH 公钥在刷写时用 `--ssh-key` 注入，公钥和私钥都不进 Actions。三个 artifact 是：

| artifact | 内容 |
| --- | --- |
| root-install | 预扩并配置后的 F2FS sparse、容量/元数据校验记录、摘要 |
| boot-tools | 同核心的 selector、Android 静态原生工具、产品固件、自包含安装入口 |
| host-boot-tool | 静态宿主 BOOT 重打包工具及来源记录 |

boot-tools 必须与所选 deploy-bundle 的 UEFI 产品镜像逐字节相同。安装入口在 boot-tools 的 `installer/scripts`。锁定的上游代码附带源码清单，不需要在设备主机上重编译。普通发行版主机需要 Python 3、adb、fastboot。BOOT 准备用 Actions 提供的宿主工具。

`host-check` 的 `package_prepare_run` 是另一个可选入口。填一个成功的 `prepare-install` run，`host-check` 调用 `package-install.yml` 把那份 `root-install` 压成 zstd。`package-install.yml` 只压缩已校验的产物，不重建文件系统。

只读排查用的两个脚本不进流水线，直接在本机跑。`scripts/probe-device.sh` 取设备身份、槽位与 GPT。`scripts/verify-new-image.sh` 挂载镜像核对交接前提。两者都只读。

## 真机取证脚本

以下脚本连着平板跑，只在维护者机器上用，不进 Release 也不进 root 文件系统。
`make test` 是离线契约测试，和这些分开。

编解码验收：

```sh
make test-device-media MEDIA_HOST=<ssh 目标> MEDIA_OUTPUT=<新的证据目录> \
  FIXTURE_DIR=<已有的 B 帧素材目录>   # 可选
```

脚本本体必须在设备上跑，它读 `/proc/interrupts` 判断 Iris 是否真的在干活。
但目标镜像没有 `make`，`ffmpeg-free` 也没编译 `libx264`/`libx265`，所以
`scripts/run-device-media.sh` 在主机侧准备素材、推进 `/tmp`、执行、再拉回证据。
主机有 `libx264`/`libx265` 时素材当场生成，没有就用 `FIXTURE_DIR` 指向已有的一份。
设备上的 `ffprobe` 把开源码流的 `pict_type` 报成 `?`，帧类型清单由主机算好一起带过去。
`vainfo` 在目标镜像里不存在，那一步记为 skipped。

COPR 仓库验收：`scripts/verify-copr-device.py`，在设备上跑，只读模式与
`--install-test` 两种，见 [COPR 软件源](copr-repository.md#设备侧验证)。

浏览器硬解验收：`scripts/verify-browser-video.py --output <新的证据目录>`，
在设备上以登录用户身份跑，需要一个能播放的 H.264 文件（`--video`，默认
`/tmp/browser-h264.mp4`）。它逐个启动 Chromium、Chrome 与 Firefox，播放同一段
视频，然后只看两件事：Iris 中断在播放窗口内有没有增长，以及有没有进程打开
`/dev/video0`。两者都成立才算硬解。浏览器自报的 GPU 状态不算证据，软件解码
同样会拉高 GPU 中断。

持续触控验收：`scripts/verify-device-touch.py --output <新的证据目录>`，
同样在设备上跑，需要有人配合划动屏幕。脚本先等第一次真实触摸再开始计时，
所以等待时间不计入统计。它报 SYN 速率、NVT 中断增长、坐标覆盖范围、多点
slot 与相邻跳变分位数。速率用 SYN 报文而不是 ABS 事件：协议 B 会把每个
手指的坐标各发一遍，ABS 计数随手指数翻倍。

## 本机 BOOT

先确认当前原 BOOT 的完整摘要、槽位和系统版本。`prepare-device-boot.py` 用这些输入和同核心的 Actions 工具生成 `boot_android.img`、`boot_linux.img` 与 `boot-request.json`。默认目标是普通 Android。

包装后恢复必须得到完整原 BOOT 的同一 SHA256。Linux 持久请求、只读预览和改回 Android 后的再次完整恢复都要成功。文件检查替代不了真机旁路和 Recovery 验收。

使用 `python3 scripts/prepare-device-boot.py --help` 查看文件参数。原 BOOT、生成的设备 BOOT、安装记录和私钥都留在本地，不进入代码仓库。

## 实际首次安装

以下命令是 PR 真机验收的唯一主流程。所有材料必须来自同一条新流水线。已经跑着的系统、历史镜像和热修复都不算通过。

自动化的破坏性步骤（`install-f2fs.py apply`）与手动分区做同一件事。手动分区、刷写与回滚的逐条命令在[安装指南](installation.md)第四节与第五节。

### 1. 固定流水线与设备身份

记录以下 ID，后续每一步都核对它们不变：

```text
build-kernel run
host-check / build-piano-rpms run
build-rootfs run（rootfs-<rel>-<desktop>）
build-image run（root-image-<rel>-<desktop>）
build-bundle run（deploy-bundle）
prepare-install run（root-install，bundle_run 必须等于上面的 build-bundle run）
```

多档桌面时每档各有一对 build-bundle 与 prepare-install run，`release.yml` 的 `desktop_runs` 逐档登记：

```sh
gh workflow run release.yml -f tag=<tag> \
  -f desktop_runs="gnome=<bundle>:<prepare>,kde=<bundle>:<prepare>,niri=<bundle>:<prepare>,hyprland=<bundle>:<prepare>"
```

每档独立走身份、状态与提交绑定校验，平台资产四份逐字节比对后只发一份。不带 `desktop_runs` 时按 `desktop` 单档发布，行为与多桌面之前一致。单档验收也要确认 rootfs、镜像、bundle 与 root-install 四处的 desktop 字段一致，混档会在写设备之前被拒。

平板先完整启动到 Android，开启 USB 调试并确认 `sys.boot_completed=1`：

```sh
adb devices -l
adb -s <serial> shell getprop sys.boot_completed
adb -s <serial> shell getprop ro.boot.slot_suffix
```

序列号、槽位、原 BOOT 摘要和完整 GPT 基线写进安装会话。设备不是 `piano`、Bootloader 未解锁，或 `sys.boot_completed` 不是 `1`，全部停止。

### 2. 生成并复核 BOOT 与首次分区计划

使用 `prepare-install` 产出的 `boot-tools`、`host-boot-tool` 和当前原 BOOT：

```sh
# 先记录当前原厂 BOOT 的完整摘要。下面两处必须使用同一个值。
fastboot getvar current-slot

# 原厂 ABL 不实现 fastboot fetch，改从 Android 侧读块设备（需要 root ADB）。
adb -s <serial> shell su -c 'sha256sum /dev/block/by-name/boot_a'
adb -s <serial> exec-out su -c 'dd if=/dev/block/by-name/boot_a bs=1048576' \
  > stock-recovery/boot.img
sha256sum stock-recovery/boot.img
```

两条摘要必须相同，文件必须是 96 MiB（`stat -c%s`），否则执行器会拒绝。原厂 ROM 包里的 `images/boot.img` 也是 96 MiB，但只有设备还跑着那一版固件时才逐字节相同。用前先比摘要，不一致就以设备读出的为准。

```sh
python3 boot-tools/installer/scripts/prepare-device-boot.py \
  --stock-boot stock-recovery/boot.img \
  --boot-tools boot-tools \
  --host-tool host-boot-tool \
  --bundle deploy-bundle \
  --slot _a \
  --source-sha256 <current-boot-sha256> \
  --output device-boot
```

```sh
python3 boot-tools/installer/scripts/install-f2fs.py plan \
  --sunuefi boot-tools/installer/sunuefi \
  --serial <serial> \
  --bundle deploy-bundle \
  --root-install root-install \
  --device-boot device-boot \
  --stock-boot stock-recovery/boot.img \
  --plan first-install-plan.json
```

`plan` 只读，不写设备。逐项复核计划里的产品、序列号、磁盘、槽位、完整 GPT 基线、原 BOOT 摘要、输入包摘要与 Linux root 容量。任何一项与现场不符就停止。同时确认设备当前槽位是 `a`，`root-install-manifest.json` 顶层的 `android_slot` 是 `_a`。首装只能用 `_a`：合体 BOOT 写进 Android 的当前槽，`_b` 没有原厂 super 逻辑分区。

计划本身是一份 JSON，`--plan` 指到哪里就写到哪里，通常是 `first-install-plan.json`。它记录磁盘几何、原分区表、每个分区的 `first`/`last` 变化与容量，`baseline_sha256` 绑定这份表的来源。`apply` 重算一遍并逐字段比较，设备或输入包有任何变化都停。

同一份算术也能离线跑：`plan-first-partition.py` 读整盘设备、两段 `dd` 读数或文本 dump，写出同样的计划，加 `--emit-gpt` 还能写出两段可直接 `dd` 的整表 blob 与分区前的原表。手动分区流程用的就是它，逐条命令见[安装指南](installation.md#2-手动分区)。`--emit-gpt` 产出的是文件，不写设备。

默认把扣除 ESP 后的 userdata 对半分。要固定 Android 的容量，在 `plan` 与 `apply` 上加同一个 `--android-data-mib`，例如 `--android-data-mib 307200` 给 Android 留 300 GiB，Linux 取剩余空间。`plan` 把取值写进计划，`apply` 只接受相同的取值。

root 镜像与分区容量解耦。执行器要求 `expanded_bytes <= linux_root_mib` 且不小于 F2FS 下限，计划里记下 `grow_at_first_boot` 与差额。封存镜像只有 `product.json` 的 `root_size_mib` 那么大，所以默认就会触发首启扩容。initramfs 在挂载根之前比 `blkid -p -o export` 的 `FSSIZE` 与分区字节数。`fs_bytes < part_bytes` 时依次跑 `fsck.f2fs -f`、`resize.f2fs`、`fsck.f2fs -f`，再复核两者相等。任一步失败即停，不会带着半截容量进系统。`fs_bytes > part_bytes` 直接报错，`resize.f2fs` 不支持缩小。第二次启动时两者相等，`resize.f2fs` 什么都不做。

想把镜像预先扩到目标容量，`prepare-install` 的 `root_size_mib` 填目标 MiB 数，`prepare-root-capacity.py` 会离线扩容并读回校验。这不是必需步骤：`prepare-install` 留空时直接用封存镜像，传输文件由 `build-bundle` 提供。

ESP 默认 512 MiB。ESP 分区必须和 `deploy-bundle/sunuefi_esp.img` 等大：镜像按 `product.json` 的 `esp_size_mib` 造出，执行器要求镜像字节数等于计划算出的 ESP 分区容量。两者不一致就停。改过 `esp_size_mib` 就重跑 `build-bundle`，再在 `plan` 与 `apply` 上加同一个 `--esp-mib`。只有 `--esp-mib` 而没有配套的重建镜像时，执行器会在写设备前拒绝。

`device-boot/boot-request.json` 的 `boot_device` 是 `/dev/disk/by-partlabel/boot_a`（跟随 `--slot`），`app_generation` 必须与 `deploy-bundle/PianoUEFI-product.img` 的 APPv1 载荷一致。

选择器读请求页的位置是“正在引导的那份镜像”末尾：ABL 只加载当前槽，`BootSelect.c` 按 `Base + align(original_image_size)` 取页。合体 BOOT 装在 A 槽，Android 与 Linux 都从同一份 `boot_a` 启动，请求页因此指向 `boot_a`。系统不切槽，也不存在第二份需要同步的页。

合体 BOOT 方案不切槽，因此不触发 GPT 的槽位约束。槽位 type GUID 与 `qbootctl -s` 的坑见[已知问题](known-issues.md#双系统切换)。手工重建 GPT 时主表和备份表要一起写，两份 CRC 都要重算。

带了 `root-install` 时，镜像里的 `etc/piano/boot-request.json` 就是这条配置，`plan` 会逐字段核对。不带时镜像里没有这个文件，首启的 `piano-boot-request-autoconfig.service` 用 `qbootctl -c` 找到当前槽（首装后是 `_a`）后由 `piano-next-boot-configure` 重建。`plan` 的 `boot_request.source` 记下这次用的是哪一条。首启后不要猜测设备名或改槽，否则 Linux 的“返回 Android”和后续启动请求会失败。

### 3. 执行破坏性首次分区并初始化 Android userdata

确认已备份 Android 数据后，使用新的会话目录执行：

```sh
python3 boot-tools/installer/scripts/install-f2fs.py apply \
  --sunuefi boot-tools/installer/sunuefi \
  --serial <serial> \
  --bundle deploy-bundle \
  --root-install root-install \
  --device-boot device-boot \
  --stock-boot stock-recovery/boot.img \
  --plan first-install-plan.json \
  --session first-install-session \
  --execute --accept-data-loss
```

需要在首装时一并装一把 SSH 公钥，就给 `apply` 加 `--ssh-key`（公钥必须单行、
类型为 `ssh-ed25519` 或 `ssh-rsa`）：

```sh
python3 boot-tools/installer/scripts/install-f2fs.py apply \
  ... --ssh-key ~/.ssh/id_ed25519.pub
```

执行器在 GPT 写入和擦除 userdata 之前就校验公钥，并确认 `mcopy` 可用。失败时
不动设备。公钥写进会话目录里的一份 ESP 副本，`deploy-bundle/sunuefi_esp.img`
保持原样。`result.json` 的 `ssh_key_injected` 与 `sunuefi_esp_sha256` 记录这次
注入和实际刷入的摘要。不传 `--ssh-key` 时只保留口令登录。

执行器重新读取设备，保存原 GPT 与 BOOT。执行器随后写入并读回完整 GPT，显式执行 `fastboot erase userdata`，再传输 ESP、F2FS sparse root 和当前槽的 Android 默认 BOOT。结束时状态应为 `IMAGES_TRANSFERRED_FIRST_BOOT_PENDING`。

GPT 写入用 `dd`，不用 `sgdisk`：设备上的 Fedora 只装了 util-linux，Android 侧也不保证有 `sgdisk`。整表编成两段 blob 写，顺序是**先尾后头**：先写备份分区表与备份表头，`fsync`，再写保护性 MBR、主表头与主分区表。尾部 blob 从备份表头记录的 `partition_entry_lba` 开始，一直到磁盘最后一个 LBA。原厂布局在表与表头之间有 2 个空块，长度不能按“表 + 表头”推算。任何一次中断后，旧主表头的 `alternate_lba` 仍指向刚写好的新尾部，重启能解析出一份自洽的表。反过来先写头会留下"头新尾旧"，尾部还指向旧布局。写入前执行器用一次 4 KiB 的 `dd` 探针证明设备上的 `dd` 接受 `conv=fsync,notrunc`，失败则不动设备。写完读回主表头、主分区表、备份表头与备份分区表，用锁定上游的 `parse_gpt` 再解析一次，与计划逐项比对。不一致就按"先头后尾"写回会话里保存的原表，再报错。

这时 Linux 还没启动，`apply` 结束时设备停在原厂 Fastboot。先断开 USB，再执行一次普通重启，让设备按当前槽启动 Android：

```sh
fastboot reboot
```

等首次开机完成，`adb shell getprop sys.boot_completed` 返回 `1`。这是让 Android 在缩短后的分区上重建加密 userdata 的必要步骤，不能跳过，也不能在宿主上格式化成 Android 加密分区。

### 4. 回到 Fastboot 并刷入 Linux

Android 首次初始化完成后关机，按【电源键 + 音量减键】进入原厂 Fastboot。
确认 `current-slot` 是 `a`，且 root-install 的 `android_slot` 是 `_a`。然后运行：

```sh
chmod +x flash-linux.sh
./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub   # 不装密钥时去掉 --ssh-key
```

如果第 3 步已经用 `install-f2fs.py --ssh-key` 注入过，这里不必再传。重复注入
只会覆盖同一个 `/piano-ssh-key.pub`。两条路径都要求本机有 `mcopy`（mtools）。

脚本校验 deploy-bundle 与 root-install 各自的摘要、bundle/root provenance、槽位，以及 `expanded_bytes` 能装进实时 `sunuefi_root`。脚本只写 `boot_a`、`sunuefi_esp`、`sunuefi_root`，不切活动槽，拒绝 fastbootd、错误产品、多台设备和不完整材料。

首装路径拒绝 `--skip-checksum`。两份摘要、provenance、槽位与容量都必须在第一笔写入前通过。完成后设备从 Slot A 重启，请求页按 `device-boot/boot-request.json` 进入 Linux。

Windows 脚本用系统自带的 Windows PowerShell 读 JSON 清单。槽位与容量核对走 `ConvertFrom-Json`，找不到 `powershell` 就直接停下。

## SELinux 策略模块

`rootfs/selinux/piano_hardware.te` 是策略源码，`piano_hardware.pp` 是编译产物。镜像里装的是 `.pp`，改 `.te` 不重新编译不会生效。`piano-selinux-policy.service` 在首次开机时导入 `piano_hardware.pp`，之后的更新靠 `semodule -i`。

编译要在有 `checkpolicy` 的 Fedora 上做，模块名必须和文件名一致：

```sh
cd rootfs/selinux
checkmodule -M -m -o piano_hardware.mod piano_hardware.te
semodule_package -o piano_hardware.pp -m piano_hardware.mod
```

每条 `allow` 都对应一次真机 AVC 拒绝，注释里写明是哪个单元、哪次启动。策略放宽前先在设备上 `semodule -i` 试装。确认对应单元从 `failed` 变成 `inactive` 后，再用 `semodule -r piano_hardware` 卸载。验证过的规则才写回 `.te` 并重新编译。

## PR 验收门槛

五项全部通过后，才能把 PR 标为设备验收通过：

1. 同一次新流水线产出的 `deploy-bundle` 和 `root-install` 已记录 run ID 与摘要。
2. 从原始 GPT/UFS 开始完成首次分区、显式擦除 userdata、Android 首次开机。
3. Slot A 冷启动进入所选桌面档（`gnome`、`kde`、`niri` 或 `hyprland`），不是热修复或历史镜像启动。
4. 完整重启一次后仍能进入同一档，且 `systemctl --failed` 为空。
5. Android→Linux、Linux→Android 和原厂 Recovery 都完成，保存屏幕、命令输出、
   `uname -r`、`/usr/lib/modules/`、F2FS 挂载与分区容量证据。

任一项失败都不能写成“设备验收通过”。构建或离线测试通过只记为 CI 证据。证据格式见[安装指南的真机验收](installation.md#十-真机验收清单)。

设备热修复替代不了发布资产测试。PR 最终必须从原始 GPT/UFS 布局开始，用流水线重新生成的 rootfs 与 ESP 完成冷启动、进入所选桌面和重启复现。

`fastboot erase userdata` 是首次安装的必经写入，不能省略。`plan` 会检查 `/data` 的 formattable 合同，但 AOSP `fs_mgr` 只在 `wiped`、或元数据加密关闭且 `!is_fs_valid` 时自动格式化。缩短 GPT 本身不会清掉旧的 F2FS 超级块。

实机上擦除后 Android 会停在 Recovery，要在 Recovery 里再选一次“清除数据”才重建加密 userdata。现象与现场步骤见[已知问题](known-issues.md#首次安装的-userdata)。这一步目前要人工处理，执行器只负责擦除。

所以执行器必须显式擦除，再让 Android 第一次启动，重建加密 userdata 并完成首次开机。不要直接在宿主上格式化 Android 的加密映射。

会话的 `result.json` 记录每个完成的阶段和错误。GPT 写入或擦除之后中断时，先看记录和实际设备状态，不要自动重试、恢复旧 GPT 或启动 Android。恢复 GPT 也找不回已擦除的 Android 文件。

## 里程碑

1. **PR 与骨架**：建立 PR 分支与 `packages/` 结构，先落没有硬件争议的基础包
   （`piano-fedora-configs`、`sm8750-video-accel`、`alsa-xiaomi-piano`）。
2. **守护进程规范化**：`xiaomi-piano-thp`、`piano-camera`、`uperf-linux`、`libssc`
   的打包定义入库，`scripts/build-rpms.sh` 能在本机或容器里跑完整的 rpmbuild。
3. **纯净 DNF 装配链**：`scripts/build-clean-rootfs.py` 从本地 RPM 源直接装配 F2FS
   镜像，替掉旧的 finalize 注入路径。`tests/check_clean_rootfs.py` 与 `make plan`
   校验镜像里的文件全部归属 RPM 数据库。`make plan-desktop DESKTOP=kde` 打印
   另一档的装配计划。
4. **安装链**：`flash-linux.sh` 与 `flash-linux.bat` 带设备身份、分区容量、
   摘要校验。安装文档同时保留不用脚本的手动路径。以下能力尚未具备：
   - 脚本只接受 Android 当前槽为 `a`，不能装到任意槽。
   - 只检查 `boot_a`、`sunuefi_esp`、`sunuefi_root` 是否存在及容量下限，不核对
     GPT 的 CRC、类型 GUID 或整表布局。
   - 分区不存在时直接拒绝，首次分区仍走 `install-f2fs.py`。
5. **真机终验**：九类 RPM 已上机，触控滤波、音频、编解码、传感器与自动旋转
   都可用。冷启动、五门硬件验收与双系统往返仍待完整记录。
