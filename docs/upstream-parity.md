# 与上游的功能差距

对照上游 [Project SunUEFI](https://github.com/slwyts/Project-SunUEFI)（当前构建 pin 见 `sources.lock.json`），记录 Fedora 适配与上游在各子系统的对齐现状及关键技术分叉。

表中标记为 extras 提供的桌面补丁（KDE HDR 背光恢复、自动亮度、键盘背光热插拔、GTK3 XI2、mutter 触摸/gamma 守卫、GDM connector 等待、GCC 指纹等待）位于可选仓库 `xinyang26/piano-fedora-extras`，默认镜像采用 Fedora 原版包。

## 子系统对齐状态

| 子系统 / 特性 | Fedora 实现现状 | 已知限制 / 未验部分 |
| --- | --- | --- |
| 指纹用户态 | 预装 `piano-fingerprint` + `piano-libfprint`（随上游 TEE 重写，设备枚举就绪） | 真机录入、匹配与锁屏解锁未验，模板与 Android 隔离 |
| 相机（闪光灯 / JPEG / 高像素 / 录像 / 隐私灯 / 对焦） | 前后摄录像（1920×1440@30）、后摄 8160×6120 RAW10→NV12、闪光频闪、`piano-camera-ctl` JPEG 直出、隐私灯与双向 GT9764BA 对焦已接入 | 开流首帧黑帧与 AE 收敛待优化；新对焦执行器实机长测待补 |
| 电源调度 | `tuned-ppd` 桥接到 `uperf-linux` 三档（不直接写 sysfs 频点） | 无 |
| 充电、温控与 FFC | 已合入 `0397`/`0398`/`0411`/`0414` 序列 | 满电停充、高温降流与关机充电缺长期实机数据 |
| 内核触控解码 | 跟进 `0399` / `0431` / `0432`，用户态检测到 `kernel_input: enabled` 后退出 | 内核路径不读逐屏电极校准，跟手度与多指长测待验 |
| 中文与输入法 | 预装 `glibc-langpack-zh`、`zh_CN.UTF-8` 与拼音输入法 | 无 |
| 传感器与罗盘 | `iio-sensor-proxy-ssc` + `libssc`（含 Compass 子对象） | 无 |
| 桌面环境（自动亮度 / KDE HDR / mutter / KDE 默认值） | 默认用 Fedora 原版组件，extras 提供补丁增强；KDE 默认写 `KWIN_DRM_FORCE_SCALING_MODE=FULL` 而禁用 `QT_SCALE_FACTOR` 叠加 | KDE HDR 色准/峰值亮度未测量；外接键盘背光恢复待测 |
| 红外发射 | `ir_spi` + `/dev/lirc0` 就绪 | 发射功率与波形未仪表校验 |
| 手写笔 | Type 29 压感（0-1）、坐标与倾斜可用；含第二支笔 `stylus_2_*` 校准参数 | 侧键、捏握、防误触与第二支笔真机实测未完成 |
| 扬声器 DAP / DP 音频 | 已合成 4 路 DAP 矩阵拓扑与 DP 音频路由 | DAP 实机四声道分频效果与外接 DP 音频未验收 |
| exFAT / NTFS / 非 4 行对齐显示模式 | 内核启用 `EXFAT_FS` / `NTFS3_FS`；Mesa 带 `renderonly` 高度对齐补丁 | 外置存储挂载与 2400×1602 等非常规模式未真机测 |

## 架构分歧说明

针对部分上游方案，本仓库基于 Fedora 生态与本机硬件特性采用了不同做法：

- **跟进内核触控解码并保留用户态契约**：启用 `0399` 内核矩阵解码后，用户态桥接按 `kernel_input: enabled` 自动不创建输入设备以避免冲突；但用户态仍保留 `BUS_SPI` / `0603:6532`、`ABS_MT_TOUCH_MINOR` 与 `MT_TOOL_PALM` 上报逻辑作兜底。
- **电源策略走 Uperf 桥接而非写 sysfs**：直接在 TuneD profile 修改 `scaling_max_freq` / GPU 频率会被 `uperf-linux` 定时覆盖并可能锁死 GPU 频率，故通过 `/usr/lib/piano/piano-power-mode` 将 PPD 映射给 `uperfctl`。
- **允许分数缩放并避免 KDE 二次放大**：屏幕物理尺寸为 239×163 mm，GNOME 默认使用 Mesa/Mutter 自动协商的 2.67 倍分数缩放；KDE 仅启用 `KWIN_DRM_FORCE_SCALING_MODE=FULL` 而拒绝同时设 `QT_SCALE_FACTOR=2`。
- **跳过不需要的上游补丁**：面板拥有规范 384 字节 EDID（不引入 `0002-color-no-edid-standard-profile`）；设备树只在子节点声明 Gunyah，无须引入依赖根节点的 `0001-about-gunyah-primary-domain` 关于页补丁。

## 基线自查命令与暂不跟进项

在设备终端只读对比硬件与上游状态（上游基线对应 `build/test-sources/sunuefi` 的 `linux/desktops/`、`linux/userspace/camera/`、`patches/libfprint/` 与 `docs/status.md`）：

```sh
modetest -M msm -c
busctl introspect net.hadess.SensorProxy /net/hadess/SensorProxy
busctl get-property net.hadess.PowerProfiles /net/hadess/PowerProfiles net.hadess.PowerProfiles Profiles
lsmod | grep -E 'ir_spi|uio_piano_fingerprint|qcomtee|leds_qcom_flash'
zcat /proc/config.gz | grep -E 'CONFIG_LIRC|CONFIG_IR_SPI|CONFIG_UIO_PIANO|CONFIG_ARM64_PIANO'
rpm -q mutter libfprint fprintd tuned glibc-langpack-zh
```

暂未跟进的上游项目：

| 项 | 上游形态 | Fedora 现状与重评条件 |
| --- | --- | --- |
| PipeWire v4l2/libcamera 补丁 | `patches/pipewire/1.6.11` 另含 7 条补丁 | 当前仅重编 `piano-pipewire-gstreamer`，其余用官方 PipeWire；仅当相机应用明确失败时跟进 |
| FFmpeg 9.1 | `patches/ffmpeg/9.1/series.json` | Fedora 45 当前为 FFmpeg 9.0.2，待 Fedora 升级 9.1 后跟进 |
| ACPI 与 Windows 支持 | 固件含 `uefi/components/acpi` | 产物附带 ACPI 表，Fedora 仍只使用 DTB |
| Qt Multimedia 录像与 Plasma Camera | `patches/qt6-multimedia/6.11.2` 让 MP4/MOV 录像先试 V4L2 H.264 编码器；`patches/plasma-camera/26.08.3` 带 libcamera 0.8 适配与 `record-captured-frames` 两条补丁。两者由上游重编后装进它的桌面镜像 | 不重编也不预装，Qt Multimedia 用 Fedora 原版；相机走 `piano-camerad` 与 GNOME Snapshot。镜像要带 Qt 相机应用录像时跟进 |
| IBus 候选窗焦点 | `patches/ibus/1.5.34`：KWin 下候选窗抢走 Xwayland 输入框的键盘焦点，预编辑被清空，敲一个词只上屏一个空格；上游只在 SteamOS 镜像重编 | 用 Fedora 原版 IBus；KDE 档在 Xwayland 应用里输入中文没有在设备上试过，复现后跟进 |
| Denial 方向传感器 | `patches/denial/0.6.8` 让 `deniald` 优先读 SensorProxy，没有加速度计时再读 steinerd；上游带这条补丁从源码构建 Denial | 直接装 `packages.denialwm.org` 的二进制包，不带这条补丁，`denial` 档没有自动旋转，见[桌面档](desktops.md#自动旋转)；Denial 接入 SensorProxy 或本仓库改成自行构建后重评 |
| SteamOS 与 Nyarch 专属补丁 | `patches/gamescope`、`patches/steamos-manager`、`patches/webrtc-audio-processing`（SteamOS 镜像），`patches/blur-my-shell`、`patches/material-you-colors`（Nyarch 桌面） | 没有对应的镜像与桌面，不跟 |
