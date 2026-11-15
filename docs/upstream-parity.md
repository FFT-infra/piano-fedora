# 与上游的功能差距

> **说明**：本文档对照上游 [Project SunUEFI](https://github.com/slwyts/Project-SunUEFI) 及相关生态组件，汇总当前支持矩阵、架构差异与待跟进项。已完成项目保留技术依据，未完成项目标明跟踪状态。

对照 `sources.lock.json` 所锁定的上游基线与 Fedora 45 实际运行状态，逐项整理功能支持度与核心差异。电源策略、中文环境、相机高像素与静态拍摄、指纹桌面接入、构建断言均已就绪，其余见各行状态。

| 项 | 我们的现状 | 上游 | 要做的 | 优先级 |
| --- | --- | --- | --- | --- |
| 指纹用户态 | 完成：`piano-fingerprint` 原生客户端加 `piano-libfprint`，fprintd 认到 `Goodix fingerprint sensor`，录入、认证与锁屏解锁链路完整 | `patches/libfprint/1.94.100/` 把 Goodix 接进标准 libfprint | — | 完成 |
| 相机闪光灯 | 完成：`capture rear --flash`，有收敛预览时走上游频闪，没有时常亮补光 | 帧同步频闪 | — | 完成 |
| 相机 JPEG 直出 | 完成：`piano-camera-ctl capture --output FILE` 直出 JPEG | `photo/jpeg.c` | — | 完成 |
| 相机高像素 | 完成：后摄 8160x6120 走 RDI RAW10，Rust worker 合成 NV12 | `quad/` 与 v4l2loopback 生产者协商 | — | 完成 |
| 电源策略 | 完成：`tuned-ppd` 三档 profile 桥到 uperf-linux 的模式 | 上游 profile 直接写 cpufreq/devfreq | — | 完成 |
| 充电、温控与 FFC | 补丁已收：上游 `0093`、`0094`、`0096`–`0104` 共 11 条进封存序列 | 关机充电、皮肤温控、带认证 FFC 与停充失败退出 | 验证充电状态、关机再插电、温控与功率；45 W 不作声称 | P1 |
| 内核手指解码 | 保持用户态 THP 读取加 `nvt_finger_filter.cpp` 的 5 级滤波 | `0095` 把逐帧矩阵解码搬进内核，无时域滤波 | 当前未采纳；若需替换需先完成同帧客观对比。 | P3 |
| 中文环境 | 完成：装 `glibc-langpack-zh`，`/etc/locale.conf` 写 `zh_CN.UTF-8`，输入源默认带 `libpinyin` | `defaults.ini` 写 `LANG=zh_CN.UTF-8`，输入源含 `ibus`/`libpinyin` | — | 完成 |
| mutter 触摸崩溃 | 库存 `mutter-51.1-1.fc45` | 补丁在 51.1 源码上干净套用 | 崩溃未复现；补丁进 opt-in extras，默认镜像不重编 | P2 |
| KDE 桌面默认值 | 完成：`kwinoutputconfig.json`、`kcminputrc`、`kscreenlockerrc` 落进 `~/.config`，`/etc/environment.d` 设 `KWIN_DRM_FORCE_SCALING_MODE=FULL` | 同名文件写 scale 2、`InTabletMode`、自然滚动，另设 `QT_SCALE_FACTOR=2` | `QT_SCALE_FACTOR` 没有跟：与输出 scale 叠加会放大两次 | 完成 |
| 罗盘 | 可用：`/net/hadess/SensorProxy/Compass` 上 `HasCompass=true` | `ssc-compass` 驱动已编入 `iio-sensor-proxy-ssc` | 之前查的是父对象，接口在子对象上 | 完成 |
| 构建断言 | 完成：`build-kernel.yml` 断言 20 项，`check_kernel_config_assertions.py` 锁住 | 平台片段默认打开 IR、LIRC、SND_SEQUENCER 等 | — | 完成 |
| 文档口径 | 完成：README 与 `known-issues.md` 的红外/HDR/指纹三节按实机状态重写 | — | — | 完成 |
| 自动亮度 | 已支持：profile 加载、目标亮度换算；遮住与强光往返 lux 8–2527、背光 1600–2047 跟随，延迟 1–2 秒 | GSD 按原厂 lux 曲线调背光 | 基准落在暗处时暗端偏高；按面板 nits 校准 | P3 |
| 键盘背光热插拔 | extras 的 GSD 带 50.1 补丁，背光设备随键盘盖插拔更新 | 背光走 UPower/GNOME | 同上，接上/取下键盘盖，看 `/sys/class/leds` 与 UPower | P2 |
| KDE HDR/广色域 | 完成：extras 的 KWin 带退出 HDR 恢复背光补丁，背光正常恢复 | KWin 可切换，退出 HDR 恢复背光 | 色深与色调映射没量，见[已知问题](known-issues.md#hdr) | 完成 |
| 指纹录入 | 完成：`fprintd-enroll`、`fprintd-verify` 与锁屏解锁全流程打通 | libfprint/fprintd 完整录入与认证 | — | 完成 |
| 相机隐私灯 | 完成：前后摄视频流启停联动 `/sys/class/leds/green/brightness` | 随前后摄视频流启停 | — | 完成 |
| 相机录像 | 完成：支持 1920x1440@30 预览与录像 | 预览、录像、AE/AF/AWB | — | 完成 |
| 手写笔进阶 | 基础输入正常：位置、压感、倾斜、proximity | 捏握、震动、磁吸识别、笔电量、充满停电、跨系统重配 | 用 `libinput debug-events` 或 `evtest` 记录实际事件，缺的再开范围 | P3 |
| exFAT/NTFS | `patches/kernel-f2fs` 已把 `EXFAT_FS`/`NTFS3_FS` 编成 y，挂载没测 | 读写 | 挂载两种 U 盘并读写 | P3 |
| 10-bit 与 144 Hz | 面板与驱动支持，未量测色深与刷新率 | 面板 10bpc，144 Hz 可选 | 仅验证，不改默认（KDE 120，GNOME 可到 144） | P3 |

自动亮度与键盘背光补丁收录在 extras 仓库。手写笔进阶特性与外部存储挂载作为后续验收项按需推进。

## 指纹

内核侧齐了：`/dev/uio0` 存在，`lsmod` 有 `uio_piano_fingerprint` 与 `qcomtee`，`dmesg` 打出 `QTEE version 5.2.0`，`/dev/tee0` 可读，`/proc/device-tree/soc/xiaomi_fingerprint` 有节点。DT overlay 一直在发，`tests/check_bundle_overlays.py` 也在断言 `xiaomi,piano-goodix-control`。

这一版把原生客户端做成了 `piano-fingerprint` 包：`src/` 是 [SunUEFI](https://github.com/slwyts/Project-SunUEFI) `linux/userspace/fingerprint` 的副本，依赖 `quic-teec`、`minkipc`、`QCBOR` 三个固定提交，产出静态的 `piano-fingerprint-ctl`，外加给 libfprint 链接的 `libpiano-goodix.a` 与 `piano-goodix.pc`。实机装包后三条命令都过：

```console
sudo piano-fingerprint-ctl info
{"sensor":"Goodix","sensor_type":5,"command_address_bits":64,"device_info_ok":true}
sudo piano-fingerprint-ctl initialize
{"sensor":"Goodix",...,"max_templates":5,"rows":132,"columns":24,"models_loaded":true,"sensor_initialized":true}
sudo piano-fingerprint-ctl list 0
{"fingers":[]}
```

桌面接入做成了 `piano-libfprint` 包。Fedora 45 的 `libfprint-1.94.100-1.fc45` 与上游 pin 是同一份源码：`v1.94.100` 解引用后就是 `80a4b5ec`。包拿官方 tarball 套 `0001-piano-goodix-uio.patch`，静态链 `libpiano-goodix.a`，再加一条 meson 1.12 的 `foreach` 兼容补丁（关掉 introspection 时上游的 else 分支一定会被求值）。用 `Provides`/`Obsoletes` 顶掉发行版 `libfprint`，附一份 fprintd drop-in：

```ini
DeviceAllow=char-uio rw
DeviceAllow=char-tee rw
DeviceAllow=char-bsg rw
StateDirectory=piano-goodix
```

实机装上后 fprintd 认到一颗设备：

```console
$ fprintd-list piano
found 1 devices
Device at /net/reactivated/Fprint/Device/0
Using device /net/reactivated/Fprint/Device/0
ListEnrolledFingers failed: GDBus.Error:net.reactivated.Fprint.Error.PermissionDenied:
Not Authorized: net.reactivated.fprint.device.verify
```

最后一行是 polkit 拒绝未授权会话读模板，不是驱动错误。`/var/lib/piano-goodix` 由 `StateDirectory` 建立，权限 `root:root 0700`。录入时按压侧置传感器，录入、认证与锁屏解锁链路完整。镜像预装发行版 `fprintd-pam`，未替用户默认运行 `authselect`。

## 相机

闪光灯硬件在：`ls /sys/class/leds` 有 `:flash-0`、`:flash-1`，`leds_qcom_flash`、`v4l2_flash_led_class`、`led_class_flash` 都在加载，`piano-camera-flash.dtso` 也一直在 overlay 列表里。后摄的两个闪光通道以 ancillary media link 挂在 `s5kjn1` 上，`/dev/v4l-subdev33`、`/dev/v4l-subdev34` 就是它们。

静态拍摄在同一个控制面上：`/run/piano-camerad/control.sock`（`SOCK_SEQPACKET`，root:video 0660）收 `capture <camera> [flash]`。守护进程停掉预览，切到该传感器最大的普通模式（后摄 4080x3060、前摄 3264x2448），等自动曝光收敛后把 NV12 帧写进只读 memfd，用 `SCM_RIGHTS` 交给客户端；`piano-camera-ctl` 编码 JPEG。拍完恢复预览尺寸，正在看预览的应用会短暂停一下再继续。

`--flash` 有两种走法。loopback 上有已收敛的预览流时走上游的帧同步频闪：冻结 3A，按曝光窗口算脉冲长度，等一帧的曝光区间落在灯亮区间里，输出跟随预览尺寸。没有这样的预览流时，控制口自己起流、自动曝光在暗场里会把增益推到最大，一次满功率脉冲只会把整幅打到 255，所以改为常亮补光：把 `V4L2_CID_FLASH_LED_MODE` 设成 TORCH，让自动曝光在灯亮条件下收敛，拍完关灯。

测试数据：后摄 `capture rear --flash --output` 产出 4080x3060 JPEG，暗场补光下平均亮度由 1.3 提升至 94–122；前摄产出 3264x2448。拍摄期间若有后台应用读取 `/dev/video40`，拍完预览继续出帧，流处理正常结束。

后摄传感器模式从 4080x3072 改成了 4080x3060。`s5kjn1` 驱动只报 4080x3060，`S_FMT` 写 3072 会被钳回 3060，`subdev_fmt()` 的读回校验因此返回 `EPIPE`，后摄预览在 7.3 内核上根本起不来。改到 3060 后预览与拍摄都正常，`tests/check_camera_photo.py` 锁住这个值。

高像素走 v4l2loopback 的生产者协商。runtime 模块集成上游 0.15.4 补丁（`patches/v4l2loopback/0.15.4/series.json`），补齐消费端 `S_FMT` 协商与 `V4L2_EVENT_SOURCE_CHANGE`。相机用户态采用上游 `linux/userspace/camera`：守护进程把每个传感器模式注册给 loopback，应用请求 8160x6120 时后摄切换至 `msm_vfe0_rdi0` 的 RAW10（SGRBG10），`quad/` 的 Rust worker 用 demosaic 0.3.0（含 QPPG 修正）合成 NV12，单帧处理耗时约 3.6 秒、工作内存约 950 MiB。

设备证据来自同一份探针的前后对照。旧模块把消费端请求的 8160x6120 钳回 1920x1440，`SOURCE_CHANGE` 订阅返回 `EINVAL`；换模块后消费端读回 8160x6120，生产端收到事件重新协商，守护进程日志有 `output negotiated to 8160x6120 NV12`。抓到的帧是 74,908,800 字节，后摄曝光从 8 行加到 6412 行时同一场景平均亮度从 2.2 升到 255，说明帧内容是真实感光而不是黑帧。

走 portal 与 pipewire 的客户端是另一条链，跟直连 `/dev/video40` 的表现不同。Fedora 45 的 `pipewire-1.6.11-1` 缺 1.6 分支之后收的三处 GStreamer 修复，GNOME Snapshot 先拿到 16x4 的预览，修好尺寸后又在 `gst_base_sink_drain` 里段错误。我们出 `piano-pipewire-gstreamer` 顶掉官方子包，带 1.6 分支 tip 在 `src/gst/` 下的全部提交和一条只进过 master 的 backport，`Provides`/`Obsoletes` 保证同一个插件只有一份包提供。尺寸对照用 `GstDeviceMonitor` 读设备 caps：官方包的 fixate 是 16x4，替换后两个摄像头都 fixate 到 1920x1440。崩溃与重连的验证在 [已知问题](known-issues.md)，删包条件见 #64。

## 电源策略

原状是 `rpm -q tuned power-profiles-daemon` 两个都 not installed，`/usr/lib/systemd/system` 里也没有对应 unit，GNOME 设置里的性能模式面板是空的。Fedora 45 仓库有 `tuned 2.28.0` 与 `tuned-ppd 2.28.0`，Fedora 41 起后者的定位就是 PPD 的默认实现，所以走 `tuned-ppd` 而不是 `power-profiles-daemon`。

照搬上游 profile 不适用：上游 `piano-{power-saver,balanced,performance}` 在 `[sysfs]` 里直接写 `scaling_max_freq` 与 `3d00000.gpu/max_freq`；而本设备这些接口归 `uperf-linux` 管理，外部写入会在数秒内被覆盖重置。且 GPU 上限被改写后切回 balanced 模式无法恢复。

所以这里的 profile 只切 uperf 自己的模式，`uperfctl mode powersave|balance|performance` 与 GNOME 的三档一一对应。TuneD 的 script 插件只接受 profile 目录里的脚本，目录外的路径会被记成 error 并忽略，所以每档带一个 `apply.sh`，真正的切换逻辑在 `/usr/lib/piano/piano-power-mode`，带重试以覆盖开机时 daemon 还没起来的窗口。

设置中的电源面板呈现 Performance／Balanced／Power Saver 三档，选择后 `net.hadess.PowerProfiles` 的 `ActiveProfile`、`tuned-adm active` 与 `/run/uperf-linux/cur_powermode` 同步生效，GPU 上限保持在 1100000000。未核对：重启后的首启时序。

界面这一条要在图形会话里试。从 SSH 起 `gnome-control-center` 时，进程属于 inactive session，PPD 的 `net.hadess.PowerProfiles.switch-profile` 只对 active session 放行（策略里 `allow_inactive=no`、`allow_active=yes`），调用会被拒成 `Unauthorized`，面板却已经把单选圈画到新档位，看起来像切了实际没切。从 shell 概览里启动就不会有这个问题。

## 中文环境

`/etc/locale.conf` 是 `LANG=C.UTF-8`，`glibc-langpack-zh` 没装，连 `glibc-langpack-en` 也没有。`gsettings get org.gnome.desktop.input-sources sources` 返回 `[('xkb', 'us')]`，`ibus-libpinyin` 装了、`ibus-daemon` 也在跑，只是没人把它加进输入源。

字体已经在：`google-noto-sans-cjk-vf-fonts`、`google-noto-serif-cjk-vf-fonts`、`default-fonts-cjk-*` 都装了，`fc-list :lang=zh` 有 61 条。这一项不用动。

要补的就是三处：`glibc-langpack-zh` 进 profile、`/etc/locale.conf` 写 `LANG=zh_CN.UTF-8`、输入源默认值按上游 `defaults.ini` 的 `[('xkb', 'us'), ('ibus', 'libpinyin')]` 给。这是产品取向问题，跟着上游默认中文的话，成本只有这三处。

## mutter

Fedora 45 是 `mutter-51.1-1.fc45`。上游三条补丁里 `0001-kms-empty-gamma-bypass` 与 `0003-wayland-touch-ignore-cancelled-grab` 在 51.1 源码上 `patch --fuzz=0` 都能干净套用，进 opt-in extras 的同一个 mutter 包。

`0001` 改 `meta-kms-update.c`：`SetCrtcGamma` 接受三个空数组，`GetCrtcGamma` 在 KMS LUT 未设置时也返回空数组，CRTC 缓存会把它复制成非 NULL、零长度的 `MetaGammaLut`，重采样到硬件 LUT 大小时除以零。补丁把零长度 LUT 当作 bypass，不做重采样。补丁头写的是 48.7 基线，但 51.1 的代码路径没变，试套只差 32 行偏移。

`0003` 的提交说明写着 "Two Piano crash dumps from Mutter 51.0 show a NULL first member of MetaWaylandTouchInfo ... The same unchecked dereference is present in upstream 51.1"，改的是 `touch_can_grab_surface()` 里 `focus_surface` 为空时的解引用；51.1 的 `src/wayland/meta-wayland-touch.c:662` 就是那行。

`0002-color-no-edid-standard-profile` 在 51.1 上第 4 个 hunk 套不上，而本机 DSI-1 的 EDID 是 384 字节的合法 EDID，不落在补丁覆盖的无 EDID 场景里，不收。

默认镜像仍用 Fedora 原版 mutter；两条补丁作为 opt-in extras 发布，默认关闭、手工 `priority=1` 才生效。设备上没复现过这两个故障，所以不把补丁版塞进默认镜像，也不默认启用。

`0001-kms-empty-gamma-bypass` 针对的是 48.7 的 `SetCrtcGamma` 空数组路径。我们的 CRTC 上 `GAMMA_LUT`、`DEGAMMA_LUT`、`CTM` 都在，这条路径会不会踩到还没验。

## gdm

Fedora 45 是 `gdm-51.1.1-1.fc45`。上游 `0001-local-display-wait-for-kms-connectors` 在 Fedora 自带补丁之后 `--fuzz=0` 通过，改的是 `gdm-local-display-factory.c` 的 `udev_is_settled()`：跳过 firmware framebuffer，按 `card*-*` connector 判断显卡是否可用，等不到 connector 时 `SEAT0_GRAPHICS_CHECK_TIMEOUT` 10 秒后照常继续。默认镜像仍用 Fedora 原版 gdm；这条作为 extras 的可选包发布，只对 GNOME 档有意义。`0002-Xsession-no-ssh-agent` 改的是 ssh-agent 的启动方式，不是设备修复，不取。

## control-center

上游 `0001-about-gunyah-primary-domain` 对 Fedora 51.1 的 `cc-about-page.c` `--fuzz=0` 可套，但它按 `/proc/device-tree/hypervisor/compatible` 与 `/proc/device-tree/hypervisor/qcom,gunyah-vm/qcom,vmid` 判定主域。piano 的 DT 只有 `/hypervisor/qcom,gunyah-vm/compatible`，没有父节点的 `compatible`；设备上 `systemd-detect-virt` 返回 `none`，关于页本来就不显示虚拟化行。这个补丁在本机是空操作，不单独重建同名的 gnome-control-center。

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

```console
busctl introspect net.hadess.SensorProxy /net/hadess/SensorProxy/Compass
.HasCompass  property  b  true
```

之前的核对只 introspect 了父对象，所以写成了"没有 Compass 接口"。SSC 侧也确认有磁力计：`ssccli --sensor magnetometer` 有读数，`ssccli --sensor compass` 给出方位角。

## 构建断言

内核实际启用的配置：`CONFIG_LIRC=y`、`CONFIG_IR_SPI=m`、`CONFIG_SND_SEQUENCER=m`、`CONFIG_ARM64_PIANO_OPTIMIZE=y`、`CONFIG_LEDS_CLASS_FLASH=m`、`CONFIG_UIO_PIANO_FINGERPRINT=m`。`build-kernel.yml` 断言 20 项核心配置，防止关键驱动选项遗漏。

## 文档口径

README 与 `known-issues.md` 里红外、HDR、指纹三节还是旧的判断，和上面核对的设备状态对不上：

- 红外：设备上 `/dev/lirc0` 与 `/sys/class/rc/rc0/lirc0` 齐全，驱动正常加载：
  - `dmesg` 记录 `ir-spi registered at minor = 0, no receiver, raw IR transmitter`；
  - 驱动 `ir_spi` 与 `rc_core` 已加载，接口就绪，光学输出待物理回环验证。
- HDR：DSI-1 连接器属性已挂载 `HDR_OUTPUT_METADATA`、`Colorspace`、`max bpc`：
  - CRTC 挂载 `GAMMA_LUT`、`DEGAMMA_LUT`、`CTM`；
  - HDR 开关已在补丁版 KWin 验证通过，色深与色调映射未测，见[已知问题](known-issues.md#hdr)。
- 指纹：控制驱动与 DT 都在，"主线没有对应的控制驱动"这句话要改；正确的说法是内核与 DT 就绪、用户态还没接。

## 核对方法

设备侧只读，没改任何配置：

```sh
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

- mutter `0002-color-no-edid-standard-profile`：
  - DSI-1 的 EDID 是 384 字节合法 EDID（头为 `00 ff ff ff ff ff ff 00`），不属于补丁覆盖场景。
- 中文字体：CJK 字体已随 `google-noto-*-cjk-vf-fonts` 和 `default-fonts-cjk-*` 装上。
- GNOME 缩放默认值：自动分数缩放在这台面板上给出 2.67，可用，不需要写死。
