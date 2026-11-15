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
- [驱动路径未命中（dw9768 与 fsa4480）](#驱动路径未命中)
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
- 反复关屏实测每 2–3 次产生一次 devcoredump：`intf_1`/`intf_2` 寄存器非零（差值 18 word）。
- `dmesg` 中未记录 DPU 错误（underrun 回调只累加计数不打印），设备未编 tracefs 无法继续 trace。
- `rtcwake -m mem -s 20` 唤醒正常（实测 22 秒），唤醒后无 failed unit。
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
- **应对方法**：使用打入补丁的 SunUEFI 固件，或暂时使用未改写句柄属性的早期 stub。
- **状态**：已修复待真机验证（补丁 `patches/uefi-ebs-identity/` 已收录，待冷装验证）。

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

基准测试收益参考（上游 `yzddmr6/linux-sm8750-piano@fe98a3e` 实测）：
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

## 驱动路径未命中（dw9768 与 fsa4480） <a id="驱动路径未命中"></a>

- **现象**：相机音圈马达（VCM）与 Type-C 音频开关芯片驱动未绑定。
- **影响**：相机马达无法通过 dw9768 控制对焦，音频切换芯片无法通过 fsa4480 驱动受控。
- **技术根因**：实机设备树声明与补丁匹配项不一致导致探测跳过：
  - 声明为 `giantec,gt9769` 与 `qcom,fsa4480-i2c`；
  - 补丁添加项为 `giantec,gt9764` 与 `dioo,dio4485`。
- **状态**：未解决（待修正设备树或驱动中的 compatible 声明）。

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

## 双系统切换

- **架构原理**：
  - 本项目采用合体 BOOT（Hybrid BOOT）机制；
  - 原厂 Android 内核、3 秒选择器与 Linux SunUEFI 核心统一写入活动槽的 `boot` 分区；
  - 双系统启动均在当前槽内完成，不切换底层 A/B 槽位，不破坏 GPT 属性。
- **日常切换**：
  - Linux 切回 Android：终端执行 `piano-next-boot android --reboot`，或点击应用菜单「返回 Android」；
  - Android 切回 Linux：使用「SunUEFI 引导助手」模块切换并重启；
  - 无模块时切换：开机亮屏前 3 秒按音量键呼出选择菜单启动 Linux。
- **实测往返**：
  - 两轮真机往返时序记录：
    - `boot_android.img` 请求页 target 0→2→0→2→0、sequence 0→4；
    - 刷入未签名 `device-boot/boot_linux.img` 进 Linux（target=2, sequence=1, 耗时约 40 秒）；
    - helper 切回 Android（target=0, sequence=2, 耗时约 45 秒）；
    - 模块重新采纳后再切 Linux（target=2, sequence=3, 耗时约 2 分钟）；
    - helper 再切回 Android（target=0, sequence=4, 耗时约 35 秒）；
    - `ro.boot.slot_suffix` 全程保持 `_a`，`sys.boot_completed=1`。
  - 请求页存储位置：固定位于镜像末尾偏移 37490688（`0x23C1000`），魔数为 `SUNUEFI-NEXTv1`。
  - 活动槽定位：helper 读取主备 GPT 与高通 A/B 属性（priority 48..49、active 50、successful 54）：
    - 实机 `boot_a` 属性为 `0x0077…`（priority 3、active、successful）；
    - `boot_b` 为 `0x0072…`（priority 2、非 active），正确定位 `boot_a`。
- **排障提醒**：
  - 严禁使用 `qbootctl -s` 切槽：`qbootctl` 仅修改活动状态，不联动切换 ABL 槽位 GUID；
  - A 槽活动时执行 `qbootctl -s b` 会导致 GUID 校验失败，触发 ABL 报错拒开机；
  - 活动槽位由主备 GPT 与启动属性自动识别，切换槽位或 OTA 后无需重新配置。

---

## Android 侧

- **引导助手模块**：
  - 已装回与当前 BOOT generation 匹配的「SunUEFI 引导助手」模块；
  - 安装采用 `adopt` 只读识别现有合体 BOOT，不重写内核；
  - 若重新刷写过 `boot` 分区，需在模块页面重新执行一次“重新安装引导”以重新采纳。
- **SELinux 状态**：
  - Android 侧实测运行在 SELinux Permissive 状态，启动参数带有 `selinux=permissive`；
  - 手动执行 `setenforce 1` 可用，但开机自动固化 Enforcing 的机制尚未落实。

---

## 无损首装

- **方案说明**：
  - 无损首装（`--lossless`）可在保留既有 Android 数据前提下缩容 `userdata` 并建 Linux 分区；
  - 执行顺序必须为先缩容文件系统再调整分区表，确保文件系统始终小于或等于物理分区。
- **前置条件与空间归还**：
  - 设备已取得 Android root 权限并安装「SunUEFI 引导助手」模块；
  - `userdata` 内部有足够的连续空闲空间；
  - 删除 Linux 分区并归还空间可在引导助手 WebUI 点击「删除并归还 Android 空间」；
  - 下次重启由 SukiSU 早期维护执行 `resize.f2fs -k -t` 扩大加密分区。
- **未验路径**：
  - 重复安装路径；
  - 携带 `--accept-data-loss` 的全盘重建路径；
  - `userdata` 缩容到空闲余量极小（临界边界）时的稳定性。
- **注意事项**：若缩容成功但分区表写入中断，F2FS 文件系统仍小于分区，属于安全状态：
  - Android 可正常开机（仅显示容量变小）；
  - 此时应复核计划与备份，切勿盲目擦除。

---

## 充电

- **控制参数**：
  - 内核已包含 MCA 直充驱动与 SC8541 芯片支持；
  - 参数 `cp_charge`、`hv_charge`、`charge_policy`、`mipps_auth`、`charge_current` 已启用。
- **实测表现**：
  - 在当前镜像（`kernel-piano-7.3.3-2.piano`）上使用 PD 充电器实测：
    - `MiPPS adapter authenticated` 之后进入 `FFC battery charging mode`；
    - 直充总线从 7.6 V 抬到 8.94 V，电池电流升到 8.8–8.9 A、电压约 4.0 V；
    - 按 sysfs 估算电池侧功率约 35 W；
    - SC8541 online 从 0 变 1，温度由 42.5 °C 升至 55.5 °C 后回落，双电芯 29–36 °C；
    - 电量从 17% 充至 39% 耗时约 17 分钟；
    - 断开电源时打出 `source lost during direct charging`，退出直充回退至标准充电。
- **边界说明**：
  - 以上数据均为 sysfs 读数而非外置功率计测量；
  - 满电停充、温控降流与关机充电尚未取得完整记录，原厂 45 W 极限快充不作声称；
  - `piano-charge-monitor.service` 默认处于 disable 状态，真机尚未长期开机记录。

---

## 传感器与自动旋转

- **硬件说明**：在系统下查看 `/sys/bus/iio/devices` 为空是正常现象。SM8750 的传感器由高通底层 SSC 管理。
- **运行机制**：
  - 底层驱动依赖 `qrtr_smd`、`qrtr_mhi` 与 `mhi` 模块；
  - 内置 `libssc` 与 `iio-sensor-proxy-ssc` 服务将传感器数据转为标准 D-Bus 接口；
  - D-Bus 接口 `HasAccelerometer`、`HasAmbientLight`、`HasProximity` 均为 true；
  - 环境光传感器读数正常（如 164 lux），`AccelerometerTilt` 随姿态变化（`face-up`/`tilted-up`）。
- **自动旋转验证**：
  - GNOME（走 Mutter）与 KDE（走 KWin + `qt6-qtsensors`）均订阅方向属性；
  - 实机验证把平板转为 `left-up` 时，逻辑显示器变换矩阵从 0 变 1（顺时针 90°），桌面平滑旋转；
  - niri 与 Hyprland 默认未接入自动旋转守护进程，支持通过配置手动指定 `transform`。

---

## 手写笔

- **现状与取证**：
  - 触控主控支持手写笔 Type 29 协议，底层驱动压感、坐标定位与倾斜角事件均能正常捕获；
  - libinput 捕获 1906 条轴事件、22 条接近事件、18 条笔尖事件，压感 0–1，位置倾斜正常；
  - 未捕获 `TABLET_TOOL_BUTTON` 事件。
- **设备节点**：
  - 节点映射为 `Piano Focus Pen Pro`（`virtual:0022:5081`）；
  - 蓝牙侧节点映射为 `Xiaomi Focus Pen Pro Mouse/Keyboard`。
- **待验证项**：蓝牙侧按键映射、捏握手势、防误触、绘图调校、磁吸充电与重连仍需验证。

---

## 触屏、网络、音频与相机

- **触控取证**：
  - 走 SPI 通信，DMA 延迟优化压至 2.5ms 以内；
  - 45 秒持续划动 SYN 报文 97 次/秒，43/45 秒有事件，NVT 中断同期增长 5425；
  - 坐标覆盖 X 5395–28696、Y 4591–19408 全屏可达，多点触控 slot 0–5 齐全；
  - 相邻坐标跳变 p95 为 2770（多指交替时的 slot 切换，无丢帧）。
- **音频取证**：
  - 120 秒连续播放无 xrun/underrun，音量与 sink 状态保持稳定；
  - 130 秒连续录音每 10 秒分段均有清晰信号，段均值介于 -33.6 至 -18.5 dB，无静音段。
- **视频硬解**：
  - Iris V4L2 M2M 驱动支持 1080p H.264 与 HEVC 各 300 帧 GOP60 及带 B 帧素材硬件编解码；
  - 逐帧摘要与软件解码完全一致，Iris 中断同步增长；
  - 硬解判定要求 Iris 中断增长且 `/dev/video0` 节点被持有，两者缺一即非硬解；
  - 浏览器支持差异：Chrome 与 Firefox 默认硬解；Chromium 需装 RPM Fusion 解码包走软解。
- **相机成像**：
  - 前摄与后摄均可正常出图（`piano-camerad` 对接 ISP 并输出至 v4l2loopback）；
  - 取帧收敛：camerad 无读者时停流，新读者接入时重新起流并重新收敛自动曝光；
  - 收敛实测：后摄首帧 p50=30（低照度噪声），末帧 p50=78；前摄两轮 p50 均为 112；
  - GNOME Snapshot 预览崩溃问题已通过 `piano-pipewire-gstreamer` 修复；
  - 整机相机验收尚未全部走完，设备整体状态仍记为未验证。

---

## 指纹、红外与 HDR

三项硬件的具体驱动路径、取证状态与验证边界如下：

### 指纹
- **硬件与驱动**：
  - 汇顶 Goodix 侧置电容式指纹，认证在 TEE 的 `gfpia` TA 安全程序中完成；
  - 内核 `uio_piano_fingerprint` 与 `qcomtee` 已加载，`dmesg` 记录 `QTEE version 5.2.0`；
  - `/dev/uio0`、`/dev/tee0` 与 DT 节点齐全，DT overlay 持续生效。
- **用户态堆栈**：预装 `piano-fingerprint` 与 `piano-libfprint`，fprintd 识别设备。
- **验证记录**：
  - v45.3 前镜像走通录入、认证与锁屏指纹解锁（`fprintd-enroll`/`fprintd-verify`）；
  - v45.3 及后续镜像未重跑全套认证；不支持直接读取 Android 模板。

### 红外
- **硬件与驱动**：
  - 消费级红外发射器（接在 QUPv3 SE15，SPI `0x89c000`），只发不收；
  - 节点 `/dev/lirc0` 与 `/sys/class/rc/rc0` 存在，`dmesg` 记录 `ir-spi` 注册信息；
  - 驱动 `ir_spi` 与 `rc_core` 已加载，内核开启 `CONFIG_LIRC=y`、`CONFIG_IR_SPI=m`。
- **使用方法**：支持通过 LIRC 发码（如 `ir-ctl -d /dev/lirc0 --carrier=38000 --send=remote.txt`）。
- **验证边界**：光学输出功率与波形未用仪器实测，无回环端，仅确认设备节点与驱动路径。

### HDR <a id="hdr"></a>
- **面板属性**：硬件面板支持 HDR10，DSI-1 挂载 `HDR_OUTPUT_METADATA`，CRTC 暴露 LUT 属性。
  开启 HDR 后 `Colorspace` 为 `BT2020_RGB`，元数据 blob 解出 `eotf = 2`（PQ）与 BT.2020 原色，
  CRTC 的 SDR `GAMMA_LUT` 被撤掉，扫描输出保持 `AR30` 十位。DPU 的 Gamut、IGC、GC 控制字
  读回 `0x1`、`0x10114`、`0x5`，与上游记录的 PQ 模式取值一致；关闭后三处全部回到旁路。
  校准数据 `qcom/sm8750/piano-boe-display.bin` 校验通过，否则驱动不会创建上述两个属性。
- **KWin 补丁与背光恢复**：
  - extras 打补丁版 KWin 包含退出 HDR 恢复背光修复；
  - 实测两轮：`3.piano1` 背光 1024→2047→1024，`4.piano1` 1556→2047→1556，退出后准确恢复；
  - 官方未打补丁版 KWin 在退出 HDR 后背光停留在 2047 最大值不回落。
- **属性提交限制**：使用 `kscreen-doctor` 配置时，必须同时提交 `hdr.enable` 与 `wcg.enable`。
- **边界说明**：色深与色调映射精度尚未经专业仪器测量，默认配置保持 HDR 与广色域关闭。

### 键盘背光
- **热插拔与状态**：
  - 随磁吸键盘插拔动态出现和消失；
  - extras 软件源提供 `gnome-settings-daemon-piano`（带上游 50.1 热插拔刷新补丁）；
  - 默认镜像不装该包；真机尚未验收接上/取下与睡眠唤醒后的背光恢复。

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
