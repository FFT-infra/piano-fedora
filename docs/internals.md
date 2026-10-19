# 维护与验收

这一页面向维护者：流水线从哪进入、产物之间怎么绑定、run id 怎么传、PR 什么时候
算设备验收通过。只想装系统的读者看[安装指南](installation.md)。

上游 pin 与跟进规则见[上游跟踪](upstream.md)，产物定义见[部署产物](artifacts.md)。

## 流水线入口

PR 分支里的 workflow 不等于 Actions 里可启动的入口。GitHub 只为默认分支
`main` 上已注册的 workflow 提供 `workflow_dispatch`；只在 PR 分支新增的
`build-piano-rpms.yml` 在分支状态下查不到 workflow_id。发布链因此统一从
已经注册的 `host-check` 启动 RPM 矩阵复用作，再由 `build-rootfs` 按
`host-check.yml` 解析同一次 run 的 `piano-rpms` artifact。

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

发布验收仍必须使用这一整条链的新产物，并从原始 GPT/UFS 布局重新分区；
不能沿用 2026-10-19 手工补入两个会话 RPM 的 rootfs。

## Actions 材料

手动运行 `prepare-install`，选择完整 deploy-bundle 的 run、首装固定为
`android_slot=_a`。Linux 容量留空时使用 `product.json`。
这一阶段不再需要公钥：SSH 公钥在刷写时用 `--ssh-key` 注入，私钥和公钥都不进
Actions。三个 artifact 分别为：

| artifact | 内容 |
| --- | --- |
| root-install | 扩容并配置后的 F2FS sparse、容量/元数据校验记录、摘要 |
| boot-tools | 同核心的 selector、Android 静态原生工具、产品固件、自包含安装入口 |
| host-boot-tool | 静态宿主 BOOT 重打包工具及来源记录 |

boot-tools 必须与所选 deploy-bundle 的 UEFI 产品镜像逐字节相同。
安装入口位于 boot-tools 的 `installer/scripts`；其中的锁定上游代码带源码清单，
不需要在设备主机重新编译。普通发行版主机需要 Python 3、adb、fastboot；
BOOТ 准备使用 Actions 提供的宿主工具。

## 本机 BOOT

先确认当前原 BOOT 完整摘要、槽位和系统版本。`prepare-device-boot.py` 使用
这些明确输入和同核心的 Actions 工具生成 `boot_android.img`、`boot_linux.img`
及 `boot-request.json`。普通 Android 为默认目标。

包装后恢复必须得到完整原 BOOT 的同一 SHA256。Linux 持久请求、只读预览、
改回 Android 后的再次完整恢复都要成功。文件检查不替代真机旁路和 Recovery 验收。

使用 `python3 scripts/prepare-device-boot.py --help` 查看文件参数。
原 BOOT、生成的设备 BOOT、安装记录和私钥均留在本地，不进入代码仓库。

## 实际首次安装

以下命令是 PR 真机验收的唯一主流程。所有材料必须来自同一条新流水线，
当前已经启动的 GNOME、历史镜像和热修复都不算通过。自动化的破坏性步骤
（`install-f2fs.py apply`）与手动分区做同一件事；手动分区、刷写与回滚的逐条
命令在[安装指南](installation.md)第四节和三之二节。

### 1. 固定本次流水线与设备身份

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

序列号、槽位、原 BOOT 摘要和完整 GPT 基线必须写入本次安装会话。设备不是
`piano`、Bootloader 未解锁、`sys.boot_completed` 不是 `1`，全部停止。

### 2. 生成并复核 BOOT 与首次分区计划

使用 `prepare-install` 产出的 `boot-tools`、`host-boot-tool` 和当前原 BOOT：

