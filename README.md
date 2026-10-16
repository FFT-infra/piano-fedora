# Fedora 44 for Xiaomi Pad 8 Pro (piano)

原生运行在**小米平板 8 Pro（piano / 骁龙 8 至尊版 SM8750）**上的 Fedora 44 (Rawhide) + GNOME 桌面系统。基于 Linux 7.2 主线适配内核与 SunUEFI 引导，支持与原厂 HyperOS (Android) 免电脑双系统无缝往返，根文件系统采用高性能 F2FS。

---

## 硬件支持状态

| 模块 | 状态 | 详细说明 |
|---|---|---|
| **显示与渲染** | ✅ 完美 | 3200×2136 @ 120Hz，Mesa 26.2.3 Turnip (Adreno 830) 原生硬件加速，锁屏/息屏无撕裂花屏 |
| **触控交互** | ✅ 完美 | 自适应卡尔曼时域滤波 + 24 像素死区防抖 + 轴向锁，DPI/物理尺寸校准，拖窗丝滑平顺 |
| **视频编解码** | ✅ 完美 | Iris V4L2 M2M VA-API 硬件解码打通（H.264/HEVC 8/10-bit/VP9 4K 60fps）；强制关键帧硬件编码 |
| **电源与电池** | ✅ 完美 | 屏蔽 `/hypervisor` 节点，GNOME 原生双电芯电量监控恢复，支持深度休眠（deep sleep） |
| **引导稳定性** | ✅ 完美 | 部署官方 `qbootctl.service` 自动写入 `Successful: 1`，彻底根除 ABL 计数归零掉入 Fastboot 缺陷 |
| **相机中继** | ✅ 良好 | `piano-camerad` 后台常驻，硬件 ISP 直出 NV12（CPU 仅占 4%），Mesa Turnip 行偏移修复无斜条纹 |
| **音频与声学** | ✅ 良好 | 内核级 FourSemi 功放上限锁定 215 (-15dB) 物理防炸音圈，麦克风硬件增益补偿 +40dB |
| **性能调度** | ✅ 良好 | `uperf-linux` 常驻服务毫秒级响应触控提频，滑动后平滑回落，极大提升桌面跟手度 |
| **无线连接** | ✅ 良好 | Wi-Fi 6E/7 正常联网连接，蓝牙控制器正常激活工作 |
| **传感器** | ✅ 良好 | iio-sensor-proxy 正常采集加速度计、环境光、接近与罗盘传感器 |
| **虚拟化** | ✅ 良好 | 正式内核开启 `/dev/gunyah`，`librevm` 常驻，支持 2MB 大页与 virtio-net 网络桥接 |
| **双系统往返** | ✅ 良好 | Linux 端 `piano-next-boot` 工具已对齐当前引导槽，Android 端配套 SukiSU 模块实现免电脑一键切换 |
| **手写笔** | 🟡 就绪 | 触控笔守护服务与 Type 29 协议栈就绪，`/proc/nvt_thp_stylus=3` 常驻监听，等待实物终验 |
| **指纹 / IR / HDR**| ❌ 不支持 | 无硬件节点或依赖封闭 TEE 固件，开源主线明确不支持 |

---

## 快速安装指南

### 前置准备
1. 小米平板 8 Pro 已官方解锁 Bootloader；
2. 电脑已安装 `fastboot` 与 `adb` 工具并配置好环境变量；
3. 从 [GitHub Releases](../../releases) 下载最新版本的完整镜像包与刷机工具。

### 三步极速刷机
1. **进入 Fastboot 模式**：
   平板彻底关机，按住 `电源键 + 音量减键` 直至屏幕亮起出现米兔/FASTBOOT 标志，使用 USB 线连接电脑。
2. **执行一键刷写**：
   - **Linux / macOS**：
     ```bash
     chmod +x flash-linux.sh
     ./flash-linux.sh
     ```
   - **Windows**：
     双击运行 `flash-linux.bat`。
3. **开机与首登**：
   刷写完成后平板自动重启进入 Fedora 44 GNOME 桌面：
   - 默认桌面用户：`piano`
   - 默认密码：`piano123456`（首次使用后建议通过 `passwd` 修改）

---

## 双系统免电脑无缝切换

- **从 Linux 切换回 Android**：
  在 GNOME 应用抽屉点击 **「返回 Android」**（或终端执行 `piano-next-boot android --reboot`），平板即刻保存启动状态并重启进入 HyperOS。
- **从 Android 切换回 Linux**：
  在 Android 系统中安装并启用配套的 `piano-switch-ksu` 模块，点击桌面快捷磁贴即可一键重启进入 Fedora。
- **开机引导菜单**：
  开机前 3 秒内按音量键可直接唤出 SunUEFI 引导菜单，或在桌面点击 **「SunUEFI 启动选择菜单」** 进入。

---

## GitHub Release 资产清单

正式版本发布时，Releases 页面将提供以下经过完整真机复核的开箱即用文件：

| 文件名 | 类型 | 说明 |
|---|---|---|
| `pianoroot-fedora44-gnome-f2fs-*.img.zst` | 根分区镜像 | 预装全套优化服务与驱动的 Fedora 44 根文件系统（Zstandard 高压缩） |
| `sunuefi_esp-*.img` | 引导分区镜像 | 包含 SunUEFI EDK2、DTB、Linux 7.2.9 正式内核与 Initramfs 的完整 ESP 镜像 |
| `boot_sunuefi-*.img` | BOOT 引导头 | 适配 ABL 链式引导的合体引导映像 |
| `piano-switch-ksu-*.zip` | Android 模块 | KernelSU / SukiSU 专用免电脑切换回 Linux 模块 |
| `piano-installer-*.zip` | 一键刷机脚本包 | 内含 `flash-linux.sh`（Shell）与 `flash-linux.bat`（Windows 批处理）及完整性校验工具 |
| `SHA256SUMS` | 校验哈希 | 各资产文件的 SHA-256 哈希列表 |

---

## 开发者与架构文档

深入的技术设计、全链路构建脚本与单测验证规范统一整理在 `docs/` 目录下：

- **[系统架构与分层设计](docs/architecture.md)**：Fedora 44、SunUEFI 与 Android 的系统边界定义；
- **[首次安装与已有分区更新](docs/installation.md)**：详细的分区结构计算、GPT 安全检查与增量部署契约；
- **[交付物与构建流水线](docs/artifacts.md)**：Kernel、RPM、Initramfs 与最终 Bundle 的组装标准；
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
