# 上游跟踪

构建来源由 `sources.lock.json` 固定。上游 main 的进展只用于审阅。更新文档不改变已构建镜像的来源，旧二进制也不会自动获得新实现。

## 当前 pin

构建消费的上游以 `sources.lock.json` 为准，当前 pin 的 commit 写在 `sources.lock.json` 里。其他消费源的 commit 也记在 `sources.lock.json`。研究与对照仓库登记在 `upstreams.json`，不参与构建。本页的引用链接按当前 pin 固定到具体提交。

### 对照基准

`sources.lock.json` 里有两条 pin 只进对照任务、不进产物：

- `vendor-kernel`：`android_kernel_xiaomi_piano` 的 DTS 源码，被 `scripts/compare-vendor-dt.py` 读取，用来核对 SunUEFI overlay 的目标节点在厂商硬件描述里是否存在。
- `vendor-blobs`：`android_vendor_xiaomi_piano` 的固件与传感器配置，被 `scripts/compare-vendor-blobs.py` 按 git blob 摘要比对。只读文件清单，不下载内容，不进镜像。

这两条属于消费上游，所以进 `upstream-watch` 的漂移报告，但白名单里没有它们：厂商树一更新就要人工看对照报告有没有新差异。任何出货路径引用 `vendor-kernel` 都算违规——它描述的是 Android 下游 DT，和启动用的 `board.dtb` 不是同一条血缘，见 `docs/device-tree.md`。

第一份固件对照（2026-11-08）覆盖厂商仓库提交的 148 个固件文件：5 个与出货树逐字节相同，含两个 Novatek 触控固件、Nanosic MCU 与 `gen80000_gmu.bin`；4 个不同，`gen80000_aqe/sqe`、`gen80000_zap.mbn`、`vpu35_4v.mbn`；其余两边各自独有，厂商侧以 `CAMERA_ICP` 为主。厂商仓库不是 vendor 分区的全集，所以"厂商有、我们没有"只说明这份清单里没有。报告同时核对 `runtime/matrix-owned-paths.txt` 的 99 条固件所有权路径：与出货树 99 个文件一一对应，没有缺失也没有未声明的。

`packages/xiaomi-piano-firmware` 的 `%prep` 现在核对上游自带的 `SHA256SUMS`（99 项）。在此之前 `Source0` 只订了提交号，tarball 内容被换掉也发现不了。

固件包还要再叠一层厂商 ROM。SunUEFI 的 `config/release.json` 用 `firmware.overlays` 声明厂商叠层，当前 10 份、109 个文件，全部来自 OS4.0.6.0.XPYCNXM：`hyperos4-{adsp,cdsp,fingerprint}`、`piano-display`，以及 `piano-{ath12k,bluetooth,keyboard,qcom,qcom-vpu,touch}`。每份带 `manifest.json`，逐文件记 bytes 与 sha256，可选的 `directory` 给出落点。上游 piano-firmware tarball 里没有 cdsp 与指纹固件，adsp 也是另一版 ROM，所以 99 项之外还有这一层，由 `packages/xiaomi-piano-firmware/firmware-overlays.py` 在写盘前逐个核对。`piano-display` 的两份面板校准写成 `qcom/sm8750/`，其余各份落在固件根目录；落点由清单声明，脚本不另写一份映射。清单从 `release.json` 读，叠层的提交号与 `sources.lock.json` 的 `sunuefi` pin 相同，`tests/check_firmware_overlays.py` 守住两者一致。

## 自动跟踪

`.github/workflows/upstream-watch.yml` 每周一 03:23 UTC 运行一次 `scripts/audit-upstreams.py --execute`。`scripts/upstream-watch.py` 处理执行结果，按以下规则分流：

- 锁定 ref 指向的提交与 `sources.lock.json` 的 pin 不同时，算作漂移。漂移汇总进一个 `upstream-drift` issue。下次运行若没有漂移，`scripts/upstream-watch.py` 关闭这个 issue。
- 白名单（`crosvm`、`librevm`、`uperf`、`piano-mesa`、`piano-sensors`、`piano-mipps-auth`）里的漂移在 issue 里附带一条改 pin 命令。workflow 本身不碰锁文件，也不开 PR：仓库设置不允许 Actions 创建 PR。人跑 `scripts/upstream-watch.py bump` 改 `sources.lock.json` 的 `commit`，`ref` 保持不动。这些项目属于纯用户态上游，`make test` 与 CI 能够覆盖检查；受影响产物仍要按本页末节重编。
- 内核、UEFI、相机/触控、固件和 debian-piano 只作报告。离线 CI 代替不了这些组件的真机验收。维护者按后文「每次跟进上游」的规则评审 pin。

