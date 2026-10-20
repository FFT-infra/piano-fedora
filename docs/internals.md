# 维护与验收

本页面向维护者：流水线从哪进入，产物之间怎么绑定，run id 怎么传，PR 什么时候算设备验收通过。只装系统的读者看[安装指南](installation.md)。

上游 pin 与跟进规则见[上游跟踪](upstream.md)，产物定义见[部署产物](artifacts.md)。

## 流水线入口

PR 分支里的 workflow 不等于 Actions 里能启动的入口。GitHub 只给默认分支 `main` 上已注册的 workflow 提供 `workflow_dispatch`；只在 PR 分支新增的 `build-piano-rpms.yml` 在分支状态下查不到 workflow_id。所以发布链统一从已注册的 `host-check` 启动 RPM 矩阵复用作，再由 `build-rootfs` 按 `host-check.yml` 解析同一次 run 的 `piano-rpms` artifact。

```text
host-check (build_piano_rpms=true, kernel_run_id=...)
  └─ build-piano-rpms.yml (workflow_call)
       └─ piano-rpms

build-rootfs (piano_rpms_run_id=host-check run)
  └─ /usr/lib/modules/<release> 由 kernel-piano RPM 安装

build-image
  └─ 核对 rootfs 模块树 == 封存内核 manifest

build-bundle
  └─ deploy-bundle/sunuefi_esp.img + boot_sunuefi.img
```

发布验收必须用这一整条链的新产物，并从原始 GPT/UFS 布局重新分区。手工补进会话 RPM 的 rootfs 不算通过。

## Actions 材料

手动运行 `prepare-install`，选择完整 deploy-bundle 的 run，首装固定 `android_slot=_a`。Linux 容量留空时用 `product.json`。这一步不需要公钥：SSH 公钥在刷写时用 `--ssh-key` 注入，公钥和私钥都不进 Actions。三个 artifact 是：

| artifact | 内容 |
| --- | --- |
| root-install | 扩容并配置后的 F2FS sparse、容量/元数据校验记录、摘要 |
| boot-tools | 同核心的 selector、Android 静态原生工具、产品固件、自包含安装入口 |
| host-boot-tool | 静态宿主 BOOT 重打包工具及来源记录 |

boot-tools 必须与所选 deploy-bundle 的 UEFI 产品镜像逐字节相同。安装入口在 boot-tools 的 `installer/scripts`，锁定的上游代码附带源码清单，不需要在设备主机上重编译。普通发行版主机需要 Python 3、adb、fastboot；BOOT 准备用 Actions 提供的宿主工具。

`host-check` 的 `package_prepare_run` 是另一个可选入口：填一个成功的 `prepare-install` run，它调用 `package-install.yml` 把那份 `root-install` 压成 zstd，只压缩已校验的产物，不重建文件系统。

只读排查用的两个脚本不进流水线，直接在本机跑：`scripts/probe-device.sh` 取设备身份、槽位与 GPT，`scripts/verify-new-image.sh` 挂载镜像核对交接前提。两者都只读。

## 本机 BOOT

先确认当前原 BOOT 的完整摘要、槽位和系统版本。`prepare-device-boot.py` 用这些输入和同核心的 Actions 工具生成 `boot_android.img`、`boot_linux.img` 与 `boot-request.json`。默认目标是普通 Android。

包装后恢复必须得到完整原 BOOT 的同一 SHA256。Linux 持久请求、只读预览和改回 Android 后的再次完整恢复都要成功。文件检查替代不了真机旁路和 Recovery 验收。

使用 `python3 scripts/prepare-device-boot.py --help` 查看文件参数。
原 BOOT、生成的设备 BOOT、安装记录和私钥均留在本地，不进入代码仓库。

## 实际首次安装

以下命令是 PR 真机验收的唯一主流程。所有材料必须来自同一条新流水线，
当前已经启动的 GNOME、历史镜像和热修复都不算通过。自动化的破坏性步骤
（`install-f2fs.py apply`）与手动分区做同一件事；手动分区、刷写与回滚的逐条
命令在[安装指南](installation.md)第四节和三之二节。

### 1. 固定流水线与设备身份

记录以下 ID，后续每一步都核对它们不变：

