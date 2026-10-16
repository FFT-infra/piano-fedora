# RPM 架构规范化与纯净真机验收规划

## 一、 核心目标与原则

本轮演进旨在将 `piano-fedora-test` 的零散补丁、运行载荷与根文件系统固化逻辑，彻底重构为**符合 Fedora 发行版规范、来源完全可追溯、可持续协同维护的现代开源工程架构**。

### 1. 核心工程原则
1. **严格的上游溯源（Strict Provenance）**：
   - 彻底摒弃二进制黑盒或无源脚本；
   - 每个交付组件必须拥有独立的 RPM Spec，并在头部明确声明 `URL`、`Source0`（指向官方 Tag Release 或锁定 Commit）、`Patch` 系列与许可协议；
   - 杜绝跨平台硬件（如 SM8550 sheng vs SM8750 piano）盲目混用不兼容的闭源 TrustZone 固件或未经测试的高压快充私有协议。
2. **两层解耦架构（Decoupled Pipeline）**：
   - **第一层（RPM 矩阵）**：在原生或容器化 Fedora aarch64 环境下自动构建 RPM，并通过 `createrepo_c` 生成标准软件源；
   - **第二层（系统镜像组装）**：通过 DNF 从该软件源纯净安装系统，根文件系统内所有文件 100% 可通过 `rpm -qf` 溯源，零野文件残留。
3. **最终合并的唯一硬性准则：纯净真机盲测全流程（Blind Test）**：
   - 最终验收必须由一台**仅解锁 Bootloader、恢复出厂状态的 Xiaomi Pad 8 Pro (piano)** 完成；
   - 严格按照仓库发布的 Release 交付资产与安装文档从零操作；
   - 引导正常、双系统往返正常、120Hz 桌面流畅、无 failed units、手写笔与全套传感器就绪，全流程无任何异常才允许合并 PR。

---

## 二、 目标 RPM 矩阵与上游映射

| RPM 包名 | 对应上游来源 | 核心职责与硬件契约 |
|---|---|---|
| **`kernel-piano`** | `yzddmr6/linux-sm8750-piano`<br>`blu-sharky/linux-piano` | Linux 7.2 主线适配内核，内建 Gunyah 驱动、DPU 关屏撕裂修复（`0038`）、FS19xx 功放 215 硬件限幅（`0039`）、Iris 关键帧支持（`0037`）、THP 异步启动（`cb032046`） |
| **`xiaomi-piano-thp`** | `ianchb/xiaomi-sheng-thp` (母本)<br>`yzddmr6/xiaomipad-8pro-mainline` | 5 阶卡尔曼自适应时域滤波、死区锁（24 像素）、轴向锁、32000×21360 超分辨率与 134/131 units/mm 物理点距声明、P81C 笔解包 |
| **`piano-camera`** | `yzddmr6/xiaomipad-8pro-mainline/device/camera` | 手写 16 像素并行 AArch64 NEON BGRx 加速转换中继、虚拟 v4l2loopback 节点，绕过 Mesa GTK NV12 渲染异常 |
| **`sm8750-video-accel`** | `code002-2/sm8750-video-accel`<br>`radxa-pkg/libva-v4l2` | Iris (VPU35) 硬件视频编解码 VA-API 后端，驱动 Chrome/Firefox/Sunshine 4K 60fps 硬解 |
| **`uperf-linux`** | `code002-2/uperf-linux` | 触控感知即时毫秒提频调度器，包含实机验证修复的 ENODEV 死循环、热插拔漏扫与启动竞争补丁 |
| **`alsa-xiaomi-piano`** | `yzddmr6/xiaomipad-8pro-mainline/device/audio` | AudioReach 拓扑与 UCM2 配置，FS19xx 四功放默认 192、上限 215 安全限幅，VA 麦克风硬件补偿 +40dB |
| **`libssc` / `iio-sensor-proxy`** | `DylanVanAssche/libssc`<br>`hadess/iio-sensor-proxy` | 原生 libssc 0.4.4 配合 GLib 非阻塞重试补丁，消除开机 5 秒等待，打通加速度计、光感、接近、罗盘 |
| **`piano-fedora-configs`** | 本地加固与社区最佳实践集成 | `qbootctl.service` 自动写入 Successful: 1 根除 Fastboot 循环、`f2fs-root-shutdown` 安全关机、`/hypervisor` 屏蔽恢复电池面板、双系统桌面入口 |
| **`librevm`** | `code002-2/librevm` | Gunyah 虚拟机管理器（Rust + GTK4），打通 virtio-net 桥接与 2MB 大页 |

---

## 三、 推进计划与里程碑

1. **里程碑 1：开辟 PR 与基底架构搭建**
   - 建立 PR 分支，设立 `packages/` 结构骨架；
   - 梳理第一批无硬件争议的基础包（`piano-fedora-configs`、`sm8750-video-accel`、`alsa-xiaomi-piano`）。
2. **里程碑 2：核心守护进程 RPM 规范化与编译闭环**
   - 迁移并规范 `xiaomi-piano-thp`、`piano-camera`、`uperf-linux`、`libssc` 的打包定义；
   - 构建 `scripts/build-rpms.sh` 本地/容器编译流水线，确保单测与 rpmbuild 100% 通过。
3. **里程碑 3：纯净 DNF RootFS 装配链实现（✅ 已完成）**
   - 实现由本地 RPM 源直接装配 F2FS 镜像的全新脚本 `scripts/build-clean-rootfs.py`，淘汰旧的混杂 finalize 注入逻辑；
   - 引入 `tests/check_clean_rootfs.py` 与 `make plan-clean` 自动化单测，校验文件全量属于 RPM 数据库。
4. **里程碑 4：面向普通用户的工业级安装链（✅ 已完成）**
   - 编写带分区表校验、槽位自动识别、SHA256 镜像完整性校验与 zstd 自动解压的跨平台刷写程序（`flash-linux.sh` 与 `flash-linux.bat`）；
   - 编写精确到每一步命令行与界面的刷机安装文档（`README.md` 与 `docs/installation.md`），配齐 `package-release-bundle.sh`。
5. **里程碑 5：纯净真机全流程实操与终验（🟡 进行中 / 实机已全部装配）**
   - 实机（Xiaomi Pad 8 Pro / `piano`）已成功部署全部 9 个解耦 RPM 包，硬件 Iris VA-API 硬解、ALSA UCM2 音频、卡尔曼触控滤波与传感器代理全部实机就绪；
   - 待下次真机重启验证 `piano-boot-request-autoconfig.service` 开机首发自愈与盲测全流程。