```sh
# 先记录当前原厂 BOOT 的完整摘要；下面两处必须使用同一个值。
fastboot getvar current-slot
fastboot fetch boot_a stock-recovery/boot.img
sha256sum stock-recovery/boot.img

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

`plan` 只读，不写设备。逐项复核计划里的产品、序列号、磁盘、槽位、完整 GPT
基线、原 BOOT 摘要、输入包摘要和 Linux root 容量；任何一项与现场不一致就停止。
同时确认设备当前槽位是 `a`，且 `root-install-manifest.json` 的顶层
`android_slot` 是 `_a`。首装不能用 `_b`：一键脚本会固定写 `boot_b`，那会
覆盖 Android 的唯一启动路径。

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

执行器会重新读取设备、保存原 GPT/BOOT、写入并读回完整 GPT、显式执行
`fastboot erase userdata`，然后传输 ESP、F2FS sparse root 和当前槽的 Android
默认 BOOT。结束时状态应为 `IMAGES_TRANSFERRED_FIRST_BOOT_PENDING`。

此时 Linux 还没有启动，`apply` 结束时设备仍停在原厂 Fastboot。先断开 USB
并执行一次普通重启，让设备按当前槽启动 Android：

```sh
fastboot reboot
```

等首次开机完成、`adb shell getprop sys.boot_completed` 返回 `1`。这是让
Android 在缩短后的分区上重建加密 userdata 的必要步骤；不能在宿主格式化为
Android 加密分区，也不能跳过。

### 4. 回到 Fastboot 并刷入 Linux

Android 首次初始化完成后关机，按【电源键 + 音量减键】进入原厂 Fastboot。
确认 `current-slot` 是 `a`，且 root-install 的 `android_slot` 是 `_a`，然后运行：

```sh
chmod +x flash-linux.sh
./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub   # 不装密钥时去掉 --ssh-key
```

如果第 3 步已经用 `install-f2fs.py --ssh-key` 注入过，这里不必再传；重复注入
只会覆盖同一个 `/piano-ssh-key.pub`。两条路径都要求本机有 `mcopy`（mtools）。

脚本会校验 deploy-bundle 与 root-install 各自的摘要、bundle/root provenance、
槽位和 `expanded_bytes` 与实时 `sunuefi_root` 的精确容量，只写 `boot_b`、
`sunuefi_esp`、`sunuefi_root`，并拒绝 fastbootd、错误产品、多个设备和不完整
材料。首装路径拒绝 `--skip-checksum`：两份摘要、provenance、槽位和容量都必须在
第一笔写入前完成。完成后设备重启到 Slot B。Windows 脚本要求 PowerShell 5
或更高版本，因为槽位和容量核对使用了 `ConvertFrom-Json`。

## PR 验收门槛

以下五项全部通过后，才可以把 PR 标为设备验收通过：

1. 同一次新流水线产出的 `deploy-bundle` 和 `root-install` 已记录 run ID 与摘要。
2. 从原始 GPT/UFS 开始完成首次分区、显式擦除 userdata、Android 首次开机。
3. Slot B 冷启动进入 GNOME 桌面，不是热修复或历史镜像启动。
4. 完整重启一次后仍能进入 GNOME，且 `systemctl --failed` 为空。
5. Android→Linux、Linux→Android 和原厂 Recovery 均完成，保存屏幕、命令输出、
   `uname -r`、`/usr/lib/modules/`、F2FS 挂载与分区容量证据。

任一项失败都不能写成“设备验收通过”；构建或离线测试通过只能记为 CI 证据。
验收证据格式见[安装指南的真机验收](installation.md#真机验收)。

设备热修复不能替代发布资产测试。PR 最终必须从原始 GPT/UFS 布局开始，使用本
流水线重新生成的 RootFS 与 ESP 完成冷启动、GNOME 桌面与重启复现。

`fastboot erase userdata` 是首次安装的必经写入，不能省略。`plan` 仍会检查
`/data` 的 formattable 合同，但 AOSP `fs_mgr` 只在 `wiped` 或元数据加密关闭
且 `!is_fs_valid` 时自动格式化；GPT 缩短本身不会清掉旧 F2FS 超级块。因此
执行器必须显式擦除，随后第一次启动 Android，让它重建加密 userdata 并完成
首次开机。不能直接在宿主格式化 Android 的加密映射。

会话的 result.json 记录每个已完成阶段和错误。GPT 写入或擦除后发生中断时，
先看记录与实际设备状态，不自动重试、恢复旧 GPT 或启动 Android。
恢复 GPT 不能恢复已擦除的 Android 文件。

## 里程碑

1. **里程碑 1：开辟 PR 与基底架构搭建**
   - 建立 PR 分支，设立 `packages/` 结构骨架；
   - 梳理第一批无硬件争议的基础包（`piano-fedora-configs`、`sm8750-video-accel`、`alsa-xiaomi-piano`）。
2. **里程碑 2：核心守护进程 RPM 规范化与编译闭环**
   - 迁移并规范 `xiaomi-piano-thp`、`piano-camera`、`uperf-linux`、`libssc` 的打包定义；
   - 构建 `scripts/build-rpms.sh` 本地/容器编译流水线，确保单测与 rpmbuild 100% 通过。
3. **里程碑 3：纯净 DNF RootFS 装配链实现（✅ 已完成）**
   - 实现由本地 RPM 源直接装配 F2FS 镜像的全新脚本 `scripts/build-clean-rootfs.py`，淘汰旧的混杂 finalize 注入逻辑；
   - 引入 `tests/check_clean_rootfs.py` 与 `make plan-clean` 自动化单测，校验文件全量属于 RPM 数据库。
4. **里程碑 4：面向普通用户的工业级安装链（🟡 代码完成，真机未验收）**
   - 编写带设备身份校验、目标分区存在性/容量校验、SHA256 镜像完整性校验与
     zstd 自动解压的跨平台刷写程序（`flash-linux.sh` 与 `flash-linux.bat`）；
   - 编写精确到每一步命令行的刷机安装文档（`README.md` 与
     `docs/installation.md`），配齐 `package-release-bundle.sh`；
   - 同时提供**不使用一键脚本**的手动刷机章节：Release 资产清单、逐条
     `fastboot` 命令、SHA256 校验、读回验证与回滚方法。

   尚未做到、不得声称的能力：
   - **没有动态目标槽位**：脚本固定 `fastboot set_active b`；它只接受
     Android 当前槽为 `a`，否则在写入前拒绝，不能把 Linux 装到任意槽；
   - **没有完整 GPT 分区表校验**：只检查 `boot_b`/`sunuefi_esp`/`sunuefi_root`
     是否存在及容量下限，不核对分区表 CRC、类型 GUID 或整表布局；
   - **没有首次分区能力**：`sunuefi_esp`/`sunuefi_root` 不存在时脚本直接拒绝，
     首次分区仍走 `install-f2fs.py`；
   - 上述脚本路径已用桩设备跑通（无 `erase`、写入序列与预检顺序符合预期），
     **但尚未在一台纯净真机上按 Release 资产完整执行**。

5. **里程碑 5：纯净真机全流程实操与终验（🟡 进行中 / 实机已全部装配）**
   - 实机（Xiaomi Pad 8 Pro / `piano`）已成功部署全部 9 个解耦 RPM 包，硬件 Iris VA-API 硬解、ALSA UCM2 音频、卡尔曼触控滤波与传感器代理全部实机就绪；
   - 待下次真机重启验证 `piano-boot-request-autoconfig.service` 开机首发自愈与盲测全流程。

