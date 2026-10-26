> **实验性项目。** 这个仓库面向驱动适配与硬件调试，不是可以日常使用的稳定版本。
>
> 刷写会解锁 Bootloader、改写 GPT、替换 `boot_a` 里的合体 BOOT，并重写 Linux 分区。操作不规范会让平板变砖或丢失全部数据。刷写之前，备份全部分区与个人数据。
>
> 本项目按 [MIT 许可证](LICENSE) 原样提供，作者与贡献者不承担设备损坏、数据丢失或保修失效的责任。

# Fedora 44 for Xiaomi Pad 8 Pro (piano)

[English](README.en.md) | 简体中文

[![host-check](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml/badge.svg)](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml)

在小米平板 8 Pro（代号 `piano`，高通骁龙 8 至尊版 SM8750）上原生运行 Fedora 44，默认桌面是 GNOME。引导链是 SunUEFI（EDK2），内核是 Linux 7.3 主线适配，root 文件系统用 F2FS。它与原生 HyperOS 组成双系统，合体 BOOT 里的 3 秒选择器与引导助手负责往返，两个方向都不需要电脑。

## 硬件支持

| 模块 | 状态 | 说明 |
|---|---|---|
| 显示与渲染 | ✅ | 3200×2136 @ 120Hz，Mesa 26.2.3 Turnip（Adreno 830）硬件加速 |
| 触控 | ✅ | 卡尔曼滤波、24 像素死区防抖、正交轴向锁。45 秒持续划动 97 次/秒，全屏可达，多点正常 |
| 视频编解码 | ✅ | Iris V4L2 M2M 已通：H.264 与 HEVC 各 300 帧的硬件编码与解码、带 B 帧素材的解码，逐帧摘要与软件解码一致，Iris 中断计数同步增长（1080p）。浏览器硬解已验收：Chrome 与 Firefox 默认走硬件解码，Fedora 的 Chromium 不走硬件解码，原因见[已知问题](docs/known-issues.md#触屏网络音频与相机)。VP9、10-bit 与 4K60 未实测 |
| 电源与电池 | ✅ | 双电芯电量监控，支持深度挂起 |
| 引导稳定性 | ✅ | `qbootctl.service` 写入 `Successful: 1`，避免 ABL 计数归零掉进 Fastboot |
| 相机 | ✅ | `piano-camerad` 常驻，ISP 直出 NV12，前摄与后摄都能正常成像。刚起流的前几帧是黑的，自动曝光要几帧才收敛，见[已知问题](docs/known-issues.md#触屏网络音频与相机) |
| 音频 | ✅ | 功放上限 215（-15 dB）防炸音圈，麦克风增益 +40 dB。120 秒播放无 xrun，130 秒录音全程有信号 |
| 性能调度 | ✅ | `uperf-linux` 触控提频 |
| 无线 | ✅ | Wi-Fi 6E/7、蓝牙 |
| 传感器 | ✅ | 走 SSC 后端（`libssc` + `iio-sensor-proxy-ssc`，QRTR/MHI），加速度计、环境光、接近均有数据。自动旋转实测可用，转成 `left-up` 后屏幕跟着转 90° |
| 虚拟化 | ✅ | `/dev/gunyah`、`librevm`、virtio-blk 与 virtio-net、单 vCPU 客体启动。SMP、图形与 GUI 操作待验收 |
| 双系统往返 | ✅ | 合体 BOOT 装在 `boot_a`。Linux→Android 用 `piano-next-boot android --reboot`，Android→Linux 用「SunUEFI 引导助手」模块或开机 3 秒选择器。两次往返实测，见[已知问题](docs/known-issues.md#双系统切换) |
| 手写笔 | ✅ | Type 29 协议就绪，压感 0–1，位置与倾斜正常。实测捕获 1906 条轴事件、22 条 proximity、18 条 tip，见[已知问题](docs/known-issues.md#手写笔) |
| 指纹 | ❌ | 侧置电容式，汇顶 Goodix，认证在 TEE 的 `gfpia` TA 里完成。主线没有对应的控制驱动与 libfprint 后端，短期不可用，见[已知问题](docs/known-issues.md#指纹红外与-hdr) |
| 红外 | ❌ | 消费级红外发射器（QUPv3 SE15）。主线 `ir-spi` 驱动完整，但我们这条链上 DT 缺 IR 节点、内核也没编 `LIRC`。与 TEE 无关，是可解项，见[已知问题](docs/known-issues.md#指纹红外与-hdr) |
| HDR | ❌ | 面板支持 HDR10，但 msm 没有挂 `HDR_OUTPUT_METADATA`、没有 colorop 管线，现有的 msm 静态 HDR 系列只覆盖 DP/eDP，内屏走 DSI 用不上。等上游，见[已知问题](docs/known-issues.md#指纹红外与-hdr) |

## 桌面

镜像按档位构建，同一份发布链出四档 root 文件系统。四档共用内核、ESP 与
一键脚本，只有 root 镜像与 `root-install/` 不同。

| 档位 | 会话 | 登录 | 自动旋转 | 验收状态 |
|---|---|---|---|---|
| `gnome` | GNOME 50（Workstation 环境） | GDM 自动登录 | Mutter | 已登录桌面，旋转已实机验证 |
| `kde` | KDE Plasma | plasma-login-manager 自动登录 | KWin，需 `qt6-qtsensors` | 已登录桌面，旋转已验。旋转瞬间有一次画面闪烁，见[已知问题](docs/known-issues.md) |
| `niri` | niri | greetd + tuigreet | 无，手动 `transform` | 已登录桌面 |
| `hyprland` | Hyprland（第三方 COPR） | greetd + tuigreet | 无，手动 rotate | 已登录桌面 |

每档一个归档，装哪一档就下哪一档。每档的登录、电源行为、旋转能力与已知
缺口见[桌面档](docs/desktops.md)。

## 安装

安装分两条路。手动路径是主路径，逐条给出命令，每一步都能单独核对。一键脚本执行与手动路径相同的写入，是可选路径。两条路径的完整步骤见[安装指南](docs/installation.md)。

1. 解锁 Bootloader，准备好 `fastboot`。
2. 原始 GPT 的设备先按[从原始 GPT/UFS 开始的首次安装](docs/installation.md#四-从原始-gptufs-开始的首次安装)分区、擦除 `userdata`、让 Android 首次开机。**没有这一步，一键脚本不能运行。**
3. 从 [Releases](https://github.com/FFT-infra/piano-fedora/releases) 下载所选档位的 `piano-<tag>-<档位>.sha256` 与全部分片。把它们放进同一个空目录，然后依次执行：

   ```sh
   sha256sum -c piano-<tag>-<档位>.sha256
   cat piano-<tag>-<档位>.tar.zst.part-* | zstd -dc | tar -x
   sha256sum -c SHA256SUMS
   ```

   其他档位是各自的归档，不必下载。解压后的目录就是暂存目录，一键脚本、`root-install/` 与组件 manifest 都在里面。也可以执行 `bash scripts/unpack-release.sh --tag <tag> --desktop <档位>` 让脚本代做这三步。从源码构建时，用同一次成功 `build-bundle` 的 `deploy-bundle-<档位>`，以及这次 run 派生的 `prepare-install` 的 `root-install-<档位>`。不要混用来自不同 run 的资产。
4. 在暂存目录执行 `./make-device-boot.sh`，生成 `device-boot/boot_linux.img`。`boot_linux.img` 由这台平板自己的原厂 BOOT 重打包而成，封存镜像里的 `boot_sunuefi.img` 不能顶替。
5. 关机后长按 `电源键 + 音量减键` 进入 Fastboot，确认 `fastboot getvar current-slot` 返回 `a`。然后按[手动刷写](docs/installation.md#五-手动刷写-linux)逐条执行：校验两份 `SHA256SUMS`，确认设备身份，依次写入 `boot_a`、`sunuefi_esp`、`sunuefi_root`，最后执行 `fastboot reboot`。

   要一次完成刷写，在暂存目录执行 `./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub`（Linux / macOS），或双击 `flash-linux.bat`（Windows，不自动注入公钥）。一键脚本执行与手动路径相同的写入，并在写入前额外核对几项。

6. 刷完自动重启进入所选桌面。用户名 `piano`，默认密码 `piano123456`，登录后尽快用 `passwd` 修改。

**不要擦除 `dtbo_a` 或 `dtbo_b`**，ABL 会因此拒绝整条引导链。回滚方法见[手动刷写的回滚一节](docs/installation.md#4-回滚到-android)。

### 双系统切换

- Linux → Android：应用抽屉点「返回 Android」，或执行 `piano-next-boot android --reboot`。已真机验收。
- Android → Linux：安装「SunUEFI 引导助手」模块，在模块页面选 Linux。没有引导助手时，开机 3 秒内按音量键在 3 秒选择器里选，选择会保存。
- 两个方向都写同一份 `boot_a` 请求页，不切 Slot A，也不改 GPT。

## 发布产物

Release 由维护者用 tag 手工 dispatch，tag push 不会触发发布。一个 Release 只包含同一次 `build-bundle` 与这次 run 派生的 `prepare-install` 的已验证产物，不重新编译。一个 Release 带四档桌面，每档一个归档 `piano-<tag>-<档位>.tar.zst`，超过 2 GiB 时切成 `.part-NN`。

归档里有平台镜像、所选档位的 `root-install/`、组件 manifest、一键脚本与 boot-tools。装机只下所选档位，GNOME 约 2.1 GiB，niri 约 1 GiB。归档自带的 `SHA256SUMS` 只列真实存在的文件，两条校验命令都能直接通过。逐个文件的作用见[交付物与流水线](docs/artifacts.md#release-发布)。

## 文档

- [系统架构](docs/architecture.md)：Fedora、SunUEFI 与 Android 的边界
- [桌面档](docs/desktops.md)：四档的登录、电源、旋转与验收状态
- [安装与分区](docs/installation.md)：分区计算、GPT 检查、一键与手动刷写、首次登录
- [交付物与流水线](docs/artifacts.md)：内核、RPM、initramfs 与 bundle 的组装标准
- [维护与验收](docs/internals.md)：流水线入口、产物绑定、各步的 run id、PR 门槛
- [COPR 软件源](docs/copr-repository.md)：全部 10 个包的在线升级，包括内核。回滚边界与失败模式
- [上游跟踪](docs/upstream.md)：当前 pin、漂移看板、跟进规则
- [已知问题](docs/known-issues.md)：未解决与观察中的问题
- [Gunyah 虚拟化](docs/virtualization.md)：SM8750 上的虚拟机生命周期与网络
- [公开分发前的缺口](docs/public-release-gaps.md)：构建期仍绑着这一台设备、这一个人的输入
- [参与贡献](CONTRIBUTING.md)：本地检查、提交格式与 PR 门槛
- [安全策略](SECURITY.md)：漏洞报告渠道与处理范围


## 构建与检查

```bash
make test                  # 全套离线单测：GPT 计算、镜像与元数据校验
make plan RELEASEVER=44    # 打印 Fedora 44 根文件系统生成计划
```

流水线入口是 `.github/workflows/`。主链是 `host-check`（带 `build_piano_rpms=true`）→ `build-rootfs` → `build-image` → `build-bundle` → `prepare-install`。每一步都要显式指定上游的成功 run id：`build-rootfs` 要 RPM 矩阵、运行时、Mesa 与 FFmpeg，`build-image` 再加内核，`build-bundle` 要镜像与 UEFI，`prepare-install` 要 bundle。各步的依赖不是单纯的线性接力。

每一步只接受成功 run 的产物，混用不同 run 会被拒绝。细节见 [docs/internals.md](docs/internals.md)。

## 许可

仓库自有代码遵循 [MIT](LICENSE)，第三方上游组件与内核补丁保留各自许可证，见 [sources.lock.json](sources.lock.json) 与 [NOTICE](NOTICE)。
