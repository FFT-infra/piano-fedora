# 上游更新与产物边界

构建来源由 sources.lock.json 固定。上游 main 的进展用于审阅；更新文档不改变已构建镜像的来源，也不使旧二进制自动获得新实现。

## 2026-10-14 复核：登记表本身不会报"谁更新了"

`python3 scripts/audit-upstreams.py --execute` 对 88 个登记仓库取证：85 个拿到完整
提交号，CrabApple、llfzs/8pro、runesign/mipad-6s-pro-linux 仍是 404。这份审计报
不出更新——88 行里只有 fedora-sheng 一行填了 `reference_commit`，而
`inspect_repository()` 只读 `lock_key`（9 个消费源），该字段写了没人用。79 个
reference 上游因此只能拍快照，下次运行不会提示任何一个变了。这是待补的缺口。

手工比对补上了这次遗漏：`code002-2/librevm` 在 2026-10-13T15:10Z 新增提交
`9833d43c`，加进 `packaging/gunyah-driver-7.2/`——从 Android common kernel
`android17-6.18` 导入、且已适配主线 7.2 的完整 Gunyah 驱动，附 `gh_vmtest.py`
冒烟测试与 OnePlus Pad 4 上的验证记录。

据此 Gunyah 实验档改为消费上游现成驱动，本仓库不再移植：

| 项 | 旧 | 新 |
| --- | --- | --- |
| 锁 | `piano-vendor-kernel`：MiCode piano-w-oss `45fb9bd6`（6.6.57 基线） | `gunyah-driver`：`SuperTurtleDev/iceland-mainline-kernel-linux@fd503a49`（分支 `pr/gunyah`） |
| 派生 | 19 个文件导入 + 四处 API 改写 | 32 个文件原样搬运，零改写 |
| 配置 | 五个 Gunyah 符号 | 八个，按上游 Kconfig 全选 |

旧线为什么停：6.6→7.2 是一条逐处移除的 API，手工移植每轮只暴露一处。整核那轮
在第 51 分钟撞上 `folio_index()` 与 `of_node_to_fwnode()`，十分钟预检里又撞上
`PageSwapBacked()`。细节见[虚拟化记录](virtualization.md)。

## 2026-10-13 晚间复核与 Fedora 缺口

去重研究索引扩为 [88 个仓库](upstreams.json)，补入 code002-2 的 piano-linux、
librevm、视频加速、调度器与 UEFI fork，以及真正消费的 Qualcomm FastRPC。
本轮对每个所选 ref 执行 GitHub API 查询：最终 88 项中 85 个取得完整提交号；CrabApple、
llfzs/8pro、runesign/mipad-6s-pro-linux 返回 404，记为未核验，不推断不存在
其他公开替代。命令为 `python3 scripts/audit-upstreams.py --execute`，逐仓库
证据在本机 `build/upstream-audit-20261013-expanded.json`，不把生成输出当源码提交。
后续补入完整 Gunyah 源码镜像、GGLK 客体内核与 S25 Ultra 客体运行记录，
再次全量核验；它们仍为研究入口，不自动成为本机构建输入。

SunUEFI main 仍为 `1b0e201e3e7fb0737d73c41624a855482a4df7c5`，没有新增启动
核心修复；消费 pin 仍为 `17537522`。fedora-sheng 的差异仍为 Denial 打包，
其余原有消费上游与固定提交一致。新增 FastRPC 消费源固定 v1.0.8 的
`d247519650fe5cb16de6c78edaa95bcc4be25073`，仅构建 Fedora RPM 没有安装的
adsprpcd 守护进程，继续使用 Fedora 提供的 listener 共享库。

当前真机 `nft list ruleset` 报 Netlink Protocol not supported，配置也没有
NF_TABLES。新增 `0005-fedora-nftables.patch` 将 18 项配置纳入完整 Fedora
策略；对固定 piano 内核执行原生 olddefconfig 后，这 18 项全部保留。
这不是已运行内核的能力，仍需新 Image/模块/运行时重新构建与设备验收。

相机本次启动被临时配置禁用，移除该配置后既有 oneshot 仍保持已完成状态。
重启上游相机服务后，CAMSS 与前后传感器绑定，前后 loopback 节点各完成三帧
GStreamer 读取。尚未验证 GNOME 相机界面与冷启动，不把节点存在视为完整通过。

补装 adsprpcd 后服务稳定运行、重启计数为 0，SSC registry 可响应；但 accel
仍未枚举，iio-sensor-proxy 仍退出，传感器功能尚未修复完成。
VM 源码与配套 QEMU 的重新核验见[虚拟化记录](virtualization.md)。

## 2026-10-13 核对（含补丁去重评估）

`git fetch` 后逐个比对八个消费上游：

