# 设备树

本机启动用的 `board.dtb` 有三条血缘，职责不同，不能互相替换。判断任何一个 DT 问题前先确认说的是哪一条。

| 来源 | 形态 | 出货 | 用途 |
| --- | --- | --- | --- |
| `sunuefi/vendor/piano-linux/board.dtb` | 二进制，1,208,011 字节 | 是 | 启动基线；overlay 打在它上面 |
| `xiaomi-pad-8-pro-development/android_kernel_xiaomi_piano` 的 `arch/arm64/boot/dts/vendor/qcom/` | Android 下游 DTS 源码，808 个 `.dts`/`.dtsi` | 否 | overlay 的审阅基准 |
| `sunuefi/linux/dts/*.dtso` | 22 个附加 overlay 加 1 个 CPU 模型 overlay | 部分 | 把厂商 DT 改成主线内核可用 |

内核自带的 `arch/arm64/boot/dts/qcom/sm8750-xiaomi-piano.dts` 不参与启动。上游所有 `arm64: dts: qcom: sm8750-xiaomi-piano: ...` 提交都搬不过来，`docs/known-issues.md` 里被挡住的 77 条 + 3 条就是这个原因。

## 出货基线的谱系

`vendor/piano-linux/manifest.json` 在 SunUEFI pin 上写着：

```json
"profile": "piano-boe-16gb",
"source_sha256": "2073f74c9d17c7722bf8ab2ee559fa8fddda028610e24a684b66ef800259a2c9",
"sha256": "36e6adef75306d7727f79e97d0406376baa43e1b454e29d1c40bc0fc36d47db3",
"bytes": 1208011,
"kernel_source_tree": "17d484d4771a140e1eeb18eaf0315028adc5a16a",
"status": "SANITIZED_BOARD_INPUT_NOT_NEW_HARDWARE_ACCEPTANCE"
```

2026-11-08 逐个查过，`17d484d4771a...` 在下列六个仓库里都不存在：

- `xiaomi-pad-8-pro-development/android_kernel_xiaomi_piano`
- `blu-sharky/linux-piano`
- `slwyts/Project-SunUEFI`
- `yzddmr6/xiaomipad-8pro-mainline`
- `yzddmr6/linux-sm8750-piano`
- `code002-2/Linux-6.6.y-StrelitziaApus-piano`

也就是说，启动最关键的那个输入，谱系字段指向一棵公开查不到的树。`source_note` 自称 "Derived from the documented full Linux DTB"，说明它是 SunUEFI 自己那条线，不是 Xiaomi 的 Android DTB。

同一份 manifest 里 `profile` 写 `piano-boe-16gb`，而 `factory_panel.vendor` 写 `csot`，两者矛盾，尚未确认哪一个是笔误。

### 与厂商 DT 的可观测差异

出货基线 `vendor/piano-linux/board.dtb` 的根节点是：

```
model = "Qualcomm Technologies, Inc. Piano based on SM8750";
compatible = "qcom,sun-mtp", "qcom,sun", "qcom,sunp-mtp", "qcom,sunp", "qcom,mtp";
qcom,board-id = <0x40008 0x00>;
```

厂商源码 `piano-sm8750-overlay.dts` 给的 model 与 board-id 和这一致，说明厂商 overlay 参与了组合。最终装进 ESP 的那份 model 是 `Xiaomi Pad 8 Pro`，由 `linux/dts/piano-board-identity.dtso` 的 `target-path = "/"` fragment 改写。

节点命名两套不一样，判断 overlay 有没有打中节点时要按地址找对应物：

| 出货（主线命名） | 厂商（下游命名） |
| --- | --- |
| `/soc/display-subsystem@ae00000` | `/soc/qcom,mdss_mdp@ae00000` |
| `/soc/pinctrl@f100000` | `/soc/pinctrl@f000000` |

## 厂商源码的结构

厂商的 DT 不是单文件：

