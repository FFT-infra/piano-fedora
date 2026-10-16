# ⚠️ 实验性移植仓库（风险告知与免责声明）

> **【重要警告】**
>
> 1. 本项目为**早期高度实验性的主线 Linux 移植工程**，主要面向底层驱动逆向、硬件适配与极客开发者，**绝非面向普通消费者的日用稳定版本**；
> 2. 刷入第三方 Linux 系统涉及**解锁 Bootloader、改写底层 GUID 分区表（GPT）、擦写高通 A/B 槽位及设备启动链**。任何不规范的操作均可能导致设备变砖、硬件永久损坏或用户数据完全丢失；
> 3. 在尝试构建或刷入本项目前，请务必**自行充分评估技术风险**，并提前使用专业工具完整备份设备所有物理 LUN 分区及个人重要数据；
> 4. 项目开发者与贡献者不对任何因使用本项目而造成的设备硬件损毁、数据丢失或保修失效承担任何连带或直接责任。

---

# Fedora 44 for Xiaomi Pad 8 Pro (piano)

原生运行在**小米平板 8 Pro（代号 `piano` / 搭载高通骁龙 8 至尊版 SM8750）**上的 Fedora 44 (Rawhide) + GNOME 桌面系统。基于 Linux 7.2 主线适配内核与 SunUEFI 引导架构，支持与原厂 HyperOS (Android) 免电脑双系统无缝往返，Linux 根文件系统采用闪存优化的高性能 F2FS。

---

## 硬件支持状态

| 模块 | 状态 | 详细说明 |
|---|---|---|
| **显示与渲染** | ✅ 完美 | 3200×2136 @ 120Hz，Mesa 26.2.3 Turnip (Adreno 830) 原生硬件加速，锁屏与息屏无撕裂或花屏 |
| **触控交互** | ✅ 完美 | 引入自适应卡尔曼时域滤波、24 像素死区防抖与正交轴向锁，校准物理 DPI，消除高频抖动 |
| **视频编解码** | ✅ 完美 | 打通 Iris V4L2 M2M VA-API 硬件解码（支持 H.264、HEVC 8/10-bit、VP9 4K 60fps）；支持强制关键帧硬件编码 |
| **电源与电池** | ✅ 完美 | 屏蔽 `/hypervisor` 节点以恢复 GNOME 原生双电芯电量监控与电源管理，支持深度挂起（Deep Sleep） |
| **引导稳定性** | ✅ 完美 | 部署官方 `qbootctl.service` 自动写入 `Successful: 1`，彻底根除 ABL 计数归零掉入 Fastboot 的缺陷 |
| **相机中继** | ✅ 良好 | `piano-camerad` 后台常驻，硬件 ISP 直出 NV12 格式（CPU 占用约 4%），配合 Mesa Turnip 修复彻底消除斜条纹 |
| **音频与声学** | ✅ 良好 | 内核级硬件限制 FourSemi 功放上限为 215 (-15dB) 物理防炸音圈，麦克风硬件增益补偿 +40dB |
| **性能调度** | ✅ 良好 | `uperf-linux` 常驻后台实现触控即时提频，滑动后平滑回落，极大提升桌面操作跟手度 |
| **无线连接** | ✅ 良好 | Wi-Fi 6E/7 正常联网连接，蓝牙控制器正常激活与扫描 |
| **传感器** | ✅ 良好 | `iio-sensor-proxy` 正常采集加速度计、环境光、接近与罗盘传感器数据 |
| **虚拟化** | ✅ 良好 | 正式内核开放 `/dev/gunyah`，`librevm` 常驻后台，支持 2MB 大页与 virtio-net 网络桥接 |
| **双系统往返** | ✅ 良好 | Linux 端 `piano-next-boot` 工具已对齐引导槽位，Android 端配合 SukiSU 模块实现免电脑一键切换 |
| **手写笔** | 🟡 就绪 | 触控笔守护服务与 Type 29 协议栈已就绪，驱动节点常驻监听，等待实物划线终验 |
| **指纹 / IR / HDR**| ❌ 不支持 | 无对应硬件节点或深度依赖闭源 TEE 专有固件，开源主线明确不支持 |

---

## 快速安装指南