漂移判断只看 pin，不看 `docs/upstreams.json` 的 `reference_commit`。`reference_commit` 是人工基线，只在 `--record-baseline` 时推进。拿 `reference_commit` 判断，会把没有位移的上游报成更新。

自动跟踪只覆盖 16 个消费上游。`docs/upstreams.json` 里的 83 个对照仓库不进 `upstream-watch`：它们没有 `lock_key`，`scripts/upstream-watch.py` 只处理带 `lock_key` 的行。这些仓库要人工复核，用下面两条命令：

```sh
python3 scripts/audit-upstreams.py --execute --output build/upstream-audit.json
python3 scripts/audit-upstreams.py --execute --record-baseline --output build/upstream-audit.json
```

第一条列出谁比基线新，第二条把核到的提交写成新基线。第二条会改 `docs/upstreams.json`，只在确认复核过之后跑。

登记表的价值在于「谁值得看」，不在于「谁被自动盯着」。把仓库目录整个抄进来会淹没真正要跟的那几行，所以未登记不等于遗漏。

## 每次跟进上游

1. 读取远端 main 的完整提交号。比对当前 pin 与远端的提交、文件和实际代码。发布说明只当索引用。
2. 启动、交接、内存、DTB、模块或硬件准备有变时，重读相关合同与消费者。检查补丁 series 的 base 和目标摘要。不要直接替换某一件旧产物。
3. 改 pin 之前核对所有消费路径、构建容器和依赖。重新生成受影响的内核、运行时、root 文件系统、initramfs、ESP 和 BOOT。清单同步更新。
4. 下载并核对实际产物，更新部署入口。构建、文件验证和实机行为分别记账。不能继承上游另一台设备的结果。

### 2026-11-14

16 个锁定源的 pin 都停在各自远端 HEAD，没有新提交要跟。`sunuefi` 从 `018634f1` 推进到 `4d369d37`，`crosvm` 推进到 `1472e011`，其余 14 个源不动。

变化集中在充电栈。7 条补丁进入封存序列 `0106`–`0112`，把原厂 4.0.6 循环充电策略、QC/PPS 输入与直充能力选择、电池健康降流和 PPS 调压步长接进 `piano_mca.c` 与 `bq27xxx`。这批代码随封存序列编译，功率、温控与关机充电还没有真机记录，45 W 不作声称。用户态新增按需运行的 `piano-charge-monitor.service`，默认关闭，用法与边界见 `docs/known-issues.md` 充电节。固定源码下载增加同源镜像回退，`config/release.json` 补入 `stable_source_mirrors`，上游 `command_line` 未改。

删除 Linux 分区、把空间还给 Android 不再需要手工改表。上游引导助手在 WebUI 存储页提供 **删除并归还 Android 空间**。分区目标与 F2FS 元数据能覆盖的容量分开记账，SIT、SSA 与 section 大小决定文件系统能扩到多大，盖不住整块 `userdata` 时只扩到可覆盖范围；重启后由 SukiSU 早期维护用 `resize.f2fs -k -t` 完成。`docs/installation.md` 与 `docs/known-issues.md` 已改成指向这个入口。`install-f2fs.py` 不消费新增的两个目标字段，安装接口没变。

内核封存序列完成全链重放与身份修复。`0095-input-nt36532e-matrix.patch` 把手指矩阵解码搬进内核，与用户态 5 级卡尔曼滤波路径冲突，排除。排除后上游保留 111 条，自有 35 条后移到 `0112`–`0146`，全序列 146 条，末树 `target_tree 507067737e5e`。旧生成脚本把 `original_commit` 写成了 `git am` 新生成的提交哈希，17 条上游行的 `tree` 还带着含 `0095` 的全链结果；现在前者改回补丁 `From` 头里的格式补丁身份，全部 `tree` 按排除 `0095` 后的基线重放重算。7 个 `patches/kernel/000*` 与 `patches/kernel/series.json` 的目标摘要及 creates 清单同步重生成。

首次启动 GPU 未绑定的修复随 `0001` 进 `config/release.json` 的 `command_line`：加正的 `deferred_probe_timeout`，给 `gpucc` 留出延迟探测窗口。`tests/check_boot_cmdline.py` 离线锁这条合同，编译前后按正整数断言，数值只写在补丁里。判据与证据分层见 `docs/known-issues.md`。

