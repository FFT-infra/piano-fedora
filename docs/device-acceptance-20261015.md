# 2026-10-15 接手验收

Fedora 44/GNOME 的 F2FS 系统已在本机启动。SunUEFI 固定输入为
`50f29a92a9f43a96e0e8aaf28e0fdbd5a7c64c73`；源码准备、二进制构建、真机行为分开记录。
以下不构成全部硬件、充电功率、Android 往返或 VM 已完成的声明。

## 已完成的基线更新

从 `7.2.9-piano-gnome-g184e09d96775` 增量更新至
`7.2.9-piano-gnome-g07c1f92db62b`，重启后 `uname -r` 确认生效。
源码 commit 为 `07c1f92db62bcf247e8d26cc63bd5045a9a26b94`，tree 为
`fb842b86ffb1bae70f6a59d28dc4886b7523d35e`；1639 个封存模块全部核对。

根挂载为 `/dev/sda35 f2fs`，ESP 为 `/dev/sda36 vfat`，SELinux Enforcing。
GDM、相机、传感器与 sensorspd 服务 active；两个虚拟相机节点各取得 3 帧。
这些检查证明启动与基础采集，不覆盖持续视频通话或交互式桌面体验。

更新只替换 ESP 中的 Image/initramfs/boot.img，板级 DTB 字节保持一致；
持久 BOOT 分区摘要在更新前后相同。替换前保存 ESP 全部文件，旧内核目录保留。
因同名 runtime RPM 更新会删除旧包拥有的 v4l2loopback，先恢复旧模块并执行
`depmod`，使旧内核仍具备相机回退条件。

第一轮 ESP 备份 `rollback-esp.tar.gz` 的 SHA256 为
`085edad796952e11894450bd51686cb1600860eca91b70774c9c0927ea71066a`。
备份保存在设备和主机的本轮工作目录；恢复时应把 Image、initramfs 和 boot.img
作为同一套回退，并保留对应 release 的模块。

## 已修复的用户态问题

离线 RTC 时间下限回到已有 boot.log 日归档的同一天，logrotate 会因同名目标
存在而失败。已保留旧日志，将 bootlog 后缀改为 `-%Y%m%d-%H%M%S`，并纳入
rootfs finalize。校正当前时间、保存时钟下限后，`systemctl start logrotate`
通过，`systemctl --failed --no-legend` 为空。回归测试先复现原配置冲突，再验证
新配置保留旧归档及当前日志。

原 `libssc-wait-for-qmi.patch` 在 QRTR 异步回调里睡眠，阻塞服务公告处理，
且没有进入构建链。现在先立即查找，缺服务时在 GTask 所属 GLib context 上
每 100 ms 重试，最多 5 秒，支持取消；构建入口在上游传感器补丁后消费它。
原补丁实测阻塞约 5.02 秒，新测试覆盖立即就绪、晚公告、超时和取消。

真实固定 libssc 0.4.4 源码已在 Fedora 原生编译为 RPM 并装到设备，旧文件先行
归档。新库 SHA256 为
`08dd2b61a0a1e7c3370732b0744c5eb9f48332f1f317400b8fe64962632371fc`。
重启 iio-sensor-proxy 后，root 下 `timeout 12 monitor-sensor` 显示加速度计、
环境光、接近和罗盘；实测出现 face-up、8–11 lux 和持续罗盘变化。
这次服务重启没有人为延迟 QRTR 公告，开机竞争的覆盖由 GLib 回归测试提供。

## 视频故障与补丁消费

原先的短视频和 `vainfo` 记录不足以证明完整可用。基线 `07c1f92` 的 1080p
H.264 VA-API 编码在默认 GOP120 的第 119 帧失败；设 GOP60 后在第 59 帧复现。
调试日志给出 `encoder control id=0x9909e5 value=0` 后 `EINVAL`，该控制即
`V4L2_CID_MPEG_VIDEO_FORCE_KEY_FRAME`，当前 Iris 源码没有注册。

