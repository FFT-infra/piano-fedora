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

原厂 ABL 不实现 `fastboot fetch`。主机 fastboot 37.0.0 认得这个子命令，设备
回 `Device does not support fetch command`，宿主上留下一个 0 字节读回文件。
`sunuefi_esp` 分区也比镜像大（537899008 对 536870912 字节），即使换了支持
`fetch` 的引导器，整份哈希也不会等于镜像摘要。读回只能作辅助证据，刷写正确
与否最终以启动后的 `uname -r`、`findmnt /` 和 GNOME 桌面为准。

## 双系统切换

Linux → Android 的自动切换尚未通过验收。已定位两个独立原因：

1. `/etc/piano/boot-request.json` 的 `app_generation` 曾过期
   （`6c38318a290175eba5ff2bb5ef106768`），与当前 BOOT 的
   `2b191f0dd3bca52d20950ec58199f95b` 不符；`piano-next-boot` 会拒绝写入。
   已用 `piano-next-boot-configure --device /dev/disk/by-partlabel/boot_b`
   从在线 BOOT 重建。
2. 产品固件带开机选择画面。`bootselect/BootSelect.c` 在 aarch64 且定义
   `PIANO_EARLY_CHOOSER` 时进入 `PianoEarlyChoose(Fdt, Target)`；按键会覆盖
   请求页，不按才在 3 秒后采用请求页里的 DefaultTarget。排查“写入成功但没切
   过去”时必须先排除这个 3 秒窗口。

请求页布局已实测：`boot_b` 的 NEXT 页在偏移 `37621760`（0x23E1000），两页
相隔 4096 字节，滚动写入。`BOOT_a` 没有 SunUEFI 布局。当前请求保持在
target=2（Linux）；无人现场时不要留下 target=0。

完整的 Android→Linux→Android→Linux 往返仍需真机重启，时机由操作者定。
这条自动往返尚未验收。

## Android 侧

Android 侧模块已安装，`install-state.json` 为 `ADOPTED_READ_ONLY_VERIFIED`、
`boot_written=false`，`app_sha256=6c38318a…`，与当前 BOOT 的 generation 不同。
这份历史不一致需要随模块一起复核。

Android 实测运行在 SELinux Permissive，启动参数带 `selinux=permissive`。
手动 `setenforce 1` 可用，但不能当持久策略，固化方式还没落实。

`piano_sunuefi_module.zip` 只有模块脚手架及根目录 request 程序，缺少 manager
要求的 bin/ 原生工具和策略/状态，不能安装使用。

## 充电

固定源码已包含 SC8541/MCA 直充支持，设备参数 cp_charge、hv_charge、
charge_current、mipps_auth、charge_policy 均开启。这不等于实际达到 45 W。
0036 不应重复套用到已有实现。

已有遥测是高电量下的一次采样：电量 94%，MCA USB online、PD_PPS，约
8.60–8.74 V，sysfs 电压乘电流估算输入 12.075–21.429 W；双电池均 Charging，
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
输入、浏览器实际硬解、音频录放、前摄与后摄分别取帧。全部项目验收前，设备
整体状态记为未验证。验收项见[安装指南的真机验收](installation.md#真机验收)。

相机另有一条容易误判的路径：`piano-camerad` 在没有上游产帧方时会给
v4l2loopback 写黑帧，所以"节点能取到帧"不等于传感器在出图。判定要看 camerad
日志里有没有该路相机的 CAMSS 媒体设备，以及帧内容是不是全黑。2026-10-17
在设备上取的两张样张就分属两种情况：前摄那张是正常灰阶画面，另一张只有
一小块亮区、其余全黑。只统计"两个节点各取到 3 帧"会把后者算成通过。

## 仓库整理

- `README.md` 与 `README.en.md` 的骨架由 `tests/check_readme_parity.py` 锁定：
  标题层级数量、语言切换行、指向的文档列表必须一致。改一版要同步另一版。
- `docs/public-release-gaps.md` 的电极校准一项仍待许可决定。在此之前不要把
  带校准表的 bundle 挂成公开 Release。
