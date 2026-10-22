# 已知问题

这里只列仍未解决的问题，和已经定位、还要继续观察的噪声。已修复的缺陷不在
这里，修复过程留在本地验收记录。每项写明当前证据和还缺什么。

## 显示与关屏

0038 补丁已合入当前内核，模块字符串里能找到
`slave intf:%d still enabled before cleanup`。反复关屏时，每 2–3 次仍产生
一次 devcoredump：`intf_1`/`intf_2` 的 `is_en` 与配置寄存器非零，而现状应为
全零，差值 18 word。`dmesg` 里 0 条 dpu 错误（underrun 回调只累加计数不打印），
设备未编 tracefs（`/sys/kernel/debug/tracing` 不存在），无法用 tracepoint 继续
定位。屏幕显示正常。这是残余噪声，不是可见故障。

`rtcwake -m mem -s 20` 唤醒正常（实际 22 s），唤醒后无 failed unit。dmesg 中
`IRQ…: set affinity failed(-22)` 与 CMA 分配失败出现在关机 CPU 路径上，属既有
噪声，未发现新增故障。

## 刷写与读回

写 `sunuefi_root`（6.1 GB sparse）之后，设备侧的 ABL 会挂死。`lsusb` 仍能
看到 `18d1:d00d`，但 bulk 端点上任何传输都超时，`fastboot getvar` 不回包。
主机侧救不回来：`USBDEVFS_RESET`、hub 端口 `disable` 重新枚举、`authorized`
翻转都试过，设备重新枚举后仍然不回包。只能长按电源键断电重来。断电回来后再
写 `boot_a` 只要 2.4 秒，说明挂死只跟大分区写入有关。

因此一次完整安装要按这个顺序做：先写 `boot_a`，再写 `sunuefi_esp`，最后写
`sunuefi_root`；root 写完直接断电，重新进 Fastboot 只重启，不再写大分区。一键脚本已经是这个顺序。

原厂 ABL 不实现 `fastboot fetch`。主机 fastboot 37.0.0 认得这个子命令，设备
回 `Device does not support fetch command`，宿主上留下一个 0 字节读回文件。
`sunuefi_esp` 分区也比镜像大（537899008 对 536870912 字节），即使换了支持
`fetch` 的引导器，整份哈希也不会等于镜像摘要。读回只能作辅助证据，刷写正确
与否最终以启动后的 `uname -r`、`findmnt /` 和进入所选桌面为准。

`boot_a` 的读回可以直接读分区前 4 KiB 与镜像内的 `SUNUEFI-NEXTv1` 请求页判断：
首块是 `ANDROID!` 包装头，请求页的目标与 generation 和 `device-boot` 清单一致。

## 首次安装的 userdata

