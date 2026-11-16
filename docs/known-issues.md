# 已知问题与硬件现状

本页列出当前版本下尚未完全解决的问题、特定硬件限制、系统运行日志中的已知噪声，以及安装与维护过程中的注意事项。每项均保留技术根因、复现条件与真机排障取证数据。

已在当前版本中通过真机验证修复的问题不再列入本页；未经过真机冷装验证的修复项继续保留。

---

## 目录

- [显示与关屏](#显示与关屏)
- [固件拒绝 UKI（uki-identity）](#固件拒绝-uki)
- [首次启动 GPU 未绑定](#首次启动-gpu-未绑定)
- [系统缓存（LLCC）没有生效](#系统缓存llcc没有生效)
- [未开启的内核配置](#未开启的内核配置)
- [驱动路径未命中（fsa4480）](#驱动路径未命中)
- [启动时的间歇 Oops](#启动时的间歇-oops)
- [刷写与读回](#刷写与读回)
- [首次安装的 userdata](#首次安装的-userdata)
- [双系统切换](#双系统切换)
- [Android 侧](#android-侧)
- [无损首装](#无损首装)
- [充电](#充电)
- [传感器与自动旋转](#传感器与自动旋转)
- [手写笔](#手写笔)
- [触屏、网络、音频与相机](#触屏网络音频与相机)
- [指纹、红外与 HDR](#指纹红外与-hdr)
- [开机日志里的红色报错](#开机日志里的红色报错)

---

## 显示与关屏

### 1. 关屏 devcoredump 残留日志
- **现象**：反复关屏或休眠时，内核日志偶发记录 `slave intf... still enabled before cleanup` 并生成转储。
- **影响**：属于内核调试信息噪声。屏幕显示与背光正常，休眠与唤醒功能正常，无硬件损坏风险。
- **技术根因**：MSM DPU 驱动在关闭显示接口时，从接口寄存器在清理窗口期未完全清零，触发驱动断言。
- **状态**：未解决（作为残余内核日志噪声观察中，不影响正常使用）。

取证与排查记录：
- 反复关屏采样中约每 2–3 次产生一次 devcoredump：`intf_1`/`intf_2` 寄存器非零（差值 18 word）。
- `dmesg` 中未记录 DPU 错误（underrun 回调只累加计数不打印），设备未编 tracefs 无法继续 trace。
- `rtcwake -m mem -s 20` 唤醒耗时约 22 秒，唤醒后无 failed unit。
- 关机 CPU 路径上的 `IRQ…: set affinity failed(-22)` 与 CMA 分配失败属系统既有噪声。

### 2. 面板 compatible 标识命名反转
- **现象**：在设备内查看 `/proc/device-tree`，面板 compatible 为 CSOT，而物理绑带识别为 BOE。
- **影响**：无功能影响。屏幕显示与色彩输出正常。
- **技术根因**：上游驱动函数名与 compatible 规则命名对调，实际加载的是 BOE 初始化序列与命令流。
- **状态**：无需更改（属于驱动内部符号命名差异）。

---

## 固件拒绝 UKI（uki-identity） <a id="固件拒绝-uki"></a>

- **现象**：在 [SunUEFI](https://github.com/slwyts/Project-SunUEFI) 启动菜单选择 Linux 后弹回，提示镜像格式不支持：
  - 日志记录 `PIANO_EBS_REFUSE stage=before-exit status=Access Denied`。
- **影响**：使用 systemd-stub 258+ 生成的 UKI 镜像无法被固件引导。
- **技术根因**：systemd-stub 258 起跳转前将 UKI 的 `LoadedImage` 属性改写为内嵌内核地址与大小：
  - SunUEFI 校验启动前记录值发现改写，返回 `EFI_ACCESS_DENIED`。
- **应对方法**：使用上游 SunUEFI `fe5b7aca` 及之后的固件；该提交在固件侧识别直入内核路径，本仓库不再维护补丁。
- **状态**：上游已修复（SunUEFI `fe5b7aca`，待真机冷装验证）。

---

## 首次启动 GPU 未绑定

- **现象**：全新冷装首次开机日志记录 `msm_dpu ae01000.display-controller: no GPU device was found`：
  - 整次开机未完成 Adreno GPU 绑定，KWin 退回 llvmpipe 渲染；
  - 第二次开机恢复正常。
- **影响**：首次冷装启动进入桌面时缺乏硬件图形加速，界面响应缓慢。
- **技术根因**：根因未逐段定位，现按延迟探测定时器静默期耗尽假设放宽窗口：
  - 候选段包括 UEFI 交接、内核解压或 UFS 枚举；
  - DPU 先于 Adreno 探测属表面症状。
- **应对方法**：首次冷装若遇到无硬件加速，正常重启一次设备即可恢复。
- **状态**：已缓解待真机验证（补丁已在默认命令行加入正整数 `deferred_probe_timeout`，待冷装验证）。

---

## 系统缓存（LLCC）没有生效

- **现象**：内核编译开启 `CONFIG_QCOM_LLCC=y`，但 `/sys/bus/platform/drivers/qcom-llcc/` 下未绑定设备。
- **影响**：系统未配置专用的最后一级缓存切片，Adreno GPU 与系统总线缺 LLCC slice 运行。
- **技术根因**：设备树原厂节点为 `compatible = "qcom,sun-llcc"`，而驱动仅匹配 `"qcom,sm8750-llcc"`。
- **状态**：未解决（待上游驱动或内核补丁增加 compatible 匹配项）。

基准性能测试参考（上游 `yzddmr6/linux-sm8750-piano@fe98a3e` 基准）：
- Geekbench 6 单核 +0.9%、多核 +0.5%；
- Vulkan 计算 +2.7%；
- 内存延迟类子项最高 +9.5%。

---

## 未开启的内核配置

- **现象**：当前实机 `/proc/config.gz` 中未开启以下配置：`CONFIG_PSI`、`CONFIG_WIREGUARD`、`CONFIG_USB_UAS`。
- **影响**：
  - `CONFIG_PSI` 缺失导致 systemd-oomd 无法获取内存压力指标；
  - `CONFIG_WIREGUARD` 缺失导致无法建立内核级 WireGuard VPN 隧道；
  - `CONFIG_USB_UAS` 缺失导致高速 USB 存储回退至传统 usb-storage 驱动，限制连续吞吐。
- **技术根因**：当前内核构建配置中未将上述三项符号置为开启状态。
- **状态**：未解决（待后续内核构建更新中统一开启并编译验证）。

---

## 驱动路径未命中（fsa4480） <a id="驱动路径未命中"></a>

- **现象**：Type-C 音频开关芯片驱动未绑定。
- **影响**：`fsa4480` 不被驱动接管，USB-C 耳机与 DP 音频的 SBU 切换走不了软件控制。
- **技术根因**：出货 DTB 里写的是 `qcom,fsa4480-i2c`，内核 `typec/mux/fsa4480.c` 只匹配 `fcs,fsa4480` 与 `dioo,dio4485`，两边没有交集，探测直接跳过。
- **状态**：未解决（待补一条把 `qcom,fsa4480-i2c` 加入匹配表的补丁）。

---

## 启动时的间歇 Oops

- **现象**：偶发启动时内核崩溃，报 `Internal error: Oops - Undefined instruction: 0000000020000000`：
  - 发生在启动约 9.4 秒 `udevd-worker` 内存页映射路径，停在 panic 画面。
- **影响**：偶发开机失败，未能正常进入桌面。
- **技术根因**：尚未逐段定位，未获稳定复现。
- **应对方法**：遇到时先正常重启一次（若屏幕无响应时长按电源键强制断电）；不要据此修改启动链。
- **状态**：未解决（全仓库暂无修复提交，作为未定位间歇问题观察中）。

---

## 刷写与读回

- **现象**：原厂 Fastboot 通信存在以下限制：
  - ABL 不支持 `fastboot fetch`（报错 `Device does not support fetch command`）；
  - 刷写 6.1 GB 稀疏根镜像后，USB bulk 端点可能超时，后续 `fastboot getvar` 无响应。
- **影响**：无法通过 fastboot 读回镜像比对哈希；大分区刷写后继续执行命令可能挂死。
- **技术根因**：原厂 Bootloader 的 Fastboot USB 通信实现与传输缓冲区限制。
- **应对方法**：
  - 完整安装刷写顺序严格按 `boot` → `sunuefi_esp` → `sunuefi_root` 执行；
  - 刷写完根分区后，直接长按电源键断电或重启设备，避免在同一会话中继续写入大文件；
  - 安装校验以开机进入桌面后的实际挂载状态（`findmnt /`、`uname -r`）为准。
- **状态**：已缓解（通过规范一键脚本刷写顺序与重启流程规避）。

---

## 首次安装的 userdata

- **现象**：全新安装执行 `fastboot erase userdata` 后，重启平板停在官方 Recovery 界面：
  - 屏幕底部提示 `Couldn't initialize user 0`。
- **影响**：平板无法直接启动进入 Android 系统。
- **技术根因**：`fastboot erase` 仅擦除块设备，未写入 Android 启动所需的 BCB 重建标志。
- **应对方法**：在 Recovery 菜单中使用音量键和电源键手动执行一次「清除数据」（Wipe Data）：
  - 重启后 Android 会自动重建加密 userdata，详见[安装指南](installation.md#24-让-android-重建-userdata)。
- **状态**：已缓解（需在现场手动执行一次 Recovery 清除数据）。

---

## 双系统切换 <a id="双系统切换"></a>

- **切槽限制与风险**：严禁使用 `qbootctl -s` 切换活动槽位。
  - **技术根因**：本项目采用合体 BOOT（Hybrid BOOT）机制，原厂 Android、选择器与 Linux 内核统一部署于当前活动槽，不切底层 A/B 槽位。调用 `qbootctl -s` 仅翻转高通活动属性，无法联动变更 ABL 校验所需的槽位 GUID，会导致 ABL 报错拒开机。
- **日常切换规范**：
  - Linux 切回 Android：终端执行 `piano-next-boot android --reboot`，或点击应用菜单「返回 Android」；
  - Android 切回 Linux：使用「SunUEFI 引导助手」模块切换并重启；
  - 临时切换：开机亮屏前 3 秒按音量键呼出选择菜单启动 Linux。
  - 具体操作步骤与升级说明参见[升级与维护（upgrade.md#二-双系统往返）](upgrade.md#二-双系统往返)。
- **请求页存储机制**：
  - 请求页位于正在引导的那份 BOOT 镜像末尾，偏移由 `Base + align(original_image_size)` 算出，不是固定值（魔数 `SUNUEFI-NEXTv1`）；
  - 活动槽由 helper 运行时从只读主备 GPT 与高通 A/B 属性自动识别，镜像与系统内不固化槽位字面量。

---

## Android 侧

- **引导助手模块**：
  - 已适配当前 BOOT generation 的「SunUEFI 引导助手」模块；
  - 采用 `adopt` 只读识别现有合体 BOOT，不重写内核；
  - 若重新刷写过 `boot` 分区，需在模块页面重新执行一次“重新安装引导”以重新采纳。
- **SELinux 状态**：
  - Android 侧处于 SELinux Permissive 状态，启动参数带有 `selinux=permissive`；
  - 手动执行 `setenforce 1` 可用，但开机自动固化 Enforcing 的机制尚未落实。

---

## 无损首装 <a id="无损首装"></a>

- **方案原理**：无损首装（`--lossless`）在保留 Android 数据前提下缩容 `userdata` 并划出 Linux 分区。执行顺序严格为先缩容文件系统再调整分区表。
- **前置条件与空间归还**：
  - 设备已取得 Android root 权限并安装「SunUEFI 引导助手」模块；
  - `userdata` 内部有足够的连续空闲空间；
  - 删除 Linux 分区并归还空间可在引导助手 WebUI 点击「删除并归还 Android 空间」，下次重启由 SukiSU 自动扩容加密分区。
- **边界说明**：
  - 若缩容成功但分区表写入意外中断，F2FS 文件系统仍小于分区，属于安全状态，Android 可正常启动（仅显示容量变小）；此时应复核计划，切勿盲目擦除。
  - `userdata` 缩容到空闲余量极小（临界边界）时的稳定性待进一步验证。

---

## 充电

- **控制参数与直充机制**：
  - 内核已包含 MCA 直充驱动与 SC8541 芯片支持，参数 `cp_charge`、`hv_charge`、`charge_policy`、`mipps_auth` 均已启用；
  - 连接支持的 PD/MiPPS 充电器后，系统自动完成适配器认证并进入 FFC 直充模式（总线升至约 8.9V、电池电流约 8.8A）。
  - 以上数值来自 sysfs 读数，不是外置功率计测量。
- **已知限制与未验证项**：
  - 满电自动停充、高温降流保护与关机充电尚未取得长期验证数据，原厂 45W 极限快充不作声称；
  - `piano-charge-monitor.service` 监控服务默认处于 disable 状态。

---

## 传感器与自动旋转

- **硬件总线特征（/sys/bus/iio/devices 为空）**：
  - **现象**：系统下查看 `/sys/bus/iio/devices` 路径为空。
  - **技术根因**：SM8750 平台的加速度计、陀螺仪、环境光等传感器由高通底层 SSC（Sensor Subsystem）直接管理，经 QRTR/MHI 总线通信，不暴露于内核标准 IIO 虚拟总线。
  - **用户态接入**：系统通过 `libssc` 与 `iio-sensor-proxy-ssc` 常驻服务将数据桥接至标准 D-Bus 接口（`net.hadess.SensorProxy`）。
- **桌面自动旋转边界**：
  - GNOME（走 Mutter）与 KDE（走 KWin + `qt6-qtsensors`）均原生支持自动旋转；
  - niri 与 Hyprland 合成器不主动监听传感器 D-Bus 接口，需通过配置文件或命令行手动指定 `transform`，详见[桌面档位（desktops.md#自动旋转）](desktops.md#自动旋转)。

---

## 手写笔 <a id="手写笔"></a>

- **支持现状**：触控主控支持手写笔 Type 29 协议，底层驱动的压感（0–1）、坐标定位与倾斜角事件均正常上报。
- **已知限制与未实现**：
  - 笔身侧键暂未映射有效事件（未捕获 `TABLET_TOOL_BUTTON`）；
  - 蓝牙侧按键映射、捏握手势、防误触、绘图曲线调校、磁吸充电状态识别与跨系统重连机制待后续推进；
  - 校准 INI 已带普通 Focus Pen 的 `stylus_2_*` 参数，但这支笔的实际定位与压感还没上机验证。

---

## 触屏、网络、音频与相机 <a id="触屏网络音频与相机"></a>

- **浏览器硬件解码差异**：
  - Google Chrome 与 Firefox 默认启用 Iris V4L2 M2M 硬件解码（1080p H.264 与 HEVC 硬件出帧）；
  - Fedora 官方 Chromium 因依赖系统 `ffmpeg-free`（剥离了 H.264 专利解码器），安装 RPM Fusion 后仅走软件解码。
- **相机出流首帧与曝光收敛**：
  - `piano-camerad` 在首次拉起或切换分辨率时，首帧为暗场黑帧，自动曝光（AE）算法需数帧完成增益收敛；
  - GNOME Snapshot 预览崩溃问题已通过 `piano-pipewire-gstreamer` 替换官方子包修复；
  - 前后摄成像、收敛与预览路径都已单独验证，整机相机验收尚未全部走完。
- **硬件通信边界**：
  - 触控走高频 SPI 通信，DMA 优化延迟控制在 2.5ms 内；音频子系统连续播放与录音保持稳定无 xrun 欠载。
- **DisplayPort 音频**：
  - topology 由 SunUEFI 的 `piano-displayport.m4` 编出，含 `DISPLAY_PORT_RX_0` 与 `MultiMedia2` 链路；UCM 的 `SectionDevice."HDMI"` 走 `hw:${CardId},1`，`JackControl` 为 `DP0 Jack`；
  - 链路已接通，插 DP 的实机验收还没做。

---

## 指纹、红外与 HDR <a id="指纹红外与-hdr"></a>

### 指纹
- **硬件与驱动**：汇顶 Goodix 侧置电容式指纹，安全认证在 TEE 的 `gfpia` TA 中执行，内核 `uio_piano_fingerprint` 与 `qcomtee` 驱动正常加载。
- **系统堆栈**：预装 `piano-fingerprint` 与 `piano-libfprint`，fprintd 识别设备并支持录入与锁屏解锁。
- **已知限制**：系统不支持跨系统直接读取 Android 侧录入的指纹模板，需在 Linux 环境下独立录入。

### 红外
- **硬件与驱动**：消费级红外发射器挂载于 QUPv3 SPI 总线，`/dev/lirc0` 节点与 `ir_spi` 驱动就绪，支持通过 `ir-ctl` 发送红外码。
- **边界说明**：光学发射功率与波形待专用光度设备校验，目前已确认驱动调用路径完整。

### HDR <a id="hdr"></a>
- **面板属性**：硬件面板支持 HDR10，DSI-1 挂载 `HDR_OUTPUT_METADATA`，CRTC 暴露 LUT 属性。
  开启 HDR 后 `Colorspace` 为 `BT2020_RGB`，元数据 blob 解出 `eotf = 2`（PQ）与 BT.2020 原色，
  CRTC 的 SDR `GAMMA_LUT` 被撤掉，扫描输出保持 `AR30` 十位。DPU 的 Gamut、IGC、GC 控制字
  读回 `0x1`、`0x10114`、`0x5`，与上游记录的 PQ 模式取值一致；关闭后三处全部回到旁路。
  校准数据 `qcom/sm8750/piano-boe-display.bin` 校验通过，否则驱动不会创建上述两个属性。
- **KWin 补丁与背光恢复**：
  - 官方未打补丁版 KWin 在退出 HDR 后背光停留在 2047 最大值不回落；
  - extras 打补丁版 KWin 已修复退出 HDR 后的背光恢复问题；
  - 使用 `kscreen-doctor` 配置时，必须同时提交 `hdr.enable` 与 `wcg.enable`。
- **边界说明**：色深与色调映射精度尚未经专业仪器测量，默认配置保持 HDR 与广色域关闭。

### 键盘背光
- **热插拔与状态**：随磁吸键盘插拔动态识别；extras 提供 `gnome-settings-daemon-piano` 补丁支持热插拔刷新；睡眠唤醒后的背光恢复待进一步验收。

---

## 开机日志里的红色报错

冷启动后查看 `journalctl -b -p err` 可能会记录若干红色错误条目。这些属于已定位且无害的底层时序或兼容性噪声，无需排查：

| 来源 / 关键词 | 现象归因 | 说明 |
|---|---|---|
| `adsprpcd` | SEE daemon 尝试在 `/odm/etc/sensors/config/` 下查找额外的厂商专有配置 | 本机为 Fedora 运行环境，未携带额外的 Android 专有 json 配置，不影响传感器正常工作 |
| `60-block-scheduler.rules` | 尝试写入 `bfq` 调度器时报 `Device or resource busy` | udev 规则与内核默认 I/O 调度器的并发时序冲突，各块设备调度器已正常就绪 |
| `[NVT-ts-spi]` | 触控驱动在状态同步时输出日志 | 驱动使用错误日志级别输出了常规信息，触控功能完全正常 |
| `Bluetooth: HCI_IBS_WAKE_ACK` | 蓝牙芯片上电序列状态机输出 | 芯片通信状态机的正常交互过程，蓝牙连接正常 |
| `msm_dpu: no GPU device was found` | DPU 显示控制器比 Adreno GPU 先完成探测（正常开机） | 属于正常开机时模块探测先后的瞬态日志，Adreno 完成探测后两者正常绑定；全新首装整次未绑定的情况见「首次启动 GPU 未绑定」 |
