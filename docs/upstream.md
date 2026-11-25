# 上游跟踪

构建来源由 `sources.lock.json` 固定。上游 main 的进展只用于审阅。更新文档不改变已构建镜像的来源，旧二进制也不会自动获得新实现。

## 当前 pin

构建消费的上游以 `sources.lock.json` 为准，当前 pin 的 commit 写在 `sources.lock.json` 里。其他消费源的 commit 也记在 `sources.lock.json`。研究与对照仓库登记在 `upstreams.json`，不参与构建。本页的引用链接按当前 pin 固定到具体提交。

SunUEFI 现在锁在上游的正式版 tag `v1.0.0`（`a6c321d`）。上游把 GitHub Release 叫「正式发布」、把 Actions 的 Build products 叫「开发版」，所以跟版本时锁 tag 对应的提交，不跟 main 头；`sources.lock.json` 的 `ref` 仍留 `main`，让周报能看见 main 又往前走了多少。

### 下游与上游的文档分工

上游 `docs/` 只写它自己的四套发行版（Debian/Nyarch/SteamOS/Arch）与它的构建方式，没有给下游项目的文档规范。本仓库的文档边界按下面几条：

- 上游怎么实现、为什么这样实现，写进 `docs/upstream-parity.md` 的对照表，不复制上游正文。
- 我们在 Fedora 上怎么做、哪些没做、为什么不做，写进本仓库的 `docs/`。
- 上游仓库的状态页是它的设备状态页，不能当成我们的验收记录；我们的状态一律以本仓库的 `known-issues.md` 与 manifest 里的 `device_boot_performed` 为准。
- 上游改了什么功能，只在本仓库的 `docs/upstream-parity.md` 记一行差异，不复述它的发布说明。

### 对照基准

`sources.lock.json` 里有两条 pin 只进对照任务、不进产物：

- `vendor-kernel`：
  - `android_kernel_xiaomi_piano` DTS 源码，用于核对 SunUEFI overlay 目标节点是否存在。
- `vendor-blobs`：
  - `android_vendor_xiaomi_piano` 固件与传感器配置，用于比对 git blob 摘要，不进镜像。

这两条属于消费上游，所以进 `upstream-watch` 的漂移报告，但白名单里没有它们：厂商树一更新就要人工看对照报告有没有新差异。任何出货路径引用 `vendor-kernel` 都算违规——它描述的是 Android 下游 DT，和启动用的 `board.dtb` 不是同一条血缘，见 `docs/device-tree.md`。

固件对照基准覆盖厂商仓库提交的 148 个固件文件：5 个与出货树逐字节相同（含 Novatek 触控固件、Nanosic MCU 与 `gen80000_gmu.bin`），4 个不同（如 `gen80000_zap.mbn`、`vpu35_4v.mbn`），其余各为独有项（厂商侧以 `CAMERA_ICP` 为主）。厂商仓库并非 vendor 分区全集，比对差异仅代表清单覆盖范围。同时核对 `runtime/matrix-owned-paths.txt` 的 99 条固件所有权路径，与出货树 99 个文件完全对应。

`packages/xiaomi-piano-firmware` 的 `%prep` 现在核对上游自带的 `SHA256SUMS`（99 项）。在此之前 `Source0` 只订了提交号，tarball 内容被换掉也发现不了。

固件包还要再叠一层厂商 ROM。SunUEFI 的 `config/release.json` 用 `firmware.overlays` 声明厂商叠层，当前 10 份、109 个文件，全部来自 OS4.0.6.0.XPYCNXM：`hyperos4-{adsp,cdsp,fingerprint}`、`piano-display`，以及 `piano-{ath12k,bluetooth,keyboard,qcom,qcom-vpu,touch}`。每份带 `manifest.json`，逐文件记 bytes 与 sha256，可选的 `directory` 给出落点。上游 piano-firmware tarball 里没有 cdsp 与指纹固件，adsp 也是另一版 ROM，所以 99 项之外还有这一层，由 `packages/xiaomi-piano-firmware/firmware-overlays.py` 在写盘前逐个核对。`piano-display` 的两份面板校准写成 `qcom/sm8750/`，其余各份落在固件根目录；落点由清单声明，脚本不另写一份映射。清单从 `release.json` 读，叠层的提交号与 `sources.lock.json` 的 `sunuefi` pin 相同，`tests/check_firmware_overlays.py` 守住两者一致。

## 自动跟踪

`.github/workflows/upstream-watch.yml` 每周一 03:23 UTC 运行一次 `scripts/audit-upstreams.py --execute`。`scripts/upstream-watch.py` 处理执行结果，按以下规则分流：

- 锁定 ref 提交与 `sources.lock.json` 的 pin 不同时记为漂移：
  - 漂移汇总进 `upstream-drift` issue；下次无漂移时自动关闭 issue。
- 白名单漂移在 issue 中附带改 pin 命令：
  - 白名单包括 `crosvm`、`librevm`、`uperf`、`piano-mesa`、`piano-sensors`、`piano-mipps-auth`；
  - 纯用户态上游由人执行 `upstream-watch.py bump`，Actions 不直接修改锁文件。
- 内核、UEFI、相机/触控、固件与 debian-piano 仅作报告：
  - 离线 CI 无法代替真机验收，维护者按后文规则评审更新。

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

## 核心分叉与补丁策略

本仓库在 `patches/kernel/` 追加 7 条设备适配补丁，把上游 7.3 序列推到 0500。Fedora 临时重编包的删除条件见 `scripts/fedora-overlays.json`。

