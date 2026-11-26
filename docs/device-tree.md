# 设备树与 Overlay

本仓库启动链涉及三条不同的设备树血缘，排查硬件问题前先核对所属来源：

| 来源 | 形态 | 是否随镜像发货 | 作用 |
| --- | --- | --- | --- |
| `sunuefi/vendor/piano-linux/board.dtb` | 二进制，1,208,011 字节 | 是 | 启动基线，供全部 overlay 叠加 |
| `android_kernel_xiaomi_piano`（`arch/arm64/boot/dts/vendor/qcom/`） | Android 下游 DTS 源码（808 个 `.dts`/`.dtsi`） | 否 | 仅供 CI `compare-vendor-dt.py` 对照参考 |
| `sunuefi/linux/dts/*.dtso` | 1 个 CPU 模型 + 24 个附加 overlay（发货 23 个） | 是（合并进 `board.dtb`） | 将基线修补到主线 Linux 7.3 可用状态 |

内核源码自带的 `arch/arm64/boot/dts/qcom/sm8750-xiaomi-piano.dts` 当前不参与启动，因此主线 DT 上游提交暂无法直接搬运。

## 出货基线谱系

锁定 SunUEFI 处的 `vendor/piano-linux/manifest.json` 记载 `"profile": "piano-boe-16gb"`、`"bytes": 1208011`、`"kernel_source_tree": "17d484d4771a140e1eeb18eaf0315028adc5a16a"`（经检索，该 `kernel_source_tree` 哈希未出现在六个常见公开上游树中，属于 SunUEFI 独立导出的基线产物；manifest 中同时出现的 `factory_panel.vendor: csot` 与 `profile` 存在内部拼写矛盾）。

- **与厂商 DTS 的根节点对应**：`vendor/piano-linux/board.dtb` 根节点为 `"Qualcomm Technologies, Inc. Piano based on SM8750"`（`qcom,board-id = <0x40008 0x00>`），与厂商 `piano-sm8750-overlay.dts` 一致；合进 `piano-board-identity.dtso` 后，最终 ESP 里的 `model` 改为 `"Xiaomi Pad 8 Pro"`。
- **节点命名差异**：主线出货树用 `/soc/display-subsystem@ae00000`、`/soc/pinctrl@f100000`，对应厂商源码里的 `/soc/qcom,mdss_mdp@ae00000` 与 `/soc/pinctrl@f000000`，对照脚本按地址而非名字匹配。

厂商源码由基线 `sun.dts`（262 字节）、`piano-sm8750-overlay.dts` 与 789 行的 `piano-sm8750.dtsi` 合成，板级显示、音频、相机、WLAN/BT 硬件常量位于 `techpack/` 目录（如 `piano-sde-display.dtsi`、`piano-audio-mtp.dts`）。

## 启动使用的 24 个 Overlay

SunUEFI `config/release.json` 声明 `piano-cpu-model` 及 24 个附加 overlay；其中 `product.json` 的 `dtb_overlays_not_shipped` 排除 `piano-tsens`，其余 23 个由 `build-bundle.yml` 全部合并进 ESP（`tests/check_bundle_overlays.py` 强制对齐两份清单）：

| Overlay | 目标节点 / 范围 | 作用 |
| --- | --- | --- |
| `piano-cpu-model` | `/cpus` | CPU 型号标识 |
| `piano-board-identity` | `/` | 改写设备标识为 `Xiaomi Pad 8 Pro` |
| `piano-regdma-resource` | regdma | 资源窗口 |
| `piano-audio-dmic-clock` | 音频 | DMIC 时钟 |
| `piano-camera-sensors` | CCI 传感器 | 传感器 19.2 MHz TCXO 时钟 |
| `piano-camera-interconnects` | 相机 | 互连带宽投票 |
| `piano-camera-flash` | 相机 | 闪光灯节点 |
| `piano-camera-privacy-led` | 相机 | 隐私指示灯 |
| `piano-camera-csi2-phys` | 相机 | 两路 SM8750 CPHY/DPHY、供电与 graph |
| `piano-touch-panel` | 触控 / 面板 | 触控与面板绑定关系 |
| `piano-pen-charger` | 充电 | 手写笔无线充配置 |
| `piano-cma` | `/reserved-memory/linux,cma` | CMA 区域设为 256 MiB（`0x10000000`） |
| `piano-pcie-interrupt-map` | `/soc/pcie@1c00000` | INT A-D 映射 SPI 149-152，折叠 GIC 地址差异 |
| `piano-pcie-clocks` | `/soc/clock-controller@100000` | 为 PCIe PHY 连接 GCC `clocks[3]` |
| `piano-usb-typec` | `/soc/qcom,pmic_glink` | `qcom,sm8750-pmic-glink` 与 Type-C 端口 |
| `piano-displayport` | `/soc/display-subsystem@ae00000` | DP0 与 USB/DP combo PHY 资源及 graph |
| `piano-battery` | `/i2c@a94000/bq27z561_master@55` | 双并联电量计汇总与认证参数 |
| `piano-ufs-mcq` | `/soc/ufshc@1d84000` | MCQ 窗口 `0x1da0000` 与 ITS 中断绑定 |
| `piano-dsp-resources` | `/soc/remoteproc-cdsp@32300000` | CDSP 时钟与电源域 |
| `piano-cdsp-fastrpc` | CDSP `qcom,fastrpc` | `qcom,non-secure-domain` 与 compute-cb |
| `piano-fingerprint` | `/soc/rsc@16500000/regulators-4` | `piano_fp_vdd`（ldo9）供电 |
| `piano-bluetooth-nvm` | 蓝牙 | 指定 `brhbtnv20.b2e` 与板级 RF 校准 |
| `piano-ir-spi-controller` | QUP2 SE7 / CS0 | LIRC 红外发射与 GPIO29 |
| `piano-display-interconnects` | `/soc/interconnect@1780000` | SM8750 MDSS/RPMh 互连绑定 |

合并后的 DTB 在 BLS 布局下位于 `/linux/<release>/board.dtb`（UKI 布局打入 PE `.dtb` 节区），由 `kernel-piano` `%posttrans` 随内核同步升级。每次启动时 UEFI 固件再通过 `EFI_DT_FIXUP_PROTOCOL` 动态重写 `/memory`、面板 `compatible`、蓝牙地址、RTC 偏移与 Gunyah RM 属性。

注意：`piano-cma` 与 `piano-ufs-mcq` 若漏传将直接无法开机；回退只需刷回上一版可用 bundle 里的 `sunuefi_esp.img`。

## Oracle 厂商对照检查

CI 的 `vendor-dt` 任务运行 `scripts/compare-vendor-dt.py`：用厂商基线 DTB 依次叠入 `piano-sm8750-overlay` 及 techpack 显示/相机/音频 overlay，逐 fragment 检查各个 SunUEFI overlay 的目标节点与属性值，并将结果输出到 `build/vendor-dt-report/report.{json,md}`。

由于与出货树结构非同一源码树，预期内的命名或存在差异登记在 `KNOWN_MISSING`；出现列表外的新增缺失时脚本报错，提醒维护者审查新增 overlay 或厂商更新。所有出货构建脚本均禁止直接消费 `vendor-kernel`，见[上游跟踪](upstream.md)。面板 profile 与 RAM 边界判据见[上游跟踪的内存与面板章节](upstream.md#内存与面板边界)。
