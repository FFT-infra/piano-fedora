# 已知问题

本页只列仍未解决的问题，以及已定位、仍在观察的噪声。已修复的缺陷不在本页，修复过程留在本地验收记录。每项写明当前证据与复现条件。

## 显示与关屏

0038 补丁已合入当前内核。模块字符串里能找到 `slave intf:%d still enabled before cleanup`。反复关屏时，每 2–3 次仍产生
一次 devcoredump：`intf_1`/`intf_2` 的 `is_en` 与配置寄存器非零，而现状应为
全零，差值 18 word。`dmesg` 里 0 条 dpu 错误（underrun 回调只累加计数不打印），
设备未编 tracefs（`/sys/kernel/debug/tracing` 不存在），无法用 tracepoint 继续
定位。屏幕显示正常。这是残余噪声，不是可见故障。

`rtcwake -m mem -s 20` 唤醒正常（实际 22 s），唤醒后无 failed unit。dmesg 中
`IRQ…: set affinity failed(-22)` 与 CMA 分配失败出现在关机 CPU 路径上，属既有
噪声，未发现新增故障。

旋转屏幕时画面会短暂花掉一下，随后自行恢复。任何一次方向切换都能复现。
`dmesg` 在同一秒内留下两条：

```
[drm:dpu_encoder_phys_vid_wait_for_commit_done:550] [dpu error]vblank timeout: 400000
[drm:dpu_kms_wait_for_commit_done:529] [dpu error]wait for commit done returned -110
```

`400000` 是 `CTL_FLUSH` 寄存器读回的值，`0x400000` 即 `BIT(22)`。`dpu_hw_ctl.c`
里 `DSC_IDX` 是 22，这一位代表 DSC 的 flush 请求，硬件没有在等待窗口内清掉它。
等待超时在 `dpu_encoder_phys_vid.c` 的 `wait_for_commit_done` 里，
`msecs_to_jiffies(50)`，即 50 ms。当前模式 144 Hz，相当于 7 帧。

内屏是双 DSI 加 DSC 的面板，3200x2136，两路各 4 lane，链路固定 1197.5 Mbps，
刷新率靠改水平前肩切换，DSC 为 8 bpp。旋转只改 DPU 图层的 transform，但合成
器会重新提交整条管线（几何从 3200x2136 变成 971x1455、缩放 2.2），DPU 要重算
图层划分和 DSC slice 边界。flush 位靠 vblank 中断清除，50 ms 内两次提交都没等
到，第三次成功。

启动日志里同一条 DSI/DPU 链上还有 `arm-smmu 3da0000.iommu … -110` 与
`msm-mdss ae00000.display-subsystem … -110`，两者都是电源域依赖导致的 deferred
probe。旋转时的 DSC flush 超时属于同一类时序余量不足，重试即恢复，之后不再
累积（两次之后计数保持不变）。

调大这个超时没有用。一次 vblank 在 144 Hz 下约 7 ms，50 ms 已经留了 7 帧余量，
改大只会延长花屏持续时间。要真正消除得动 msm 驱动：在 transform-only 的提交里
跳过 DSC flush，或在提交前显式等 DPU idle。这属于上游驱动的范围。

这台设备的面板身份是 BOE。原厂触控驱动把 `lcd_id_value` 定义为 0=CSOT、1=BOE
（`nt36xxx.c`），本机 `/proc/nvt_thp_status` 的 `lcd_id: 1`，内核因此加载
`novatek_nt36532_piano_fw_boe.bin`，`panel_vendor` 为 `boe`。这条判据与原厂
UEFI 的两份面板描述文件、`PianoFactoryPanel.h` 的选择表一致。

UEFI 按原厂 ABL 的 `msm_drm.dsi_display0` 改写面板 `compatible`，实机
`/proc/device-tree` 读回是 `xiaomi,piano-csot-nt36532`。ESP 上的 `board.dtb` 出厂
只有 `xiaomi,piano-boe-nt36532`，整份文件没有 `piano-csot` 字样，所以这个值是 UEFI
写进去的。

看似矛盾，实际不矛盾：内核面板驱动内部的两个函数名与 `of_match` 的对应关系是反的。
`piano_csot_init_sequence` 发的是 `bc 77 07`、ESD `0x00`、先 `B2` 后 `B3`，按原厂
命令特征那是 `35_02_0b`（BOE）的序列；`piano_boe_init_sequence` 发的才是
`c4 82 …`、ESD `0x20`（`42_02_0a`）。上游 `linux-sm8750-piano` 的命名正好相反。
所以 `xiaomi,piano-csot-nt36532` 实际加载的是 BOE 的命令流，与绑带读数一致，屏幕
显示正常。

