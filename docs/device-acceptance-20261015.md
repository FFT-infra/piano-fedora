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

## 仍未验收

固定源码已包含 SC8541/MCA 直充支持，设备参数 cp_charge、hv_charge、
charge_current、mipps_auth、charge_policy 均开启；不能因此声称实际达到 45W。
0036 不应重复套用到已有实现。需使用真实充电器、线材和负载记录功率与温度。

当前标准内核没有 `/dev/gunyah` 或 `/dev/kvm`。librevm-daemon active 和
`librevm list` 返回空列表只证明用户态管理器响应，VM 创建、客体控制台、
磁盘和网络仍未完成，见[虚拟化验收门槛](virtualization.md)。

Linux 启动请求配置与桌面入口在位。Claude 留下的 `piano_sunuefi_module.zip`
只有模块脚手架及根目录 request 程序，缺少 manager 要求的 bin/ 原生工具和
策略/状态，不能安装使用。

已用上游 `build_android_module.py` 的 installed-core 入口生成完整匹配模块，
保存在 `out/android-matching-core-20261015/piano-matching-core-20261015.zip`。
ZIP SHA256 为 `c8577f04ff0683cc375fcbdec166fe50ca62262ec1c5a957a4db9df5633d381c`；
27 项文件的 CRC、必需载荷和原生工具执行权限通过检查。设备当前 BOOT 的
只读副本经过原生 `adopt --input` 验证，四个载荷摘要与模块完全一致；
APP generation 仍为 `6c38318a290175eba5ff2bb5ef106768`，Linux 请求 sequence=2。
读取前后实际 BOOT 摘要相同，完整包装及 stock 源重建校验通过。

该包安装时只接管已有匹配核心，不重写 BOOT；Android 模块安装、root ADB
返回通道及 Linux → Android → Linux 往返尚未验收。策略中的未测设备证明
仍为 false，文件级 adopt 不能替代 Android 安装或启动证明。

## 主机回归

本轮 `make test` 退出 0。使用 `/usr/bin` Fedora 工具链和
`PIANO_TEST_SUNUEFI=build/takeover-test-sources/sunuefi`，日志为
`make-test-4.log`。其中固定 iio-sensor-proxy 源码断言首次跳过，随后指定真实
3.9 源码单独执行 `tests/check_sensor_runtime.py` 退出 0，覆盖 auto/显式启用
的真实 Meson 红绿对照。其余检查无 SKIP。

## 本轮证据位置

主机临时证据位于 `~/Lab/Bridge/tmp/trash/piano-takeover-20261015-01/`，设备证据
位于 `/home/piano/piano-takeover-20261015-01/`。临时目录会清理，关键结论、
身份和验证边界已写入本文，原始日志应随交付另行留存。