| 上游 | pin | 远端 | 差异 |
| --- | --- | --- | --- |
| sunuefi | `17537522` | `1b0e201` | 1 个提交，只动 Nyarch 打包三行文件（`linux/bsp/arch/chromium-flags.conf`、`tools/build_nyarch_apps.py`、`tools/stage_arch_piano.py`），与启动、交接、内存无关 |
| fedora-sheng | `3bd197fe` | `641c991e` | 9 个提交，全部是 Denial 合成器打包（v0.5.0、ARM64 补丁扩到 Arch/openSUSE/Debian/Ubuntu、构建加 libpolkit-agent-1-dev、去掉 RPM 后缀环境变量） |
| linux-piano(`piano-7.2.6`)、piano-firmware、piano-mesa、piano-sensors、piano-mipps-auth、debian-piano | 不变 | 与 pin 一致 | — |

**补丁去重评估**：对上游 HEAD 逐条核对，结论是四条系列都要保留，没有一条已被吸收。

| 系列 | 上游现状 | 结论 |
| --- | --- | --- |
| kernel-f2fs（3 条） | `tools/build_piano_full_kernel.py` 没有 F2FS/SELinux 配置；选择器 `.rodata`/`.data` 只有段首 `ALIGN(16)`，没有段尾对齐 | 保留 |
| uefi-ufs（1 条） | 上游有 `PianoUfsBuildResumeActive`，但只在描述符读失败（`EFI_DEVICE_ERROR`）时调用 | 保留：本机 1 TB UFS 睡眠态也能读成功，那个闸门不触发 |
| uefi-live-memory（1 条，本轮新增） | 全部远端分支 `grep ddr_size` 为空，raw 交接不碰 `/memory` | 保留：24 GB 采纳，真机 `MemTotal` 22.57 GiB |
| runtime/camerad-gcc-bounds | 上游无 | 保留 |

**pin 是否移动**：`sunuefi` 提到 `1b0e201` 已在本地重放验证（补丁干净应用、`check_patches` 通过），但该提交只改 Nyarch 打包，不改变任何启动或内核产物，因此本轮不动 pin；与其它输入一起评审时再决定。`fedora-sheng` 只作后端参照，不消费其代码。

**不改变的事**：本轮核对不让任何已构建镜像获得新实现；新部署包仍须记录 UEFI run、生产提交、实际 checkout、项目补丁与产品摘要。

## 本轮核对

2026-10-11 22:14（Asia/Shanghai），GitHub API 与 git ls-remote 对 main 的结果一致：`964459705190fb7b83809e5b059e90b9718f619d`，领先本仓库 pin `6a8763d010adb00b18911d6d12e6dd02869f98b9` 三个提交。

| 上游提交 | 内容 | 本仓库处理 |
| --- | --- | --- |
| 1b3c538 | piano 默认登录密码 | Fedora finalizer 采用相同的文档化初始密码，保留自动登录与 root 密码锁定；不把修改 pin 当作 Fedora 已实现 |
| d8f5026 | 从 ECR Public 拉取同一摘要的官方 Debian 基础镜像 | 属于构建下载来源；目前 UEFI 二进制和原编译容器不变，后续改 pin 时连同官方 Dockerfile 审查 |
| 9644597 | HMCL 的 JavaFX 加速说明 | 可选应用说明，不表示本项目已安装或测试 HMCL |

本轮没有发现启动核心、Linux DTB、内核或 Recovery 合同的新改动，因此仍保留当前构建 pin 与配套 UEFI。新部署包必须记录 UEFI run、生产提交、实际 checkout、项目补丁和产品摘要，并逐项核对，不能只选择“最新成功”文件。

同轮查询确认 linux-piano 的 piano-7.2.6 分支，以及 piano-firmware、piano-mesa、debian-piano、piano-sensors、piano-mipps-auth 的 main 均与 sources.lock.json 中对应 pin 一致；没有据此变更内核或设备运行时输入。

