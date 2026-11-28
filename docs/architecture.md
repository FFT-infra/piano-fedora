# 系统架构

[SunUEFI](https://github.com/slwyts/Project-SunUEFI) 按 commit 锁定为上游提供启动链；本仓库不 fork 上游，所有增量通过自有脚本和 `patches/` 维护。

## 组件边界

| 层 | 维护归属 |
| --- | --- |
| 合体 BOOT、UEFI、内核编排、打包、基础安装器 | SunUEFI（`sources.lock.json` 锁定） |
| UEFI 启动/休眠/USB 停机补丁 | 本仓库（`patches/uefi-*`） |
| Fedora rootfs 装配后端与桌面配方 | 本仓库（参考 [fedora-sheng](https://github.com/mumuxiao722/fedora-sheng) 的 spec 与参数化设计，不迁移 SM8550/mkbootimg 逻辑） |
| F2FS 全链路（内核配置、`release-disk-bootstrap`、fstab、udev、镜像打包、一键脚本） | 本仓库 |
| 首次分区规划与执行（上游仅报错 `NEW_INSTALL_NOT_READY`） | 本仓库 |

F2FS 镜像打包不走 `f2fs-tools` 的 `sload -P`（它会丢掉 xattr、ACL 与 SELinux capability），而是在挂载后的真实 F2FS 分区上通过 `tar --xattrs --acls --numeric-owner` 写入，再卸载 `fsck.f2fs` 校验并只读挂回逐项比对。GitHub Actions Linux runner 需先装 `linux-modules-extra` 才能 `modprobe f2fs`。封存镜像尺寸由 `product.json` 的 `root_size_mib` 固定，装机后首次启动由 initramfs 自动撑满设备分区。

## CI 工作流分工

所有工作流均仅在主机端构建和校验，上传失败视作未交付；详细产物规格与归档命名见[交付物与流水线](artifacts.md)。

| Workflow | 触发方式 | 产出与职责 |
| --- | --- | --- |
| `host-check.yml` | push / PR / 手动 | 跑 `make test`；手动时可复用调用 `build-piano-rpms`、`build-ffmpeg`、`package-install` |
| `build-piano-rpms.yml` | `host-check` 复用 | 编译 14 个矩阵 RPM（含匹配内核的 `kernel-piano`）与 SRPM |
| `build-ffmpeg.yml` | `host-check` 复用 | 编译启用 H.264/HEVC v4l2m2m 的 `ffmpeg-piano` |
| `build-uefi.yml` | 手动 | UEFI 固件产品及原始 manifest |
| `build-kernel.yml` | 手动 | 启用 F2FS/SELinux/Gunyah 的内核 `Image`、模块树与 SDK |
| `build-mesa.yml` | 手动 | Fedora 原生 Adreno A830v1 `mesa-piano-*` RPM |
| `build-device-runtime.yml` | 手动 | `piano-runtime`、`piano-crosvm`、`librevm` |
| `build-rootfs.yml` | 手动 | 按 `scripts/profiles/fedora-desktops.json` 并行展开五档 rootfs（`desktop=all`） |
| `build-image.yml` | 手动 | 安装运行时/Mesa/FFmpeg，校验内核模块树一致性并打出 F2FS raw 镜像与 initramfs |
| `build-bundle.yml` | 手动 | 组装单档 `deploy-bundle-<desktop>`（ESP + UEFI 产品 + sparse root） |
| `prepare-install.yml` | 手动 | 可选离线扩容 `root-install-<desktop>` 与 `boot-tools` |
| `package-install.yml` | `host-check` 复用 | 打包校验后的 `root-install-<desktop>` |
| `publish-copr.yml` | 手动 | 从矩阵 SRPM 向 COPR 提交源码重建 |
| `release.yml` | 手动 | 按 tag commit 校验并打包五档归档、sidecar 与 provenance |
| `verify-f2fs.yml` | push / PR | 小 fixture 的 F2FS 元数据往返测试 |
| `upstream-watch.yml` | 每周一 | 巡检上游漂移并开/关 issue，不自动改 pin |

## 启动链与载荷布局

固件从 `sunuefi_esp` 加载 Linux，启动入口按 ESP 实际布局二选一：

1. **BLS 布局**（当前默认）：由 `EFI/BOOT/BOOTAA64.EFI`（systemd-boot）引导 `/loader/entries/linux-<release>.conf`，内核、`initramfs` 与合成 `board.dtb` 分置 `/linux/<release>/`。
2. **单 UKI 布局**（早期兼容）：由固件直接加载 `/EFI/Linux/sunuefi.efi`（`systemd-ukify` 封装 `.linux`/`.initrd`/`.dtb`/`.cmdline`/`.osrel`/`.uname`）。

每次启动前，固件内建的 `EFI_DT_FIXUP_PROTOCOL`（`PianoDeviceTreeFixup.c`）动态重写 `/memory` 区间、面板 `compatible`、蓝牙地址、RTC 偏移及 Gunyah RM 属性。系统切回依赖活动槽合体 BOOT 末尾的 `SUNUEFI-NEXTv1` 请求页。

在线通过 `dnf upgrade` 升级 `kernel-piano` 时，`%posttrans` 会重建 initramfs 并按 ESP 现有布局原位更新内核与 `board.dtb`（详情与回滚见 [COPR 软件源](copr-repository.md)）。

## 桌面矩阵组织方式

五档桌面的结构参考 [jhuang6451/nabu_fedora](https://github.com/jhuang6451/nabu_fedora)（每档独立归档、统一配方矩阵），但构建机制改为：

- **单一配置真源**：[`scripts/profiles/fedora-desktops.json`](../scripts/profiles/fedora-desktops.json) 定义五档差异，[`scripts/piano_desktop.py`](../scripts/piano_desktop.py) 统一解释，CI 与安装脚本不硬编码档位列表。
- **独立签名事务**：不使用基础 rootfs `cp -a` 增量派生或全局 `--nogpgcheck`，而是按 Fedora `releasever` 容器并行执行带 GPG 校验的 DNF 安装事务。

## 设备前提与系统约束

- **硬件基线**：上游状态基线为 16 GB 机型，本仓库针对 24 GB / 1 TB 机型（小米平板 8 Pro）适配；装机前必须按每台平板实时 GPT 计算分区，不假设第三方扩容机型的固定偏移。
- **分区与加密限制**：Android `userdata` 占满盘尾且以 dm 加密挂载，宿主机无法直接无损缩放，因此默认首次安装擦除重建 `userdata`，仅 `--lossless` 模式通过设备侧 vold 在线缩容（详情见[安装指南](installation.md#0-保留-android-数据的首装--lossless)）。
- **虚拟化路径**：内核运行于 `kvm-arm.mode=protected`（无 `/dev/kvm`），虚拟化统一走 Gunyah + `crosvm` / `librevm`。

## 许可

仓库自有代码采用 [MIT 许可证](../LICENSE)，内核补丁遵循 GPL-2.0，SunUEFI 保留各自文件级许可（汇总见 [NOTICE](../NOTICE)）。专有固件与屏幕校准数据不入库。