- `arch/arm64/boot/dts/vendor/qcom/sun.dts` 是基线，只有 262 字节，`model = "Qualcomm Technologies, Inc. Sun SoC"`。
- `piano-sm8750-overlay.dts` 是 `/plugin/` overlay，`#include "piano-sm8750.dtsi"`，靠 `qcom,msm-id`、`qcom,board-id`、`xiaomi,miboard-id` 选择。
- `piano-sm8750.dtsi` 789 行，串起 `piano-pinctrl.dtsi`、`sm8750-xiaomi-common.dtsi`、`sm8750-ir-sensor-common.dtsi`、`piano-charger-common.dtsi`、`piano-xiaomi-touch-pinctrl.dtsi`。
- `arch/arm64/boot/dts/vendor/qcom/Makefile:18-19` 定基线集：`sun.dtb sun-v2.dtb sun-tp.dtb sun-tp-v2.dtb`，以及对应的 `sunp*`。
- 同一份 Makefile 的 `piano-sm8750-overlay.dtbo` 在 `SUN_BOARDS` 里，`add-overlays` 会用 `fdt_overlay` 把基线与 overlay 合成 `sun-piano-sm8750-overlay.dtb` 这类产物。

techpack 目录里有显示、音频、摄像头、WLAN、BT 的板级片段，其中 `piano-audio-mtp.dts` 4264 行，`piano-sde-display.dtsi` 4.2 KB，`piano-charger-common.dtsi` 742 行。这些是我们 overlay 里那些硬件常量（SPI 号、稳压器、时钟）的原始出处。

## 23 个 overlay

`config/release.json` 的 `kernel` 段列 `cpu_model_overlay` 一个，加 `additional_dtb_overlays` 二十二个。`build-bundle.yml` 按这个顺序全部传入，`tests/check_bundle_overlays.py` 断言两份清单逐条相等。

| overlay | 目标节点 | 作用 |
| --- | --- | --- |
| `piano-cpu-model` | `/cpus` | CPU 型号串 |
| `piano-board-identity` | 根节点 | 板级标识 |
| `piano-regdma-resource` | regdma | 资源窗口 |
| `piano-audio-dmic-clock` | 音频 | DMIC 时钟 |
| `piano-camera-sensors` | CCI 下的相机传感器 | 出厂模式的传感器时钟（19.2 MHz TCXO） |
| `piano-camera-flash` | camera | 闪光灯 |
| `piano-camera-privacy-led` | camera | 隐私指示灯 |
| `piano-touch-panel` | 触控 / 面板 | 触控与面板关联 |
| `piano-pen-dock` | `/soc/pen-dock` | 笔的霍尔与 pinctrl |
| `piano-pen-charger` | 充电 | 笔的充电 |
| `piano-cma` | `/reserved-memory/linux,cma` | CMA 改 256 MiB（`0x10000000`） |
| `piano-pcie-interrupt-map` | `/soc/pcie@1c00000` | INT A-D 用 SPI 149-152，折叠 GIC 的地址单元差异 |
| `piano-pcie-clocks` | `/soc/clock-controller@100000` | GCC 的 `clocks[3]` 接 PHY 的标准时钟提供者 |
| `piano-usb-typec` | `/soc/qcom,pmic_glink` | `qcom,sm8750-pmic-glink` 与 Type-C 端口 |
| `piano-displayport` | `/soc/display-subsystem@ae00000` | DP0 与 USB/DP combo PHY 的资源和 graph |
| `piano-battery` | `/i2c@a94000/bq27z561_master@55` | 并联电量计汇总成一块电池，带认证键 |
| `piano-ufs-mcq` | `/soc/ufshc@1d84000` | MCQ 窗口 `0x1da0000`，`msi-parent = <&gic_its 0x60>` |
| `piano-dsp-resources` | `/soc/remoteproc-cdsp@32300000` | cdsp 的时钟与电源域 |
| `piano-cdsp-fastrpc` | cdsp 的 `qcom,fastrpc` | `qcom,non-secure-domain` 与 compute-cb |
| `piano-fingerprint` | `/soc/rsc@16500000/regulators-4` | `piano_fp_vdd`（ldo9） |
| `piano-bluetooth-nvm` | 蓝牙节点 | 选 `brhbtnv20.b2e`，补板级 RF 校准 |
| `piano-ir-spi-controller` | QUP2 SE7 / CS0 | LIRC 红外发射，GPIO29 驱动 |
| `piano-display-interconnects` | `/soc/interconnect@1780000` | 换成标准 SM8750 MDSS/RPMh 互连绑定 |