来源：[固定上游与当前 main 的完整比较](https://github.com/slwyts/Project-SunUEFI/compare/6a8763d010adb00b18911d6d12e6dd02869f98b9...964459705190fb7b83809e5b059e90b9718f619d)、[上游登录策略](https://github.com/slwyts/Project-SunUEFI/blob/964459705190fb7b83809e5b059e90b9718f619d/config/release.json)、[构建说明](https://github.com/slwyts/Project-SunUEFI/blob/964459705190fb7b83809e5b059e90b9718f619d/docs/devel/building.md)。

## 2026-10-12 更正：pin 已从 6a8763d 前移到 e600ada

上一节（2026-10-11 22:14）是当时的状态快照，原文不改。它记录的是 pin `6a8763d`，本轮把 pin 前移到了 `e600adaae93cfb9f8783969705ecac639ac44a1c`。

前移原因：交付包 r2 的 UEFI 核心来自 `6a8763d`，那是显示修复提交 `e600ada` 的祖先，中间隔 12 个提交；`e600ada` 之前的 `6a8763d` 没有延后原厂 ClockDxe 的 CESTA/media 与 DCD 启动初始化。改动同时更新了 `patches/uefi-ufs/series.json` 与 `patches/kernel-f2fs/series.json` 的 `base.sunuefi_commit`。

2026-10-12 15:03（Asia/Shanghai）用 GitHub API 复核：main 为 `158f526ae5cfa58f4707a540f3e59c1f7d4ae46e`（2026-10-11T20:21:34Z），pin 为 `e600ada`，**main 领先 pin 5 个提交**。

| 上游提交 | 内容 | 本仓库处理 |
| --- | --- | --- |
| 1423fc2 | 放大设置控制台并加入中文和标准按键输入 | 改的是 `uefi/core/PianoKeys.c` 与新增的 ConsoleScale 组件。**未取**：本仓库 UEFI 核心仍是 pin 的版本，这项实机仍未验证 |
| 256763f | 允许引导助手重新安装旧版 BOOT 核心 | Android 模块行为，只改 `android/module/manager.sh` 与 `tools/build_android_module.py` |
| 34b65da | 检查默认桌面和蓝牙触控笔组件完整性 | rootfs 构建期检查；本仓库 rootfs 由 `build-rootfs.yml` 自行组装，不消费该检查 |
| 55fec45 | 以引导助手为默认安装入口并简化首页 | 文档与首页 |
| 158f526 | 自动识别单台平板并附带备用安装说明 | `tools/export_installer.py`、`tools/install_piano.py`；本仓库安装入口是 `scripts/install-f2fs.py` / `update-f2fs.py`，不消费上游安装器 |

## 2026-10-12 更正：pin 已前移到 158f526（SunUEFI main 最新提交）

上一轮基于 `e600ada`。用户要求开新分支编译上游最新，经复核 `158f526ae5cfa58f4707a540f3e59c1f7d4ae46e` 相比 `e600ada` 新增了 3K 控制台缩放支持（`1423fc2`）与安装器优化：
- 五个 `targets` 摘要（`PianoUfsReadOnlyDma.c`、`build_piano_full_kernel.py` 两处、`piano-ram-hardware-prepare`、`piano-debug-bootstrap`、`BootSelect.ld`）在 `158f526` 上与原文完全一致，全部 5 个补丁顺利就绪。
- 新分支 `sync/sunuefi-158f526` 将 `sources.lock.json` 与各补丁 series 均更新指向 `158f526ae5cfa58f4707a540f3e59c1f7d4ae46e`。

来源：[158f526 提交历史](https://github.com/slwyts/Project-SunUEFI/commits/158f526ae5cfa58f4707a540f3e59c1f7d4ae46e)。

## 每次跟进上游

1. 先读取远端 main 的完整提交号，并与当前 pin 比较提交、文件和实际代码；发布说明只作索引。
2. 启动、交接、内存、DTB、模块或硬件准备有变时，重读相关合同及消费者，检查补丁 series 的 base 与目标摘要；不直接替换某一件旧产物。
3. 修改 pin 前核对所有消费路径、构建容器和依赖；必须重新生成受影响的内核、运行时、root、initramfs、ESP、BOOT 配套与清单。
4. 下载并核对实际产物，更新部署入口。构建、文件验证和实机行为分别记账，不能继承上游另一台设备的结果。

## 本机内存与面板

锁定上游的状态页只覆盖 16 GB、另一版 HyperOS、Debian/ext4 的设备；本机是改装 24 GB、Fedora/F2FS，不能沿用其全部设备结论。

当前 Linux DTB 的 16 个非零 RAM 区间合计 16,470,685,696 字节。Raw Linux 交接修改面板 compatible、启动参数和 initrd 地址，保留输入内存描述；自动面板选择不等于自动发现 RAM。上线后的只读更新计划必须验证 Linux 描述的 RAM 是本次 Android FDT 的子集，并且当前固定保留区与 Linux RAM 相交的部分仍受保护。比较失败时拒绝更新，不猜测扩容后的地址或放宽内存门槛。比较通过仍不证明物理 DDR 所有权或完整 24 GB 可用。

来源：[上游设备覆盖范围](https://github.com/slwyts/Project-SunUEFI/blob/6a8763d010adb00b18911d6d12e6dd02869f98b9/docs/status.md)、[面板选择合同](https://github.com/slwyts/Project-SunUEFI/blob/6a8763d010adb00b18911d6d12e6dd02869f98b9/docs/devel/panel-selection.md)、[实际 Raw Linux 交接](https://github.com/slwyts/Project-SunUEFI/blob/6a8763d010adb00b18911d6d12e6dd02869f98b9/uefi/core/PianoRawLinuxBoot.c)。