这是命名缺陷，不是行为缺陷，目前不改：改 `of_match` 会让 compatible 与序列对调，
而 UEFI 按 ABL 标识写的值已经是正确的供应商名。要统一命名得连 UEFI 的
`PianoFactoryPanel.h` 一起动，属于跨仓库改动。

原厂触控驱动只把 `lcd_id_value` 用来选
`piano_nova_{csot,boe}_thp_config.ini`，从不据此翻转坐标。`panel_vendor` 因此不能
当作触控坐标的判据，触控方向由实测决定，判据见
`tests/check_finger_orientation.py`。

两个标识分别对应哪块屏、归属哪个供应商，见[上游跟踪的面板标识](upstream.md#两个原厂面板标识)。

## GPI DMA 深度空闲

触控中断挂在 `ac0000.qupv3_1_geni_se` 上，走 GPI DMA。SM8750 在 DMA 运行期间
进入 system low power domain（PSCI `0x0200c354`，DT 里 4400 us 退出延迟），
传输被卡住直到退出：5450 字节的读本该 2.0–2.7 ms，实测 2.46 ms 或恰好 4.85 ms。
上游 `yzddmr6/linux-sm8750-piano` 的 `322fd1bfbda9` 用 ftrace 量过，2945 次慢读
里有 2678 次正好落在 system domain 内；把 CPU 延迟上限压到 2000 us（高于
cluster 状态的 1983 us）之后，每次读都在 2.0–2.7 ms。

补丁作为 0029 进了 7.3 封存序列，`target_tree` 为 `81983254f51a`。
2026-10-26 `kernel-piano-7.3.0-4` 装进真机后量过，两件事都成立：

```
$ grep pm_qos_update_request /sys/kernel/tracing/trace | head -2
 irq/215-NVT-ts--1100  pm_qos_update_request: CPU_DMA_LATENCY value=2000
 irq/215-NVT-ts--1100  pm_qos_update_request: CPU_DMA_LATENCY value=-1
```

每次 SPI 消息都把 CPU 延迟上限压到 2000 us、用完恢复。配对这两条事件得到
4799 次传输的耗时：p50 231 us、p95 2424 us、p99 2452 us、max 2513 us，
**没有一次超过 3.5 ms**，4.85 ms 那簇不再出现。上游记的修复后区间是
2.0–2.7 ms，量到的尾部与之吻合。

这条不能靠「没看到整屏泛白」判定：没有对照内核时，泛白本身也不是每次都会
出现。耗时分布才是判据。

## 系统缓存（LLCC）没有生效

内核编了 `CONFIG_QCOM_LLCC=y`，但驱动从未绑定任何设备，系统缓存因此没有配置，
Adreno 在缺 LLCC slice 的状态下运行。设备上的证据：

```
$ ls /sys/bus/platform/drivers/qcom-llcc/
bind  module  uevent  unbind      # 没有任何设备
$ dmesg | grep -i llcc            # 空
$ ls /sys/bus/platform/devices/ | grep llcc
24095000.llcc-pmu                 # 只有 PMU 和两个 bwmon 子节点
240b3400.qcom,bwmon-llcc-gold
240b7400.qcom,bwmon-llcc-prime
```

原因是 compatible 与 `reg-names` 都对不上。设备树里是 SunUEFI 的
`vendor/piano-linux/board.dtb` 提供的原厂节点：

```
compatible = "qcom,sun-llcc";
reg-names  = "llcc0_base", "llcc1_base", "llcc2_base", "llcc3_base",
             "llcc_broadcast_or_base", "llcc_broadcast_and_base";
```

而 7.3 的 `drivers/soc/qcom/llcc-qcom.c` 只认 `qcom,sm8750-llcc`，并且按名字取
`llcc_broadcast_base`。`qcom_llcc_init_mmio()` 只按序号 ioremap、名字仅用于给
regmap 命名，所以 `reg-names` 的差异本身不致命，致命的是 compatible 不匹配，
probe 根本不会发生。

上游 `yzddmr6/linux-sm8750-piano` 的 `fe98a3e` 记录了同一问题的另一半：它把
`CONFIG_QCOM_LLCC` 从 `=m` 改成 `=y`，理由是内置的 `DRM_MSM=y` 无法满足
`QCOM_LLCC` 的依赖。我们这边已经是 `=y`，但 `DRM_MSM=m`，依赖方向不同。它测到的
收益是 Geekbench 6 单核 +0.9%、多核 +0.5%、Vulkan 计算 +2.7%，内存延迟类子项最高
+9.5%。

2026-10-25 又核了一遍寄存器布局，两边的差别只剩 compatible 一个字符串：

```
本机 cache-controller@24800000        上游 sm8750.dtsi
0x24800000 2 MiB                      0x24800000 2 MiB   llcc0_base
0x25800000 2 MiB                      0x25800000 2 MiB   llcc1_base
0x24c00000 2 MiB                      0x24c00000 2 MiB   llcc2_base
0x25c00000 2 MiB                      0x25c00000 2 MiB   llcc3_base
0x26800000 2 MiB                      0x26800000 2 MiB   broadcast
0x26c00000 2 MiB                      0x26c00000 2 MiB   broadcast_and
```

顺序也一致。`num_banks` 由驱动从 `LLCC_COMMON_STATUS0` 读，不依赖 DTB 声明。所以
给 `qcom_llcc_of_match` 加一条 `{ "qcom,sun-llcc", &sm8750_cfgs }` 就够，cfg 数据
可以照用——`sun` 本来就是 SM8750 的代号。

修复要么改 DTB 的 compatible，要么在内核补丁里加这条 match 项。前者动 ESP 上的
板级输入，后者动 7.3 的封存序列。两者都还没做，因为验证需要刷内核，而收益只有
Geekbench 个位数百分点。

## 刷写与读回

写 `sunuefi_root`（6.1 GB sparse）之后，设备侧的 ABL 会挂死。`lsusb` 仍能
看到 `18d1:d00d`，但 bulk 端点上任何传输都超时，`fastboot getvar` 不回包。
主机侧无法恢复：`USBDEVFS_RESET`、hub 端口 `disable` 重新枚举、`authorized` 翻转都已试过，设备重新枚举后仍然不回包。只能长按电源键断电重来。断电回来后再
写 `boot_a` 只要 2.4 秒，说明挂死只跟大分区写入有关。

一次完整安装按这个顺序执行：先写 `boot_a`，再写 `sunuefi_esp`，最后写 `sunuefi_root`。`sunuefi_root` 写完后直接断电；重新进 Fastboot 时只重启，不再写大分区。
一键脚本已按这个顺序实现。

原厂 ABL 不实现 `fastboot fetch`。主机 fastboot 37.0.0 认得这个子命令，设备
回 `Device does not support fetch command`，宿主上留下一个 0 字节读回文件。
`sunuefi_esp` 分区也比 512 MiB 的镜像大，即使换了支持
`fetch` 的引导器，整份哈希也不会等于镜像摘要。读回只能作辅助证据，刷写正确与否以启动后的 `uname -r`、`findmnt /` 和进入所选档位为准。

`boot_a` 的读回可以直接读分区前 4 KiB 与镜像内的 `SUNUEFI-NEXTv1` 请求页判断：
首块是 `ANDROID!` 包装头，请求页的目标与 generation 和 `device-boot` 清单一致。

## 首次安装的 userdata

`fastboot erase userdata` 只清分区，不写 BCB 的 `wiped` 标志。在
OS3.0.309.0 上两次安装，重启后 Android 都停在 Recovery，底部显示
`Couldn't initialize user 0`。在 Recovery 里选一次“清除数据”才重建加密
userdata。安装脚本目前只做擦除，操作者要在现场执行这一步，步骤见
[安装指南](installation.md#24-让-android-重建-userdata)。

## 首次启动扩容

封存镜像的容量由 `product.json` 的 `root_size_mib` 固定。文件系统在首次启动时
由 initramfs 里的 `resize.f2fs` 扩到 `sunuefi_root` 的实际大小。这条路径的离线部分有测试覆盖：归档里有
`fsck.f2fs` 与 `resize.f2fs` 的符号链接，`blkid -p -o export` 能读出
`FSSIZE`，`resize.f2fs` 对已满的镜像是 no-op。真机扩容验证通过：首启后
`FSSIZE` 与 `blockdev --getsize64` 都等于分区大小，`findmnt /` 看到的是
扩容后的容量。

扩容失败时引导停在 initramfs。查 `ROOT_GROW_STARTED` 之后的日志，能看出是
`fsck`、`resize` 还是复核那一步失败。系统不会带着旧容量启动。

分区被收缩到小于文件系统时，报 `The F2FS root is larger than its partition`。
`resize.f2fs` 不支持缩小，此时只能重刷 `sunuefi_root`。

用 `blkid -p /dev/<分区> -o export` 的 `FSSIZE` 或 `blockdev --getsize64` 核对
容量。两者都应该等于 `fastboot getvar partition-size:sunuefi_root` 的字节数。
`findmnt -bno SIZE /` 报的是 statfs 数据块，比分区小约 2 MiB，那是 F2FS 扣掉保留块后的结果，不能拿它和分区
字节数直接比。

## 双系统切换

双系统切换按上游 SunUEFI 的合体 BOOT 模型工作。`boot_a` 里是原厂 Android 内核、3 秒选择器与 UEFI 核心，请求页在 `boot_a` 镜像末尾。Android 与 Linux 都从 Slot A 启动，不切活动槽，也不改 GPT。

- Linux → Android：`piano-next-boot android --reboot` 把持久请求写成 target=0，
  正常重启后选择器进入 Android。
- Android → Linux：安装「SunUEFI 引导助手」模块（KernelSU/SukiSU），在模块页面
  选 Linux。没有引导助手时，开机前 3 秒按音量键在 3 秒选择器里选 Linux，选择会被保存。
- 两轮真机往返：`boot_android.img` 从请求页 target 0→2→0→2→0、
  sequence 0→4。随后 `flash boot_a device-boot/boot_linux.img`（未签名）
  直接进 Linux（target=2、sequence=1，约 40 秒），helper 回
  Android（target=0、sequence=2，约 45 秒），模块重新采纳后再切 Linux
  （target=2、sequence=3，约 2 分钟），helper 再回 Android（target=0、
  sequence=4，约 35 秒）。`ro.boot.slot_suffix` 始终是 `_a`，`sys.boot_completed=1`。
- 请求页偏移 37490688（0x23C1000），magic `SUNUEFI-NEXTv1`，两页相隔 4096
  字节滚动写入。

helper 不再读任何槽位配置文件。上游 7dbabdc 之后它只读主备 GPT（两份头与条目 CRC
都校验）和高通 A/B 属性（priority48..49、active50、retries51..53、successful54、
unbootable55）定位活动槽，再读那份 BOOT 的实际 generation。本机实测
`boot_a` 属性 `0x0077…`（priority 3、active、successful）、`boot_b` `0x0072…`
（priority 2、非 active），helper 选出的就是 `boot_a`，与旧配置一致。
换槽或 OTA 之后不需要重新配置。

旧的 B 槽布局（Linux 写进 `boot_b`、`set_active b`）不再使用。以下是一条历史排障记录：qbootctl 的 `set_active` 只改活动标志，不交换槽位 type GUID。A 活动
时执行 `qbootctl -s b` 会留下“B 活动 + generic GUID”的表，ABL 的
`ValidateSlotGuids` 会拒绝启动，报 `BootableSlot _b does not have valid guids`。
`fastboot set_active` 走 ABL 的 `SetActiveSlot → SwitchPtnSlots`，会交换 GUID
并切 UFS boot LUN。UFS boot LUN 必须与活动槽一致（1 = A，2 = B）。手工重建 GPT
时主表和备份表要一起写，两份 CRC 都要重算。

仍停在旧 B 槽布局的设备按这条路径迁移：把 `device-boot/boot_android.img` 写进
`boot_a`，再用 `fastboot set_active a` 切回 A（B→A 方向 qbootctl 也安全）。
旧安装上的 `/etc/piano/boot-request.json` 可以直接删掉，新 helper 不读它。

## Android 侧

已装回与当前 BOOT generation 匹配的「SunUEFI 引导助手」模块
（`/data/adb/modules/piano_sunuefi`）。安装走 `adopt` 只读识别现有合体
BOOT，不重写内核。模块页面用它写请求页与重装引导。SukiSU root 仍在
`init_boot_a`：`su -c id` 返回 uid=0、context `u:r:ksu:s0`。

更早的 `piano_sunuefi_module.zip` 只包含模块结构与根目录 request 程序，缺少 manager 要求的 bin/ 原生工具和策略/状态。这个版本不能安装，也不要和已匹配的那份模块混用。

`boot_a` 被重刷成另一份镜像（例如重新跑 `fastboot flash boot_a device-boot/boot_linux.img`）
后，模块的 `install-state.json` 仍指向旧镜像，`switch linux` 会报
`current BOOT header is not the owned wrapper header`。在模块页面执行一次
“重新安装引导”（`sh /data/adb/modules/piano_sunuefi/manager.sh reinstall`），
让只读 `adopt` 重新采纳当前 BOOT 即可，不需要重写内核。

Android 实测运行在 SELinux Permissive，启动参数带 `selinux=permissive`。
手动 `setenforce 1` 可用，但不能当持久策略，固化方式还没落实。

## 充电

固定源码已包含 SC8541/MCA 直充支持，设备参数 cp_charge、hv_charge、
charge_current、mipps_auth、charge_policy 都开启。参数开启不等于实际达到
45 W。
补丁 0036 是这条驱动的历史参考，对应实现已经在固定内核里，不要重复套用。

已有遥测是高电量下的一次采样：电量 94%，MCA USB online、PD_PPS，约
8.60–8.74 V，sysfs 电压乘电流估算输入 12.075–21.429 W。双电池都 Charging，
温度 32.8–35.5 °C 与 31.9–34.4 °C。SC8541 online 始终为 0。低电量直充与
最高 45 W 未证明，也没有外置功率计读数。要记录功率与温度，需要真实充电器、
线材和负载。

## 传感器与自动旋转

传感器走 SSC，不走 IIO。`libssc` 与 `iio-sensor-proxy-ssc` 都已装好，
`qrtr_smd`、`qrtr_mhi` 与 `mhi` 三个模块加载后，代理能拿到设备：
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

- 触控：45 秒持续单指划动，SYN 报文 97 次/秒，43/45 秒有事件（两个空秒
  是换手间隙），NVT 中断同期增长 5425。X 覆盖 5395–28696、Y 覆盖
  4591–19408，全屏可达。多点 slot 0–5 都出现过。相邻坐标跳变的 p95 为 2770，
  来自多指交替时的 slot 切换，不是丢帧。
- 音频：120 秒播放无 xrun/underrun，音量与 sink 状态全程不变。130 秒录音
  每 10 秒分段都有信号，段均值在 -33.6 到 -18.5 dB 之间，没有静音段。

- 相机：前摄与后摄都能正常成像，详见下方"取帧"两段。整机验收还没走完，
  设备整体状态记为未验证。验收项见
  [安装指南的真机验收](installation.md#十-真机验收清单)。

编解码已复验通过。H.264 与 HEVC 各 300 帧的 GOP60 硬件编码与解码，
以及各 300 帧带 B 帧的硬件解码，逐帧摘要与软件解码一致，Iris 中断在每段都有
增长。验收脚本在维护者机器上驱动平板运行，命令见[维护与验收](internals.md#真机取证脚本)。
目标镜像没有 `make` 和 `libx264`/`libx265`，素材在主机上准备。

浏览器硬解已单独取证，三家结果不同：

| 浏览器 | 能否播 H.264 | 是否硬解 |
|---|---|---|
| Fedora Chromium 155 | 装上 `libavcodec-freeworld` 后能 | 否 |
| Google Chrome 156（aarch64） | 能 | 是，默认开启 |
| Firefox 158 | 能 | 是，默认开启 |

Chromium 不走硬解有两个叠加原因。Fedora 的 Chromium 链接系统 `libavcodec`，
而 `ffmpeg-free` 在构建时用 `--disable-decoder` 去掉了软件 H.264，所以原样连 MP4 都打不开，报 `FFmpegDemuxer: open context failed`。补上 RPM Fusion 的
`libavcodec-freeworld` 才能播放。播放之后 Chromium 仍然交给软件解码：GPU 进程没有打开
`/dev/video0`，Iris 中断在整个播放窗口内不增长。Chrome 自带 FFmpeg 与 V4L2
解码路径，Firefox 走自己的 VA-API 路径，两者都直接把 `msm_drv_video.so` 用起来。

验收判定不能只看浏览器自己报的状态：软件解码同样拉高 GPU 中断。脚本同时要求
Iris 中断增长和 `/dev/video0` 有持有者，两者缺一即判为非硬解。

相机另有一条容易误判的路径：`piano-camerad` 在没有上游产帧方时会给
v4l2loopback 写黑帧，所以“节点能取到帧”不等于传感器在出图。判定时查 camerad 日志，确认有对应相机的 CAMSS 媒体设备，并确认帧内容不是全黑。设备上取的两张
样张就分属两种情况：前摄那张是正常灰阶画面，另一张只有
一小块亮区、其余全黑。只统计“两个节点各取到 3 帧”会把后者算成通过。

复测时两颗传感器都走上了 CAMSS 产帧路径。用 `gst-launch` 从两个
loopback 节点各取 30 帧，前摄末帧灰度均值 117（满量程 255），画面是正常
室内成像。后摄末帧均值 24、最大值 113，是低照度噪声，当时设备平放在桌面上。

后来把两颗放在同一时段一起取，前摄末帧 p50=94、p90=180、p99=255，四乘六
分块均值从 20 到 216，空间结构清楚。后摄 p50=30、p90=46，分块均值在 23 到 37
之间，平坦且暗。后者能出图但对着暗处，仍按低照度记录，不算画面失败。

在采光正常的同一时段连取两轮后，两颗都给出了稳定且可重复的空间结构：
后摄 p50 为 78 与 65、p90 为 209 与 180，四乘六分块均值 16.9 到 197.9。
前摄两轮 p50 都是 112、p90 为 193 与 194，分块均值 20.2 到 200.7。
两颗都能正常成像，之前后摄那张暗帧是取帧时对着暗处，不是相机本身的问题。

取帧有个时序问题容易误判：`piano-camerad` 在 loopback 没有读者时会停流，
来一个读者就重新起流，而自动曝光每轮都要重新收敛。刚起流的前几帧是黑的，
灰度会从 1 单调爬到稳定值。用第一帧判断画面会把正常出图判成黑屏，判定时必须用收敛之后的帧。上面前摄与后摄两组数据的第一帧与末帧差距明显：
后摄第一帧 p50=30、同一轮末帧 p50=78，只取开头就会把它误判成暗场。

## 指纹、红外与 HDR

这三项以前被合写成一句"依赖闭源 TEE 固件，主线不支持"。逐项核实后，只有指纹跟 TEE 有关，红外与 HDR 各有其他原因。

### 指纹

侧置电容式，汇顶 Goodix，认证在 TEE 的 `gfpia` TA 里完成。主线没有
`drivers/input/fingerprint/` 这个目录，厂商树里的 `mi_fp` 也只做电源、复位、
中断和 netlink 上报，图像采集与比对全在 TA 里。把 `mi_fp` 移植过来不解决任何实质问题。

主线的 QSEECOM 只白名单 `qcom.tz.uefisecapp` 一个应用，不提供通用 TA 客户端
接口，任何第三方 TA（含 `gfpia`）都无法通过 QSEECOM 启动。`drivers/tee/qcomtee/`
自 6.18 起在主线，但走 object-IPC，与 piano 的 legacy QSEECom HAL 协议不同，
不能替代。

用户态没有 libfprint 后端能驱动 `gfpia`。唯一跑通的 Goodix + QSEE 完整栈针对
的是另一颗 TA（`gfenu`）、另一套传感器，且明确未上游。

### 红外

消费级红外发射器，接在 QUPv3 SE15（SPI `0x89c000`）。它不是结构光、ToF 或
接近传感器，这个设备**只发不收**。"没有 IR 输入设备节点"不能作为它不支持
的判据，正确的检查是 `/dev/lirc0` 存在且能发码。

主线 `drivers/media/rc/ir-spi.c` 完整实现了 CIR 发射，但当前这条链上有四处
缺口叠在一起：

- 设备上 `/dev/lirc*` 与 `/sys/class/rc/` 都不存在。
- 内核配置是 `CONFIG_RC_CORE=m` 但 `# CONFIG_LIRC is not set`，而 `IR_SPI`
  依赖 `LIRC`。
- DT 里没有 SE15 的节点：设备上唯一的 SPI 设备是触控
  （`/soc/qcom,qupv3_1_geni_se@ac0000/spi@a88000/touchscreen@0`），
  `0x89c000` 那段不存在。
- 主线驱动认的 compatible 是 `ir-spi-led`，而厂商 DT 写的是 `ir-spi`。

要让红外出 `/dev/lirc0`，需要补 SE15 控制器节点、修正 compatible 字符串、
打开 `CONFIG_LIRC` 与 `CONFIG_IR_SPI`。原厂的波形是 32 位 SPI 字、1.92 MHz 采样、
40% 占空比，主线驱动是 16 bits/pulse、默认 50%，载波会有偏差。要完全一致，还要小改驱动。

### HDR

面板硬件支持 HDR10，30bpp DSC 传输。Linux 侧没有输出路径：msm 不挂
`HDR_OUTPUT_METADATA` 属性，也没有接 DRM 的 colorop 管线。SM8750 的 DSPP
gamma 块直到 2026 年 9 月才修好。目前唯一的 msm 静态 HDR 系列只覆盖 DP 与
eDP，内屏走 DSI，用不上。

面板驱动也不在主线（`drivers/gpu/drm/panel/` 里没有 nt36532），本仓库的补丁只把 `display_info.bpc` 报对，提交信息自己写明"不声称支持 HDR 或
12-bit 传输"。这一项要等上游 DRM 层的实质推进，本机单方面做不了。

## 内核在线升级

`dnf upgrade` 现在会带上 `kernel-piano`。事务收尾时，`%posttrans` 重建 initramfs 并重打 ESP 上的 `boot.img`。这条路径在平板上跑过一次真实升级
（`7.2.9-1` → `7.2.9-3`）：`%posttrans` 重建了 initramfs，ESP 上的 `Image`、
`initramfs` 与 `boot.img` 都换了，`.bak` 三份都在，重启后 `uname -r` 是新的
内核，`graphical.target` 起来，`systemctl --failed` 为空，`v4l2loopback` 从新
模块树的 `updates/` 加载。

写盘失败时，旧 `boot.img` 保持不动，设备继续用旧内核启动。装好但起不
来的内核没有自动回滚，回滚方式见 `docs/copr-repository.md`。

挂起路径上每次会打 4 条 `qdss already disabled` / `qdss already unprepared`
（`drivers/clk/clk.c` 的 `clk_core_disable` / `clk_core_unprepare`，从
`amba_pm_runtime_suspend` 进来）。两轮挂起都复现，唤醒正常、桌面恢复、
`systemctl --failed` 为空，目前只有日志噪声。来源是 coresight qdss 的时钟在
runtime PM 里被重复关闭，属于上游驱动的问题，不是本仓库的补丁。

`kernel-piano` 是 installonly 包，升级后旧内核的模块树与 `modules.weakdep`、
`updates/` 会留在 `/usr/lib/modules/` 下（rpm 删掉的是 rpm 自己拥有的文件，这些
是 `depmod` 之后生成的）。这些残留文件不影响启动，`dnf` 也不会再引用。要清理只能手工删目录。

manifest 按 NVRA 命名（`/usr/share/piano/kernel-manifest-<version>-<release>.json`）。
固定名字会让两个版本争同一个路径，rpm 直接中止升级事务——这条在设备上撞过
一次，症状是 `file ... conflicts with file from package`。

## 上游修复的可移植边界

2026-10-25 把 `yzddmr6/linux-sm8750-piano` 的 `piano/dev`（`235ab34e2`）逐条对照了
一遍。基线取 `v7.2.6`（`500df175a`），到分支末端共 1011 个非合并提交，按当前 7.3 树
分类：643 条已在本树、29 条可直接应用、339 条被挡。

上一轮只按 `--author=yzddmr6` 过滤，漏掉了上游从主线 cherry-pick 进来的整批提交
（带 `(cherry picked from commit ...)` 的行），那批有 18 条真修复。按作者过滤不能
当判据。

29 条可直接应用的里 18 条已收成封存序列 `0037`–`0054`，另有 `0055`（iris 关电等
线程化 IRQ 退出）来自 339 条里的 `0adb163ceb`——它和已取的 `041e33ce19` 是同一组
修复的两步，只取前一步会留下 `disable_irq_nosync` 后线程化 handler 访问已断电硬件
的窗口。

2026-10-26 上游 `piano/dev` 推进到 `9321086f2e2`（共 10 条新提交，2 条 merge + 8 条主题）：
- UFS MCQ 多队列支持（`c155f0cfe97`、`d21a171ee48`，对应本序列 `0056`、`0057`）
- PMIC5 Gen4 ADC、PMK8550 ADC 与 11 路板级测温传感器（`ff6018d383a`、`5812ec75bb0`、`fa38723bd8c`、`43dd63f2f45`、`898cec7421c`，对应本序列 `0058`–`0062`）
已并入封存序列并重放，`target_tree` 推进到 `783e27278886`。唯一未取的是上游自己的 `bd273459857`（改 `piano-persistent.config`）：本树内核编译由 SunUEFI 的 `piano_defconfig` 与 `piano-platform.config` 管控，其中 `CONFIG_GENERIC_ADC_THERMAL=m` 已内建，ADC 驱动配置由平台层接管。

挡住的 339 条按原因分：

| 条数 | 原因 |
| --- | --- |
| 178 | 上下文冲突。绝大多数是 7.2.10 的 stable 回填，7.3 里已有更新版本；少数是上游自己的改动与 7.3 基线不同 |
| 77 | 改 `arch/arm64/boot/dts/qcom/` 下的内核 DTS。本机 DTB 不来自内核 DTS，见下 |
| 28 | 只改上游自己的 config 片段（`piano-persistent`/`piano-release` 等），我们不消费 |
| 20 | `nt36532e-thp` 重写版驱动。本机用原厂 `nt36xxx.c` 移植，见下 |
| 15 | `xiaomi_mca`/`sc8541` 驱动，本树没有这两个文件 |
| 11 | `Revert "..."`，被 revert 的提交从来不在本树 |
| 9 | `hid-nanosic` 重写版 |
| 1 | 目标文件在本树不存在 |

另外 11 条能干净应用但没取，各有具体原因：

| 提交 | 不取的原因 |
| --- | --- |
| `1e23b49b80`、`48879e6e07`、`1d02527d1d`、`f35c680290` | 上游自己 revert 了；GPU 那两条由 `cbb3f4900b`（已取，`0040`）替代 |
| `ecb9d2588b`、`c78194b27b`、`ec181b09d9` | 改 `sm8750.dtsi`，本机 DTB 不来自内核 DTS |
| `7cfa3bd691` | revert Adreno 830 支持，会删掉我们移植的 288 行 catalog |
| `ac637afc63` | 把功放上限放宽到 231，我们保留 215 |
| `925bbbba6e` | 7.3 的 `rpmhpd_update_level_mapping()` 已全局跳过 retention，无 per-PD 字段 |
| `8c4edb0399` | revert DPU 清理顺序，与我们自己的 `0025` 冲突；上游随后用 `c55958fa0` 重落了同一件事 |

两类结构性原因值得单独记：

**本机设备树不是内核 DTS。** `build.sh` 打包时用
`--dtb vendor/piano-linux/board.dtb`，再加 `config/release.json` 的 15 个
`.dtso`。内核 `arch/arm64/boot/dts/qcom/sm8750-xiaomi-piano.dts` 不参与设备启动。
所以上游所有 `arm64: dts: qcom: sm8750-xiaomi-piano: ...` 提交都搬不过来；要搬
得先换 DTB 来源，那是另一件事。LLCC 的 compatible 不匹配也出自同一处。

**触控是两套驱动。** 上游把触控重写成 `drivers/input/touchscreen/nt36532e-thp/`
（`core.c` 等），本机是原厂 `nt36xxx.c` 的 2696 行移植。逐条核过三条修正在本驱动
里不成立：`11b207f747`（不要求 `actual_length`）——本驱动的 `spi_read_write()`
不读这个字段；`e6c332c614`（容忍一次帧读失败）——本驱动帧读失败只记一条
`CTP_SPI_READ failed` 就返回 `IRQ_HANDLED`，不会停流；`ba725ea02c`（每流只选一次
event buffer 页）——本驱动的页选择在 `nvt_set_page()` 里按需做。要拿这些收益得先
把驱动换成上游那套。

搬进来的部分按子系统收成 6 个 commit，追加在封存序列 `0029` 之后
（`0030`–`0035`），另有 `0036`（GLINK 暂存早期消息）和 `0037`–`0054`（18 条主线
cherry-pick）。有六处没有照抄上游：

- `ktz8866` 的 `DEFAULT_BRIGHTNESS` 保持 1500。上游那条会把开机亮度一起改掉，
  不在这次范围里。同一文件的可读寄存器表、`FLAG` 读后清零、HWEN 上电时序和
  错误上报是本轮自己写的，不是上游原文。
- 功放增益上限保持 215。上游 `ac637afc63` 把它放宽到 231，同时自己注明那不是
  验证过的安全上限。
- `7cfa3bd691` 会删掉我们移植的 A830 支持，整条不要。
- `69421e5de9` 把 fs19xx 的真 bug 修和默认音量改动混在一起，只取前者。
- `925bbbba6e` 在 7.3 是空操作：7.3 的 `rpmhpd_update_level_mapping()` 已经全局
  跳过 `RPMH_REGULATOR_LEVEL_RETENTION`，没有 per-PD 的 `skip_retention_level` 字段。
- `clk_test.c` 保留本树的 `HZ_PER_MHZ` 写法，只取上游新增的孤儿时钟接管测试。
- `8c4edb0399` 与我们的 `0025` 冲突：上游先 revert 了「清理放在停时序之前」，
  又用 `c55958fa0` 重新落地 slave 清理。我们 `0025` 的写法与 `c55958fa0` 等价，
  所以不跟这条 revert。

上游 `piano-persistent.config` 里有一批本机没开的选项，属于真实缺口但都不影响
当前可用性：`CONFIG_PSI`（systemd-oomd 依赖）、`FW_LOADER_COMPRESS_{XZ,ZSTD}`、
`USB_UAS`、`EXFAT_FS`、`WIREGUARD`、`SECURITY_YAMA`、`BPF_LSM`。我们的配置来自
`piano_defconfig` + `linux/configs/*.config`，没有消费上游的片段，所以这些要单独
决定加不加。

搬进来的驱动改动里有两条在本机是**未生效路径**，不能算成修复：

- `media: i2c: dw9768` 的 GT9764 序列按 `giantec,gt9764` 选择。本机 `board.dtb`
  的对焦执行器是 `giantec,gt9769`，上游自己的
  `sm8750-xiaomi-piano-camera.dtsi` 里那个节点也是 `status = "disabled"`。代码编进
  去了，但没有设备会匹配它。
- `usb: typec: mux: fsa4480` 的 DIO4485 支持按 `dioo,dio4485` 选择。本机
  `board.dtb` 的 `fsa4480@42` 是原厂 compatible `qcom,fsa4480-i2c`，驱动的
  `of_match` 不认，所以这个节点根本没绑定过。

要让这两条真正生效，得先改板级 DTB 的 compatible，属于设备树来源的问题，和上面
那 63 条 DTS 提交是同一个前提。

## 仓库整理

- `README.md` 与 `README.en.md` 的骨架由 `tests/check_readme_parity.py` 锁定：
  标题层级数量、语言切换行、指向的文档列表必须一致。改其中一版时，同步改另一版。
- `docs/public-release-gaps.md` 的电极校准一项已决定：数据早已在公开仓库与公开 COPR 里，`NOTICE` 写明来源，发布不再因此阻挡。`release.yml` 仍默认只建 draft，转公开要显式传 `publish=true`。
- 三个 UEFI 补丁的 `series.json` 里 `note` 引用的文档分两类。`docs/status.md`、
  `docs/ufs-dedicated-area-audit.md` 与 `docs/piano-full-ddr-producer-gaps.md`
  在上游 SunUEFI 仓库，按 `sources.lock.json` 的 pin 取。还有一份设备验收记录
  已移到不入库的本地笔记，这条引用是历史记录。改 `series.json` 会改变
  `project_source_sha256`，现有 bundle 的 UEFI 来源校验会失效，因此本轮不改它。
