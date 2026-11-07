# 设备树

本机启动用的 `board.dtb` 有三条血缘，职责不同，不能互相替换。判断任何一个 DT 问题前先确认说的是哪一条。

| 来源 | 形态 | 出货 | 用途 |
| --- | --- | --- | --- |
| `sunuefi/vendor/piano-linux/board.dtb` | 二进制，1,208,011 字节 | 是 | 启动基线；overlay 打在它上面 |
| `xiaomi-pad-8-pro-development/android_kernel_xiaomi_piano` 的 `arch/arm64/boot/dts/vendor/qcom/` | Android 下游 DTS 源码，808 个 `.dts`/`.dtsi` | 否 | overlay 的审阅基准 |
| `sunuefi/linux/dts/*.dtso` | 15 个附加 overlay 加 1 个 CPU 模型 overlay | 部分 | 把厂商 DT 改成主线内核可用 |

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

把出货 `board.dtb` 反编译后看到根节点是：

```
model = "Xiaomi Pad 8 Pro";
compatible = "xiaomi,piano", "qcom,sm8750", "qcom,sun-mtp", "qcom,sun", "qcom,sunp-mtp", "qcom,sunp", "qcom,mtp";
qcom,board-id = <0x40008 0x00>;
```

而厂商源码 `piano-sm8750-overlay.dts` 给的是 `model = "Qualcomm Technologies, Inc. Piano based on SM8750"` 和同样的 `qcom,board-id = <0x40008 0>`。

board-id 对得上，说明厂商 overlay 参与了组合；model 不同，说明中间还有一次改写。谁改的没有记录，这正是 oracle 要盯的东西之一。

## 厂商源码的结构

厂商的 DT 不是单文件：

- `arch/arm64/boot/dts/vendor/qcom/sun.dts` 是基线，只有 262 字节，`model = "Qualcomm Technologies, Inc. Sun SoC"`。
- `piano-sm8750-overlay.dts` 是 `/plugin/` overlay，`#include "piano-sm8750.dtsi"`，靠 `qcom,msm-id`、`qcom,board-id`、`xiaomi,miboard-id` 选择。
- `piano-sm8750.dtsi` 789 行，串起 `piano-pinctrl.dtsi`、`sm8750-xiaomi-common.dtsi`、`sm8750-ir-sensor-common.dtsi`、`piano-charger-common.dtsi`、`piano-xiaomi-touch-pinctrl.dtsi`。
- `arch/arm64/boot/dts/vendor/qcom/Makefile:18-19` 定基线集：`sun.dtb sun-v2.dtb sun-tp.dtb sun-tp-v2.dtb`，以及对应的 `sunp*`。
- 同一份 Makefile 的 `piano-sm8750-overlay.dtbo` 在 `SUN_BOARDS` 里，`add-overlays` 会用 `fdt_overlay` 把基线与 overlay 合成 `sun-piano-sm8750-overlay.dtb` 这类产物。

techpack 目录里有显示、音频、摄像头、WLAN、BT 的板级片段，其中 `piano-audio-mtp.dts` 4264 行，`piano-sde-display.dtsi` 4.2 KB，`piano-charger-common.dtsi` 742 行。这些是我们 overlay 里那些硬件常量（SPI 号、稳压器、时钟）的原始出处。

## 16 个 overlay

`config/release.json` 的 `kernel` 段列 `cpu_model_overlay` 一个，加 `additional_dtb_overlays` 十五个。`build-bundle.yml` 目前只传前八个加 cpu-model。

| overlay | 目标节点 | 作用 |
| --- | --- | --- |
| `piano-cpu-model` | `/cpus` | CPU 型号串 |
| `piano-board-identity` | 根节点 | 板级标识 |
| `piano-regdma-resource` | regdma | 资源窗口 |
| `piano-audio-dmic-clock` | 音频 | DMIC 时钟 |
| `piano-camera-flash` | camera | 闪光灯 |
| `piano-camera-privacy-led` | camera | 隐私指示灯 |
| `piano-touch-panel` | 触控 / 面板 | 触控与面板关联 |
| `piano-pen-dock` | `/soc/pen-dock` | 笔的霍尔与 pinctrl |
| `piano-pen-charger` | 充电 | 笔的充电 |
| `piano-cma` | `/reserved-memory/linux,cma` | CMA 改 256 MiB（`0x10000000`） |
| `piano-pcie-interrupt-map` | `/soc/pcie@1c00000` | INT A-D 用 SPI 149-152，折叠 GIC 的地址单元差异 |
| `piano-usb-typec` | `/soc/qcom,pmic_glink` | `qcom,sm8750-pmic-glink` 与 Type-C 端口 |
| `piano-ufs-mcq` | `/soc/ufshc@1d84000` | MCQ 窗口 `0x1da0000`，`msi-parent = <&gic_its 0x60>` |
| `piano-dsp-resources` | `/soc/remoteproc-cdsp@32300000` | cdsp 的时钟与电源域 |
| `piano-cdsp-fastrpc` | cdsp 的 `qcom,fastrpc` | `qcom,non-secure-domain` 与 compute-cb |
| `piano-fingerprint` | `/soc/rsc@16500000/regulators-4` | `piano_fp_vdd`（ldo9） |

后七个没有传。`ufs-mcq` 对应封存序列里已经合入的 `0056`/`0057`，`dsp-resources`、`cdsp-fastrpc` 对应 FastRPC，`usb-typec` 对应充电与 OTG。没有 overlay，这些代码合了也不生效。

## oracle 校验

`scripts/compare-vendor-dt.py` 在 CI 里从厂商源码编出基线 DTB 和 piano overlay，用 `fdtoverlay` 合成，再对每个 SunUEFI overlay 的 `target-path` 逐节点比对：节点在不在、属性名集合差多少、`phandle` 指向的节点类型对不对。

它回答的是"我们的 overlay 改的东西，厂商硬件描述里有没有对应物"，不是"能不能替换"。两份 DT 结构不同源，逐字节对齐不是目标。

厂商源码只在这个任务里读。任何出货路径（`build-bundle.yml`、`kernel-piano`、`piano-kernel-esp-update`）引用 `vendor-kernel` 都算违规，`docs/upstream.md` 记了这条规则。

## 面板与内存

面板 profile 与 RAM 区间的判据在 `docs/upstream.md` 的「内存与面板边界」。那一段里的 profile 名字来自本页第一条血缘，不要拿厂商源码里的 `piano-sde-display.dtsi` 去套。
