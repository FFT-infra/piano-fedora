> **实验性项目。** 面向驱动适配与硬件调试，不是稳定日用系统。
> 刷写会解锁 Bootloader、改写 GPT 并替换活动槽 `boot` 与 Linux 分区，请先备份全盘与个人数据。本项目按 [MIT 许可证](LICENSE) 原样提供。

# Fedora 45 for Xiaomi Pad 8 Pro (piano)

[English](README.en.md) | 简体中文

[![host-check](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml/badge.svg)](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml)

在小米平板 8 Pro（`piano`，骁龙 8 至尊版 SM8750）上运行原生 Fedora 45（F2FS 根分区，Linux 7.3 主线适配内核），通过 [SunUEFI](https://github.com/slwyts/Project-SunUEFI) 与原生 HyperOS 共存双系统。两个方向都能脱机切换。

启动链、内核打包、设备树 overlay 与部分 UEFI 补丁来自或移植自 Project SunUEFI；本仓库负责 Fedora 后端、F2FS 全链路、首次分区与相关补丁，架构边界见[系统架构](docs/architecture.md)，署名与文件级许可见 [NOTICE](NOTICE)。

## 硬件支持

状态图例：✅ 真机验证通过 | ⚠️ 部分可用（带已知限制） | ❌ 未实现或未验证

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 显示与渲染 | ✅ | 3200×2136 @ 120Hz，Mesa 26.2.7 Turnip（Adreno 830）硬件加速 |
| 触控 | ⚠️ | 内核 IRQ 线程已启用手指矩阵解码；用户态卡尔曼滤波路径保留作兜底。内核路径暂不读取原厂逐屏电极校准，跟手度与多指表现待实机长期验收 |
| 视频编解码 | ✅ | Iris V4L2 M2M 1080p H.264/HEVC 硬编硬解就绪；Chrome 与 Firefox 默认硬解，Fedora Chromium 因 `ffmpeg-free` 限制仅软解，见[已知问题](docs/known-issues.md#触屏网络音频与相机) |
| 电源与电池 | ✅ | 双电芯电量上报、深度挂起与唤醒正常 |
| 引导稳定性 | ✅ | `qbootctl.service` 上报 `Successful: 1`，避免 ABL 计数归零掉进 Fastboot |
| 相机 | ✅ | `piano-camerad` 前后摄直出 NV12 成像正常；开流首帧黑帧与 AE 收敛问题见[已知问题](docs/known-issues.md#触屏网络音频与相机) |
| 音频 | ⚠️ | 拓扑按原厂矩阵把立体声铺到四扬声器 DAP（功放上限 235）；播放录音连续无 xrun，四扬声器分频与 DAP 效果待实机验收 |
| 性能调度 | ✅ | `uperf-linux` 触控提频，并通过 `tuned-ppd` 桥接三档电源模式 |
| 无线 | ✅ | Wi-Fi 6E/7、蓝牙 |
| 传感器 | ✅ | SSC 后端（`libssc` + `iio-sensor-proxy-ssc`）提供加速度计、光感与接近数据，支持自动旋转 |
| 虚拟化 | ✅ | `/dev/gunyah`、`librevm`、virtio-blk/net 与单 vCPU 客体启动通过；SMP 与图形客体未验 |
| 双系统往返 | ✅ | 活动槽合体 BOOT 支持 Linux 与 Android 互切，细节见[双系统切换](docs/known-issues.md#双系统切换) |
| 手写笔 | ⚠️ | Type 29 压感（0-1）、坐标与倾斜正常；侧键与磁吸交互未实现，见[手写笔](docs/known-issues.md#手写笔) |
| 指纹 | ⚠️ | 预装 `piano-fingerprint` / `piano-libfprint`（Goodix TEE 路径），与 Android 模板隔离；当前版本实机录入解锁未验，见[指纹与硬件](docs/known-issues.md#指纹红外与-hdr) |
| 红外 | ✅ | `/dev/lirc0` 与 `ir-spi` 驱动就绪，可用 `ir-ctl` 发码 |
| HDR | ⚠️ | 面板与 DPU 支持 HDR10 及 BT.2020 输出，extras 的 KWin 修复退出 HDR 后背光恢复；色准与峰值亮度未用仪器测量，见[HDR 说明](docs/known-issues.md#hdr) |

## 桌面

五档桌面共用同一内核、ESP 与刷机脚本，仅 rootfs 与 `root-install/` 按档区分。各档的登录管理器、电源策略与配置细节见[桌面档](docs/desktops.md)。

| 档位 | 会话 | 登录 | 自动旋转 | 当前状态 |
| --- | --- | --- | --- | --- |
| `gnome` | GNOME 51（默认） | GDM 自动登录 | Mutter | 已登录桌面，旋转实机验证通过 |
| `kde` | KDE Plasma | `plasma-login-manager` | KWin（`qt6-qtsensors`） | 已登录桌面，旋转实机验证通过 |
| `niri` | niri | greetd + tuigreet | 无（手动 `transform`） | 已登录桌面 |
| `hyprland` | Hyprland（第三方 COPR） | greetd + tuigreet | 无（手动设置） | 已登录桌面 |
| `denial` | Denial（第三方 DNF 源） | greetd + tuigreet | 无（缺少 SensorProxy） | `0.6.10-1` 已实机冷启并验证 GPUI/Vulkan `denial-settings` |

### extras 可选组件

默认镜像使用 Fedora 原版桌面包。如需原厂自动亮度曲线、KWin HDR 背光恢复等补丁组件，可按 [COPR 软件源](docs/copr-repository.md#可选的-extras-软件源) 启用默认关闭的 `xinyang26/piano-fedora-extras`：

| 能力 | 适用档位 | 包与作用 |
| --- | --- | --- |
| 环境光自动亮度与键盘背光热插拔 | GNOME | `gnome-settings-daemon-piano` 按原厂 lux 曲线调光并刷新键盘背光 |
| X11 应用手写笔适配 | 五档 | `gtk3` 修复 XI2 工具原子查询 |
| GNOME 触摸崩溃与启动等待保护 | GNOME | `mutter` 增加空 gamma LUT / grab 守卫；`gdm` 等 KMS connector 就绪再启动；`gnome-control-center` 指纹录入等待 `fprintd` |
| HDR 关闭后恢复背光 | KDE | `kwin` 修复退出 HDR 后亮度停在最大值的问题 |

## 安装

完整流程与故障排查见[安装指南](docs/installation.md)。手动路径逐条核对命令，是主路径；一键脚本做完全相同的写入。

1. 解锁 Bootloader 并准备好电脑端 `adb` 与 `fastboot`。
2. **原始 GPT 设备必须先分区**：按[从原始 GPT/UFS 开始的首次安装](docs/installation.md#四-从原始-gptufs-开始的首次安装)划出 Linux 分区、擦除 `userdata` 并让 Android 完成首次开机。**没有这一步，一键脚本不能运行。**
3. 从 [Releases](https://github.com/FFT-infra/piano-fedora/releases) 下载所选档位的 `.sha256` 与全部分片到空目录，校验并解包（或直接运行 `bash scripts/unpack-release.sh --tag <tag> --desktop <档位>`）：

   ```sh
   sha256sum -c piano-<tag>-<档位>.sha256
   cat piano-<tag>-<档位>.tar.zst.part-* | zstd -dc | tar -x
   sha256sum -c SHA256SUMS
   ```

   从源码构建时，需配套同一次成功 `build-bundle` 的 `deploy-bundle-<档位>` 与同 run 派生的 `prepare-install` 的 `root-install-<档位>`，不要混用不同 run 的产物。
4. 在暂存目录运行 `./make-device-boot.sh`，基于平板自己的原厂 BOOT 生成 `device-boot/boot_linux.img`。
5. 关机后长按 `电源键 + 音量减键` 进入 Fastboot，确认 `fastboot getvar current-slot` 返回 `a` 或 `b`。按[手动刷写](docs/installation.md#五-手动刷写-linux)校验材料后依次写入活动槽 `boot`、`sunuefi_esp`、`sunuefi_root` 并重启；或直接运行一键脚本：

   ```sh
   ./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub   # Windows 使用 flash-linux.bat（不注入公钥）
   ```

6. 自动重启进入桌面。默认用户 `piano`，密码 `piano123456`，登录后请立即用 `passwd` 改密。

**不要擦除 `dtbo_a` 或 `dtbo_b`**，否则 ABL 会拒绝引导。回滚原厂 BOOT 见[回滚到 Android](docs/installation.md#4-回滚到-android)。

### 双系统切换

- **Linux 到 Android**：点击应用抽屉里的「返回 Android」，或执行 `piano-next-boot android --reboot`。
- **Android 到 Linux**：在 KernelSU/SukiSU 中使用「SunUEFI 引导助手」模块，或开机亮屏 3 秒内按音量键进入选择器。

装好后通过 `sudo dnf upgrade --refresh` 即可直接升级内核与适配包，无需重刷镜像，详见[升级与回滚](docs/upgrade.md)。

## 文档与发布产物

Release 由维护者手动 dispatch 生成，每档打成一个完整归档 `piano-<tag>-<档位>.tar.zst`（超 2 GiB 自动分片；GNOME 约 2.1 GiB，niri 约 1 GiB），文件构成见[交付物与流水线](docs/artifacts.md#release-发布)。

- [文档中心](docs/README.md)：按安装、日常使用、桌面、开发维护分类索引
- [构建与维护参考](docs/internals.md)：CI 流水线、真机取证脚本与 PR 验收门槛
- [参与贡献](CONTRIBUTING.md)：本地测试、补丁检查与提交流程
- [安全策略](SECURITY.md)：漏洞反馈方式

## 构建与测试

```sh
make test                  # 全套离线检查（含 GPT、镜像、补丁与文档链接检查）
make plan RELEASEVER=45    # 打印 Fedora 45 默认桌面装配计划
```

自有代码遵循 [MIT 许可证](LICENSE)，上游组件与内核补丁保留各自许可证，详情见 [sources.lock.json](sources.lock.json) 与 [NOTICE](NOTICE)。