已核：干净 HEAD 上 9 组补丁 `CHECKED`/`APPLIED`，`load_series()` 通过，146 条 `git am` 重放逐条对上 `tree`，`make test` 87 OK / 1 SKIP。未核：内核与固件编译、发布链、F2FS 归还、充电功率与温控、GPU 绑定、UKI `%posttrans` 升级、冷装。

### 2026-11-13（续）

`sunuefi` `4f663563` → `018634f1`（14 个提交），`crosvm` `75f930e9` → `9b63ea5f`，`piano-integration` `d9178221` → `e24b3007`。

SunUEFI 这 14 个提交里有三处动到了本仓库的补丁面。`uefi/core` 按子系统拆成 `usb/`、`product/`、`storage/` 等目录，`uefi-dwc3-halt`、`uefi-ebs-identity`、`uefi-ufs` 三个补丁的目标路径跟着改。`tools/host/` 改名 `tools/desktop/`，`kernel-f2fs/0001` 里引用这两个路径的一行要重写，其余三个内核配置补丁靠这一行的输出顺序自然接上。`tools/package_release.py` 加了 `cpp` 预处理和 label 目标解析，本仓库的 `packaging-overlays/0001` 重新 rebase，保留 `__fixups__` 解析、`target-path` 判空与 phandle 覆盖检查，并把 include 目录在 `--kernel-source` 缺省时回落到锁定的内核检出。

内核封存序列 104 → 105 条，新增 `0105-dpu-piano-fractional-sampling.patch`（非整数倍放大的接缝采样）。它和本仓库的 `0105-dpu-defer-slave-cleanup` 同号但不同文件（改 `dpu_plane.c`，本仓库改 `dpu_encoder*.c`），所以 35 条自有补丁整体后移到 `0106`–`0140`，`target_tree` 由 `60db9588` 推进到 `8848124d`。

固件换到 OS4.0.6.0.XPYCNXM，叠层从 4 份扩到 10 份。新增的 `piano-tsens.dtso` 没有跟进：它的 `#include` 由上游在 `--kernel-source` 下从内核源码生成 `sm8750-thermal-zones.dtsi`，本仓库的 bundle 任务没有那棵源码树，温区仍由厂商 `board.dtb` 的 `/soc/thermal-zones` 提供。

`crosvm` 的 5 个提交全是 recipe 依赖滚动。`piano-integration` 的 40 个提交是 1.3.0 候选版本的 Ubuntu 侧改动，本仓库只取 `device/piano-thp/`、`device/camera/` 与 `tools/extract-thp-calibration.py`，这些路径在这批提交里没有变化，只有 `NOTICE` 多了 Mesa 与 NPU 的署名段。

### 2026-11-13

`vendor-kernel` 的上游删掉了 `seventeen` 分支，默认分支改为 `6.6`。pin 从 `ed494167` 移到 `6.6` 的 `0d7cc8a`，比原来多 6 个提交：Focus Pen 的 HID 上报、nt36532 笔设备去掉 `ABS_BRAKE`、`qcom_reboot_reason` 的越界修复、cnss2 加载 piano 板级文件，以及一次 android15-6.6 合并。这些提交没有改 `arch/arm64/boot/dts/`，重跑的 `compare-vendor-dt.py` 结果不变：23 个 overlay、75 个目标，`missing_in_vendor_unexplained` 为 0。`vendor-blobs` 分支未变，仍在 `seventeen` 的 `c1e5a318`。

同时核对了其余 15 个锁定源，pin 都是远端当前 HEAD。

### 2026-11-12（续）

`sunuefi` `3a7e5c1b` → `4f663563`，`crosvm` `1eb6337c` → `75f930e9`，`piano-integration` 不动。SunUEFI 的 10 个提交里，`a139e996` 与 `3ab0085a` 修 DWC3 停止，其余是构建、GDM 等待 KMS 显示接口、Wayland 开发包与 CI 缓存权限。内核封存序列、`config/release.json`、`tools/package_release.py`、`tools/build_piano_full_kernel.py` 在这批提交里没有变化。crosvm 只有一条 dev-container 依赖滚动。