`usb-typec`、`ufs-mcq`、`dsp-resources`、`cdsp-fastrpc` 在 2026-11-08 补进 `build-bundle.yml`。`displayport`、`bluetooth-nvm`、`ir-spi-controller` 更早就有，本文到 2026-11-10 才补齐。`pcie-clocks`、`battery`、`camera-sensors`、`display-interconnects` 随 SunUEFI `61b6c592` 一起来，同时进了工作流。

没有 overlay，对应的内核代码合了也不生效。这些项的真机验收还没做完：`piano-cma`、`piano-ufs-mcq` 一旦不合，表现为开不了机，回退是刷回上一版 bundle 里的 `sunuefi_esp.img`。

## 启动读的是 UKI 里的 DT

板级 DTB 由厂商 `board.dtb` 与 `config/release.json` 里的 overlay 合成，打进 `/EFI/Linux/sunuefi.efi` 的 `.dtb` 节区。`/EFI/Piano/stable/` 与单独的 `board.dtb` 不再是启动输入。

- 固件只打开 `\EFI\Linux\sunuefi.efi`，按 PE 节区取内核、initramfs、DTB 与 cmdline。
- 内置的 `EFI_DT_FIXUP_PROTOCOL` 在每次启动时改写这份 DTB。`/memory` 换成设备本次的 RAM 布局，面板 `compatible`、蓝牙地址、RTC 偏移与 Gunyah 资源管理器的 `reg`、`interrupts` 也在这里更新。载荷里的这些值只是构建时的占位，不是设备实际值。

改 DT 要重打 UKI。只换 ESP 上某一个文件不会生效。`kernel-piano` 的 `%posttrans` 用 ESP 里现有 UKI 的 `.dtb` 重新组装，板级 overlay 保持不变；换 overlay 要重新构建 bundle 或手工重打 UKI。

## oracle 校验

`scripts/compare-vendor-dt.py` 在 CI 里从厂商源码编出基线 DTB、piano overlay，以及 techpack 的显示、摄像头、音频三个板级 overlay，用 `fdtoverlay` 依次合成。techpack 那三个不合成的话，厂商侧就没有 display-subsystem、codec、cci 这些子树，对照会把它们误报成缺失。

然后对 `config/release.json` 里的每个 overlay，按 fragment 逐个比对：目标节点在不在、要加的属性厂商有没有、要改的属性和厂商原值差在哪里。目标节点在厂商侧不存在时，按单元地址和名字前缀在同一父节点下找对应物，报告里给出厂商的实际路径。

它回答的是"我们的 overlay 改的东西，厂商硬件描述里有没有对应物"，不是"能不能替换"。两份 DT 结构不同源，逐字节对齐不是目标。

报告写到 `build/vendor-dt-report/report.{json,md}`。`vendor-dt` job 设了 `continue-on-error`，第一阶段的定位是审阅材料，不是门禁。

厂商侧找不到的目标分两类：`scripts/compare-vendor-dt.py` 的 `KNOWN_MISSING` 登记的是已经看过的正常差异（下游命名不同、节点由别的 overlay 建立、厂商 DT 本来就不描述这块硬件），其余算新增，报告里单独列出来，job 也在这里显式失败一次。加 overlay 或厂商换 pin 之后，先看这一节再决定要不要往登记表里加。

厂商源码只在这个任务里读。任何出货路径（`build-bundle.yml`、`kernel-piano`、`piano-kernel-esp-update`）引用 `vendor-kernel` 都算违规，`docs/upstream.md` 记了这条规则。

## 面板与内存

面板 profile 与 RAM 区间的判据在 `docs/upstream.md` 的「内存与面板边界」。那一段里的 profile 名字来自本页第一条血缘，不要拿厂商源码里的 `piano-sde-display.dtsi` 去套。
