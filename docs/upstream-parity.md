# 与上游的功能差距

这份清单来自 2026-11-12 的一次只读核对：对照 `build/test-sources/sunuefi`（当时 pin `3a7e5c1b`）的源码，和一台 Fedora 45 GNOME 镜像的实机状态，逐项看哪些上游已经支持、我们这边还没跟上。之后 pin 推进到 `4f663563` 再到 `018634f1`，差异只在 DWC3 停止、显示接缝采样、固件叠层与构建，见 `docs/upstream.md`。每项写明证据与核对命令，下次接手不用重新猜。电源策略、中文环境、相机高像素与静态拍摄、指纹桌面接入、构建断言、文档口径已在本分支完成，其余见各行状态。

| 项 | 我们的现状 | 上游 | 要做的 | 优先级 |
| --- | --- | --- | --- | --- |
| 指纹用户态 | 完成：`piano-fingerprint` 原生客户端加 `piano-libfprint`，fprintd 认到 `Goodix fingerprint sensor`；录入、认证与锁屏解锁已验（v45.3 之前） | `patches/libfprint/1.94.100/` 把 Goodix 接进标准 libfprint | — | 完成 |
| 相机闪光灯 | 完成：`capture rear --flash`，有收敛预览时走上游频闪，没有时常亮补光 | 帧同步频闪 | — | 完成 |
| 相机 JPEG 直出 | 完成：`piano-camera-ctl capture --output FILE` 直出 JPEG | `photo/jpeg.c` | — | 完成 |
| 相机高像素 | 完成：后摄 8160x6120 走 RDI RAW10，Rust worker 合成，实机出帧 | `quad/` 与 v4l2loopback 生产者协商 | — | 完成 |
| 电源策略 | 完成：`tuned-ppd` 三档 profile 桥到 uperf-linux 的模式 | 上游 profile 直接写 cpufreq/devfreq | — | 完成 |
| 充电、温控与 FFC | 补丁已收：上游 `0093`、`0094`、`0096`–`0104` 共 11 条进封存序列 | 关机充电、皮肤温控、带认证 FFC 与停充失败退出 | 真机验充电状态、关机再插电、温控与功率；45 W 不作声称 | P1 |
| 内核手指解码 | 保持用户态 THP 读取加 `nvt_finger_filter.cpp` 的 5 级滤波 | `0095` 把逐帧矩阵解码搬进内核，无时域滤波 | 本轮不收；要换先做同帧对比 | P3 |
| 中文环境 | 完成：装 `glibc-langpack-zh`，`/etc/locale.conf` 写 `zh_CN.UTF-8`，输入源默认带 `libpinyin` | `defaults.ini` 写 `LANG=zh_CN.UTF-8`，输入源含 `ibus`/`libpinyin` | — | 完成 |
| mutter 触摸崩溃 | 库存 `mutter-51.1-1.fc45` | 补丁在 51.1 源码上干净套用 | 崩溃未复现，这版不重编 mutter | P2 |
| KDE 桌面默认值 | 完成：`kwinoutputconfig.json`、`kcminputrc`、`kscreenlockerrc` 落进 `~/.config`，`/etc/environment.d` 设 `KWIN_DRM_FORCE_SCALING_MODE=FULL` | 同名文件写 scale 2、`InTabletMode`、自然滚动，另设 `QT_SCALE_FACTOR=2` | `QT_SCALE_FACTOR` 没有跟：与输出 scale 叠加会放大两次 | 完成 |
| 罗盘 | 可用：`/net/hadess/SensorProxy/Compass` 上 `HasCompass=true` | `ssc-compass` 驱动已编入 `iio-sensor-proxy-ssc` | 之前查的是父对象，接口在子对象上 | 完成 |
| 构建断言 | 完成：`build-kernel.yml` 断言 20 项，`check_kernel_config_assertions.py` 锁住 | 平台片段默认打开 IR、LIRC、SND_SEQUENCER 等 | — | 完成 |
| 文档口径 | 完成：README 与 `known-issues.md` 的红外/HDR/指纹三节按实机状态重写 | — | — | 完成 |
| 自动亮度 | 已验：profile 加载、目标亮度换算；遮住与强光往返 lux 8–2527、背光 1600–2047 跟随，延迟 1–2 秒 | GSD 按原厂 lux 曲线调背光 | 基准落在暗处时暗端偏高；按面板 nits 校准 | P3 |
| 键盘背光热插拔 | extras 的 GSD 带 50.1 补丁，背光设备随键盘盖插拔更新 | 背光走 UPower/GNOME | 同上，接上/取下键盘盖，看 `/sys/class/leds` 与 UPower | P2 |
| KDE HDR/广色域 | 完成：extras 的 KWin 带退出 HDR 恢复背光补丁，2026-11-12 冷启动真机开关两轮，背光 1024 → 2047 → 1024 | KWin 可切换，退出 HDR 恢复背光 | 色深与色调映射没量，见[已知问题](known-issues.md#hdr) | 完成 |
| 指纹录入 | 完成：`fprintd-enroll` 走完、`fprintd-verify` 通过、锁屏用指纹解开，在 v45.3 之前的镜像上验过 | libfprint/fprintd 完整录入与认证 | — | 完成 |
| 相机隐私灯 | 完成：前后摄各录 4 秒，`/sys/class/leds/green/brightness` 从 0 到 4，流结束回 0 | 随前后摄视频流启停 | — | 完成 |
| 相机录像 | 完成：`ffmpeg` 从 `/dev/video40` 与 `/dev/video41` 各录 4 秒，1920x1440@30，各 120 帧 | 预览、录像、AE/AF/AWB | — | 完成 |
| 手写笔进阶 | 只验了位置、压感、倾斜、proximity | 捏握、震动、磁吸识别、笔电量、充满停电、跨系统重配 | 用 `libinput debug-events` 或 `evtest` 记录实际事件，缺的再开范围 | P3 |
| exFAT/NTFS | `patches/kernel-f2fs` 已把 `EXFAT_FS`/`NTFS3_FS` 编成 y，挂载没测 | 读写 | 真机挂载两种 U 盘并读写 | P3 |
| 10-bit 与 144 Hz | 面板与驱动支持，没量过色深与刷新率 | 面板 10bpc，144 Hz 可选 | 只验，不改默认（KDE 120，GNOME 可到 144） | P3 |

自动亮度与键盘背光的补丁都在 extras 里，自动亮度已在真机验过，键盘背光还缺验收。HDR 的 KWin 补丁在 2026-11-12 冷启动真机上验过退出后背光恢复。隐私灯与录像已在 2026-11-11 的真机上验过。手写笔进阶与存储挂载仍是验收项，不是新的适配项。测出真实缺口再单独开范围，不在这轮顺手改。

## 指纹

内核侧齐了：`/dev/uio0` 存在，`lsmod` 有 `uio_piano_fingerprint` 与 `qcomtee`，`dmesg` 打出 `QTEE version 5.2.0`，`/dev/tee0` 可读，`/proc/device-tree/soc/xiaomi_fingerprint` 有节点。DT overlay 一直在发，`tests/check_bundle_overlays.py` 也在断言 `xiaomi,piano-goodix-control`。

这一版把原生客户端做成了 `piano-fingerprint` 包：`src/` 是 SunUEFI `linux/userspace/fingerprint` 的副本，依赖 `quic-teec`、`minkipc`、`QCBOR` 三个固定提交，产出静态的 `piano-fingerprint-ctl`，外加给 libfprint 链接的 `libpiano-goodix.a` 与 `piano-goodix.pc`。实机装包后三条命令都过：

```
sudo piano-fingerprint-ctl info
{"sensor":"Goodix","sensor_type":5,"command_address_bits":64,"device_info_ok":true}
sudo piano-fingerprint-ctl initialize
{"sensor":"Goodix",...,"max_templates":5,"rows":132,"columns":24,"models_loaded":true,"sensor_initialized":true}
sudo piano-fingerprint-ctl list 0
{"fingers":[]}
```

桌面接入做成了 `piano-libfprint` 包。Fedora 45 的 `libfprint-1.94.100-1.fc45` 与上游 pin 是同一份源码：`v1.94.100` 解引用后就是 `80a4b5ec`。包拿官方 tarball 套 `0001-piano-goodix-uio.patch`，静态链 `libpiano-goodix.a`，再加一条 meson 1.12 的 `foreach` 兼容补丁（关掉 introspection 时上游的 else 分支一定会被求值）。用 `Provides`/`Obsoletes` 顶掉发行版 `libfprint`，附一份 fprintd drop-in：

```
DeviceAllow=char-uio rw
DeviceAllow=char-tee rw
DeviceAllow=char-bsg rw
StateDirectory=piano-goodix
```

实机装上后 fprintd 认到一颗设备：

```
$ fprintd-list piano
found 1 devices
Device at /net/reactivated/Fprint/Device/0
Using device /net/reactivated/Fprint/Device/0
ListEnrolledFingers failed: GDBus.Error:net.reactivated.Fprint.Error.PermissionDenied:
Not Authorized: net.reactivated.fprint.device.verify
```

最后一行是 polkit 拒绝未授权会话读模板，不是驱动错误。`/var/lib/piano-goodix` 由 `StateDirectory` 建好，权限 `root:root 0700`。录入要按一次侧置传感器；在 v45.3 之前的镜像上录入、认证与锁屏解锁都走通过，v45.3 没重测。镜像里带的是发行版的 `fprintd-pam`，没有替用户自动跑 `authselect`。

## 相机

闪光灯硬件在：`ls /sys/class/leds` 有 `:flash-0`、`:flash-1`，`leds_qcom_flash`、`v4l2_flash_led_class`、`led_class_flash` 都在加载，`piano-camera-flash.dtso` 也一直在 overlay 列表里。后摄的两个闪光通道以 ancillary media link 挂在 `s5kjn1` 上，`/dev/v4l-subdev33`、`/dev/v4l-subdev34` 就是它们。

静态拍摄在同一个控制面上：`/run/piano-camerad/control.sock`（`SOCK_SEQPACKET`，root:video 0660）收 `capture <camera> [flash]`。守护进程停掉预览，切到该传感器最大的普通模式（后摄 4080x3060、前摄 3264x2448），等自动曝光收敛后把 NV12 帧写进只读 memfd，用 `SCM_RIGHTS` 交给客户端；`piano-camera-ctl` 编码 JPEG。拍完恢复预览尺寸，正在看预览的应用会短暂停一下再继续。

`--flash` 有两种走法。loopback 上有已收敛的预览流时走上游的帧同步频闪：冻结 3A，按曝光窗口算脉冲长度，等一帧的曝光区间落在灯亮区间里，输出跟随预览尺寸。没有这样的预览流时，控制口自己起流、自动曝光在暗场里会把增益推到最大，一次满功率脉冲只会把整幅打到 255，所以改为常亮补光：把 `V4L2_CID_FLASH_LED_MODE` 设成 TORCH，让自动曝光在灯亮条件下收敛，拍完关灯。

实机结果：后摄 `capture rear --flash --output` 得到 4080x3060 的 JPEG，同一暗场不开补光时平均亮度 1.3，开补光后 94 到 122；前摄 `capture front` 得到 3264x2448。拍摄期间 ffmpeg 在读 `/dev/video40`，拍完预览继续出帧，ffmpeg 正常结束。

后摄传感器模式从 4080x3072 改成了 4080x3060。`s5kjn1` 驱动只报 4080x3060，`S_FMT` 写 3072 会被钳回 3060，`subdev_fmt()` 的读回校验因此返回 `EPIPE`，后摄预览在 7.3 内核上根本起不来。改到 3060 后预览与拍摄都正常，`tests/check_camera_photo.py` 锁住这个值。

高像素走 v4l2loopback 的生产者协商。runtime 的模块改成上游 0.15.4 的三份补丁（`patches/v4l2loopback/0.15.4/series.json`，按 sha256 校验后套用），补上消费端 `S_FMT` 协商与 `V4L2_EVENT_SOURCE_CHANGE`。相机用户态换成上游 `linux/userspace/camera`：守护进程把每个传感器模式注册给 loopback，应用请求 8160x6120 时后摄切到 `msm_vfe0_rdi0` 的 RAW10（SGRBG10），`quad/` 的 Rust worker 用随包的 demosaic 0.3.0（加一份 QPPG 修正）合成 NV12，实测每帧约 3.6 秒、工作内存约 950 MiB。

设备证据来自同一份探针的前后对照。旧模块把消费端请求的 8160x6120 钳回 1920x1440，`SOURCE_CHANGE` 订阅返回 `EINVAL`；换模块后消费端读回 8160x6120，生产端收到事件重新协商，守护进程日志有 `output negotiated to 8160x6120 NV12`。抓到的帧是 74,908,800 字节，后摄曝光从 8 行加到 6412 行时同一场景平均亮度从 2.2 升到 255，说明帧内容是真实感光而不是黑帧。

走 portal 与 pipewire 的客户端是另一条链，跟直连 `/dev/video40` 的表现不同。Fedora 45 的 `pipewire-1.6.11-1` 缺 1.6 分支之后收的三处 GStreamer 修复，GNOME Snapshot 先拿到 16x4 的预览，修好尺寸后又在 `gst_base_sink_drain` 里段错误。我们出 `piano-pipewire-gstreamer` 顶掉官方子包，带 1.6 分支 tip 在 `src/gst/` 下的全部提交和一条只进过 master 的 backport，`Provides`/`Obsoletes` 保证同一个插件只有一份包提供。尺寸对照用 `GstDeviceMonitor` 读设备 caps：官方包的 fixate 是 16x4，替换后两个摄像头都 fixate 到 1920x1440。崩溃与重连的验证在 [已知问题](known-issues.md)，删包条件见 #64。

## 电源策略

原状是 `rpm -q tuned power-profiles-daemon` 两个都 not installed，`/usr/lib/systemd/system` 里也没有对应 unit，GNOME 设置里的性能模式面板是空的。Fedora 45 仓库有 `tuned 2.28.0` 与 `tuned-ppd 2.28.0`，Fedora 41 起后者的定位就是 PPD 的默认实现，所以走 `tuned-ppd` 而不是 `power-profiles-daemon`。

照搬上游的 profile 不行。上游 `piano-{power-saver,balanced,performance}` 在 `[sysfs]` 里直接写 `scaling_max_freq` 与 `3d00000.gpu/max_freq`；这台机器上这些文件归 `uperf-linux` 管，它开机接管两个簇与 GPU，外部写入几秒内就被覆盖。实测把 `policy0/scaling_max_freq` 写成 1785600，3 秒后回到 2745600。GPU 上限更麻烦：被写成 342000000 之后切回 balanced 不会恢复，等于一直压着。

所以这里的 profile 只切 uperf 自己的模式，`uperfctl mode powersave|balance|performance` 与 GNOME 的三档一一对应。TuneD 的 script 插件只接受 profile 目录里的脚本，目录外的路径会被记成 error 并忽略，所以每档带一个 `apply.sh`，真正的切换逻辑在 `/usr/lib/piano/piano-power-mode`，带重试以覆盖开机时 daemon 还没起来的窗口。

已在设备上核对：设置里的电源面板出现 Performance／Balanced／Power Saver 三档，从界面点选之后 `net.hadess.PowerProfiles` 的 `ActiveProfile`、`tuned-adm active` 与 `/run/uperf-linux/cur_powermode` 三者同步变化，GPU 上限全程停在 1100000000。未核对：重启后的首启时序。

界面这一条要在图形会话里试。从 SSH 起 `gnome-control-center` 时，进程属于 inactive session，PPD 的 `net.hadess.PowerProfiles.switch-profile` 只对 active session 放行（策略里 `allow_inactive=no`、`allow_active=yes`），调用会被拒成 `Unauthorized`，面板却已经把单选圈画到新档位，看起来像切了实际没切。从 shell 概览里启动就不会有这个问题。

## 中文环境

`/etc/locale.conf` 是 `LANG=C.UTF-8`，`glibc-langpack-zh` 没装，连 `glibc-langpack-en` 也没有。`gsettings get org.gnome.desktop.input-sources sources` 返回 `[('xkb', 'us')]`，`ibus-libpinyin` 装了、`ibus-daemon` 也在跑，只是没人把它加进输入源。

字体已经在：`google-noto-sans-cjk-vf-fonts`、`google-noto-serif-cjk-vf-fonts`、`default-fonts-cjk-*` 都装了，`fc-list :lang=zh` 有 61 条。这一项不用动。

要补的就是三处：`glibc-langpack-zh` 进 profile、`/etc/locale.conf` 写 `LANG=zh_CN.UTF-8`、输入源默认值按上游 `defaults.ini` 的 `[('xkb', 'us'), ('ibus', 'libpinyin')]` 给。这是产品取向问题，跟着上游默认中文的话，成本只有这三处。

## mutter

Fedora 45 是 `mutter-51.1-1.fc45`。上游三个补丁里只有 `0003-wayland-touch-ignore-cancelled-grab` 对得上：它的提交说明写着 "Two Piano crash dumps from Mutter 51.0 show a NULL first member of MetaWaylandTouchInfo ... The same unchecked dereference is present in upstream 51.1"，改的是 `touch_can_grab_surface()` 里 `focus_surface` 为空时的解引用。

拿 mutter 51.1 官方源码核对过：`src/wayland/meta-wayland-touch.c:662` 就是 `touch_info->focus_surface->surface`，补丁 `patch --fuzz=0 --dry-run` 干净套用。这一版不重编 mutter：设备上没复现过这个崩溃，而发布一份替换系统 compositor 的包，需要先把崩溃场景跑出来，不然只是把一个未验证的二进制塞进镜像。

`0001-kms-empty-gamma-bypass` 针对的是 48.7 的 `SetCrtcGamma` 空数组路径。我们的 CRTC 上 `GAMMA_LUT`、`DEGAMMA_LUT`、`CTM` 都在，这条路径会不会踩到还没验。

## 桌面默认值

GNOME 侧 `DisplayConfig.GetCurrentState` 给出的逻辑显示器是 `scale 2.6666667461395264`，按 239x163mm 自动选了分数缩放，界面可用。上游 `defaults.ini` 写死 `scaling-factor=uint32 2`，那是它的口味，照搬会把界面缩小一档。这一项不算缺口。

KDE 侧的默认值补上了。KWin 的输出、输入与锁屏设置只在 `~/.config` 下，没有 `/etc/xdg` 级别的落点，所以 profile 多了 `user_config` 一项：列出源文件与目标路径，装配时按 `home/piano` 的属主落盘，KWin 运行时能改写。

| 文件 | 内容 |
| --- | --- |
| `~/.config/kwinoutputconfig.json` | DSI-1 3200x2136@120、scale 2、`autoRotation: InTabletMode`、亮度 0.8 |
| `~/.config/kcminputrc` | `Xiaomi Touchpad` 自然滚动 |
| `~/.config/kscreenlockerrc` | `Autolock=false`、`LockOnResume=true` |

`/etc/environment.d/60-piano-display.conf` 加 `KWIN_DRM_FORCE_SCALING_MODE=FULL`。KWin 默认只缩放自己生成的模式，DSI-1 的模式来自 DRM，没有这个变量时它不肯按 scale 2 放大。上游的 `QT_SCALE_FACTOR=2` 没有跟：输出 scale 与它会叠加，界面放大两次。

实机核对：`kscreen-doctor -o` 报 DSI-1 `3200x2136@120`、几何 `1600x1068`、scale 2、`Auto Rotate Policy: inTabletMode`；`kwinoutputconfig.json` 被 KWin 回写成运行时值；`kreadconfig6` 读出 `Autolock=false`、`LockOnResume=true`；KWin 进程的环境里有 `KWIN_DRM_FORCE_SCALING_MODE=FULL`。把平板横竖转 90°，屏幕跟随旋转。触控板自然滚动要接键盘盖，没测到。

## 罗盘

可用。Compass 接口不在 `/net/hadess/SensorProxy` 上，而在子对象 `/net/hadess/SensorProxy/Compass`：

```
busctl introspect net.hadess.SensorProxy /net/hadess/SensorProxy/Compass
.HasCompass  property  b  true
```

之前的核对只 introspect 了父对象，所以写成了"没有 Compass 接口"。SSC 侧也确认有磁力计：`ssccli --sensor magnetometer` 有读数，`ssccli --sensor compass` 给出方位角。

## 构建断言

设备上实测的配置：`CONFIG_LIRC=y`、`CONFIG_IR_SPI=m`、`CONFIG_SND_SEQUENCER=m`、`CONFIG_ARM64_PIANO_OPTIMIZE=y`、`CONFIG_LEDS_CLASS_FLASH=m`、`CONFIG_UIO_PIANO_FINGERPRINT=m`。`build-kernel.yml` 现在只断言 F2FS、SELinux、Gunyah 三组，历史上 `LIRC` 与 `IR_SPI` 真的掉过一次。把已经依赖上的项补进断言，是几行的事。

## 文档口径

README 与 `known-issues.md` 里红外、HDR、指纹三节还是旧的判断，和上面核对的设备状态对不上：

- 红外：设备上 `/dev/lirc0`、`/sys/class/rc/rc0/lirc0` 都在，`dmesg` 有 `rc rc0: lirc_dev: driver ir-spi registered at minor = 0, no receiver, raw IR transmitter`，`ir_spi` 与 `rc_core` 已加载。文档写的"DT 缺 IR 节点、内核没编 LIRC"不成立。上游自己的说法是接口就绪、光学输出未实测，照这个口径写。
- HDR：DSI-1 连接器属性里有 `HDR_OUTPUT_METADATA`，同时有 `Colorspace`、`max bpc`，CRTC 上有 `GAMMA_LUT`、`DEGAMMA_LUT`、`CTM`。文档写的"msm 没有挂 HDR_OUTPUT_METADATA"不成立。HDR 开关 2026-11-12 已在 KDE 里跑过，色深与色调映射没量，见[已知问题](known-issues.md#hdr)。
- 指纹：控制驱动与 DT 都在，"主线没有对应的控制驱动"这句话要改；正确的说法是内核与 DT 就绪、用户态还没接。

## 核对方法

设备侧只读，没改任何配置：

```
sudo python3 ~/Lab/Bridge/tmp/drm-props.py /dev/dri/card2 /dev/dri/card2   # 连接器与 CRTC 属性
busctl introspect net.hadess.SensorProxy /net/hadess/SensorProxy
busctl get-property net.hadess.PowerProfiles /net/hadess/PowerProfiles net.hadess.PowerProfiles Profiles
rpm -q mutter libfprint fprintd tuned power-profiles-daemon glibc-langpack-zh
fc-list :lang=zh | wc -l
lsmod | grep -E 'ir_spi|uio_piano_fingerprint|qcomtee|leds_qcom_flash'
zcat /proc/config.gz | grep -E 'CONFIG_LIRC|CONFIG_IR_SPI|CONFIG_UIO_PIANO|CONFIG_ARM64_PIANO'
```

上游侧对照的是 `build/test-sources/sunuefi`（`4ef8740`）的 `tools/build_piano_fingerprint.py`、`linux/userspace/camera/`、`linux/desktops/`、`patches/libfprint/` 与 `docs/status.md`。

## 核对过但不需要做的

- mutter `0002-color-no-edid-standard-profile`：DSI-1 的 EDID 是 384 字节、头 `00 ff ff ff ff ff ff 00` 的合法 EDID，不落在这个补丁覆盖的场景里。
- 中文字体：CJK 字体已随 `google-noto-*-cjk-vf-fonts` 和 `default-fonts-cjk-*` 装上。
- GNOME 缩放默认值：自动分数缩放在这台面板上给出 2.67，可用，不需要写死。