`fastboot erase userdata` 只清分区，不写 BCB 的 `wiped` 标志。在
OS3.0.309.0 上两次安装，重启后 Android 都停在 Recovery，底部显示
`Couldn't initialize user 0`；在 Recovery 里选一次“清除数据”才重建加密
userdata。安装脚本目前只做擦除，这一步需要操作者现场处理，步骤见
[安装指南](installation.md#4-让-android-重建-userdata)。

## 首次启动扩容

封存镜像固定 8192 MiB，文件系统在首次启动时由 initramfs 里的 `resize.f2fs`
扩到 `sunuefi_root` 的实际大小。这条路径的离线部分有测试覆盖：归档里有
`fsck.f2fs` 与 `resize.f2fs` 的符号链接，`blkid -p -o export` 能读出
`FSSIZE`，`resize.f2fs` 对已满的镜像是 no-op。真机扩容验证通过：首启后
`FSSIZE` 与 `blockdev --getsize64` 都等于分区大小，`findmnt /` 看到的是
扩容后的容量。

扩容失败时引导停在 initramfs，`ROOT_GROW_STARTED` 之后的日志里能看到是
`fsck`、`resize` 还是复核那一步失败，不会带着旧容量进系统。分区被收缩到
小于文件系统时报 `The F2FS root is larger than its partition`，`resize.f2fs`
不支持缩小，只能重刷 root。

核对容量用 `blkid -p /dev/<分区> -o export` 的 `FSSIZE` 或
`blockdev --getsize64`，两者都应该等于 `fastboot getvar
partition-size:sunuefi_root` 的字节数。`findmnt -bno SIZE /` 报的是 statfs
数据块，比分区小约 2 MiB，那是 F2FS 扣掉保留块后的结果，不能拿它和分区
字节数直接比。

## 双系统切换

按上游 SunUEFI 的合体 BOOT 模型工作：`boot_a` 里是原厂 Android
内核、3 秒选择器与 UEFI 核心，请求页在这份镜像末尾。Android 与 Linux 都从
A 槽启动，不切活动槽，也不改 GPT。

- Linux → Android：`piano-next-boot android --reboot` 把持久请求写成 target=0，
  正常重启后选择器进入 Android。
- Android → Linux：安装「SunUEFI 引导助手」模块（KernelSU/SukiSU），在模块页面
  选 Linux；没有模块时，开机前 3 秒按音量键选 Linux，选择会被保存。
- 两次独立实测：`boot_android.img` 从请求页 target 0→2→0→2→0、
  sequence 0→4；随后 `flash boot_a device-boot/boot_linux.img`（未签名，
  SHA256 `8fc67dc4…`）直接进 Linux（target=2、sequence=1，40 秒），helper 回
  Android（target=0、sequence=2，45 秒），模块重新采纳后再切 Linux
  （target=2、sequence=3，120 秒），helper 再回 Android（target=0、
  sequence=4，35 秒）。`ro.boot.slot_suffix` 始终是 `_a`，`sys.boot_completed=1`。
- 请求页偏移 37490688（0x23C1000），magic `SUNUEFI-NEXTv1`，两页相隔 4096
  字节滚动写入。

helper 优先读 Android 安装器维护的 ESP 记录
`/boot/efi/EFI/SunUEFI/boot-request.json`，OTA 换槽后由 Android 侧刷新；
`/etc/piano/boot-request.json` 只在早于共享槽位追踪的旧安装上回退使用。

旧的 B 槽布局（Linux 写进 `boot_b`、`set_active b`）不再使用。保留一条历史
排障记录：qbootctl 的 `set_active` 只改活动标志，不交换槽位 type GUID；A 活动
时执行 `qbootctl -s b` 会留下“B 活动 + generic GUID”的表，ABL 的
`ValidateSlotGuids` 会拒绝启动，报 `BootableSlot _b does not have valid guids`。
`fastboot set_active` 走 ABL 的 `SetActiveSlot → SwitchPtnSlots`，会交换 GUID
并切 UFS boot LUN；UFS boot LUN 必须与活动槽一致（1 = A，2 = B）。手工重建 GPT
时主表和备份表要一起写，两份 CRC 都要重算。

仍停在旧 B 槽布局的设备按这条路径迁移：把 `device-boot/boot_android.img` 写进
`boot_a`，把 `/etc/piano/boot-request.json` 的 `boot_device` 改成 `boot_a`，
再用 `fastboot set_active a` 切回 A（B→A 方向 qbootctl 也安全）。

## Android 侧

已装回与当前 BOOT generation 匹配的「SunUEFI 引导助手」模块
（`/data/adb/modules/piano_sunuefi`）。安装走 `adopt` 只读识别现有合体
BOOT，不重写内核；模块页面用它写请求页与重装引导。SukiSU root 仍在
`init_boot_a`：`su -c id` 返回 uid=0、context `u:r:ksu:s0`。

更早的脚手架 `piano_sunuefi_module.zip` 只有模块脚手架及根目录 request 程序，缺少 manager 要求的 bin/ 原生工具和策略/状态。它不能安装，也不要和上面的匹配模块混用。

`boot_a` 被重刷成另一份镜像（例如重新跑 `fastboot flash boot_a device-boot/boot_linux.img`）
后，模块的 `install-state.json` 仍指向旧镜像，`switch linux` 会报
`current BOOT header is not the owned wrapper header`。在模块页面执行一次
“重新安装引导”（`sh /data/adb/modules/piano_sunuefi/manager.sh reinstall`），
让只读 `adopt` 重新采纳当前 BOOT 即可，不需要重写内核。

Android 实测运行在 SELinux Permissive，启动参数带 `selinux=permissive`。
手动 `setenforce 1` 可用，但不能当持久策略，固化方式还没落实。

## 充电

固定源码已包含 SC8541/MCA 直充支持，设备参数 cp_charge、hv_charge、
charge_current、mipps_auth、charge_policy 都开启。这不等于实际达到 45 W。
0036 不应重复套用到已有实现。

已有遥测是高电量下的一次采样：电量 94%，MCA USB online、PD_PPS，约
8.60–8.74 V，sysfs 电压乘电流估算输入 12.075–21.429 W；双电池都 Charging，
温度 32.8–35.5 °C 与 31.9–34.4 °C；SC8541 online 始终为 0。低电量直充与
最高 45 W 未证明，也没有外置功率计读数。需要真实充电器、线材和负载记录功率
与温度。

## 传感器与自动旋转

传感器走 SSC，不走 IIO。`libssc` 与 `iio-sensor-proxy-ssc` 都已装好，
`qrtr_smd`、`qrtr_mhi` 与 `mhi` 三个模块加载后代理能拿到设备：
`HasAccelerometer`、`HasAmbientLight`、`HasProximity` 全为 true，
环境光有实际读数（例如 164 lux），`AccelerometerTilt` 随摆放姿态变化
（平放 `face-up`，举起 `tilted-up`）。`/sys/bus/iio/devices` 为空是正常的，
SSC 后端不经过 IIO 子系统。

Mutter 订阅 `AccelerometerOrientation`，自动旋转已实测：把平板从平放转成
`left-up`，`GetCurrentState` 的逻辑显示器变换从 0 变成 1（顺时针 90°），
屏幕跟着转。旋转锁为 false。罗盘没有对应属性，不参与这一链。

## 手写笔

libinput 捕获 1906 条轴事件、22 条 proximity、18 条 tip 事件，压感 0–1，
位置及倾斜变化正常，未捕获 TABLET_TOOL_BUTTON。设备节点是
`Piano Focus Pen Pro`（`virtual:0022:5081`）与蓝牙侧的
`Xiaomi Focus Pen Pro Mouse/Keyboard`。蓝牙键盘/鼠标节点上的按键、捏握、
防误触、绘图应用效果、充电与重连仍需分别验证。

## 触屏、网络、音频与相机

这些模块已有实机行为记录，持续负载下的表现也单独取证过：

- 触控：45 秒持续单指划动，SYN 报文 97 次/秒，43/45 秒有事件（两个空秒
  是换手间隙），NVT 中断同期增长 5425。X 覆盖 5395–28696、Y 覆盖
  4591–19408，全屏可达；多点 slot 0–5 都出现过。相邻坐标跳变的 p95 为 2770，
  来自多指交替时的 slot 切换，不是丢帧。
- 音频：120 秒播放无 xrun/underrun，音量与 sink 状态全程不变；130 秒录音
  每 10 秒分段都有信号，段均值在 -33.6 到 -18.5 dB 之间，没有静音段。

- 相机：前摄与后摄都能正常成像，详见下方"取帧"两段。
  全部项目验收前，设备整体状态记为未验证。
  验收项见[安装指南的真机验收](installation.md#真机验收清单)。

编解码本身已复验通过。H.264 与 HEVC 各 300 帧的 GOP60 硬件编码与解码，
以及各 300 帧带 B 帧的硬件解码，逐帧摘要与软件解码一致，Iris 中断在每段都有
增长。验收脚本在维护者机器上驱动平板运行，命令见[维护与验收](internals.md#真机取证脚本)；
目标镜像没有 `make` 和 `libx264`/`libx265`，素材由主机准备。

浏览器硬解已单独取证，三家结果不同：

| 浏览器 | 能否播 H.264 | 是否硬解 |
|---|---|---|
| Fedora Chromium 155 | 装上 `libavcodec-freeworld` 后能 | 否 |
| Google Chrome 156（aarch64） | 能 | 是，默认开启 |
| Firefox 158 | 能 | 是，默认开启 |

Chromium 不走硬解有两个叠加原因。Fedora 的 Chromium 链接系统 `libavcodec`，
而 `ffmpeg-free` 在构建时用 `--disable-decoder` 去掉了软件 H.264，所以原样
连 MP4 都打不开，`FFmpegDemuxer: open context failed`；补上 RPM Fusion 的
`libavcodec-freeworld` 才能播放。播放之后它仍然交给软件解码：GPU 进程没有打开
`/dev/video0`，Iris 中断在整个播放窗口内不增长。Chrome 自带 FFmpeg 与 V4L2
解码路径，Firefox 走自己的 VA-API 路径，两者都直接把 `msm_drv_video.so` 用起来。

验收判定不能只看浏览器自己报的状态：软件解码同样拉高 GPU 中断。脚本同时要求
Iris 中断增长和 `/dev/video0` 有持有者，两者缺一即判为非硬解。

相机另有一条容易误判的路径：`piano-camerad` 在没有上游产帧方时会给
v4l2loopback 写黑帧，所以“节点能取到帧”不等于传感器在出图。判定要看 camerad
日志里有没有对应相机的 CAMSS 媒体设备，以及帧内容是不是全黑。设备上取的两张
样张就分属两种情况：前摄那张是正常灰阶画面，另一张只有
一小块亮区、其余全黑。只统计“两个节点各取到 3 帧”会把后者算成通过。

复测时两颗传感器都走上了 CAMSS 产帧路径。用 `gst-launch` 从两个
loopback 节点各取 30 帧，前摄末帧灰度均值 117（满量程 255），画面是正常
室内成像；后摄末帧均值 24、最大值 113，是低照度噪声，当时设备平放在桌面上。

后来把两颗放在同一时段一起取，前摄末帧 p50=94、p90=180、p99=255，四乘六
分块均值从 20 到 216，空间结构清楚；后摄 p50=30、p90=46，分块均值在 23 到 37
之间，平坦且暗。后者能出图但对着暗处，仍按低照度记录，不算画面失败。

在采光正常的同一时段连取两轮后，两颗都给出了稳定且可重复的空间结构：
后摄 p50 为 78 与 65、p90 为 209 与 180，四乘六分块均值 16.9 到 197.9；
前摄两轮 p50 都是 112、p90 为 193 与 194，分块均值 20.2 到 200.7。
两颗都能正常成像，之前后摄那张暗帧是取帧时对着暗处，不是相机本身的问题。

取帧有个时序问题容易误判：`piano-camerad` 在 loopback 没有读者时会停流，
来一个读者就重新起流，而自动曝光每轮都要重新收敛。刚起流的前几帧是黑的，
灰度会从 1 单调爬到稳定值。用第一帧判断画面会把正常出图判成黑屏，必须看
收敛之后的帧。上面前摄与后摄两组数据的第一帧与末帧差距明显：
后摄第一帧 p50=30、同一轮末帧 p50=78，只取开头就会把它误判成暗场。

## 指纹、红外与 HDR

这三项以前被合写成一句"依赖闭源 TEE 固件，主线不支持"。逐项核实后只有指纹
跟 TEE 有关，红外与 HDR 是另外两回事。

### 指纹

侧置电容式，汇顶 Goodix，认证在 TEE 的 `gfpia` TA 里完成。主线没有
`drivers/input/fingerprint/` 这个目录，厂商树里的 `mi_fp` 也只做电源、复位、
中断和 netlink 上报，图像采集与比对全在 TA 里，移植过来不解决任何实质问题。

主线的 QSEECOM 只白名单 `qcom.tz.uefisecapp` 一个应用，不提供通用 TA 客户端
接口，任何第三方 TA（含 `gfpia`）都无法通过它启动。`drivers/tee/qcomtee/`
自 6.18 起在主线，但走 object-IPC，与 piano 的 legacy QSEECom HAL 协议不同，
不能替代。

用户态没有 libfprint 后端能驱动 `gfpia`。唯一跑通的 Goodix + QSEE 完整栈针对
的是另一颗 TA（`gfenu`）、另一套传感器，且明确未上游。

### 红外

消费级红外发射器，接在 QUPv3 SE15（SPI `0x89c000`）。它不是结构光、ToF 或
接近传感器，这个设备**只发不收**——所以"没有 IR 输入设备节点"不能作为它
不支持的判据，正确的检查是 `/dev/lirc0` 存在且能发码。

主线 `drivers/media/rc/ir-spi.c` 完整实现了 CIR 发射，但当前这条链上有四处
缺口叠在一起：

- 设备上 `/dev/lirc*` 与 `/sys/class/rc/` 都不存在；
- 内核配置是 `CONFIG_RC_CORE=m` 但 `# CONFIG_LIRC is not set`，而 `IR_SPI`
  依赖 `LIRC`；
- DT 里没有 SE15 的节点：设备上唯一的 SPI 设备是触控
  （`/soc/qcom,qupv3_1_geni_se@ac0000/spi@a88000/touchscreen@0`），
  `0x89c000` 那段不存在；
- 主线驱动认的 compatible 是 `ir-spi-led`，而厂商 DT 写的是 `ir-spi`。

要让它出 `/dev/lirc0`，得补 SE15 控制器节点、把 compatible 改对、打开
`CONFIG_LIRC` 与 `CONFIG_IR_SPI`。原厂的波形是 32 位 SPI 字、1.92 MHz 采样、
40% 占空比，主线驱动是 16 bits/pulse、默认 50%，载波会有偏差，需要小改驱动
才能完全一致。

### HDR

面板硬件支持 HDR10，30bpp DSC 传输。Linux 侧没有输出路径：msm 不挂
`HDR_OUTPUT_METADATA` 属性，也没有接 DRM 的 colorop 管线。SM8750 的 DSPP
gamma 块直到 2026 年 9 月才修好。目前唯一的 msm 静态 HDR 系列只覆盖 DP 与
eDP，内屏走 DSI，用不上。

面板驱动本身也不在主线（`drivers/gpu/drm/panel/` 里没有 nt36532），我们的
补丁只把 `display_info.bpc` 报对，提交信息自己写明"不声称支持 HDR 或
12-bit 传输"。这一项要等上游 DRM 层的实质推进，本机单方面做不了。

## 内核在线升级

`dnf upgrade` 现在会带上 `kernel-piano`，事务收尾时由 `%posttrans` 重建
initramfs 并重打 ESP 上的 `boot.img`。这条路径在平板上跑过一次真实升级
（`7.2.9-1` → `7.2.9-3`）：`%posttrans` 重建了 initramfs，ESP 上的 `Image`、
`initramfs` 与 `boot.img` 都换了，`.bak` 三份都在，重启后 `uname -r` 是新的
内核，`graphical.target` 起来，`systemctl --failed` 为空，`v4l2loopback` 从新
模块树的 `updates/` 加载。

失败边界是写盘失败时旧 `boot.img` 保持不动，设备继续用旧内核启动。装好但起不
来的内核没有自动回滚，回滚方式见 `docs/copr-repository.md`。

`kernel-piano` 是 installonly 包，升级后旧内核的模块树与 `modules.weakdep`、
`updates/` 会留在 `/usr/lib/modules/` 下（rpm 删掉的是它自己拥有的文件，这些
是 `depmod` 之后生成的）。它们不影响启动，`dnf` 也不会再引用；要清掉只能手工
删目录。

manifest 按 NVRA 命名（`/usr/share/piano/kernel-manifest-<version>-<release>.json`）。
固定名字会让两个版本争同一个路径，rpm 直接中止升级事务——这条在设备上撞过
一次，症状是 `file ... conflicts with file from package`。

## 仓库整理

- `README.md` 与 `README.en.md` 的骨架由 `tests/check_readme_parity.py` 锁定：
  标题层级数量、语言切换行、指向的文档列表必须一致。改一版要同步另一版。
- `docs/public-release-gaps.md` 的电极校准一项已决定：数据早已在公开仓库与公开 COPR 里，`NOTICE` 写明来源，发布不再因此阻挡。`release.yml` 仍默认只建 draft，转公开要显式传 `publish=true`。
- 三个 UEFI 补丁的 `series.json` 里 `note` 引用的文档分两类。`docs/status.md`、
  `docs/ufs-dedicated-area-audit.md` 与 `docs/piano-full-ddr-producer-gaps.md`
  在上游 SunUEFI 仓库，按 `sources.lock.json` 的 pin 取。`docs/device-acceptance-20261015.md`
  已移到不入库的本地笔记，这条引用是历史记录。改 `series.json` 会改变
  `project_source_sha256`，现有 bundle 的 UEFI 来源校验会失效，所以先不动。