`patches/uefi-dwc3-halt/` 的四版补丁删除，只剩 `0001-halt-soft-reset-fallback.patch`：上游 `Halt()` 在等待超时或 END_TRANSFER 失败时直接返回 `EFI_TIMEOUT`，补丁在这条错误分支上补 core soft reset，以复位后的 `DevCtrlHlt` 作成功判据。`patches/uefi-ebs-identity/` 的目标摘要随 `PianoProductCore.c` 的上游改动重算。

已核：`scripts/apply-patches.py --check` 在新 pin 上给出 `CHECKED`；新固件用 wrapped BOOT 换核写回 `boot_b` 后启动到 Plasma，`stage=halt status=Success` 一次通过，兜底没有触发。未核：整条发布链、新固件配合新内核的冷装。

### 2026-11-12

三个 pin 推进：`sunuefi` `c720568` → `3a7e5c1b`，`crosvm` → `1eb6337c`，`piano-integration` → `d9178221`。后两个只动 recipe、tools、打包与文档，`device/`、`kernel/`、`initramfs/` 没有变化，按纯 pin 前进处理。

SunUEFI 的 `59e52548`（`feat(boot)!`）删掉了 Android boot v2 的 Linux 启动路径。固件改为用 `PianoApplicationRunFile` 直接加载 `\EFI\Linux\sunuefi.efi`，ESP 上不再有 systemd-boot、BLS 或 `Image` + `initramfs` + `board.dtb` 三件载荷。内核、initramfs、DTB 与 cmdline 打进 UKI，由 `systemd-ukify` 组装，节区是 `.linux`、`.initrd`、`.dtb`、`.cmdline`、`.osrel`、`.uname`。

DT 交接同时换了实现。`PianoDeviceTreeFixup.c` 实现 `EFI_DT_FIXUP_PROTOCOL`，在 `APPLY_FIXUPS` 下依次改写内存、面板、蓝牙地址、RTC 偏移与 Gunyah 资源管理器；`systemd-stub` 带 `RESERVE_MEMORY` 调用它，载荷 DTB 的 `/memory` 在启动时被换成设备当次布局，并按 `DramBytes` 与固定保留区校验。raw Linux 交接走同一个协议。我们自己的 `patches/uefi-live-memory` 与 `patches/uefi-live-rm` 已被上游覆盖，两组退役，不再参与 `apply-patches` 与固件哈希。

上游 7.3 封存序列从 92 条增到 104 条。新增的 12 条里，11 条是充电、温控与 FFC，收进封存序列；`0095-input-nt36532e-matrix.patch` 把手指矩阵解码搬进内核，没有时域滤波，还会撞我们已有的 `0108-input-nt36532e-panel-vendor-misc.patch`，这一条排除。我们自己的 35 条整体后移为 `0105`–`0139`，`0095` 留空号。

`build_piano_full_kernel.py` 的 `command_line()` 改成从 `config/release.json` 读整条 cmdline，`CONFIG_CMDLINE` 清空、`CONFIG_CMDLINE_FORCE` 改为 `n`。`patches/kernel/0001` 把那份配置里的 `rootfstype=ext4` 换成 `f2fs`，根类型由 UKI 的 `.cmdline` 节区携带，不再靠编译期 `CONFIG_CMDLINE`。

ESP 载荷随之改成单个 UKI。`build-esp-image.py` 只写 `/EFI/Linux/sunuefi.efi`，`verify-bundle.py` 核对同一路径，`release-assets.py` 的成员表去掉 `boot_sunuefi.img`。`kernel-piano` 的 `%posttrans` 与 `scripts/piano-kernel-esp-update` 改成重建 initramfs、重打 UKI、原子替换并留 `.bak`。

已核：`scripts/apply-patches.py --check` 在 HEAD 的新 clone 上给出 `CHECKED`；`make test` 全绿；`fedora-finger.patch` 对 HEAD 派生的触控源码 9 个 hunk 全部套上。未核：新 pin 的内核与固件编译、整条发布链、UKI 启动与设备实机行为。

### 2026-11-11

两个 pin 推进：`sunuefi` `4ef8740` → `c720568ea492e839040ea72c5bc79afb6812465d`，`dtc` `b1e46c0` → `774adc25940050fb06f241b1a2a98e7421e6ed7b`。`crosvm` 与 `piano-integration` 不动。

SunUEFI 的 7.3 封存序列从 90 条增到 92 条，新增上游的 `0091-dpu-hdr-identity-gamma.patch` 与 `0092-nanosic-detachable-leds.patch`。公开基线树是 `a3d0b8db615452e89f37be4454c80262c06ecc99`，92 条之后的上游树是 `e188f4582c2ede46d6d5d6b8649fd0f0316a3343`，末行提交 `79e32ae2694bd74626b78f4201719f2e7e56b560`。

