# 与上游的功能差距

> **说明**：本文档对照上游 [Project SunUEFI](https://github.com/slwyts/Project-SunUEFI)（首次核对 2026-11-11，当前构建 pin 见 `sources.lock.json`）及相关生态组件，汇总当前支持矩阵、架构设计分歧与基线复核指引。
>
> 表中标「完成」的桌面补丁（KDE HDR 背光恢复、夜灯 mutter gamma、自动亮度、键盘背光热插拔、GDM 重认证、GCC 指纹等待、GTK3 XI2、mutter 触摸取消）位于 `piano-fedora-extras` 可选包；默认镜像使用 Fedora 原版组件，装 extras 后对应能力才生效。

---

## 一、 功能对齐状态总表

| 项 | 当前实现 | 上游基线 | 说明 |
| --- | --- | --- | --- |
| 指纹用户态 | 部分：`piano-fingerprint` 与 `piano-libfprint` 预装并跟到上游 TEE 重写，fprintd 能枚举设备 | 补丁集把 Goodix 接进标准 libfprint | 新 TEE 路径只有初始化和枚举保证，录入与解锁未验，模板与 Android 隔离 |
| 相机闪光灯 | 完成：`capture rear --flash`，预览流同步频闪，无流时常亮补光 | 帧同步频闪 | 补光与出流正常 |
| 相机 JPEG 直出 | 完成：`piano-camera-ctl capture --output FILE` 直出 JPEG | `photo/jpeg.c` | 工具链完整 |
| 相机高像素 | 完成：后摄 8160x6120 走 RDI RAW10，Rust worker 合成 NV12 | `quad/` 与 v4l2loopback 生产者协商 | 格式协商完整，支持 8160x6120 输出 |
| 电源策略 | 完成：`tuned-ppd` 三档 profile 桥接到 uperf-linux 模式 | 上游 profile 直接写 cpufreq/devfreq | 避免频点冲突，受 uperf 保护 |
| 充电、温控与 FFC | 补丁已收：上游 `0414`–`0422` 与 `0385`–`0398` 共 11 条进封存序列 | 关机充电、皮肤温控、带认证 FFC 与停充退出 | 满电停充、温控降流与关机充电尚无实机记录 |
| 内核手指解码 | 跟进上游：`0399` 把逐帧矩阵解码移入内核 IRQ 线程（32 帧参考、中值滤波、基线漂移处理），用户态桥接在 `kernel_input: enabled` 时退出 | 同一份内核解码 | 内核解码不读原厂逐屏电极校准，上机验收未做 |
| 中文环境 | 完成：装 `glibc-langpack-zh`，`/etc/locale.conf` 设 `zh_CN.UTF-8`，默认输入法 `libpinyin` | `defaults.ini` 写 `LANG=zh_CN.UTF-8`，输入源含 `ibus`/`libpinyin` | 桌面预置中文与拼音输入 |
| mutter 触摸崩溃 | 默认采用发行版 `mutter-51.1-1.fc45`，补丁入 extras 扩展仓 | 补丁在 51.1 源码上可干净套用 | 默认镜像不合入，作为 opt-in 选项 |
| KDE 桌面默认值 | 完成：KWin 默认值与环境变量落盘，支持缩放与自然滚动 | 类似配置，另设 `QT_SCALE_FACTOR=2` | 架构分歧：避免二次放大，不设 QT 变量 |
| 罗盘 | 可用：`/net/hadess/SensorProxy/Compass` 上 `HasCompass=true` | `ssc-compass` 驱动已编入 `iio-sensor-proxy-ssc` | 接口位于 Compass 子对象 |
| 构建断言 | 完成：`build-kernel.yml` 断言 20 项，`check_kernel_config_assertions.py` 锁定 | 平台片段默认打开 IR、LIRC、SND_SEQUENCER 等 | 契约自动化守卫 |
| 硬件状态对齐 | 完成：红外发射、HDR 属性与指纹链路均已核对并对齐实现 | 各项组件零散支持 | 详见[已知问题](known-issues.md) |
| 自动亮度 | 已支持：profile 加载与背光换算，支持随环境光动态调光 | GSD 按原厂 lux 曲线调背光 | 纳入 extras 扩展仓 |
| 键盘背光热插拔 | extras 的 GSD 带 50.1 补丁，背光设备随键盘盖插拔更新 | 背光走 UPower/GNOME | 外接键盘深度验收未做 |
| KDE HDR/广色域 | 完成：extras 的 KWin 带退出 HDR 恢复背光补丁，支持正常开关 | KWin 可切换，退出 HDR 恢复背光 | 色深与色调映射精度未测，见[已知问题](known-issues.md#hdr) |
| 指纹录入 | 未验：旧 TEE 路径曾打通 `fprintd-enroll`、`fprintd-verify` 与锁屏解锁，新 TEE 路径尚无流程记录 | libfprint/fprintd 录入与认证 | 详见[已知问题](known-issues.md#指纹红外与-hdr) |
| 相机隐私灯 | 完成：前后摄视频流启停联动 `/sys/class/leds/green/brightness` | 随前后摄视频流启停 | 驱动控制链路完整 |
| 相机录像 | 完成：支持 1920x1440@30 预览与录像 | 预览、录像、AE/AF/AWB | 直连与 loopback 均正常 |
| 后摄自动对焦 | 随 18274377 换成双向 GT9764BA（`gt9764` 驱动 + `camerad` 按 ancillary link 找 LENS） | 同一颗执行器 | 新硬件上机验收未做 |
| 手写笔进阶 | 基础输入正常：位置、压感、倾斜、proximity | 捏握、震动、磁吸识别、电量、跨系统重配 | 进阶手势与按键映射未实现 |
| 普通 Focus Pen 校准 | 笔校准 INI 与上游同源，含第二支笔的 `stylus_2_*` 参数 | 同一份 BSP INI | 参数已入包，未实机验收 |
| 扬声器 DAP | 跟进上游：构建期从厂商树取 `Mi_acdb_cal.acdb`，派生 `speaker-dap-config.bin` 随 `piano-runtime` 分发；拓扑按原厂矩阵把立体声铺到四路 | 同一份 DAP 配置与矩阵 | DAP 加载与四扬声器分频未实机验收 |
| DisplayPort 音频 | topology 与 UCM 都含 `DISPLAY_PORT_RX_0`，`SectionDevice."HDMI"` 走 `hw:${CardId},1` | 同一份 BSP m4 与 UCM | 链路已接通，未实机验收 |
| exFAT/NTFS | `patches/kernel-f2fs` 已把 `EXFAT_FS`/`NTFS3_FS` 编入内核 | 文件系统读写 | 外置存储挂载未验证 |
| 10-bit 与 144 Hz | 面板与驱动支持，未量测色深与刷新率 | 面板 10bpc，144 Hz 可选 | 保持默认稳定档（KDE 120Hz，GNOME 可选 144Hz） |

---

## 二、 架构设计分歧决策

针对上游部分实现方案，本项目基于稳定性、硬件特征与系统协同考虑，作出了明确的架构分歧决策（Architectural Decisions）：

### 1. 触控矩阵滤波：跟进内核解码（0399 补丁）
* **上游做法**：`0399-LOCAL-input-nt36532e-matrix.patch` 在既有 IRQ 线程里用整数运算解码互容矩阵，做 32 帧参考、中值滤波与基线漂移处理，按标准多点触控事件上报，并保留掌托分类。
* **本项目决策**：**跟进该补丁**。序列启用后 `/proc/nvt_thp_status` 报 `kernel_input: enabled`，`piano-touch-view` 据此让用户态手指桥接退出。笔解码同样进了内核（`0431`、`0432`），从标准固件目录取逐屏校准，用户态只保留 `pen-control.py` 做 BlueZ 初始化。内核解码用写死的矩阵尺寸，不读本仓库校验并随 `xiaomi-piano-thp` 分发的原厂逐屏电极校准；这条差异与触感平滑度在真机上验收，验收前不把它写成已完成。
* **用户态路径保留的契约**：用户态桥接仍按 `BUS_SPI`、`0603:6532` 与 `piano NT36532 THP touchscreen` 上报，补报 `ABS_MT_TOUCH_MINOR`，掌托锁存时把上一帧手指以 `MT_TOOL_PALM` 重报。内核接管时该设备不创建，避免同名同 ID 的两个输入设备并存。

### 2. 电源模式调度：禁止外部直接写频点，采用 Uperf 桥接
* **上游做法**：在 TuneD profile 中直接向 `scaling_max_freq` 与 `3d00000.gpu/max_freq` 写入固定频率。
* **本项目决策**：目标机型由 `uperf-linux` 统一接管 CPU/GPU 调频。外部直接写入 sysfs 会被 uperf 定期覆盖，且 GPU 降频后切回无法自动恢复。本项目通过 `/usr/lib/piano/piano-power-mode` 将 GNOME PPD 的三档模式映射为 `uperfctl mode powersave|balance|performance`，由调频器自身完成安全调度。

### 3. 显示缩放策略：拒绝写死整数倍率与二次放大
* **上游做法**：在 `defaults.ini` 中强制指定 `scaling-factor=2`，并在环境配置中设置 `QT_SCALE_FACTOR=2`。
* **本项目决策**：平板物理尺寸为 239×163mm，Mesa 与 Mutter 自动协商出的 2.67 分数缩放更符合人体工学；在 KDE 下通过 `/etc/environment.d/60-piano-display.conf` 设置 `KWIN_DRM_FORCE_SCALING_MODE=FULL` 驱动 DRM 缩放，避免 `QT_SCALE_FACTOR` 叠加导致界面产生二次放大。

### 4. Mutter 显示补丁：排除无 EDID 适配
* **上游做法**：引入 `0002-color-no-edid-standard-profile` 补丁处理无 EDID 屏幕的回退逻辑。
* **本项目决策**：目标机型 DSI-1 面板具备合法的 384 字节标准 EDID（标头 `00 ff ff ff ff ff ff 00`），不属于无 EDID 场景，无需引入该补丁增加代码维护面。

### 5. Gunyah 虚拟机主域判定：跳过不兼容补丁
* **上游做法**：补丁 `0001-about-gunyah-primary-domain` 依赖设备树根节点的 hypervisor compatible 判定。
* **本项目决策**：目标机型设备树仅在子节点声明 Gunyah 属性，且 `systemd-detect-virt` 正常返回 `none`，关于界面无需冗余显示虚拟化标签，跳过该补丁。

---

## 三、 硬件与子系统配置索引

各硬件子系统的具体架构、运行时配置与已知边界，请查阅对应权威文档：

| 子系统 / 特性 | 核心实现与配置说明 | 缺陷、异常与技术边界 |
| --- | --- | --- |
| **桌面与显示** | [桌面档位（desktops.md）](desktops.md)（五档会话、KWin/Mutter 缩放、登录方式） | [已知问题（known-issues.md#显示与关屏）](known-issues.md#显示与关屏) |
| **HDR 与广色域** | [桌面档位（desktops.md#extras-可选组件）](desktops.md#extras-可选组件)（KWin 补丁） | [已知问题（known-issues.md#hdr）](known-issues.md#hdr)（背光恢复与色彩边界） |
| **指纹识别** | `piano-fingerprint` 与 `piano-libfprint` 软件包集 | [已知问题（known-issues.md#指纹红外与-hdr）](known-issues.md#指纹红外与-hdr)（模板隔离与鉴权） |
| **红外发射** | 内核 `ir_spi` 驱动与 `/dev/lirc0` 设备节点 | [已知问题（known-issues.md#指纹红外与-hdr）](known-issues.md#指纹红外与-hdr)（发射与发码） |
| **相机与 ISP** | `piano-camerad` 与 `piano-pipewire-gstreamer` 软件包集 | [已知问题（known-issues.md#触屏网络音频与相机）](known-issues.md#触屏网络音频与相机)（出流与收敛） |
| **双系统启动** | [升级与维护（upgrade.md#二-双系统往返）](upgrade.md#二-双系统往返)（`piano-next-boot`） | [已知问题（known-issues.md#双系统切换）](known-issues.md#双系统切换)（切槽与 GUID 约束） |
| **上游补丁流** | [上游跟踪（upstream.md）](upstream.md)（锁定源、补丁序列、自动跟踪工作流） | — |

---

## 四、 基线复核指引

在设备终端以只读方式核对当前硬件环境与上游基线状态：

```sh
# 1. 显示连接器与 CRTC 属性
modetest -M msm -c

# 2. 传感器与电源配置 D-Bus 接口
busctl introspect net.hadess.SensorProxy /net/hadess/SensorProxy
busctl get-property net.hadess.PowerProfiles /net/hadess/PowerProfiles net.hadess.PowerProfiles Profiles

# 3. 关键硬件驱动模块与内核配置
lsmod | grep -E 'ir_spi|uio_piano_fingerprint|qcomtee|leds_qcom_flash'
zcat /proc/config.gz | grep -E 'CONFIG_LIRC|CONFIG_IR_SPI|CONFIG_UIO_PIANO|CONFIG_ARM64_PIANO'

# 4. 关键适配包安装状态
rpm -q mutter libfprint fprintd tuned glibc-langpack-zh
```

上游基线对照源文件位于 `build/test-sources/sunuefi`（`a6c321d`，tag `v1.0.0`）的 `tools/build_piano_fingerprint.py`、`linux/userspace/camera/`、`linux/desktops/`、`patches/libfprint/` 与 `docs/status.md`。

---

## 五、未适配与延后

以下能力上游 v1.0.0 有、Fedora 侧暂未实现，均已登记触发条件，不按「已完成」计。另有 10-bit/144 Hz、DP 音频、外接存储、IR/RGB、充电温控等属于「代码在、设备未验收」，与本表分开记账。

| 项 | 上游做法 | Fedora 现状 | 触发与计划 |
| --- | --- | --- | --- |
| 启动项保留 | `tools/package/95-piano-boot-history.install` 在 kernel-install 后只留最新版与一套回退 | 已实现：`piano-kernel-esp-update` 写新条目后清掉更早的 piano 条目与载荷，只留当前与更新前那一代；非 piano 条目与固定 `sunuefi.efi` 不动 | 主机侧单测已覆盖；设备上验 ESP 空间与回退启动未做 |
| Mesa renderonly 高度对齐 | `patches/mesa/27.0/0001-freedreno-renderonly-height-alignment.patch`，修 2400×1602/1280×854 资源导入 | Fedora Mesa 26.2.7 只带 A830v1 chip-id | Fedora 26.3 进稳定库后测这条补丁是否适用于 26.3/27.0；适用则加进 `build-fedora-mesa.py`，不适用记 N/A |
| PipeWire v4l2/libcamera | `patches/pipewire/1.6.11/0009`–`0015` | 只发 `piano-pipewire-gstreamer`，`pipewire-libs` 用 Fedora 的 | 先在设备上验证 GNOME Snapshot / Plasma Camera 是否受影响；有具体失败再考虑替换 `pipewire-libs` 或等 Fedora |
| Droidloom | `config/droidloom.json` 与原生包，Debian 已实机 | Fedora 侧没有 droidloom/waydroid | 产品方向决策；要做就单独做 RPM 化或 Waydroid，不塞进内核 pin |
| SteamOS / Proton / FEX | SteamOS 镜像与预装 | 不复制 SteamOS 镜像 | N/A；Fedora 侧由用户自行安装 Steam/Proton/FEX，若要预装另立产品决策 |
| Denial 桌面 | `linux/desktops` 的 Denial 配方 | 仓库有 `denial` 档，但 `packages.denialwm.org` 目前只有 `fedora/44` 仓库，Fedora 45 镜像未验证 | 等 Denial 提供 `fedora/45`，或在 F45 上验证 F44 仓库与精确 pin 后再纳入发布 |
| ACPI / Windows | `uefi/components/acpi` 随产品固件编入 | UEFI 产物已带；Linux 走 DTB，不受影响 | Windows 镜像不在本仓库范围；ACPI 只随 UEFI 产物交付 |