```text
build-kernel run
host-check / build-piano-rpms run
build-rootfs run
build-image run
build-bundle run（deploy-bundle）
prepare-install run（root-install，bundle_run 必须等于上面的 build-bundle run）
```

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
# 先记录当前原厂 BOOT 的完整摘要；下面两处必须使用同一个值。
fastboot getvar current-slot

# 原厂 ABL 不实现 fastboot fetch，改从 Android 侧读块设备（需要 root ADB）。
adb -s <serial> shell su -c 'sha256sum /dev/block/by-name/boot_a'
adb -s <serial> exec-out su -c 'dd if=/dev/block/by-name/boot_a bs=1048576' \
  > stock-recovery/boot.img
sha256sum stock-recovery/boot.img
```

两条摘要必须相同，文件必须是 96 MiB（`stat -c%s`），否则执行器会拒绝。原厂 ROM 包里的 `images/boot.img` 也是 96 MiB，但只有设备还跑着那一版固件时才逐字节相同；用前先比摘要，不一致就以设备读出的为准。

python3 boot-tools/installer/scripts/prepare-device-boot.py \
  --stock-boot stock-recovery/boot.img \
  --boot-tools boot-tools \
  --host-tool host-boot-tool \
  --bundle deploy-bundle \
  --slot _a \
  --source-sha256 <current-boot-sha256> \
  --output device-boot

python3 boot-tools/installer/scripts/install-f2fs.py plan \
  --sunuefi boot-tools/installer/sunuefi \
  --serial <serial> \
  --bundle deploy-bundle \
  --root-install root-install \
  --device-boot device-boot \
  --stock-boot stock-recovery/boot.img \
  --plan first-install-plan.json
```

`plan` 只读，不写设备。逐项复核计划里的产品、序列号、磁盘、槽位、完整 GPT 基线、原 BOOT 摘要、输入包摘要与 Linux root 容量；任何一项与现场不符就停止。同时确认设备当前槽位是 `a`，`root-install-manifest.json` 顶层的 `android_slot` 是 `_a`。首装不能用 `_b`：一键脚本固定写 `boot_b`，会覆盖 Android 唯一的启动路径。

`device-boot/boot-request.json` 必须记录首装 Android 所在的
`/dev/disk/by-partlabel/boot_a`，其 `app_generation` 必须与
`deploy-bundle/PianoUEFI-product.img` 的 APPv1 载荷一致。请求页写在
`boot_a` 的合体 BOOT 内，Linux 启动镜像才写入 `boot_b`；不要把请求配置
改成 `boot_b`。`root-install` 已在 `etc/piano/boot-request.json` 写入同一份
配置，首启后也不要猜测设备名或改槽。否则 Linux 的“返回 Android”和后续
启动请求会失败。

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

执行器在 GPT 写入和擦除 userdata 之前就校验公钥并确认 `mcopy` 可用，失败时
不动设备。公钥写进会话目录里的一份 ESP 副本，`deploy-bundle/sunuefi_esp.img`
保持原样；`result.json` 的 `ssh_key_injected` 与 `sunuefi_esp_sha256` 记录这次
注入和实际刷入的摘要。不传 `--ssh-key` 时只保留口令登录。

执行器重新读取设备，保存原 GPT 与 BOOT，写入并读回完整 GPT，显式执行 `fastboot erase userdata`，然后传输 ESP、F2FS sparse root 和当前槽的 Android 默认 BOOT。结束时状态应为 `IMAGES_TRANSFERRED_FIRST_BOOT_PENDING`。

这时 Linux 还没启动，`apply` 结束时设备停在原厂 Fastboot。先断开 USB，再执行一次普通重启，让设备按当前槽启动 Android：

```sh
fastboot reboot
```

等首次开机完成，`adb shell getprop sys.boot_completed` 返回 `1`。这是让 Android 在缩短后的分区上重建加密 userdata 的必要步骤，不能跳过，也不能在宿主上格式化成 Android 加密分区。

### 4. 回到 Fastboot 并刷入 Linux

Android 首次初始化完成后关机，按【电源键 + 音量减键】进入原厂 Fastboot。
确认 `current-slot` 是 `a`，且 root-install 的 `android_slot` 是 `_a`，然后运行：

```sh
chmod +x flash-linux.sh
./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub   # 不装密钥时去掉 --ssh-key
```

