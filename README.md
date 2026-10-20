# Fedora 44 for Xiaomi Pad 8 Pro (piano)

[English](README.en.md) | 简体中文

在小米平板 8 Pro（代号 `piano`，高通骁龙 8 至尊版 SM8750）上原生运行 Fedora 44 + GNOME。引导链是 SunUEFI（EDK2），内核是 Linux 7.2.9 主线适配，根文件系统用 F2FS，可以与原厂 HyperOS 双系统免电脑往返。

> **风险提示**：刷入涉及解锁 Bootloader、改写 GPT、擦写 A/B 槽位与启动链。操作不规范可能让设备变砖或丢失全部数据。动手前自行备份。本项目按 [MIT 许可证](LICENSE) 原样提供，作者不承担设备损坏、数据丢失或保修失效的责任。

## 硬件支持

| 模块 | 状态 | 说明 |
|---|---|---|
| 显示与渲染 | ✅ | 3200×2136 @ 120Hz，Mesa 26.2.3 Turnip（Adreno 830）硬件加速 |
| 触控 | ✅ | 卡尔曼滤波、24 像素死区防抖、正交轴向锁 |
| 视频编解码 | 🟡 | Iris V4L2 M2M 已通：H.264 与 HEVC 各 300 帧解码、逐帧摘要与软件解码一致，关键帧硬件编码通过（1080p，Iris 中断计数同步增长）。VP9、10-bit 与 4K60 未实测，浏览器硬解待验收，见[已知问题](docs/known-issues.md#触屏网络音频与相机) |
| 电源与电池 | ✅ | 双电芯电量监控，支持深度挂起 |
| 引导稳定性 | ✅ | `qbootctl.service` 写入 `Successful: 1`，避免 ABL 计数归零掉进 Fastboot |
| 相机 | 🟡 | `piano-camerad` 常驻，ISP 直出 NV12；前摄已出画面，后摄与连续预览待验收，见[已知问题](docs/known-issues.md#触屏网络音频与相机) |
| 音频 | ✅ | 功放上限 215（-15 dB）防炸音圈，麦克风增益 +40 dB |
| 性能调度 | ✅ | `uperf-linux` 触控提频 |
| 无线 | ✅ | Wi-Fi 6E/7、蓝牙 |
| 传感器 | ✅ | `iio-sensor-proxy` 采集加速度计、环境光、接近、罗盘 |
| 虚拟化 | ✅ | `/dev/gunyah`、`librevm`、virtio-blk 与 virtio-net、单 vCPU 客体启动；SMP、图形与 GUI 操作待验收 |
| 双系统往返 | 🟡 | Linux→Android 的请求写入可用；Android→Linux 的自动切换尚未验收，Android 侧匹配模块已装上但只接管已有核心，见[已知问题](docs/known-issues.md#双系统切换) |
| 手写笔 | 🟡 | 守护服务与 Type 29 协议就绪，等待实物划线终验 |
| 指纹 / IR / HDR | ❌ | 依赖闭源 TEE 固件，主线不支持 |

## 安装

1. 解锁 Bootloader，准备好 `fastboot`。
2. 原始 GPT 的设备先按[从原始 GPT/UFS 开始的首次安装](docs/installation.md#四-从原始-gptufs-开始的首次安装)分区、擦除 `userdata`、让 Android 首次开机。**没有这一步，一键脚本不能运行。**
3. 用同一次成功 `build-bundle` 的 `deploy-bundle`，以及由该 bundle run 派生的 `prepare-install` 产出的 `root-install`，不要混用不同 run 的资产。
4. 按[安装指南第三节](docs/installation.md#三-已有-linux-分区的刷写跨平台一键脚本)准备一个暂存目录：把 `scripts/installer/` 里的 `flash-linux.sh`、`flash-linux.bat` 与 deploy-bundle 的全部文件放进同一层，`root-install/` 放在其下。

   脚本把当前目录当作暂存目录，不会从仓库里找镜像。

5. 关机后长按 `电源键 + 音量减键` 进入 Fastboot，确认 `fastboot getvar current-slot` 返回 `a`，在暂存目录里执行：

   ```bash
   ./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub   # Linux / macOS
   ```

   Windows 双击 `flash-linux.bat`（不自动注入公钥）。

6. 刷完自动重启进入 GNOME。用户名 `piano`，默认密码 `piano123456`，登录后尽快用 `passwd` 修改。

   传了 `--ssh-key` 就可以直接用密钥登录。

需要逐步控制时按[手动刷机](docs/installation.md#三之二-手动刷机不使用一键脚本)执行，那里列了每条 `fastboot` 命令、SHA256 校验、读回验证和回滚方法。**不要擦除 `dtbo_b`**，ABL 会因此拒绝整条引导链。

### 双系统切换

- Linux → Android：应用抽屉点「返回 Android」，或执行 `piano-next-boot android --reboot`。
- Android → Linux：自动切换尚未验收。已装的 `piano-switch-ksu` 匹配模块只接管已有核心，不重写 BOOT，见[已知问题](docs/known-issues.md#android-侧)。
- 引导菜单：开机 3 秒内按音量键，或桌面点「SunUEFI 启动选择菜单」。

## 发布产物

正式 Release 取自 `build-bundle` 的 `deploy-bundle`，`root-install` 由同一 bundle run 的 `prepare-install` 产出：

| 文件 | 说明 |
|---|---|
| `boot_sunuefi.img` | 适配 ABL 链式引导的引导头 |
| `sunuefi_esp.img` | SunUEFI EDK2、DTB、Linux 7.2.9 内核与 initramfs |
| `PianoUEFI-product.img` | SunUEFI 产品固件本体，`sunuefi_esp.img` 的载荷来源 |
| `pianoroot.f2fs.img.zst` | 封存的 Fedora 44 GNOME 根文件系统，首装经 `prepare-install` 扩容 |
| `root-install/` | 扩容后的 sparse 根镜像与清单 |
| `SHA256SUMS` | 各资产哈希 |

仓库目前还没有 Release 与 tag，上面的资产只以 workflow artifact 形式存在。`scripts/package-release-bundle.sh` 是本地兼容打包器，不是发布主入口。

## 文档

- [系统架构](docs/architecture.md)：Fedora、SunUEFI 与 Android 的边界
- [安装与分区](docs/installation.md)：分区计算、GPT 检查、一键与手动刷写、首次登录
- [交付物与流水线](docs/artifacts.md)：内核、RPM、initramfs 与 bundle 的组装标准
- [维护与验收](docs/internals.md)：流水线入口、产物绑定、run id 传递、PR 门槛
- [COPR 软件源](docs/copr-repository.md)：用户态包在线升级与回滚边界（内核不在内）
- [上游跟踪](docs/upstream.md)：当前 pin、漂移看板、跟进规则
- [已知问题](docs/known-issues.md)：未解决与观察中的问题
- [Gunyah 虚拟化](docs/virtualization.md)：SM8750 上的虚拟机生命周期与网络
- [公开分发前的缺口](docs/public-release-gaps.md)：构建期仍绑着这一台设备、这一个人的输入
- [参与贡献](CONTRIBUTING.md)：本地检查、提交格式与 PR 门槛
- [安全策略](SECURITY.md)：漏洞报告渠道与处理范围

本地调试记录、事故复盘和交接笔记不入库。

## 构建与检查

```bash
make test                  # 全套离线单测：GPT 计算、镜像与元数据校验
make plan RELEASEVER=44    # 打印 Fedora 44 根文件系统生成计划
```

流水线入口是 `.github/workflows/`。主链是 `host-check`（带 `build_piano_rpms=true`）→ `build-rootfs` → `build-image` → `build-bundle` → `prepare-install`，后四步还要各自带上内核、运行时、Mesa、FFmpeg 与 UEFI 的成功 run id，不是单纯的线性接力。

每一步只接受成功 run 的产物，混用不同 run 会被拒绝。细节见 [docs/internals.md](docs/internals.md)。

## 许可

仓库自有代码遵循 [MIT](LICENSE)，第三方上游组件与内核补丁保留各自许可证，见 [sources.lock.json](sources.lock.json) 与 [NOTICE](NOTICE)。