[Qualcomm 驱动协议实现](https://github.com/Pzqqt/android_kernel_xiaomi_marble/blob/bd6c3516f397ded311f7b7b787a52845a2d58fa0/techpack/video/driver/vidc/src/msm_vidc_control.c)
使用输入端口的 `HFI_PROP_REQUEST_SYNC_FRAME`（0x03000145），按 SPS/PPS 配置
发送两种 enum。项目 0037 按此实现 gen2 控制项，只在动态请求时发送，配置阶段
跳过这一单次请求，并把固件提交错误返回给调用方。

原 0035 解码顺序补丁曾只归档。现在 [kernel-iris 系列](../patches/kernel-iris/README.md)
将 0035、0037 加入真实准备入口、摘要门槛、应用顺序与最终 tree 校验。
`check_iris_source.py` 对原未消费入口失败、对修复入口通过，并确认篡改任一
补丁字节会被生产摘要检查拒绝。

第一次完整编译暴露出 0035 自身缺 `iris_ctrls.h` 声明，`iris_hfi_gen2.c`
四处引用 `iris_set_deblock_gen2` 时失败。补上声明后派生 tree 由
`5250b4e18b6dbce9092e0654bf5a21aab60cdf45` 变为
`8c03f6b8274db0e05bece2ce404c39e6d843d0fe`，完整编译与封存通过。
新内核 release 为 `7.2.9-piano-gnome-gf87664b9c63e`，源码 commit
`f87664b9c63e4a782e1ceecfb4a5509cb174adfb`，Image SHA256
`7ca601dd043b1b9e1a9621ac8d40af393ab99fbe33b4bee682829807cf9c651e`，
1639 个模块，config 摘要仍为
`a921d51b013f584411ac605cd88dd0ca43201ee864d4655caa597766d6c711d4`。
配套 v4l2loopback 用同一 config/SYMVERS 与 clang 19.1.7 构建，SHA256
`2d40e1f359f39a4d672ab7fc10ef285da7d3a5f9787a5581e37c08b75dcb69ec`，
vermagic 匹配新 release。

增量部署只替换 ESP 的 Image/initramfs/boot.img 并加装新 release 模块，
board.dtb 不变、持久 BOOT 摘要不变，旧内核与旧模块保留。部署脚本在
替换前复制整个 ESP 目录并生成 `rollback-esp.tar.gz`（SHA256
`e1bf6140277e9052ae5ff168d871c547961dd127072aa3f13f4b37c08ca39f5f`）；
替换后逐文件读回校验，失败时自动还原。重启后 `uname -r` 确认为新 release。

长视频回归在真机通过。验收入口：

```sh
make test-device-media MEDIA_OUTPUT=/home/piano/新的证据目录
```

脚本检查 H.264/HEVC 各 300 帧、多 GOP 的关键帧位置，另用真实 B 帧视频
逐帧比较软件与硬件解码的 NV12 摘要，记录每次 Iris 中断差值。失败时保留
`result.json`、失败阶段、命令及完整日志。基线已通过此入口复现失败。

设备结果（证据目录 `/home/piano/piano-takeover-20261015-01/media-green4`）：
H.264 与 HEVC 的 GOP60 编码各 300 帧，关键帧位置均为
`[0,60,120,180,240]`，编码中断增量 581/564；H.264 与 HEVC 的 B 帧素材
分别含 155/224 个 B 帧，四条硬解路径各 300 帧、逐帧摘要与软件解码完全一致，
解码中断增量 613/540/899/899。`result.json` 的 `passed` 为 true。

`verify-device-media.py` 修正两处测试自身缺陷：设备 FFmpeg 的硬件解码由
`h264_v4l2m2m`/`hevc_v4l2m2m` 承担，不存在 `hwdownload` 滤镜链，须显式指定
解码器；设备 FFmpeg 未编入 libx264/libx265 且 ffprobe 无法识别 `pict_type`，
故 B 帧素材在主机生成并传入 `--fixture-dir/--fixture-types`。

## 正式 Gunyah 内核启动

当前运行 `7.2.9-piano-gnome-gaf0eac10c933`，正式构建身份见
[虚拟化记录](virtualization.md)。1639 个内核模块与匹配 v4l2loopback 已安装，
重启后 uname 确认新 release；SELinux Enforcing，GDM、tablet-mode 与
librevm-daemon active，无 failed unit。`/dev/gunyah` 为 root:librevm。
此次 ESP 回滚包 SHA256 为
`27891ec7da3992318ee9909f884a306c6b36837aa7670883a8e923d3f92cf1d0`；
board.dtb 与持久 BOOT 摘要不变，旧 Iris 内核及模块保留。

四个原生运行时 RPM 已安装：libva-v4l2-iris、piano-crosvm、librevm、
piano-session-fixes。安装前备份 SHA256 为
`429456626823ecc4245df8f2321299d9724f08f742585decc554193c9b86c1ba`。
关键路径已核对 RPM 归属，六个未受包管理的旧 LibreVM 可执行文件和宽松
systemd override 已清理。SDK 已导出并保存派生源码证明，SDK 压缩包 SHA256
`d3ee53e9871113434740d25a0b8355a19d95d0c8ae25aa8697062197a90eda24`。

修复前，默认配置的单 vCPU crosvm 验收未能启动客体。进程阻塞在
`gunyah_rm_call+0x164`；对本轮 vmlinux 反汇编确认它是
`gunyah_rm_msgq_send` 的 send_ready 等待，尚未发送 alloc_vmid。
开机首次 get_log 请求返回 -EINVAL 后，发送许可未恢复。一次性诊断模块
读取当前 DT capability，直接 recv 返回原始错误 1，info-area 查询返回 -1；
诊断模块已卸载。原继承 FDT 的地址仍可读，但内容已经被后续启动覆盖，
不能从这块内存恢复本次启动的 RM 值。历史原厂抓取与静态 DT 的 capability
不同，只能作为根因线索，不能代替同次启动的对照。随后重启清除了 D 状态进程。
这条阻塞记录属于实时 capability 修复和重建 librevm RPM 之前；最终结果见下文。

### RM capability 实时交接

根因已定位到交接值而不是 Gunyah 本身：payload DTB 里
`/hypervisor/qcom,resource-manager-rpc@eb3679e7eae8c38d` 的 `reg` 是构建镜像
那一刻抓到的 TX/RX capability `eb3679e7eae8c38deb3679e7eae845e4`；ABL 每次
启动重新分配，并把当次的值放进 inherited FDT（capture-01
`2a6d3945928aadeb2a6d3945928a353f`，capture-02
`0dc5ec1f8ef953ad0dc5ec1f8ef9634e`）。驱动只按 `compatible` 匹配节点、再按
两个 u64 读 `reg`，所以过期值的后果不是「探不到设备」，而是第一次 RM 请求
带着旧 id 调 HVC、返回 ARG_INVAL(1)，`gunyah_rm_msgq_send` 等的发送许可
永不返回，调用者停在 D 状态，VM_START 永远起不来。

修复走正式路径：[patches/uefi-live-rm](../patches/uefi-live-rm/series.json) 在
raw 交接把 DTB 交给内核之前，从产品已校验的 inherited FDT 取该节点的 16 字节
`reg` 写进目标 DTB。节点名里的 unit-address 保持构建期那对 id——Linux 侧不看
它——改名要动结构块字符串，风险大于收益，故不动。

主机侧验证把**要发货的那段 C 原文**抽出来，配真实 libfdt 编译，并让同一份
输入在两个独立环境各跑一遍：本机 `build/dtc-1.8.1-20261015` 构建的 libfdt
1.8.1，以及 Bridge 宿主系统 libfdt 1.8.1。输入是正式 payload boot.img
（SHA256 `eed50063336b82b87b737b2cf08d0a431d98b0afd93ee8cd80d095882825dff6`）
内嵌的 board DTB（`c020a4edd4a555c00e358a1ba7a945caea7acb295eb08769aa0e8f881f111fef`）
与 capture-02 的 inherited FDT（`8f35cc1e1a9f378a02ceeb2e8179717a69610cff60efaafc335d9afe59184dc7`，
设备 CRC 与两次上传摘要一致）。结果：RM `reg` 由
`eb3679e7eae8c38d/eb3679e7eae845e4` 变为 `0dc5ec1f8ef953ad/0dc5ec1f8ef9634e`，
节点名不变；相对 payload DTB 只有四处属性变化，除 RM 的 `reg` 外都是
`uefi-live-memory` 已交付的内存项（`/memory/reg`、`/memory/ddr_size`、
`/ddr-regions/region2`）；FDT 头有效，输出摘要
`3f4bf806306ea3d70b6c8ae277b8d8026882fb9cdff5ed23c17b55e010a63fc9`
在两个环境逐字节相同。同一份 C 还跑了九条策略用例（正常采纳、继承缺失、
继承含歧义、继承 8 字节、rx 为 0、坏魔数、目标缺失、目标含歧义、目标 8 字节）
全部通过；把采纳长度改成 8、去掉非零校验、放过歧义节点这三种改动都会被
同一组用例判失败。证据目录
`out/gunyah-live-rm-20261015/host-check/`（生成器、两份二进制、`run-real.log`、
`cases.log`、输出 DTB），Bridge 侧同源运行记录为 `~/piano-rm-check-20261015/`。

### 正式启动路径上的 VM 验收

随后重新构建并安装 LibreVM RPM，RPM 文件校验通过；daemon 重启后为 active、
D-Bus 名称可见，`librevm info` 识别到 Gunyah。core/CLI 共 57 项测试通过，
完整 release workspace 构建成功；CLI、daemon、GUI 的已安装二进制摘要均与
本轮构建产物一致。

通过 LibreVM `Auto` 选择 Gunyah，VM 配置中的 `disable_sandbox` 为 false，
实际命令按 Gunyah 后端要求带 `--disable-sandbox`。客体使用项目 guest kernel
`/home/piano/guest-kernel/src/arch/arm64/boot/Image`（SHA256
`090416a6a0ea5ede8d0810fb9171b4dec31087a98d94f3bc795d012a19628c9d`，
`CONFIG_DMA_RESTRICTED_POOL=y`），完成 64 MiB ext4 写入及读回，VM 正常退出、
crosvm 返回码为 0，进程、PID 文件和 socket 均清理。此次首次证明正式内核的
RM 实时交接、VM_START 与 virtio-blk 数据通路可用；virtio-net、多轮生命周期和
GUI 实际创建客体仍需单独验收。设备日志和结果已归档在
`/home/piano/production-project/build/extras-librevm-gunyah-20261015/device-acceptance/`。

同一批工作的早期一次性尝试是用离线重打包把当次 capability 写进 payload
（boot SHA256 `73222cf2cea28ddd76664f84f052b842156bb835ffe8c624f1efe3eb762698ab`），
那次 RAM 启动停在黑屏、没有可用控制台，只能算诊断，不能算修复证明：
它把某一轮的值冻结进镜像，下一轮启动又会过期。真机结论见下文。

## FT 网络与充电、手写笔

新正式内核重启后，沿用原 FT 配置连接成功。连续三次断开重连均成功，耗时
4.20、2.95、1.83 秒，每次 HTTPS 返回 200；FT Wi-Fi SSH 备用通道通过。
旧会话在 WPA 握手处失败的具体根因尚未确认，不能声称已有独立 Wi-Fi 补丁。
FJUT 自动连接已禁用，临时 WPA2 配置、监控接口、代理及 SELinux 端口规则
均已清理。证据为 `production-project/wifi-ft-repeat.json`。

用户接入原装快充与匹配线材后，通过 Wi-Fi 采样 16 次、跨度 75 秒。电量
保持 94%，MCA USB online、PD_PPS，约 8.60–8.74 V；sysfs 电压乘电流估算
输入为 12.075–21.429 W。双电池均 Charging，温度分别 32.8–35.5°C 与
31.9–34.4°C；SC8541 online 始终为 0。这是高电量下的充电遥测，尚未证明
低电量直充或最高 45 W，也不是外置功率计读数。证据为
`production-project/charge-acceptance-01.json`。

用户操作手写笔期间，libinput 捕获 1906 条轴事件、22 条 proximity、18 条
tip 事件，压感 0–1，位置及倾斜变化正常。未捕获 TABLET_TOOL_BUTTON；
蓝牙键盘/鼠标节点上的按键、捏握、防误触、绘图应用效果、充电与重连仍需
分别验证。证据为 `production-project/pen-events-01.log`。

## 仍未验收

固定源码已包含 SC8541/MCA 直充支持，设备参数 cp_charge、hv_charge、
charge_current、mipps_auth、charge_policy 均开启；不能因此声称实际达到 45W。
0036 不应重复套用到已有实现。需使用真实充电器、线材和负载记录功率与温度。

当前标准内核已通过 RM 实时交接和一次单 vCPU VM_START；客体完成 ext4 写入、
读回并正常退出。virtio-net 已打通（`NET_OK=1 NET_RX=2`），三轮 disk+net
与一轮有界失败退出全部通过，crosvm/hub/PID/socket 清理干净（见
[虚拟化验收门槛](virtualization.md)）。GUI 实际操作仍需人手在场确认。

Linux 启动请求配置与桌面入口在位。Claude 留下的 `piano_sunuefi_module.zip`
只有模块脚手架及根目录 request 程序，缺少 manager 要求的 bin/ 原生工具和
策略/状态，不能安装使用。

## 2026-10-16 补充：切换根因、suspend、关屏噪声与 uperf

### Linux → Android 失败的根因

原先记录“helper 写入 target=0/sequence=3 读回成功、重启仍进 Linux”。本轮
实证有两个独立原因，其中一个已把状态改坏：

1. **`/etc/piano/boot-request.json` 的 `app_generation` 是过期值**
   （`6c38318a290175eba5ff2bb5ef106768`），而当前 BOOT 实际是
   `2b191f0dd3bca52d20950ec58199f95b`。generational 校验不匹配时
   `piano-next-boot` 会拒绝操作，请求永远不会被写入。已用标准入口
   `piano-next-boot-configure --device /dev/disk/by-partlabel/boot_b`
   从在线 BOOT 重建，读回一致。修复前的失败记录因此不能只归因于 selector。
2. **产品固件带开机选择画面。** `sources/Project-SunUEFI` 的
   `bootselect/BootSelect.c` 在 aarch64 且定义 `PIANO_EARLY_CHOOSER` 时进入
   `PianoEarlyChoose(Fdt, Target)`，而 `bootselect/Makefile` 确实定义了该宏。
   `EarlySplash.c` 的实现是：音量上键 → Android，音量下键 → UEFI，不按则
   3 秒后采用请求页里的 DefaultTarget。也就是说按键会覆盖请求页决定，不按
   才会遵守它。排查“写入成功但没切过去”时必须先排除这个 3 秒窗口。

请求页布局已实测确认：`boot_b` 的 NEXT 页在偏移 `37621760`（0x23E1000），
两页相隔 4096 字节，滚动写入（page0 seq=3 target=2、page1 seq=2 target=0）。
native helper `piano-boot-request.c` 的边界校验 `page + 2*PAGE == selector`
正好命中该偏移；`BOOT_a` 没有 SunUEFI 布局。

当前请求保持在 **target=2（Linux）**，这是刻意恢复的状态；在没有人在现场
的情况下不得留下 target=0，否则下一次重启会直接进 Android。完整
Android→Linux→Android→Linux 往返需要真机重启，必须由操作者决定时机。

Android 侧模块已安装，`install-state.json` 为 `ADOPTED_READ_ONLY_VERIFIED`、
`boot_written=false`，`app_sha256=6c38318a…`（即上文的旧 generation，与当前
BOOT 不同——这份历史不一致需要随模块一起复核）。实测 Android 运行在
SELinux **Permissive**（启动参数带 `selinux=permissive`），手动 `setenforce 1`
可用但不能当持久策略；固化方式仍待落实。

### suspend/resume

`rtcwake -m mem -s 20` 成功唤醒（实际 22 s），唤醒后无 failed unit，显示与
触控、相机服务都在，Wi-Fi 约 3 s 内自动重连（`4way_handshake → completed`
后 DHCP 拿回局域网地址）。dmesg 中的 `IRQ…: set affinity failed(-22)` 与
CMA 分配失败出现在关机 CPU 路径上，属既有噪声，本轮未发现新增故障。
证据：`stability-verify/suspend-cycles.txt`。

### 关屏仍在产生 DPU underrun 快照

0038 补丁确实已合入当前内核（模块字符串里能找到
`slave intf:%d still enabled before cleanup`），但反复关屏时**每 2–3 次仍会
产生一次 devcoredump**：`intf_1`/`intf_2` 的 `is_en` 与配置寄存器非零，而
现状应为全零，差值 18 word。同时 `dmesg` 里 **0 条 dpu 错误**（underrun
回调只累加计数不打印），且设备未编 tracefs（`/sys/kernel/debug/tracing`
不存在），无法用 tracepoint 进一步定位。用户确认屏幕显示正常，因此这是
**需要文档化的残余噪声**，不是当前可见故障。证据：
`stability-verify/dpu-split.txt` 与 `stability-verify/screen-crash/`。

### 设备端包库同步

修复后的二进制最初是直接替换安装的，`rpm -V` 因此报 `S.5....T.`。随后在
设备上按同一 spec 结构重建并升级了两个包（`uperf-linux-0.1.2-2.fc44`、
`librevm-0.2.0-2.fc44`），`rpm -V` 现在对两者都干净，服务重启后 VM 回归
（`magic_ok=True net_ok=1`、`hub 0 / zombies 0 / crosvm 0 / pid+socket clean`）
与 uperf 空闲 CPU（6 s 内 0 s 增量）全部保持。证据：
`stability-verify/pkgs-20261016/`。

### uperf ENODEV 死循环（已修复）

现场发现 `uperf-linux` 对已经消失的输入设备 fd 持续 `read` 返回 `ENODEV`，
日志以 42 次/秒刷屏，累计 CPU 时间 3 分 50 秒／运行 1 小时 34 分。修法是
`runtime/uperf/fedora.patch` 里的三处改动：

1. `input_monitor_poll` 在 `ENODEV` 时关闭整组设备、安排 250 ms 后重扫并
   立即返回，不再逐次打日志（死节点会永远保持 `EPOLLIN`）；
2. 新增 `/dev/input` 的 inotify 监听与 200 ms 防抖，配合 2 s 空转重扫兜底，
   这样设备晚出现（早期启动竞争）或热插拔后都能重新挂上；
3. `main.c` 启动时找不到触屏不再永久关闭输入监控（原来会
   `input_monitor_free` 并置空），改为保留监控等待设备出现。

设备复验（同内核）：设备热插拔出现/消失、启动时两个节点都隐藏再恢复、
真实 `ENODEV`（虚拟触屏被销毁）三种情形全部按预期重新挂载，空闲 8–10 s
CPU 时间 0 s，最后 1 分钟 `No such device` 日志 0 条。部署二进制 SHA256
`7c220321…`；证据：`stability-verify/uperf-fix-20261016/`。

已用上游 `build_android_module.py` 的 installed-core 入口生成完整匹配模块，
保存在 `out/android-matching-core-20261015/piano-matching-core-20261015.zip`。
ZIP SHA256 为 `c8577f04ff0683cc375fcbdec166fe50ca62262ec1c5a957a4db9df5633d381c`；
27 项文件的 CRC、必需载荷和原生工具执行权限通过检查。设备当前 BOOT 的
只读副本经过原生 `adopt --input` 验证，四个载荷摘要与模块完全一致；
APP generation 仍为 `6c38318a290175eba5ff2bb5ef106768`，Linux 请求 sequence=2。
读取前后实际 BOOT 摘要相同，完整包装及 stock 源重建校验通过。

该包安装时只接管已有匹配核心，不重写 BOOT；本轮 Android 实装结果见下节。
Linux → Android → Linux 完整自动往返尚未验收。策略中的未测设备证明
仍为 false，文件级 adopt 不能替代启动证明。

## Android 切换与 SukiSU

Linux helper 写入 Android target=0、sequence=3，并读回匹配；重启后仍进入
Linux，embedded handoff 的 BootTarget=2。自动选择未通过，具体原因待查。
用户在启动选择画面用音量上键选择 Android 后成功进入桌面，ADB 可用。
当前槽位为 B，系统版本 OS3.0.305.0.WPYCNXM，Android 内核为
`6.6.77-android15-8-gf9a1d4bd8353-abogki440974771-4k`。
初始 Android 无 su，生产版 adbd 拒绝 root；已安装的 Magisk 应用不代表 root 可用。

按用户要求改用 SukiSU Ultra。使用[官方 v4.2.0 release](https://github.com/SukiSU-Ultra/SukiSU-Ultra/releases/tag/v4.2.0)
的管理器和 android15-6.6 LKM，下载摘要与 GitHub asset digest 一致。
先通过已验证的临时 RAM UEFI 只读 fetch 当前 `init_boot_b`，8 MiB 备份 SHA256
`16274837901191f0debb8eb447c71b0f088928d46d14cf86cfa8c3841e708973`，
与匹配版本原厂输入完全一致。原始备份已复制到研究仓库的
`out/sukisu-root-20261015/backup/`，不依赖会自动清理的临时目录。

用管理器 APK 内 ksud 对备份文件执行 boot-patch，明确指定官方 LKM，
未开启 allow-shell 或 enable-adbd。补丁保留原始 init 为 init.real，新增
kernelsu.ko 并替换 init，其余 cpio 项不变；镜像头仅改变 ramdisk_size，
kernel_size 仍为 0。补丁 SHA256 为
`c261274fa7ffd521f503ccffb572eb1a1c9c39b963deb983433a6c51fa27a405`。
核对 OEM fastboot 的 B 槽、解锁和分区容量后，只执行 flash init_boot_b，
传输与写入均返回 OKAY。Android 成功启动，管理器显示工作中
`v4.2.0 (40900-2)`，ksud 报告 version=40900、lkm=true、late_load=false。
管理器单独授权 com.android.shell（UID 2000）后，su -c id 返回 uid=0、
`u:r:ksu:s0`；实际 init_boot_b 读回摘要与补丁完全一致。

随后通过 ksud module install 安装匹配模块，返回安装成功，实际 adopt 状态为
ADOPTED_READ_ONLY_VERIFIED、boot_written=false。安装前后 boot_b 摘要均为
`fd53fc91bad8982dcad4f669667a38664463de638eeb3144e329a27115c3bcfe`，
模块选择为 Android、sequence=3。一次不按音量键的默认重启成功回到 Android，
root 授权保留，模块已从 modules_update 转入 modules，quick-status 识别到
Linux 入口可用。此结果不能代替 Linux → Android 的验证。

Android 重启后实际 SELinux 为 Permissive，启动参数带 selinux=permissive，
bootconfig 中还有与前一项值连接的同名字符串。研究仓库早期已记录该启动参数，
但此前记录的运行状态为 Enforcing，故尚不能归因于单一原因。手动 setenforce 1
后 root 和模块安装/状态检查均可用；第二次启动再次为 Permissive，已重新切回
Enforcing。持久启动策略仍待核对，不能把会话内设置当成持久修复。
检查、刷写、读回、模块安装与重启记录保存在研究仓库
`out/sukisu-root-20261015/`，包括完整 BOOT 备份。

此次 RAM UEFI 的 inherited FDT 也已保存，设备 CRC 与两次上传摘要一致。
这些 capability 仅适用于当次启动；已重启，不能用旧值验证后续 Gunyah。

## 主机回归

本轮 `make test` 退出 0。使用 `/usr/bin` Fedora 工具链和
`PIANO_TEST_SUNUEFI=build/takeover-test-sources/sunuefi`，日志为
`make-test-4.log`。其中固定 iio-sensor-proxy 源码断言首次跳过，随后指定真实
3.9 源码单独执行 `tests/check_sensor_runtime.py` 退出 0，覆盖 auto/显式启用
的真实 Meson 红绿对照。其余检查无 SKIP。

## 本轮证据位置

主机临时证据位于 `~/piano-takeover-20261015-01/`，设备证据
位于 `/home/piano/piano-takeover-20261015-01/`。临时目录会清理，关键结论、
身份和验证边界已写入本文，原始日志应随交付另行留存。


## 2026-10-16 A/B 槽位引导保护与 Fastboot 根治记录

### 1. 现象与底层根因

设备在经历多次正常 Linux 重启后偶发自动掉进 Fastboot 模式。通过主机 fastboot 接口读取槽位元数据状态：
```text
current-slot: b
slot-successful:b: no
slot-unbootable:b: yes
slot-retry-count:b: 0
```
高通 ABL 引导程序（SM8750）在启动时会在 GPT 分区属性中递减活动槽位的重试计数（从 7 开始）。在 Android 原厂系统中，进入系统后由 Boot Control HAL 将槽位标记为 successful。由于 Linux 原生缺少该标记步骤，每次正常启动 Linux 均被 ABL 视为未经验证的启动尝试，累积 7 次后计数归零，ABL 自动将 Slot B 判定为不可引导（`slot-unbootable:b = yes`）并保护性停在 Fastboot。

### 2. 救援与永久根治措施

1. **槽位恢复**：
   通过主机执行 `fastboot set_active b`，清除不可引导标记（`slot-unbootable:b -> no`），并将重试计数重置为满格（`slot-retry-count:b -> 7`），随后 `fastboot reboot` 恢复正常启动。

2. **部署官方 Qualcomm Boot Control 守护进程**：
   在平板系统中安装 Fedora 官方适配高通 A/B 槽位管理的 `qbootctl`（0.2.2-2.fc44）工具，解除对 `qbootctl.service` 的屏蔽并设为开机自启：
   ```bash
   sudo systemctl unmask qbootctl.service
   sudo systemctl enable --now qbootctl.service
   sudo qbootctl -m
   ```

3. **读回状态确认**：
   ```text
   SLOT _a:
       Active      : 0
       Successful  : 0
       Bootable    : 1
   SLOT _b:
       Active      : 1
       Successful  : 1
       Bootable    : 1
   ```
   Slot B 已永久标记为 `Successful: 1`。系统在每次启动完成后会自动向 GPT 写入成功标记，ABL 识别 Slot B 为稳定健康系统，绝不再递减计数，彻底杜绝掉进 Fastboot。

## 2026-10-16 上游生态对齐与多媒体/底座配置加固

根据对全网上游（`yzddmr6/xiaomipad-8pro-mainline` v0.4.0、`sources/debian-piano`、`slwyts/Project-SunUEFI`）的系统审查，完成以下 5 项上游具备但此前遗漏的系统级加固并实机复核：

1. **WirePlumber 相机隔离（平息 DMA 报错与 CAMSS 竞争）**：
   - 部署 `/etc/wireplumber/wireplumber.conf.d/50-piano-camera.conf`：禁用 WirePlumber 的 `monitor.libcamera` 并将 `Qualcomm Camera Subsystem` 设为 `device.disabled = true`；
   - 消除 WirePlumber 后台持续报错 `Could not open any dma-buf provider`，确保物理 ISP 节点由 `piano-camerad` 独占，桌面应用经由 `v4l2loopback` 消费标准流。

2. **DMA Heap 与按键 uaccess 权限加固**：
   - 部署 `/etc/udev/rules.d/70-piano-camera.rules`：声明 `/dev/dma_heap/system` 的 `TAG+="uaccess"` 和 `video` 属组（`0660`）；
   - 部署 `/etc/udev/rules.d/70-piano-power-button.rules`：为 `pmic_pwrkey` 和 `hall_lid` 输入事件附加 `TAG+="uaccess"`；
   - 将桌面普通用户 `piano` 加入 `audio,video,input,render` 组；
   - 实测桌面应用申请系统 DMA Buffer 权限通过（`getfacl` 确认 `user:piano:rw-` 生效）。

3. **总线频宽与功耗统计模块开机自启**：
   - 在 `/etc/modules-load.d/` 固化部署 `piano-bwmon.conf`（`icc-bwmon`）与 `piano-stats.conf`（`qcom_stats`）；
   - 避开 `80-drivers.rules` 对 `of:*` coldplug 过滤的影响，确保开机自动加载 APPS->EBI1 互联总线频宽投票与 RPMh 低功耗状态统计模块。

4. **休眠保护与深度挂起策略锁定**：
   - 部署 `/etc/systemd/sleep.conf.d/piano.conf`：
     ```ini
     [Sleep]
     AllowSuspend=yes
     AllowHibernation=no
     AllowHybridSleep=no
     AllowSuspendThenHibernate=no
     SuspendState=mem
     MemorySleepMode=deep
     ```
   - 执行 `systemctl mask hibernate.target hybrid-sleep.target suspend-then-hibernate.target`，阻断未适配的磁盘休眠操作，保护内存与文件系统状态。

5. **崩溃日志掉电归档服务启用**：
   - 启用 `piano-pstore-save.service`（`multi-user.target.wants`）；
   - 保证在系统发生异常静默重启或断电后，自动将 `/sys/fs/pstore/*`（ramoops 内存 `0xa3500000`）持久化至 `/var/lib/piano/pstore/`。