我们自己的 38 条整体后移为 `0093`–`0130`，补丁内容不变，首条的 `original_parent` 改成新的上游末行提交。合并后的最终树是 `7a0b0da0fde1b932dca480505fb5e96f2ce93cc6`，末行 `original_commit` 为 `7c8ebb2458d7`。`patches/kernel` 的七段按 `0093`–`0096`、`0097`、`0098`–`0103`、`0104`、`0105`–`0122`、`0123`、`0124`–`0130` 重新生成。

`linux/userspace/piano-ram-hardware-prepare` 在新 pin 上多了 NixOS stage 1 的启动检查，根类型仍然只认 ext4。`patches/kernel-f2fs/0002` 重新套用后保留 F2FS 分支，目标摘要与 `scripts/stage-rootfs-payload.py` 的校验摘要一起更新。其余五组非内核补丁只换 `base.sunuefi_commit`，内容不变。

已核：`scripts/apply-patches.py --check` 在 `c720568` 的新 clone 上给出 `CHECKED`；`make test` 全绿。未核：新 pin 的内核编译、整条发布链与设备实机行为，按发布台账分别记账。

### 2026-11-10

四个 pin 一起推进：`sunuefi` `8b0fc42` → `4ef8740`，`fastrpc` `a56e9d4` → `8f59273`，`crosvm` `f0880b3` → `deac401`，`piano-integration` `9e3d127` → `e49a960`。后三个只动文档与一个头文件的安装，`make test` 能覆盖。SunUEFI 这一段包含内核 LLVM 23 + ThinLTO/O3、普通 Focus Pen 支持（#16/b8f2bf9）、充电热管理接口（f346521）与构建命名空间隔离（4ef8740），下面逐条记账。

内核侧换的是编译方式，不是硬件描述：`localversion` 变成 `-piano-sunuefi-cachy-optimize`，内核打开 `CC_OPTIMIZE_FOR_PERFORMANCE_O3`、`LTO_CLANG_THIN` 与 `CONFIG_ARM64_PIANO_OPTIMIZE`，工具链固定到 LLVM 23.1.5，由 `tools/host/prepare_kernel_llvm.py` 按 `config/kernel-llvm.json` 的摘要下载。外部模块编译（`scripts/prepare-runtime-kernel.py`）同步接入该环境，解决系统自带 clang 不认 `-mtune=oryon-1` 的问题。

`patches/linux/7.3/series.json` 的上游行增长到 90 条（含 7 条充电降流与热管理补丁），上游 base 树落到 `b0da508a`。我们的 38 条编号排为 `0091`–`0128`，最终 `target_tree` 是 `7b57d862`，末行 `original_commit` `7c8ebb24` 成为顶层的 `original_target_commit`。我们的 38 条无冲突合入新 base。

`patches/kernel` 的七段按新的段边界重新生成，`targets` 换成新补丁的前像摘要，`creates` 覆盖 `0091`–`0128`。另外五组补丁（`android-module`、`kernel-f2fs`、`kernel-gunyah`、`kernel-snd-seq`、`packaging-overlays`、`uefi-live-*`）只换 `base.sunuefi_commit` 并重生成，意图没变。

DTB overlay 从 18 个补到 22 个，`build-bundle.yml` 按 `release.json` 的顺序补了 `piano-pcie-clocks`、`piano-battery`、`piano-camera-sensors`、`piano-display-interconnects`。固件叠层多出 `piano-display` 一份，它的 `manifest.json` 带 `directory: qcom/sm8750`，`firmware-overlays.py` 原来一律平铺到根目录，这次改成读清单里的落点。

已核：`scripts/apply-patches.py --check` 在 `4ef8740` 的干净 clone 上给出 `CHECKED`，套到夹具上给出 `APPLIED`；`make test` 全绿；ccache 实测命中率 99.96%，全量编译时间从 56 分钟降至 6 分 52 秒。未核：新 pin 上的整条发布链与设备实机行为。这几项分别记账，在这轮验收完成前不发 v45.2。

`battery` overlay 在 `bq27z561_master@55` 上写 `ti,authentication-key`，来自上游自己的 DTB，本次未改动，记录在此便于以后审阅时认识它。

## 内存与面板边界