### 前置准备
1. 小米平板 8 Pro 已通过官方渠道解锁 Bootloader；
2. 电脑已配置好 `fastboot` 与 `adb` 命令行工具环境变量；
3. 从 [GitHub Releases](https://github.com/FFT-infra/piano-fedora-test/releases) 下载最新版本的完整镜像包与刷机工具。

### 三步极速刷机
1. **进入 Fastboot 模式**：
   将平板彻底关机，长按 `电源键 + 音量减键` 直至屏幕亮起并显示米兔/FASTBOOT 标志，使用 USB 数据线连接电脑。
2. **执行一键刷写**：
   - **Linux / macOS**：
     ```bash
     chmod +x flash-linux.sh
     ./flash-linux.sh
     ```
   - **Windows**：
     双击运行 `flash-linux.bat`。
3. **开机与登录**：
   刷写完成后平板将自动重启并进入 Fedora 44 GNOME 桌面：
   - 默认桌面用户名：`piano`
   - 默认用户密码：`piano123456`（初次登入后建议通过 `passwd` 命令及时修改）

> 不想用一键脚本、或需要自己控制每一步时，按
> [手动刷机章节](docs/installation.md#三之二-手动刷机不使用一键脚本) 逐条执行。
> 手动流程列出了 Release 中真正需要哪些资产、每条 `fastboot` 命令、SHA256
> 校验方式、读回验证与回滚方法。**不要**擦除 `dtbo_b`：高通 ABL 会因此拒绝
> 整条引导链并把平板卡在 Fastboot。

---

## 双系统免电脑无缝切换

- **从 Linux 切换回 Android**：
  在 GNOME 应用抽屉点击**「返回 Android」**（或在终端执行 `piano-next-boot android --reboot`），系统将自动保存下步启动目标并重启进入 HyperOS。
- **从 Android 切换回 Linux**：
  在 Android 系统中安装并启用配套的 `piano-switch-ksu` 模块，在下拉控制中心点击快捷磁贴即可一键重启进入 Fedora。
- **开机引导菜单**：
  开机前 3 秒内按音量键可直接唤出 SunUEFI 引导菜单，亦可在桌面点击**「SunUEFI 启动选择菜单」**进入。

---

## GitHub Release 资产清单

正式版本发布时，Releases 页面将提供以下经过完整真机复核的开箱即用交付物：

资产文件名以 `scripts/package-release-bundle.sh` 的实际输出为准（不带版本后缀）：

| 文件名 | 类型 | 说明 |
|---|---|---|
| `pianoroot-fedora44-gnome-f2fs.img.zst` | 根分区镜像 | 预装全套优化服务与驱动的 Fedora 44 根文件系统（Zstandard 高压缩） |
| `sunuefi_esp.img` | 引导分区镜像 | 包含 SunUEFI EDK2、DTB、Linux 7.2.9 正式内核与 Initramfs 的完整 ESP 镜像 |
| `boot_sunuefi.img` | BOOT 引导头 | 适配 ABL 链式引导的合体引导映像 |
| `piano-switch-ksu-<日期>.zip` | Android 模块 | KernelSU / SukiSU 专用免电脑切换回 Linux 模块 |
| `piano-installer-<日期>.zip` | 一键刷机脚本包 | 内含 `flash-linux.sh`（Shell）、`flash-linux.bat`（Windows 批处理）与 `README-INSTALL.txt` |
| `SHA256SUMS` | 校验哈希 | 各资产文件的 SHA-256 哈希校验列表 |

> **发布状态**：仓库当前还没有任何 Release 与 tag，上面的 Releases 链接暂时
> 指向空列表。发布前请先在本地跑 `./scripts/package-release-bundle.sh` 生成资产
> 并打 tag；在此之前请使用仓库内 `out/release-bundle/` 的产物。

---

## 开发者与架构文档

深入的技术设计、全链路构建脚本与单测验证规范统一整理在 `docs/` 目录下：

- **[系统架构与分层设计](docs/architecture.md)**：Fedora 44、SunUEFI 与 Android 的系统边界定义；
- **[首次安装与已有分区更新](docs/installation.md)**：详细的分区结构计算、GPT 安全检查与增量部署契约；
- **[交付物与构建流水线](docs/artifacts.md)**：Kernel、RPM、Initramfs 与最终 Bundle 的组装标准；
- **[RPM 架构重构与真机验收路线](docs/pr-rebuild-roadmap.md)**：两层解耦构建流水线与纯净实机盲测规划；
- **[真机验收与稳定性记录](docs/device-acceptance-20261015.md)**：DPU 关屏修复、触控滤波实测、VA-API 与 A/B 槽位引导保护根因；
- **[全网上游同步与追溯](docs/upstream-sync.md)**：对标上游仓库（SunUEFI、yzddmr6、code002-2）的同步记录；
- **[Gunyah 虚拟化架构](docs/virtualization.md)**：SM8750 上的 Gunyah 虚拟机生命周期、大页配置与网络桥接。

## 本地检查与构建

```bash
make test                  # 运行全套 40 项单测（离线 GPT 计算、镜像与元数据校验）
make plan RELEASEVER=44    # 打印 Fedora 44 根文件系统生成计划
```

## 许可协议

- 本仓库自有代码遵循 [MIT 许可证](LICENSE)；
- 引用的第三方上游组件与内核补丁各自保留原许可证，详见 [sources.lock.json](sources.lock.json)。
