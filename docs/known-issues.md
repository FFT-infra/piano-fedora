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
写 `boot_b` 只要 2.4 秒，说明挂死只跟大分区写入有关。

因此一次完整安装要按这个顺序做：先写 `boot_b`，再写 `sunuefi_esp`，最后写
`sunuefi_root`；root 写完直接断电，重新进 Fastboot 只做槽位切换或重启，不再
写大分区。一键脚本已经是这个顺序。

原厂 ABL 不实现 `fastboot fetch`。主机 fastboot 37.0.0 认得这个子命令，设备
回 `Device does not support fetch command`，宿主上留下一个 0 字节读回文件。
`sunuefi_esp` 分区也比镜像大（537899008 对 536870912 字节），即使换了支持
`fetch` 的引导器，整份哈希也不会等于镜像摘要。读回只能作辅助证据，刷写正确
与否最终以启动后的 `uname -r`、`findmnt /` 和 GNOME 桌面为准。

`boot_b` 的读回可以直接读分区前 4 KiB 与末尾 64 字节判断，
`prepare-device-boot.py` 写的 `os_version`、AVB 算法和请求页偏移都能对上。

## 首次安装的 userdata

`fastboot erase userdata` 只清分区，不写 BCB 的 `wiped` 标志。2026-10-21 在
OS3.0.309.0 上两次安装，重启后 Android 都停在 Recovery，底部显示
`Couldn't initialize user 0`；在 Recovery 里选一次“清除数据”才重建加密
userdata。安装脚本目前只做擦除，这一步需要操作者现场处理，步骤见
[安装指南](installation.md#4-让-android-重建-userdata)。

## 首次启动扩容

封存镜像固定 8192 MiB，文件系统在首次启动时由 initramfs 里的 `resize.f2fs`
扩到 `sunuefi_root` 的实际大小。这条路径的离线部分有测试覆盖：归档里有
`fsck.f2fs` 与 `resize.f2fs` 的符号链接，`blkid -p -o export` 能读出
`FSSIZE`，`resize.f2fs` 对已满的镜像是 no-op。2026-10-21 在 1 TB 设备上
跑过一次真实扩容：分区 684488392704 字节，首启后 `FSSIZE` 与
`blockdev --getsize64` 都等于这个值。

扩容失败时引导停在 initramfs，`ROOT_GROW_STARTED` 之后的日志里能看到是
`fsck`、`resize` 还是复核那一步失败，不会带着旧容量进系统。分区被收缩到
小于文件系统时报 `The F2FS root is larger than its partition`，`resize.f2fs`
不支持缩小，只能重刷 root。

核对容量用 `blkid -p /dev/<分区> -o export` 的 `FSSIZE` 或
`blockdev --getsize64`，两者都应该等于 `fastboot getvar
partition-size:sunuefi_root` 的字节数。`findmnt -bno SIZE /` 报的是 statfs
数据块，比分区小 2 MiB（684486295552 对 684488392704），那是 F2FS 扣掉
保留块后的结果，不能拿它和分区字节数直接比。

## 双系统切换

Linux → Android 已通过真机验收（2026-10-21，OS3.0.309.0 + 7.2.9-piano-gnome）。
`piano-next-boot android --reboot` 把活动槽切到 A，ABL 直接加载原厂 `boot_a`，
Android 16 启动完成（`ro.boot.slot_suffix=_a`、`sys.boot_completed=1`），SukiSU
root 可用。连续两次往返都通过。

Android → Linux 还缺一条不依赖电脑的路径。现在要从主机执行：

```sh
adb reboot bootloader
fastboot set_active b
fastboot reboot
```

根因在 qbootctl：`boot_ctl_set_active_slot_for_partitions` 只改活动标志，不交换
槽位的 type GUID。A 活动时执行 `qbootctl -s b` 会留下“B 活动、GUID 仍是
generic”的表。ABL 的 `ValidateSlotGuids` 比对活动槽 `boot` 与 `abl_<槽>` 的
type GUID，相等就报 `BootableSlot _b does not have valid guids`，然后掉进
Fastboot。`qbootctl -s a` 在 B 活动时是安全的：A 拿到当前活动槽的 specific
GUID，B 回到 generic，UFS boot LUN 同时切到 A。

`fastboot set_active` 走 ABL 自己的 `SetActiveSlot → SwitchPtnSlots`，会交换
GUID 并切换 UFS boot LUN，两个方向都正确。目标槽已经是活动槽时只修 LUN，
不交换 GUID。

UFS boot LUN 必须与活动槽一致（1 = A，2 = B）。`ValidateSlotGuids` 同时检查
这一项，不一致时报 `Boot lun: %x and BootableSlot: %s do not match`。

请求页实测：`boot_b` 的 NEXT 页在偏移 `37490688`（0x23C1000），magic
`SUNUEFI-NEXTv1`，两页相隔 4096 字节滚动写入。`BOOT_a` 没有 SunUEFI 布局。
`piano-next-boot linux` 只读确认活动槽是 B 再写请求页；活动槽不是 B 时拒绝，
不会调用 `qbootctl -s b`。写入后重启（sequence 1 → 2）Linux 正常启动。

`piano-next-boot android` 不带 `--reboot` 时只切槽不重启。切到 A 之后 Linux
侧没有安全的切回方式，只能用 fastboot；图形入口始终带 `--reboot`。

原厂 Recovery 往返这次没有执行：退出 Recovery 需要现场按键，远程做会把设备
留在 Recovery。

## Android 侧

2026-10-21 的 r4 重新分区擦掉了 userdata，`/data/adb/modules/` 不存在，
`piano-matching-core` 匹配模块没有装回。SukiSU root 还在：它打在 `init_boot_a`
里，`su -c id` 返回 uid=0、context `u:r:ksu:s0`。要恢复模块，重装 APK 并重新
授权 su，再刷模块 zip；模块的 `install-state.json` 与当前 BOOT generation 的
历史不一致要在那时一起复核。

更早的脚手架 `piano_sunuefi_module.zip` 只有模块脚手架及根目录 request 程序，缺少 manager 要求的 bin/ 原生工具和策略/状态。它不能安装，也不要和上面的匹配模块混用。

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

## 手写笔与输入

用户操作手写笔期间，libinput 捕获 1906 条轴事件、22 条 proximity、18 条 tip
事件，压感 0–1，位置及倾斜变化正常。未捕获 TABLET_TOOL_BUTTON。蓝牙键盘/鼠标
节点上的按键、捏握、防误触、绘图应用效果、充电与重连仍需分别验证。

## 虚拟化

正式内核已通过 RM 实时交接和单 vCPU VM_START；virtio-blk 上 64 MiB ext4 写入
与读回通过，virtio-net 连通（`NET_OK=1 NET_RX=2`），三轮 disk+net 与一轮有界
失败退出通过，进程、PID 与 socket 清理干净。GUI 实际创建和操作客体仍需人手在
场确认，完整 SELinux 流程待测。SMP、UEFI、图形和 Incus 需要分别实现与验收，
不由单 vCPU 结果推定。门槛见[虚拟化](virtualization.md)。

## 触屏、网络、音频与相机

这些模块已有实机行为记录，持续负载下的表现还要单独取证：负载下的持续触控
输入、浏览器实际硬解、音频长时间录放、前摄与后摄在正常光线下取帧。全部项目
验收前，设备整体状态记为未验证。验收项见[安装指南的真机验收](installation.md#真机验收清单)。

编解码本身已复验通过（2026-10-20）。`make test-device-media` 跑完四项：
H.264 与 HEVC 各 300 帧的 GOP60 硬件编码与解码，以及各 300 帧带 B 帧的硬件
解码，逐帧摘要与软件解码一致，Iris 中断在每段都有增长。两项前置依赖在目标
镜像上不满足，脚本已相应放宽或改用外部素材，细节留在验收记录。浏览器里
是否走向硬件解码仍要单独看。

相机另有一条容易误判的路径：`piano-camerad` 在没有上游产帧方时会给
v4l2loopback 写黑帧，所以“节点能取到帧”不等于传感器在出图。判定要看 camerad
日志里有没有对应相机的 CAMSS 媒体设备，以及帧内容是不是全黑。2026-10-17
在设备上取的两张样张就分属两种情况：前摄那张是正常灰阶画面，另一张只有
一小块亮区、其余全黑。只统计“两个节点各取到 3 帧”会把后者算成通过。

2026-10-20 复测时两颗传感器都走上了 CAMSS 产帧路径。用 `gst-launch` 从两个
loopback 节点各取 30 帧，前摄末帧灰度均值 117（满量程 255），画面是正常
室内成像；后摄末帧均值 24、最大值 113，是低照度噪声，当时设备平放在桌面上。
后摄要举起来对着亮处再看一次，才算画面验收。

## 仓库整理

- `README.md` 与 `README.en.md` 的骨架由 `tests/check_readme_parity.py` 锁定：
  标题层级数量、语言切换行、指向的文档列表必须一致。改一版要同步另一版。
- `docs/public-release-gaps.md` 的电极校准一项仍待许可决定。在此之前不要把
  带校准表的 bundle 挂成公开 Release。
- 三个 UEFI 补丁的 `series.json` 里 `note` 引用的文档分两类。`docs/status.md`、
  `docs/ufs-dedicated-area-audit.md` 与 `docs/piano-full-ddr-producer-gaps.md`
  在上游 SunUEFI 仓库，按 `sources.lock.json` 的 pin 取。`docs/device-acceptance-20261015.md`
  已移到不入库的本地笔记，这条引用是历史记录。改 `series.json` 会改变
  `project_source_sha256`，现有 bundle 的 UEFI 来源校验会失效，所以先不动。