### 1. 内核补丁序列管理
内核补丁集（`patches/kernel/`）共 326 条补丁，由上游 290 条封存序列与自有 36 条适配补丁组成，最终目标树固化于 `patches/kernel/series.json`（`target_tree: 5a404cc57cc2`）：
- **触控矩阵解码**：序列里的 `0399-LOCAL-input-nt36532e-matrix.patch` 把逐帧手指矩阵解码放进内核 IRQ 线程（32 帧参考、中值滤波、基线漂移处理），`/proc/nvt_thp_status` 报 `kernel_input: enabled`；笔序列 `0431`、`0432` 建立在它引入的矩阵基础设施上，笔也从标准固件目录读校准并在内核里上报。内核输入设备与用户态桥接设备同名同 ID，两者不会同时创建：内核接管后 `piano-touch-view` 的 `kernel_hand_input` 判定让用户态手指桥接退出。上游随之删掉 `touch-input` 与 `pen-bluetooth`，`piano-touch.service` 只剩 BlueZ 笔初始化。内核解码用写死的矩阵尺寸，不读本仓库校验并随包分发的原厂逐屏电极校准；这条差异在真机上验收。
- **自有适配序列**：将 36 条自有补丁后移至 `0465`–`0500`，涵盖设备特定的 DPU/Iris、音频、启动与 UFS MCQ 约束。
- **构建环境与优化**：采用 LLVM 23.1.5 工具链，启用 `CC_OPTIMIZE_FOR_PERFORMANCE_O3`、`LTO_CLANG_THIN` 与 `CONFIG_ARM64_PIANO_OPTIMIZE`。

### 2. 充电与电源栈适配
- **充电策略**：补丁序列 `0415`–`0422` 将原厂 4.0.6 循环充电策略、QC/PPS 输入与直充能力选择、电池健康降流及 PPS 调压步长接入 `piano_mca.c` 与 `bq27xxx`。
- **TuneD 与 uperf 桥接**：电源模式不直接操作 sysfs 频点（避免与开机接管 CPU/GPU 的 `uperf-linux` 产生冲突），而是通过 `piano-power-mode` 将 GNOME PPD 的三档模式一一桥接到 `uperfctl mode powersave|balance|performance`。

### 3. 启动载荷与固件交接
- **两种启动布局**：[SunUEFI](https://github.com/slwyts/Project-SunUEFI) 新装镜像走标准 UEFI 顺序，systemd-boot 加载 `/linux/<release>/` 下的内核与 `/loader/entries/` 里的启动项。早期单 UKI 固件没有 systemd-boot，通过 `PianoApplicationRunFile` 打开固定的 `/EFI/Linux/sunuefi.efi`，由 `systemd-ukify` 组装 `.linux`、`.initrd`、`.dtb`、`.cmdline` 等节区。
- **设备树动态修复**：采用 `EFI_DT_FIXUP_PROTOCOL`（`PianoDeviceTreeFixup.c`），在启动时根据设备实际内存布局与保留区改写 `/memory` 节点。
- **DWC3 停机容错**：在 `patches/uefi-dwc3-halt/` 中加入复位兜底，当等待超时或传输终止失败时回退至 soft reset。

### 4. 固件叠层（firmware.overlays）
固件打包分两层保障完整性：
- 基础层：`packages/xiaomi-piano-firmware` 校验上游自带的 `SHA256SUMS`（99 项基础固件）。
- 叠层：`config/release.json` 声明 10 份来自 OS4.0.6.0 的厂商叠层（109 个文件，涵盖 cdsp、指纹、蓝牙校准及显示面板校准），由 `firmware-overlays.py` 在安装时依清单校验。

### 5. 桌面环境与显示策略
- **自动亮度曲线**：GSD 自动亮度移植原厂 108 点 lux→nits 曲线与 KTZ8866 指数电流映射，灭屏时停止调光以保留用户基准。
- **锁屏电源状态同步**：针对 Fedora 51.1 缺少生成包装的问题，使用 `g_dbus_proxy_call_sync` 读取 `GetActive` 同步锁屏状态。
- **首次启动 GPU 探测**：在 `command_line` 中加入 `deferred_probe_timeout=300`，为 Adreno GPU 控制器留出探测时间窗口。

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

这与 `PianoFactoryPanel.h`、上游 `docs/devel/hardware/panel-selection.md` 的表一致，也与原厂 UEFI 的两份面板描述文件一一对应。参考设备的 `lcd_id: 1` 对应 BOE 模块，UEFI 读到的 `dsi_display0` 应当指向 `35_02_0b`。

早期移植文档曾将两者的供应商归属对应颠倒，原因在于驱动内部 `piano_boe_*` 与 `piano_csot_*` 的函数命名来自第三方移植者定义；实际面板供应商归属必须严格以原厂驱动 `nt36xxx.c` 中读出的 `lcd_id` 为准（0 为 CSOT，1 为 BOE）。

核对时注意两个 profile 在 DTB 中的语法格式不同：`35_02_0b` 采用 `[...]` 字节数组，`42_02_0a` 采用 `<0x39000040 ...>` 打包字，解析工具需同时支持两种解析路径。

来源：[上游设备覆盖范围](https://github.com/slwyts/Project-SunUEFI/blob/18274377d11e0a12288ffbb795b9982b6a9804f4/docs/status.md)、[面板选择合同](https://github.com/slwyts/Project-SunUEFI/blob/18274377d11e0a12288ffbb795b9982b6a9804f4/docs/devel/hardware/panel-selection.md)、[实际 Raw Linux 交接](https://github.com/slwyts/Project-SunUEFI/blob/18274377d11e0a12288ffbb795b9982b6a9804f4/uefi/core/boot/PianoRawLinuxBoot.c)。
