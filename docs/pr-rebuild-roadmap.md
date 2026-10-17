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
4. **里程碑 4：面向普通用户的工业级安装链（🟡 代码完成，真机未验收）**
   - 编写带设备身份校验、目标分区存在性/容量校验、SHA256 镜像完整性校验与
     zstd 自动解压的跨平台刷写程序（`flash-linux.sh` 与 `flash-linux.bat`）；
   - 编写精确到每一步命令行的刷机安装文档（`README.md` 与
     `docs/installation.md`），配齐 `package-release-bundle.sh`；
   - 同时提供**不使用一键脚本**的手动刷机章节：Release 资产清单、逐条
     `fastboot` 命令、SHA256 校验、读回验证与回滚方法。

   尚未做到、不得声称的能力：
   - **没有槽位自动识别**：脚本固定 `fastboot set_active b`，不读取
     `current-slot` 来决定目标槽位；
   - **没有完整 GPT 分区表校验**：只检查 `boot_b`/`sunuefi_esp`/`sunuefi_root`
     是否存在及容量下限，不核对分区表 CRC、类型 GUID 或整表布局；
   - **没有首次分区能力**：`sunuefi_esp`/`sunuefi_root` 不存在时脚本直接拒绝，
     首次分区仍走 `install-f2fs.py`；
   - 上述脚本路径已用桩设备跑通（无 `erase`、写入序列与预检顺序符合预期），
     **但尚未在一台纯净真机上按 Release 资产完整执行**。

   > **2026-10-17 补充：交付物本身此前不成立。**
   > 上面这些是脚本能力的边界，但当时还有一个更严重的问题——`out/release-bundle/`
   > 里的资产并不是本 PR 流水线的产物：rootfs 与 2026-10-11 的
   > `offline-bringup-20261011-r2` 镜像逐字节相同（`bundle_source_commit`
   > 记为 `97fe3f1`），ESP 是 10-14 的设备读回，内核还是 10-11 的
   > `gd33a42990baf`，且镜像里没有任何一个本 PR 的 9 个 RPM。原因是
   > `package-release-bundle.sh` 的候选列表按顺序取第一个存在的文件，
   > 而这些历史目录一直存在；`build-clean-rootfs.py --execute` 又只产出
   > rootfs 目录、从不产出 `.img`，打包脚本要的 `build/clean-distros/` 从未
   > 出现过，于是每次都静默回退。
   >
   > 已修复：打包脚本只把 `build/` 下的路径视为当前产物，回退到历史目录
   > 一律报错（`--allow-stale` 才放行），并写出 `PROVENANCE.txt`；
   > `build-clean-rootfs.py` 新增 `--image`，审计通过后直接产出 F2FS raw
   > 镜像与 `.zst`。回归测试见 `tests/check_release_provenance.py`。
   > 另一个更早的缺陷是 `kernel-piano` RPM 是空壳（vmlinuz 与 DTB 均 0 字节），
   > 已改为强制打包真实内核载荷，见 `tests/check_kernel_package.py`。

   > **2026-10-17 再补充：装配链此前一次都没成功过。**
   > 上面两条修完仍然产不出镜像，继续排查后确认是两个各自独立的缺陷：
   >
   > 1. **merged-usr 别名重复声明**。`filesystem` 包把 `/usr/local/sbin`
   >    做成指向 `bin` 的符号链接，而 `piano-fedora-configs` 的 `%files`
   >    同时列了 `/usr/local/bin/piano-debug-bootstrap` 与
   >    `/usr/local/sbin/piano-debug-bootstrap`。两条记录解析到同一个
   >    inode，rpm 6 判成“包与自己冲突”，整个 1783 包的事务回滚：
   >
   >        file /usr/local/sbin/piano-debug-bootstrap conflicts between
   >        attempted installs of piano-fedora-configs-0.1.0-4.fc44.noarch
   >        and piano-fedora-configs-0.1.0-4.fc44.noarch
   >
   >    最小复现（只含这两个路径 + `Requires: filesystem`）在 Fedora 44 上
   >    稳定触发。中间一度误判成 Obsoletes 自匹配，改界限后事务照样失败，
   >    说明那个诊断是错的。现在只发 canonical 的 `/usr/local/bin` 路径；
   >    调用方写 `/usr/local/sbin/...` 仍然可用，因为该目录就是 `bin` 的软链。
   >    `tests/check_package_replaces.py` 同时检查 spec 的 `%files` 与
   >    `out/rpms/` 里已构建的 RPM，杜绝复发。
   > 2. **装配列表里没有 `kernel-piano`**。空壳 RPM 修好之后没有任何一步把
   >    它装进镜像，新 rootfs 的 `/usr/lib/modules/` 是空的，而 ESP 里烧的
   >    是 7.2.9 内核——系统能引导，然后没有任何驱动。现在 kernel-piano 是
   >    必装项并纳入仓库校验，审计阶段还会核对
   >    `/usr/lib/modules` 恰好一个 release、存在 `modules.dep`/`modules.builtin`、
   >    `/boot` 存在 `vmlinuz-*`，任一不满足就不发布镜像。
   >
   > 实测：builder 上 1785 个包的事务首次跑通（`Complete!`）。
   > 另外 `build-clean-rootfs.py` 现在默认先清空 installroot，避免在失败
   > 残骸上叠装；镜像产物同时写 `root-image-manifest.json` 并记录
   > `kernel_release`，打包脚本靠它做 ESP/rootfs 的内核一致性交叉校验
   > （此前只写了 `image-manifest.json`，该校验一直被静默跳过）。
5. **里程碑 5：纯净真机全流程实操与终验（🟡 进行中 / 实机已全部装配）**
   - 实机（Xiaomi Pad 8 Pro / `piano`）已成功部署全部 9 个解耦 RPM 包，硬件 Iris VA-API 硬解、ALSA UCM2 音频、卡尔曼触控滤波与传感器代理全部实机就绪；
   - 待下次真机重启验证 `piano-boot-request-autoconfig.service` 开机首发自愈与盲测全流程。