如果第 3 步已经用 `install-f2fs.py --ssh-key` 注入过，这里不必再传；重复注入
只会覆盖同一个 `/piano-ssh-key.pub`。两条路径都要求本机有 `mcopy`（mtools）。

脚本校验 deploy-bundle 与 root-install 各自的摘要、bundle/root provenance、槽位，以及 `expanded_bytes` 与实时 `sunuefi_root` 的精确容量。它只写 `boot_b`、`sunuefi_esp`、`sunuefi_root`，拒绝 fastbootd、错误产品、多台设备和不完整材料。首装路径拒绝 `--skip-checksum`：两份摘要、provenance、槽位与容量都必须在第一笔写入前通过。完成后设备重启到 Slot B。Windows 脚本用系统自带的 Windows PowerShell 读 JSON 清单：槽位与容量核对走 `ConvertFrom-Json`，找不到 `powershell` 就直接停下。

## PR 验收门槛

五项全部通过后，才能把 PR 标为设备验收通过：

1. 同一次新流水线产出的 `deploy-bundle` 和 `root-install` 已记录 run ID 与摘要。
2. 从原始 GPT/UFS 开始完成首次分区、显式擦除 userdata、Android 首次开机。
3. Slot B 冷启动进入 GNOME 桌面，不是热修复或历史镜像启动。
4. 完整重启一次后仍能进入 GNOME，且 `systemctl --failed` 为空。
5. Android→Linux、Linux→Android 和原厂 Recovery 均完成，保存屏幕、命令输出、
   `uname -r`、`/usr/lib/modules/`、F2FS 挂载与分区容量证据。

任一项失败都不能写成“设备验收通过”。构建或离线测试通过只记为 CI 证据。证据格式见[安装指南的真机验收](installation.md#真机验收)。

设备热修复替代不了发布资产测试。PR 最终必须从原始 GPT/UFS 布局开始，用本流水线重新生成的 rootfs 与 ESP 完成冷启动、GNOME 桌面和重启复现。

`fastboot erase userdata` 是首次安装的必经写入，不能省略。`plan` 会检查 `/data` 的 formattable 合同，但 AOSP `fs_mgr` 只在 `wiped`、或元数据加密关闭且 `!is_fs_valid` 时自动格式化；缩短 GPT 本身不会清掉旧的 F2FS 超级块。所以执行器必须显式擦除，再让 Android 第一次启动，重建加密 userdata 并完成首次开机。不要直接在宿主上格式化 Android 的加密映射。

会话的 result.json 记录每个完成的阶段和错误。GPT 写入或擦除之后中断时，先看记录和实际设备状态，不要自动重试、恢复旧 GPT 或启动 Android。恢复 GPT 也找不回已擦除的 Android 文件。

## 里程碑

1. **PR 与骨架**：建立 PR 分支与 `packages/` 结构，先落没有硬件争议的基础包
   （`piano-fedora-configs`、`sm8750-video-accel`、`alsa-xiaomi-piano`）。
2. **守护进程规范化**：`xiaomi-piano-thp`、`piano-camera`、`uperf-linux`、`libssc`
   的打包定义入库，`scripts/build-rpms.sh` 能在本机或容器里跑完整的 rpmbuild。
3. **纯净 DNF 装配链**：`scripts/build-clean-rootfs.py` 从本地 RPM 源直接装配 F2FS
   镜像，替掉旧的 finalize 注入路径。`tests/check_clean_rootfs.py` 与 `make plan-clean`
   校验镜像里的文件全部归属 RPM 数据库。
4. **安装链**：`flash-linux.sh` 与 `flash-linux.bat` 带设备身份、分区容量、
   摘要校验；安装文档同时保留不用脚本的手动路径。以下能力尚未具备：
   - 脚本固定 `fastboot set_active b`，只接受 Android 当前槽为 `a`，不能装到任意槽；
   - 只检查 `boot_b`、`sunuefi_esp`、`sunuefi_root` 是否存在及容量下限，不核对
     GPT 的 CRC、类型 GUID 或整表布局；
   - 分区不存在时直接拒绝，首次分区仍走 `install-f2fs.py`。
5. **真机终验**：九类 RPM 已上机，硬解、音频、触控滤波与传感器代理可用；
   冷启动、五门硬件验收与双系统往返仍待完整记录。
