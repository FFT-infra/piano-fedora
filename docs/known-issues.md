# 已知问题与硬件现状

本页列出当前版本下尚未完全解决的问题、特定硬件限制、系统运行日志中的已知噪声，以及安装与维护过程中的注意事项。

已在当前版本中通过真机验证修复的问题不再列入本页。未经过真机冷装验证的修复项继续保留。

---

## 目录

- [显示与关屏](#显示与关屏)
- [固件拒绝 UKI（uki-identity）](#固件拒绝-ukiuki-identity)
- [首次启动 GPU 未绑定](#首次启动-gpu-未绑定)
- [系统缓存（LLCC）没有生效](#系统缓存llcc没有生效)
- [未开启的内核配置](#未开启的内核配置)
- [驱动路径未命中（dw9768 与 fsa4480）](#驱动路径未命中dw9768-与-fsa4480)
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
- **现象**：反复关屏或休眠时，内核日志偶发记录 `slave intf... still enabled before cleanup` 并生成 devcoredump 转储。
- **影响**：属于内核调试信息噪声。屏幕显示与背光正常，休眠与唤醒（`rtcwake`）功能正常，无硬件损坏风险。
- **技术根因**：MSM DPU 驱动在关闭显示接口时，从接口（slave interface）寄存器在清理窗口期未完全清零，触发了驱动的调试断言转储。
- **状态**：作为内核残余日志噪声观察中，不影响正常使用。

### 2. 面板 compatible 标识命名反转
- **现象**：在设备系统内查看 `/proc/device-tree`，面板 compatible 为 `xiaomi,piano-csot-nt36532`，而本机的物理面板绑带识别为 BOE。
- **技术根因**：上游内核驱动源码内部的初始化序列函数名与 compatible 规则存在命名反转对调，实际驱动加载运行的是适配 BOE 面板的初始化序列与命令流，与固件传递的供应商识别完全对应。
- **状态**：属于代码内部符号命名差异，屏幕显示与色彩输出正常，无需更改。

---

## 固件拒绝 UKI（uki-identity）

- **现象**：在 SunUEFI 启动菜单中选择 Linux 后立即弹回菜单，提示「Linux 启动镜像格式不受支持，请更新镜像」，日志报 `PIANO_EBS_REFUSE stage=before-exit status=Access Denied`。
- **影响**：使用 systemd-stub 258+ 生成的 UKI 镜像无法正常被固件引导。
- **技术根因**：systemd-stub 258 起，在跳转进入内核前会临时将 UKI 自身的 `LoadedImage.ImageBase` / `ImageSize` 改写为内嵌内核的地址与大小。SunUEFI 的 `ValidateInstalledEfiImage()` 拿启动前记录的值进行严格校验，发现改写后返回 `EFI_ACCESS_DENIED`。
- **状态**：仓库已收录补丁 `patches/uefi-ebs-identity/` 将比较限定在非 UKI 句柄调用，但按 `patches/README.md:138` 约定，在全新冷装进行真机验证前不把本项记为已修复。

---

## 首次启动 GPU 未绑定

- **现象**：全新冷装后的首次开机过程中，内核日志出现 `msm_dpu ae01000.display-controller: no GPU device was found`，整次开机期间无 `[drm] Initialized msm`，Adreno GPU 未能绑定，KWin 退回 llvmpipe 软件渲染；第二次开机恢复正常。
- **技术根因**：全新首装阶段后台服务并发密集，DPU 控制器比 Adreno GPU 先完成探测，延迟探测等待窗口提前耗尽。
- **状态**：`patches/kernel/0001-linux-73-series.patch` 中已在默认命令行加入正整数 `deferred_probe_timeout` 放宽探测窗口，但设备当前运行的旧 UKI 的 `/proc/cmdline` 尚未包含该参数（旧 UKI 不会被内核包升级改动），必须待新 bundle 全新冷装后生效并验证消除情况。

---

## 系统缓存（LLCC）没有生效

- **现象**：内核编译开启了 `CONFIG_QCOM_LLCC=y`，但在 `/sys/bus/platform/drivers/qcom-llcc/` 目录下未绑定任何设备，`dmesg` 中无 LLCC 相关日志。
- **影响**：系统未配置专用的最后一级缓存（Last Level CPU Controller）切片，Adreno GPU 与系统总线在缺 LLCC slice 状态下运行。根据上游 `yzddmr6/linux-sm8750-piano`（`fe98a3e`）实测，正确配置 LLCC 的收益为 Geekbench 6 单核 +0.9%、多核 +0.5%、Vulkan 计算 +2.7%，内存延迟类子项最高 +9.5%。
- **技术根因**：板级设备树中原厂节点使用 `compatible = "qcom,sun-llcc"`，而当前 7.3 内核驱动仅匹配 `"qcom,sm8750-llcc"`，导致驱动未能匹配 probe 流程。
- **状态**：待上游驱动或内核补丁增加 compatible 匹配项。

---

## 未开启的内核配置

- **现象**：在当前实机 `/proc/config.gz` 中，以下三项内核配置均未开启（未被设置）：
  - `CONFIG_PSI`
  - `CONFIG_WIREGUARD`
  - `CONFIG_USB_UAS`
- **影响**：
  - `CONFIG_PSI` 未开启导致 systemd-oomd 无法获取内存压力指标，PSI 监控失效；
  - `CONFIG_WIREGUARD` 未开启导致系统无法使用内核级 WireGuard VPN 隧道；
  - `CONFIG_USB_UAS` 未开启导致支持 UAS 协议的高速 USB 存储设备回退至传统 usb-storage 驱动，限制连续传输吞吐。
- **状态**：待后续内核配置调整中统一开启并编译验证。

---

## 驱动路径未命中（dw9768 与 fsa4480）

- **现象**：相机音圈马达（VCM）与 Type-C 音频开关芯片驱动未绑定。
- **技术根因**：设备实机提取的设备树中，`vcm@c` 节点声明为 `giantec,gt9769`，`fsa4480@42` 声明为 `qcom,fsa4480-i2c`。而当前补丁添加的 compatible 匹配项分别为 `giantec,gt9764` 与 `dioo,dio4485`，名称不匹配导致驱动探测跳过。
- **状态**：待修正设备树或驱动中的 compatible 字符串。

---

## 启动时的间歇 Oops

- **现象**：曾记录过一次启动时内核 Oops 崩溃，日志显示 `Internal error: Oops - Undefined instruction: 0000000020000000`，发生在启动约 9.4 秒的 `udevd-worker` 内存页映射路径（`folio_add_file_rmap_ptes` / `folio_move_anon_rmap`），屏幕停在内核 panic=0 画面。
- **影响**：偶发性开机失败，未能进入桌面。
- **应对方法**：长按电源键强制断电后重新开机即可恢复。
- **状态**：未获稳定复现，目前全仓库无修复提交，作为未定位间歇问题观察中。

---

## 刷写与读回

- **现象**：
  1. 小米原厂 ABL 不支持 `fastboot fetch` 命令，执行时设备返回 `Device does not support fetch command`。
  2. 刷写 6.1 GB 的稀疏根分区镜像（`pianoroot.f2fs.sparse.img`）完成后，部分设备在 USB bulk 端点上可能出现通信超时，导致后续 `fastboot getvar` 无法响应。
- **影响**：无法通过 fastboot 读回镜像用于校验；刷写超大分区后继续执行 fastboot 命令可能无响应。
- **建议操作**：
  1. 完整安装刷写顺序建议为：先刷写 `boot`，再刷写 `sunuefi_esp`，最后刷写 `sunuefi_root`。
  2. 刷写完根分区后，直接长按电源键断电或重启设备，避免在同一会话中继续写入大文件。
  3. 安装校验以开机进入桌面后的实际挂载状态（`findmnt /`、`uname -r`）为准。
- **技术根因**：原厂 Bootloader 的 Fastboot USB 通信实现限制。

---

## 首次安装的 userdata

- **现象**：全新安装执行 `fastboot erase userdata` 清空分区后，重启平板会进入官方 Recovery 界面，屏幕底部提示 `Couldn't initialize user 0`。
- **影响**：平板无法直接启动进入 Android 系统。
- **应对方法**：在 Recovery 菜单中使用音量键和电源键，手动执行一次**“清除数据”（Wipe Data / Format Data）**并确认。完成后重启，Android 会顺利重建加密的 userdata 并进入系统。具体步骤见[安装指南](installation.md#24-让-android-重建-userdata)。
- **技术根因**：`fastboot erase` 仅擦除了块设备内容，未能写入 Android 启动所需的 BCB（Boot Control Block）重建标志，因此需在 Recovery 中触发格式化。

---

## 双系统切换

- **架构原理**：本项目采用合体 BOOT（Hybrid BOOT）机制，原厂 Android 内核、3 秒选择器与 Linux SunUEFI 核心统一写入当前活动槽的 `boot` 分区（`boot_a` 或 `boot_b`），双系统启动均在当前槽内完成，不切换底层 A/B 槽位，不破坏 GPT 属性。
- **日常切换**：
  - **Linux 切换回 Android**：在 Linux 终端执行 `piano-next-boot android --reboot`，或点击应用程序菜单中的“返回 Android”。
  - **Android 切换回 Linux**：在 Android 中使用「SunUEFI 引导助手」模块切换并重启；若未装模块，开机亮屏前 3 秒按音量键呼出选择菜单，选择 Linux 启动。
- **排障与注意事项**：
  - **严禁使用 `qbootctl -s` 切槽**：`qbootctl` 仅修改活动状态，不联动切换 ABL 的槽位 GUID，会导致 ABL 报错 `BootableSlot does not have valid guids` 拒绝开机。
  - 活动槽位由主备 GPT 与高通启动属性自动识别，切换槽位或 OTA 后无需重新配置。

---

## Android 侧

- **引导助手模块**：已装回与当前 BOOT generation 匹配的「SunUEFI 引导助手」模块，安装采用 `adopt` 只读识别现有合体 BOOT，不重写内核。若重新刷写过 `boot` 分区，需在模块页面重新执行一次“重新安装引导”以重新采纳。
- **SELinux 状态**：Android 侧实测运行在 SELinux Permissive 状态，启动参数带有 `selinux=permissive`。手动执行 `setenforce 1` 可用，但开机自动固化 Enforcing 的机制尚未落实。

---

## 无损首装

- **说明**：无损首装（`--lossless`）可在保留既有 Android 数据的前提下，将 `userdata` 分区缩容并建立 Linux 分区。
- **前置条件与空间归还**：
  1. 设备已取得 Android root 权限并安装「SunUEFI 引导助手」模块；
  2. `userdata` 内部有足够的连续空闲空间；
  3. 若需删除 Linux 分区并将空间完整归还 Android，可在引导助手 WebUI 存储页点击 **删除并归还 Android 空间**，下次重启由 SukiSU 早期维护执行 `resize.f2fs -k -t` 扩大加密分区。
- **尚未经过真机验证的路径**：
  - 重复安装路径；
  - 携带 `--accept-data-loss` 的全盘重建路径；
  - `userdata` 缩容到空闲余量极小（临界边界）时的稳定性。
- **注意事项**：若缩容成功但分区表写入中断，F2FS 文件系统仍小于分区，属于安全状态，Android 可正常开机（仅显示容量变小）。此时应复核计划与备份，切勿盲目擦除。

---

## 充电

- **现状**：内核已包含 MCA 直充驱动与 SC8541 芯片支持，设备参数 `cp_charge`、`hv_charge`、`charge_policy` 已启用。
- **实测表现**：
  - 使用兼容的高功率 PD / PPS 充电器时，可正常握手进入直充模式，sysfs 读数显示电池侧功率可达约 35W，温度控制在正常阈值内；断开电源时直充状态安全退出。
  - 当前版本对关机充电及原厂 45W 极限快充不作声称，原厂温控与分级充电策略持续跟进上游。

---

## 传感器与自动旋转

- **说明**：在系统下查看 `/sys/bus/iio/devices` 为空是正常现象。SM8750 的传感器由高通底层 SSC（Sensors Subsystem Core）固件管理，不以原生 IIO 节点暴露。
- **运行机制**：
  - 系统内置 `iio-sensor-proxy-ssc` 服务，负责与底层 SSC 通信并将传感器数据转为标准 D-Bus 接口。
  - **自动旋转**：GNOME（走 Mutter）与 KDE（走 KWin + `qt6-qtsensors`）均已实机验证，屏幕随平板物理朝向平滑自动旋转；niri 与 Hyprland 默认未接入自动旋转守护进程，支持通过快捷键或配置手动指定 `transform` 旋转方向。

---

## 手写笔

- **现状**：
  - 平板触控主控已支持手写笔的物理交互报文（Type 29 协议），底层驱动中压感、坐标定位与倾斜角事件均能正常捕获。
  - 目前手写笔的快捷按键映射、低延迟压感调校以及完整的桌面手写应用生态尚在接入与完善中。

---

## 触屏、网络、音频与相机

- **触控与音频**：
  - 触控走 SPI 通信，DMA 延迟已优化控制在 2.5ms 以内，全屏触控平滑、无丢帧；
  - 扬声器播放与内置麦克风录音稳定，无音频缓冲异常或杂音。
- **视频硬解**：
  - Iris V4L2 M2M 驱动工作正常，支持 1080p H.264 与 HEVC 硬件编解码。
  - 浏览器支持差异：Google Chrome 与 Firefox 默认启用硬件解码；Fedora 官方 Chromium 因上游版权策略移除了 H.264 解码器，需安装 RPM Fusion 的 `libavcodec-freeworld` 方可播放 MP4，且走软件解码。
- **相机**：
  - 前置与后置摄像头均可正常出图（由 `piano-camerad` 服务对接 ISP 并输出至 v4l2loopback）。
  - 相机起流后的前数帧处于自动曝光收敛阶段，画面可能短暂偏暗，收敛后成像正常。
  - GNOME Snapshot 等通过 PipeWire 预览相机画面的应用崩溃问题，已在软件源中提供 `piano-pipewire-gstreamer` 解决。
  - **整机验收状态**：整机相机验收尚未全部走完，设备整体状态仍记为未验证。

---

## 指纹、红外与 HDR

### 指纹
- 采用 Goodix 侧置电容式指纹传感器，认证在 TEE 内的安全程序中完成。
- 系统内置 `piano-fingerprint` 客户端与打过补丁的 `piano-libfprint`，fprintd 可正常识别设备，支持指纹录入、匹配校验以及桌面锁屏解锁。
- **边界说明**：录入与解锁在 v45.3 之前的镜像上验证通过，v45.3 及之后的镜像未重新跑全套认证；不支持直接读取 Android 既有模板。

### 红外
- 消费级红外发射器（接在 QUPv3 SE15）已绑定至标准 `ir-spi` 驱动与 LIRC 框架，`/dev/lirc0` 设备节点存在，可通过 `ir-ctl` 发送红外码。
- **边界说明**：目前仅支持发射，不支持接收学习；光学输出功率与波形未经过专业仪器实测。

### HDR <a id="hdr"></a>
- 面板硬件支持 HDR10，DSI-1 输出属性中已挂载 `HDR_OUTPUT_METADATA`。
- 在 KDE Plasma 桌面环境下，使用补丁版 KWin 可正常开启与关闭 HDR 开关，并在退出 HDR 时正确恢复原背光亮度；色深与色调映射精度尚未经过专业仪器测量。

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
