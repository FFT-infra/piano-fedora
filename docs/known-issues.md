# 已知问题与硬件边界

列出当前版本仍存在的缺陷、硬件限制以及可安全忽略的开机日志。已经在当前版本真机验证修复的问题不再保留。

## 目录

- [显示与关屏](#显示与关屏)
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
- [Denial 桌面](#denial-桌面)
- [开机日志里的红色报错](#开机日志里的红色报错)

---

## 显示与关屏

**关屏 devcoredump 噪声**：反复灭屏或挂起约每 2-3 次会出现一条 `slave intf... still enabled before cleanup` 转储，属于 MSM DPU 清理从接口时的残留日志，不影响显示、背光与唤醒（`rtcwake -m mem -s 20` 实测约 22 秒唤醒无失败单元）。

**面板 compatible 命名反转**：`/proc/device-tree` 下显示兼容名可能写成 CSOT，而物理绑定为 BOE，系早期驱动函数命名对调所致；实际加载的初始化序列对应 BOE 面板，不影响显示效果。

---

## 首次启动 GPU 未绑定

部分冷装首次开机时可能出现 `msm_dpu ae01000.display-controller: no GPU device was found`，导致 KWin/Mutter 暂时回退到 llvmpipe 软渲染，第二次开机恢复。`patches/kernel/0001-linux-73-series.patch` 已在默认启动命令行中调大 `deferred_probe_timeout` 窗口作为缓解，如遇首启无 GPU 加速直接重启一次即可。

---

## 系统缓存（LLCC）没有生效

内核已编入 `CONFIG_QCOM_LLCC=y`，但当前出货设备树写的是 `"qcom,sun-llcc"`，驱动仅匹配 `"qcom,sm8750-llcc"`，`/sys/bus/platform/drivers/qcom-llcc/` 下没有绑定设备。需等待上游驱动或 DT 补齐匹配项。

---

## 未开启的内核配置

当前内核镜像 `/proc/config.gz` 中 `CONFIG_PSI`、`CONFIG_WIREGUARD` 与 `CONFIG_USB_UAS` 均未打开，因此 `systemd-oomd` 缺内存压力指标、无法使用内核态 WireGuard、高速 USB 存储会回退到 `usb-storage`。将在后续内核配置更新时补齐。

---

## 驱动路径未命中（fsa4480） <a id="驱动路径未命中"></a>

出货 DTB 的 Type-C 音频开关节点写为 `qcom,fsa4480-i2c`，而内核 `typec/mux/fsa4480.c` 只匹配 `fcs,fsa4480` 与 `dioo,dio4485`，导致驱动跳过绑定、无法软件控制 SBU 切换。待补丁加入匹配表。

---

## 启动时的间歇 Oops

极少数开机过程在约 9.4 秒 `udevd-worker` 路径上会偶发 `Oops - Undefined instruction: 0000000020000000` 并停在 panic 画面，尚未稳定复现定位。遇到时强制断电重启即可，不要据此修改启动链。

---

## 刷写与读回

原厂 ABL 不支持 `fastboot fetch`（会报 `Device does not support fetch command`），且刷完约 6.1 GiB 的稀疏根镜像后 USB bulk 传输可能超时导致后续 `fastboot getvar` 无响应。安装须严格按 `boot` → `sunuefi_esp` → `sunuefi_root` 顺序执行，根分区写完后直接重启进系统，以桌面下的 `findmnt /` 与 `uname -r` 为准。

---

## 首次安装的 userdata

全新安装执行 `fastboot erase userdata` 后，首次切入 Android 时可能会停在官方 Recovery 并提示 `Couldn't initialize user 0`（`fastboot erase` 不写 BCB 格式化标记）。用音量键与电源键在 Recovery 菜单里手动选一次「清除数据（Wipe Data）」再重启即可重建加密分区，见[安装指南](installation.md#24-让-android-重建-userdata)。

---

## 双系统切换 <a id="双系统切换"></a>

严禁使用 `qbootctl -s` 手动切换底层 A/B 活动槽。双系统使用同一个活动槽里的合体 BOOT，仅改高通槽位属性而不配合 ABL 槽位 GUID 校验会导致直接拒开机。

日常切换只写合体 BOOT 末尾偏移（`SUNUEFI-NEXTv1` 请求页），操作见[升级与维护](upgrade.md#二-双系统往返)。

---

## Android 侧

- **引导助手模块**：新刷过 `boot` 分区后，需在 KernelSU/SukiSU 的「SunUEFI 引导助手」页面重跑一次「重新安装引导（adopt）」以重新采纳当前 BOOT。
- **Android SELinux**：当前内核给 Android 传参 `selinux=permissive`；手动 `setenforce 1` 可用，默认开机固化 Enforcing 尚未落地。

---

## 无损首装 <a id="无损首装"></a>

`--lossless` 在保留 Android 数据下在线缩容加密 `userdata`，严格先缩容文件系统再写 GPT。要求已获取 Android root、装好引导助手且 `/data` 有连续空闲空间；在极小余量临界状态下缩容的稳定性仍待更多样本验证。

如缩容已完成但写 GPT 时意外中断，F2FS 只是小于分区本身，Android 仍能正常开机（可用空间显示变小），复核后继续写表即可，切勿擦除 `userdata`。在引导助手 WebUI 点击「删除并归还 Android 空间」可于下次重启恢复空间。

---

## 充电

内核含 MCA 与 SC8541 直充驱动，实测连接 PD/MiPPS 充电器可握手进入 FFC 直充（sysfs 读数约 8.9V / 8.8A，未接外置功率计）。满电自动停充、高温降流保护与关机充电缺乏长期实测数据，不宣称 45W 极限快充；`piano-charge-monitor.service` 默认处于 disabled。

---

## 传感器与自动旋转

SM8750 的加速度计、光感与接近传感器由高通 SSC 管理，通过 QRTR/MHI 与 `libssc` + `iio-sensor-proxy-ssc` 暴露到 D-Bus，因此 `/sys/bus/iio/devices` 为空属正常现象。

GNOME 与 KDE 原生订阅 SensorProxy 支持自动旋转；niri 与 Hyprland 不读取传感器 D-Bus，需手动配置输出方向，见[桌面档](desktops.md#自动旋转)。

---

## 手写笔 <a id="手写笔"></a>

内核驱动支持 Type 29 协议的坐标、0-1 压感、倾斜角与接近感应。侧键映射、防误触手势、磁吸充电状态以及 Focus Pen `stylus_2_*` 校准参数尚未在真机上完整实现或验证。

---

## 触屏、网络、音频与相机 <a id="触屏网络音频与相机"></a>

- **浏览器硬解**：Google Chrome 与 Firefox 默认走 Iris V4L2 M2M 1080p H.264/HEVC 硬解；Fedora 自带 Chromium 因链接剥离了专利解码器的 `ffmpeg-free` 而不支持 H.264 硬解（装 RPM Fusion `libavcodec-freeworld` 后也仅软件解码）。
- **相机**：`piano-camerad` 前后摄预览与拍照正常（GNOME Snapshot 预览崩溃已通过 `piano-pipewire-gstreamer` 修复）；开流或切换分辨率时首帧为暗场黑帧，自动曝光需数帧收敛。
- **触控矩阵与掌托**：默认镜像由内核 IRQ 线程直接解码手指矩阵，同时由用户态根据 `/proc/nvt_thp_status` 自动退避，保证仅暴露一个 `piano NT36532 THP touchscreen` 设备。内核路径固定矩阵尺寸，不读取 `xiaomi-piano-thp` 分发的逐屏校准；掌托识别与多指跟手性待长期验收。
- **扬声器 DAP 与 DisplayPort**：拓扑将立体声按原厂矩阵铺到四路功放（上限 235，但缺少原厂 DSP 扬声器硬件保护），UCM 同时包含 DisplayPort 音频路由；两者的完整真机听感与外接 DP 音频验收尚未收尾。

---

## 指纹、红外与 HDR <a id="指纹红外与-hdr"></a>

### 指纹

预装 `piano-fingerprint` 与 `piano-libfprint`（跟随上游重写后的 TEE 路径，加载 `uio_piano_fingerprint` 与 `qcomtee`）。当前版本只确认了初始化与 `fprintd` 设备枚举，真机录入与锁屏解锁尚未验证通过；指纹模板与 Android 完全隔离。

### 红外

QUPv3 SPI 上的红外发射器经 `ir_spi` 暴露 `/dev/lirc0`，可通过 `ir-ctl` 发码，具体光功率与波形未经专业仪器校准。

### HDR <a id="hdr"></a>

面板与 DPU 支持 HDR10（`BT2020_RGB`、PQ 元数据与 `AR30` 输出，依赖 `qcom/sm8750/piano-boe-display.bin`）。默认关闭 HDR 与广色域：原版 KWin 退出 HDR 后背光会卡在 2047，需使用 extras 中的补丁版 KWin，用 `kscreen-doctor` 开关时须同时传递 `hdr.enable` 与 `wcg.enable`。色准与峰值亮度未经仪器测量。

### 键盘背光

磁吸键盘插拔可识别，GNOME 下安装 extras 中的 `gnome-settings-daemon-piano` 后支持键盘背光热插拔刷新；休眠唤醒后的键盘背光恢复尚待实测。

---

## Denial 桌面

`denial` 与 `denial-flutter-engine` 在构建镜像时直接从上游 `packages.denialwm.org` 的 Fedora 44 仓库安装（Fedora 45 尚无上游仓库），[不钉版本](desktops.md#第三方软件源)，`v45.11` 的 `denial` 镜像装到的是 `0.6.10-1`。这两个包不进本仓库的 COPR，装好的系统里也没有 Denial 的源，`dnf upgrade` 不会更新它们。

实机记录对应 `0.6.10-1`：参考平板就地升级到这个版本后重启，greetd 自动登录拉起 `deniald --start-locked`，`DSI-1` 以 3200×2136 输出，`systemctl --failed` 为空；GPUI/Vulkan 版 `denial-settings` 在会话里运行 5 秒未退出，这次启动以来 `coredumpctl` 没有记录。检查时会话处于锁屏，解锁后的界面操作没有记录；关机后的冷启动与直接刷入 `denial` 镜像的安装不在这份记录里，镜像装到别的版本时这份记录也不适用。

`denial` 档没有自动旋转，原因见[桌面档](desktops.md#自动旋转)。

---

## 开机日志里的红色报错

冷启动后 `journalctl -b -p err` 中的以下日志属于已知无害噪声：

| 日志关键词 | 原因说明 |
| --- | --- |
| `adsprpcd` | 查找 Android `/odm/etc/sensors/config/` 专有配置失败，不影响 SSC 传感器工作 |
| `60-block-scheduler.rules` | udev 设置 `bfq` 时与内核默认调度器并发提示 `Device or resource busy`，各设备调度器已生效 |
| `[NVT-ts-spi]` | 触控驱动以 `err` 级别打印常规状态同步信息 |
| `Bluetooth: HCI_IBS_WAKE_ACK` | 蓝牙芯片上电通信状态机常规输出 |
| `msm_dpu: no GPU device was found` | 正常启动中 DPU 先于 Adreno 探测时的瞬态日志（Adreno 就绪后自动完成绑定；若整次未绑定见前文 GPU 一节） |