锁定上游的状态页只覆盖 16 GB、另一版 HyperOS、Debian/ext4 的设备。改装到 24 GB 的 Fedora/F2FS 机器不能照搬状态页的全部设备结论。

当前 Linux DTB 的 16 个非零 RAM 区间合计 16,470,685,696 字节。Raw Linux 交接修改面板 compatible、启动参数和 initrd 地址。Raw Linux 交接保留输入的内存描述。自动选面板不会自动发现 RAM。

上线后的只读更新计划要验两件事：Linux 描述的 RAM 是当次 Android FDT 的子集，固定保留区与 Linux RAM 相交的部分仍受保护。比较失败就拒绝更新。不要猜扩容后的地址，也不要放宽内存门槛。比较通过也不证明物理 DDR 所有权，也不证明完整 24 GB 可用。

### 两个原厂面板标识

原厂 ABL 用 `msm_drm.dsi_display0` 的值说明这台设备装的是哪块面板，UEFI 据此改写交接 DTB 的面板 `compatible`。

DTB 里的两个 profile 与原厂 UEFI 的两份面板描述文件一一对应。用原厂固件 `RawFiles/Panel_P81_*_dualdsi_dsc_vid.xml` 对比 `qcom,mdss-dsi-on-command` 可以验：

| 原厂标识 | 该 profile 的命令特征 | 对应原厂 XML |
| --- | --- | --- |
| `qcom,mdss_dsi_p81_42_02_0a_dualdsi_dsc_vid` | `c4 82 … c4 06` 块、`D1 20`、先 `B3` 后 `B2` | `Panel_P81_42_02_0a_...xml` |
| `qcom,mdss_dsi_p81_35_02_0b_dualdsi_dsc_vid` | `bc 77 07`、`D1 00`、先 `B2` 后 `B3` | `Panel_P81_35_02_0b_...xml` |

`35_02_0b` 那份 XML 的 `<PanelDescription>` 误写成 "P81 42 02 0a"，是原厂复制文件留下的错误，不能拿它判断归属；要看文件自己的命令块和 `PanelBuildIDReadBack`。

供应商归属由原厂触控驱动自己定：`nt36xxx.c` 的 `lcd_id_value` 注释写明 0 是 CSOT、1 是 BOE，同一处用这个值选 `piano_nova_{csot,boe}_thp_config.ini`。据此

| `lcd_id` | 供应商 | 面板 profile | 命令特征 |
| --- | --- | --- | --- |
| 0 | CSOT | `qcom,mdss_dsi_p81_42_02_0a_dualdsi_dsc_vid` | `c4 82 … c4 06` |
| 1 | BOE | `qcom,mdss_dsi_p81_35_02_0b_dualdsi_dsc_vid` | `bc 77 07` |

这与 `PianoFactoryPanel.h`、上游 `docs/devel/panel-selection.md` 的表一致，也与原厂 UEFI 的两份面板描述文件一一对应。本机 `lcd_id: 1`，所以是 BOE 模块，UEFI 读到的 `dsi_display0` 应当指向 `35_02_0b`。

2026-10-24 的文档曾把这一格写反（判成 `35_02_0b`=CSOT）。错在拿主线驱动的函数名当证据：驱动里 `piano_boe_*` 与 `piano_csot_*` 的名字来自移植本身，上游 `linux-sm8750-piano` 在 2026-10-22 把两者对调过一次，两次都没有改变任何字节。名字换来换去是循环论证，判据只能来自原厂侧。当前内核补丁里的函数名与上表相反，但发给面板的字节序列相同，功能上等价。

核对时注意两个 profile 在 DTB 里的写法不同：`35_02_0b` 用 `[...]` 字节数组，`42_02_0a` 用 `<0x39000040 0x2ff 0x27 …>` 打包字，dtc 打印时还会省略前导零。只认字节数组的解析器会漏掉后者。这个坑在 2026-10-24 实际踩过一次。

来源：[上游设备覆盖范围](https://github.com/slwyts/Project-SunUEFI/blob/065b192b863b6f99e12ba65d3cab27c0ddd6dca4/docs/status.md)、[面板选择合同](https://github.com/slwyts/Project-SunUEFI/blob/065b192b863b6f99e12ba65d3cab27c0ddd6dca4/docs/devel/panel-selection.md)、[实际 Raw Linux 交接](https://github.com/slwyts/Project-SunUEFI/blob/065b192b863b6f99e12ba65d3cab27c0ddd6dca4/uefi/core/PianoRawLinuxBoot.c)。
