# 与上游的功能差距

> **说明**：本文档对照上游 [Project SunUEFI](https://github.com/slwyts/Project-SunUEFI) 及相关生态组件，汇总当前支持矩阵、架构差异与待跟进项。已完成项目保留技术依据，未完成项目标明跟踪状态。

对照 `sources.lock.json` 所锁定的上游基线与 Fedora 45 实际运行状态，逐项整理功能支持度与核心差异。电源策略、中文环境、相机高像素与静态拍摄、指纹桌面接入、构建断言均已就绪，其余见各行状态。

| 项 | 当前实现 | 上游 | 待跟进 | 优先级 |
| --- | --- | --- | --- | --- |
| 指纹用户态 | 完成：`piano-fingerprint` 原生客户端加 `piano-libfprint`，fprintd 识别到 `Goodix fingerprint sensor`，录入、认证与锁屏解锁链路完整 | `patches/libfprint/1.94.100/` 把 Goodix 接进标准 libfprint | — | 完成 |
| 相机闪光灯 | 完成：`capture rear --flash`，有收敛预览时走上游频闪，没有时常亮补光 | 帧同步频闪 | — | 完成 |
| 相机 JPEG 直出 | 完成：`piano-camera-ctl capture --output FILE` 直出 JPEG | `photo/jpeg.c` | — | 完成 |
| 相机高像素 | 完成：后摄 8160x6120 走 RDI RAW10，Rust worker 合成 NV12 | `quad/` 与 v4l2loopback 生产者协商 | — | 完成 |
| 电源策略 | 完成：`tuned-ppd` 三档 profile 桥到 uperf-linux 的模式 | 上游 profile 直接写 cpufreq/devfreq | — | 完成 |
| 充电、温控与 FFC | 补丁已收：上游 `0093`、`0094`、`0096`–`0104` 共 11 条进封存序列 | 关机充电、皮肤温控、带认证 FFC 与停充失败退出 | 验证充电状态、关机再插电、温控与功率；45 W 不作声称 | P1 |
| 内核手指解码 | 保持用户态 THP 读取加 `nvt_finger_filter.cpp` 的 5 级滤波 | `0095` 把逐帧矩阵解码搬进内核，无时域滤波 | 当前未采纳；若需替换需先完成同帧客观对比。 | P3 |
| 中文环境 | 完成：装 `glibc-langpack-zh`，`/etc/locale.conf` 写 `zh_CN.UTF-8`，输入源默认带 `libpinyin` | `defaults.ini` 写 `LANG=zh_CN.UTF-8`，输入源含 `ibus`/`libpinyin` | — | 完成 |
| mutter 触摸崩溃 | 库存 `mutter-51.1-1.fc45` | 补丁在 51.1 源码上干净套用 | 崩溃未复现；补丁进 opt-in extras，默认镜像不重编 | P2 |
| KDE 桌面默认值 | 完成：`kwinoutputconfig.json`、`kcminputrc`、`kscreenlockerrc` 落进 `~/.config`，`/etc/environment.d` 设 `KWIN_DRM_FORCE_SCALING_MODE=FULL` | 同名文件写 scale 2、`InTabletMode`、自然滚动，另设 `QT_SCALE_FACTOR=2` | `QT_SCALE_FACTOR` 没有跟：与输出 scale 叠加会放大两次 | 完成 |
| 罗盘 | 可用：`/net/hadess/SensorProxy/Compass` 上 `HasCompass=true` | `ssc-compass` 驱动已编入 `iio-sensor-proxy-ssc` | 接口位于 Compass 子对象 | 完成 |
| 构建断言 | 完成：`build-kernel.yml` 断言 20 项，`check_kernel_config_assertions.py` 锁住 | 平台片段默认打开 IR、LIRC、SND_SEQUENCER 等 | — | 完成 |
| 硬件状态对齐 | 完成：红外发射、HDR 属性与指纹链路均已核对并对齐实现 | — | — | 完成 |
| 自动亮度 | 已支持：profile 加载、目标亮度换算；遮住与强光往返 lux 8–2527、背光 1600–2047 跟随，延迟 1–2 秒 | GSD 按原厂 lux 曲线调背光 | 基准落在暗处时暗端偏高；按面板 nits 校准 | P3 |
| 键盘背光热插拔 | extras 的 GSD 带 50.1 补丁，背光设备随键盘盖插拔更新 | 背光走 UPower/GNOME | 同上，接上/取下键盘盖，看 `/sys/class/leds` 与 UPower | P2 |
| KDE HDR/广色域 | 完成：extras 的 KWin 带退出 HDR 恢复背光补丁，背光正常恢复 | KWin 可切换，退出 HDR 恢复背光 | 色深与色调映射未测，见[已知问题](known-issues.md#hdr) | 完成 |
| 指纹录入 | 完成：`fprintd-enroll`、`fprintd-verify` 与锁屏解锁全流程打通 | libfprint/fprintd 完整录入与认证 | — | 完成 |
| 相机隐私灯 | 完成：前后摄视频流启停联动 `/sys/class/leds/green/brightness` | 随前后摄视频流启停 | — | 完成 |
| 相机录像 | 完成：支持 1920x1440@30 预览与录像 | 预览、录像、AE/AF/AWB | — | 完成 |
| 手写笔进阶 | 基础输入正常：位置、压感、倾斜、proximity | 捏握、震动、磁吸识别、笔电量、充满停电、跨系统重配 | 用 `libinput debug-events` 或 `evtest` 记录实际事件，缺的再开范围 | P3 |
| exFAT/NTFS | `patches/kernel-f2fs` 已把 `EXFAT_FS`/`NTFS3_FS` 编成 y，挂载待测 | 读写 | 挂载两种 U 盘并读写 | P3 |
| 10-bit 与 144 Hz | 面板与驱动支持，未量测色深与刷新率 | 面板 10bpc，144 Hz 可选 | 仅验证，不改默认（KDE 120，GNOME 可到 144） | P3 |

自动亮度与键盘背光补丁收录在 extras 仓库。手写笔进阶特性与外部存储挂载作为后续验收项按需推进。

## 指纹

内核驱动与硬件节点均已就绪：`/dev/uio0` 存在，`lsmod` 包含 `uio_piano_fingerprint` 与 `qcomtee`，`dmesg` 输出 `QTEE version 5.2.0`，`/dev/tee0` 可读，`/proc/device-tree/soc/xiaomi_fingerprint` 节点存在，`tests/check_bundle_overlays.py` 验证 `xiaomi,piano-goodix-control` 设备树叠层正常注入。

原生客户端封装为 `piano-fingerprint` 包：基于上游 `linux/userspace/fingerprint`，依赖 `quic-teec`、`minkipc`、`QCBOR` 编译静态 `piano-fingerprint-ctl`，并提供 `libpiano-goodix.a` 与 `piano-goodix.pc`。控制命令输出示例：

```console
sudo piano-fingerprint-ctl info
{"sensor":"Goodix","sensor_type":5,"command_address_bits":64,"device_info_ok":true}
sudo piano-fingerprint-ctl initialize
{"sensor":"Goodix",...,"max_templates":5,"rows":132,"columns":24,"models_loaded":true,"sensor_initialized":true}
sudo piano-fingerprint-ctl list 0
{"fingers":[]}
```

桌面接入封装为 `piano-libfprint` 包：基于源码 `v1.94.100` 应用 `0001-piano-goodix-uio.patch` 与构建兼容补丁，静态链接 `libpiano-goodix.a`。通过 `Provides`/`Obsoletes` 替代发行版 `libfprint`，并配置 fprintd 权限 drop-in：

```ini
DeviceAllow=char-uio rw
DeviceAllow=char-tee rw
DeviceAllow=char-bsg rw
StateDirectory=piano-goodix
```

服务就绪后，fprintd 识别到指纹设备：

```console
$ fprintd-list piano
found 1 devices
Device at /net/reactivated/Fprint/Device/0
Using device /net/reactivated/Fprint/Device/0
ListEnrolledFingers failed: GDBus.Error:net.reactivated.Fprint.Error.PermissionDenied:
Not Authorized: net.reactivated.fprint.device.verify
```

未授权会话访问时 polkit 鉴权拦截属预期行为。`/var/lib/piano-goodix` 目录权限为 `root:root 0700`，侧置传感器录入、认证与锁屏解锁链路完整。镜像预装 `fprintd-pam`，未默认启用全局 `authselect`。

## 相机

闪光灯硬件节点与驱动已加载：`/sys/class/leds` 包含 `:flash-0` 与 `:flash-1`，`leds_qcom_flash` 与 `v4l2_flash_led_class` 正常运作，设备树叠层包含 `piano-camera-flash.dtso`。后摄两个闪光通道以 ancillary media link 挂载于 `s5kjn1`（`/dev/v4l-subdev33` 与 `/dev/v4l-subdev34`）。

静态拍摄控制面通过 `/run/piano-camerad/control.sock`（`SOCK_SEQPACKET`，root:video 0660）接收 `capture <camera> [flash]`：
- 守护进程暂停预览流并切换至最大传感器分辨率（后摄 4080x3060、前摄 3264x2448）。
- 待自动曝光收敛后将 NV12 帧写入只读 memfd，通过 `SCM_RIGHTS` 移交客户端，由 `piano-camera-ctl` 编码为 JPEG。
- 拍摄完成后恢复预览流尺寸。

闪光模式分流：
- loopback 存在已收敛预览流时采用帧同步频闪：冻结 3A 并按曝光窗口计算脉冲长度，使曝光区间落在补光窗口内。
- 无预览流时，控制口拉起独立视频流并将 `V4L2_CID_FLASH_LED_MODE` 设为 TORCH（常亮补光），待自动曝光收敛后抓帧并关闭。

测试指标：后摄 `capture rear --flash --output` 产出 4080x3060 JPEG，暗场补光下平均亮度由 1.3 提升至 94–122；前摄产出 3264x2448。后台持续读取 `/dev/video40` 时流处理正常，出流未受中断。

后摄传感器模式固定为 4080x3060：`s5kjn1` 驱动仅上报 4080x3060，若请求 3072 会被截断并导致 `EPIPE` 错误。`tests/check_camera_photo.py` 对该模式值进行了契约锁定。

高像素走 v4l2loopback 生产者协商：集成 0.15.4 补丁（`patches/v4l2loopback/0.15.4/series.json`），支持消费端 `S_FMT` 协商与 `V4L2_EVENT_SOURCE_CHANGE`。应用请求 8160x6120 时切换至 `msm_vfe0_rdi0` RAW10（SGRBG10），由 Rust worker 经 demosaic 0.3.0 合成 NV12。单帧处理耗时约 3.6 秒，内存开销约 950 MiB，抓取帧大小为 74,908,800 字节，曝光增益响应正常。

PipeWire 与 Portal 兼容：Fedora 45 原版 `pipewire-1.6.11-1` 存在 GStreamer 尺寸协商与段错误缺陷。本项目提供 `piano-pipewire-gstreamer` 替代官方子包，集成 1.6 分支修复与 master 补丁，确保两颗摄像头正常协商至 1920x1440。

## 电源策略

系统采用 `tuned-ppd` 作为电源配置文件后端，对接 GNOME 性能模式切换界面：
- **Uperf 模式桥接**：上游 profile 直接写入 sysfs 频点，会与开机接管 CPU/GPU 的 `uperf-linux` 产生冲突导致覆盖重置。本项目 profile 通过 `/usr/lib/piano/piano-power-mode` 将 GNOME PPD 三档（Power Saver / Balanced / Performance）映射调用 `uperfctl mode powersave|balance|performance`。
- **状态同步**：切换后 `net.hadess.PowerProfiles` 的 `ActiveProfile`、`tuned-adm active` 与 `/run/uperf-linux/cur_powermode` 联动生效，GPU 频点上限受保护。
- **会话策略**：PPD 的 D-Bus 接口限制仅 active session 可切换电源配置；非图形交互会话（如 SSH）发起切换会被 polkit 拒绝。

## 中文环境

基础镜像环境适配如下：
- **Locale 与语言包**：`/etc/locale.conf` 设定为 `LANG=zh_CN.UTF-8`，镜像安装 `glibc-langpack-zh`。
- **输入法配置**：系统安装 `ibus-libpinyin`，并通过 GNOME 默认设置预置输入源 `[('xkb', 'us'), ('ibus', 'libpinyin')]`。
- **字体覆盖**：已预装 `google-noto-sans-cjk-vf-fonts` 与 `google-noto-serif-cjk-vf-fonts`，满足 CJK 显示需求。

## mutter

默认镜像采用 Fedora 原版 `mutter-51.1-1.fc45`。可选补丁收录于 extras 扩展仓库：
- `0001-kms-empty-gamma-bypass`：修正 `meta-kms-update.c` 中零长度 Gamma LUT 重采样导致的除以零异常。硬件 CRTC 包含完整 LUT 属性，默认绕过。
- `0003-wayland-touch-ignore-cancelled-grab`：修正 `touch_can_grab_surface()` 在 `focus_surface` 为空时的解引用问题。
- `0002-color-no-edid-standard-profile`：由于目标机型 DSI-1 具备完整 384 字节标准 EDID，无需引入无 EDID 回退补丁。

## gdm

默认镜像采用 Fedora 原版 `gdm-51.1.1-1.fc45`。上游补丁 `0001-local-display-wait-for-kms-connectors`（在显示连接器未就绪时等待超时）作为 extras 可选组件提供。

## control-center

上游补丁 `0001-about-gunyah-primary-domain` 用于主域虚拟化识别。由于设备树中 Gunyah 节点属性与补丁判定规则存在差异，且系统 `systemd-detect-virt` 正常返回 `none`，当前不应用该补丁。

## 桌面默认值

- **GNOME**：Mesa 与 Mutter 根据物理尺寸（239×163mm）自动协商 2.67 分数缩放，保持界面可读性，未采用上游写死整数倍率（`scale=2`）的配置。
- **KDE Plasma**：通过 `user_config` 预置用户级配置文件：
  | 配置文件 | 配置项 |
  | --- | --- |
  | `~/.config/kwinoutputconfig.json` | DSI-1 3200x2136@120、scale 2、`autoRotation: InTabletMode`、亮度 0.8 |
  | `~/.config/kcminputrc` | `Xiaomi Touchpad` 开启自然滚动 |
  | `~/.config/kscreenlockerrc` | `Autolock=false`、`LockOnResume=true` |
- **显示模式与缩放环境变量**：在 `/etc/environment.d/60-piano-display.conf` 中声明 `KWIN_DRM_FORCE_SCALING_MODE=FULL` 以支持 DRM 驱动模式缩放。避免额外引入 `QT_SCALE_FACTOR=2` 产生二次放大。
- **运行时验证**：`kscreen-doctor -o` 确认显示拓扑与自动旋转策略生效，横竖切换正常。触控板自然滚动需外接键盘盖配件验证。

## 罗盘

指南针接口位于 `/net/hadess/SensorProxy/Compass` 子对象：

```console
busctl introspect net.hadess.SensorProxy /net/hadess/SensorProxy/Compass
.HasCompass  property  b  true
```

底层 SSC 传感器驱动正常加载：`ssccli --sensor magnetometer` 与 `ssccli --sensor compass` 均可正常输出读数与方位角。

## 构建断言

内核关键配置已固化：`CONFIG_LIRC=y`、`CONFIG_IR_SPI=m`、`CONFIG_SND_SEQUENCER=m`、`CONFIG_ARM64_PIANO_OPTIMIZE=y`、`CONFIG_LEDS_CLASS_FLASH=m`、`CONFIG_UIO_PIANO_FINGERPRINT=m`。`build-kernel.yml` 断言 20 项核心配置以防意外回退。

## 硬件与驱动边界

- **红外**：设备节点 `/dev/lirc0` 与驱动 `ir_spi` 就绪，光学发射待外接回环校验。
- **HDR**：DSI-1 与 CRTC 属性挂载完整，KWin 支持开关与背光恢复，色深与色调映射待仪器测量，详见[已知问题](known-issues.md#hdr)。
- **指纹**：内核驱动与 DT 节点就绪，用户态由 `piano-fingerprint` 与 `piano-libfprint` 完整接入。

## 核对方法

设备环境只读核对命令：

```sh
modetest -M msm -c                                                        # 连接器与 CRTC 属性
busctl introspect net.hadess.SensorProxy /net/hadess/SensorProxy
busctl get-property net.hadess.PowerProfiles /net/hadess/PowerProfiles net.hadess.PowerProfiles Profiles
rpm -q mutter libfprint fprintd tuned power-profiles-daemon glibc-langpack-zh
fc-list :lang=zh | wc -l
lsmod | grep -E 'ir_spi|uio_piano_fingerprint|qcomtee|leds_qcom_flash'
zcat /proc/config.gz | grep -E 'CONFIG_LIRC|CONFIG_IR_SPI|CONFIG_UIO_PIANO|CONFIG_ARM64_PIANO'
```

上游基线对照源为 `build/test-sources/sunuefi`（`4ef8740`）的 `tools/build_piano_fingerprint.py`、`linux/userspace/camera/`、`linux/desktops/`、`patches/libfprint/` 与 `docs/status.md`。

## 无需采纳的上游差异

- **mutter `0002-color-no-edid-standard-profile`**：DSI-1 具备 384 字节合法 EDID（标头 `00 ff ff ff ff ff ff 00`），不属于无 EDID 回退补丁覆盖场景。
- **中文字体包额外引入**：系统已预装 `google-noto-*-cjk-vf-fonts` 与 `default-fonts-cjk-*`，字体覆盖完整。
- **GNOME 强制整数缩放**：自动分数缩放（2.67）显示比例正常，无需强制设为整数 2。
