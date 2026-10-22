# 架构

SunUEFI 提供启动链。本仓库增加四项：Fedora 后端、F2FS 全链路、首次分区、UEFI UFS 唤醒补丁。

## 分工

| 层 | 归属 |
| --- | --- |
| 合体 BOOT、UEFI、内核编排、打包、安装器 | SunUEFI（锁定） |
| UEFI UFS 休眠唤醒与 SCSI 重试补丁 | 本仓库（patches/uefi-ufs） |
| Fedora rootfs 后端与配方 | 本仓库 |
| F2FS 全链路（内核策略、initramfs、fstab、udev、镜像、安装器） | 本仓库 |
| 首次分区（上游返回 `NEW_INSTALL_NOT_READY`） | 本仓库 |

本项目不 fork 上游。改动走自有脚本或补丁。

## 阶段

每个阶段独立可用。所有步骤都在主机上完成，不碰设备。

1. Fedora 后端。给上游增加 dnf5 路径，产出 Fedora rootfs 树。参考 [fedora-sheng](https://github.com/mumuxiao722/fedora-sheng) 的 RPM spec 与 Actions 参数化。不迁移其中的 SM8550 硬件内容与 mkbootimg 启动链。
2. F2FS 全链路。处理六处 ext4 硬编码：内核配置策略、`release-disk-bootstrap`、fstab、udev 放行、`package_release.py` 的镜像制作、安装器 magic 与 SSH 注入。

   制作镜像不用 `sload -P`。上游 f2fs-tools 的 sload 只保留 owner/mode/setuid，不枚举源树的 xattr/ACL/capability，导入阶段会静默丢元数据。改用挂载真实 F2FS，用 `tar --xattrs --acls --numeric-owner` 写入，写完再挂载读回逐项比对。

   制镜像需要挂载 F2FS。GitHub runner 的内核默认不带 f2fs，装上 `linux-modules-extra` 之后 `modprobe f2fs` 才可用。挂载式往返验证已在 CI 上跑通（`IMAGE_VERIFIED`，xattr 核对通过，fsck 退出码 0）。

3. 首次分区。只读探测本机并生成计划。使用模拟 GPT 在主机上验证计划。
4. 安装与验收。实际写入，启动验证，双向切换。

F2FS 排在分区之前：先跑通 F2FS 镜像的挂载式往返验证，再用实测的 root 镜像容量定分区方案。镜像容量与设备容量已经脱钩，封存镜像固定 8192 MiB，首次启动时由 initramfs 长满分区。

## CI 分工

| workflow | 触发 | 产出 |
| --- | --- | --- |
| `host-check.yml` | push / PR / 手动 | 离线检查与 `make test`。手动 dispatch 时按输入复用调用 `build-piano-rpms`、`build-ffmpeg` 与 `package-install` |
| `build-piano-rpms.yml` | `host-check` 调用 | 九类矩阵 RPM 与匹配内核的 `kernel-piano`，同一 job 产出 SRPM |
| `build-ffmpeg.yml` | `host-check` 调用 | 带 v4l2m2m 的 FFmpeg RPM |
| `build-rootfs.yml` | 手动 | 按 profile 展开桌面矩阵，每档一棵 Fedora rootfs 树 + `.tar.zst`（`desktop=all` 一次出齐） |
| `build-kernel.yml` | 手动 | F2FS/SELinux Image、完整模块与匹配 SDK |
| `build-uefi.yml` | 手动 | UEFI 产品与原始 manifest |
| `build-mesa.yml` | 手动 | Fedora 原生 A830v1 Mesa RPM 与实际驱动检查记录 |
| `build-device-runtime.yml` | 手动 | Fedora 原生设备运行时；ABI 匹配的 `v4l2loopback` 由 `build-piano-rpms.yml` 编进 `kernel-piano` |
| `build-initramfs.yml` | 手动 | 独立 initramfs 构建与检查。正式 bundle 消费 build-image 的 initramfs |
| `verify-f2fs.yml` | push / PR | 小 fixture 的 F2FS 元数据往返验证 |
| `build-image.yml` | 手动 | 单档：同一次集成的设备 rootfs、initramfs、F2FS 镜像及相互绑定的 manifest，产物名 `root-image-<rel>-<desktop>` |
| `build-bundle.yml` | 手动 | 汇总内核、UEFI、rootfs、镜像为部署包；每档一个 `deploy-bundle`，内部文件名不随桌面变 |
| `prepare-install.yml` | 手动 | 按 bundle run 扩容 root、写入设备配置，产出 `root-install` 与 `boot-tools`；`root-install-manifest.json` 记录桌面档 |
| `package-install.yml` | 手动 / `host-check` 调用 | 压缩已校验的 `root-install`，不重建文件系统 |
| `publish-copr.yml` | 手动 | 从矩阵 SRPM 在 COPR 重建用户态包 |
| `release.yml` | tag / 手动 | 单 Release 带一档或多档桌面：平台资产逐字节比对后只发一份，桌面资产按 `-<desktop>` 加后缀 |
| `upstream-watch.yml` | 每周一 | 上游漂移看板，必要时开 draft PR 改 pin |

上表只列构建与发布链。`host-check.yml` 也会在 push 与 PR 上跑 `make test`。

产物由各 workflow 显式上传。上传失败不计为交付完成。

产物清单与部署包结构见 [部署产物](artifacts.md)。

## 为什么不用 UKI

[fedora-sheng](https://github.com/GhfunNiHe/sheng_fedora_packages) 与 [nabu_fedora](https://github.com/jhuang6451/nabu_fedora) 用 UKI（`systemd-stub` + `.efi` 内核镜像）把内核、initramfs 和 cmdline 打成一个 EFI 可执行文件，由固件直接启动。这条路在本设备上不通，原因是固件，不是 Fedora 那一侧。

`PianoEspBootSource.c` 只读 `\EFI\Piano\stable\boot.img`，格式是 Android boot v2，要求 pagesize 4096、`Second` / `RecoveryDtbo` / `Signature` / `Trailing` 全零、内核段 56 字节处是 `ARM\x64`。UKI 是 PE/COFF，塞不进这个解析器。

固件里另有一条 `Image.efi` 路径：`PianoProductOsController.c` 定义了 `\EFI\Piano\stable\Image.efi` + `piano.dtb` + `initramfs.cpio` 的加载，形式上接近 UKI。它没有接上，原因是三处编译期约束：

- 模块没有绑定到产品调用链，上游文档写明 "not yet bound to the product Core/Policy callsite"，`git grep` 也找不到 INF/DSC 引用。
- 它对三份载荷做 `PinValid()` 编译期固定哈希与长度校验，换内核必须重编固件。
- `PianoProductCore.c` 只调 `PianoRawLinuxLoadStable`，没有分支走 `PianoProductOsController`。

还有一处 live-memory 补丁与 UKI 不兼容：本项目给 UEFI 打的 `patches/uefi-live-memory` 在 ExitBootServices 之前把运行中内核的内存映射交给 `EFI_DT_FIXUP_PROTOCOL` 处理。`systemd-stub` 自己接管 LoadImage/StartImage 与内存描述符，两套机制叠在一起没有验证过的顺序。

代价对比也偏向现有路径。合体 BOOT 路径的 `AcceptedSha` 来自 ESP 上文件自身的 sha256，换内核不需要重编固件；UKI 至少要改 `PinValid()` 的编译期 pin。切系统（Android ↔ Fedora）不受影响：`PianoRawLinuxLoadStable` 的槽位与返回 Android 的路径都由 `piano-next-boot-helper` 写请求页，与内核容器格式无关。

所以内核升级走的是另一条路：`kernel-piano` 的 `%posttrans` 在设备上重建 initramfs、把新内核重新打成 Android boot v2 容器、原子替换 ESP 的 `boot.img`。细节见 [COPR 软件源](copr-repository.md)。

如果上游以后把 `PianoProductOsController` 接进产品调用链、并把 pin 改成运行时校验，UKI 才值得重新评估。当前不作为路线。

## 从 nabu_fedora 取什么

桌面矩阵与单 Release 的形态参考 [jhuang6451/nabu_fedora](https://github.com/jhuang6451/nabu_fedora)，取的是结构，不是它的构建方式。

取：

- matrix job 扫配方文件展开，而不是扫脚本文件名。这里的配方是 `scripts/profiles/fedora-desktops.json`，workflow 不手写桌面列表。
- 每个变体一个资产名，一个 Release 带齐全部桌面，下载页按桌面分组。这里只有 root 镜像与 `root-install/` 分档，ESP、内核、initramfs 四档共用一份。
- 一个产品 README 加一张变体对照表。表格与入口结构已经落地，截图等四档上真机验收后再补。

不取：

- 构建器镜像（Dockerfile → GHCR）。多一个镜像与一层缓存失效面。现有 podman 容器按 `releasever` 锁定，可复现性由 manifest 兜底。
- base rootfs `cp -a` 再增量 dnf 的变体模型。它会打断"一次审计事务、RPM 归属、SELinux 单次 relabel"这条链。四档在 arm runner 上并行，总耗时不会因此拉长。
- 每个变体一份独立脚本加全局 `--nogpgcheck`。这里是一个 profile 加一个解释器，四档共用一次有签名的安装事务。这一侧也有测试：每档的派生名、登录文件与运行时脚本都在 `make test` 里。

各档的登录、电源与旋转差异见[桌面档](desktops.md)。

## 前提

整套方案假设 SunUEFI 的启动交接在目标设备上成立。上游在 16 GB 机型和 OS3.0.309 上验证过；本项目在 24 GB / 1 TB 的同一机型上实测。首次分区前的只读探测会核对 GPT、槽位与原厂 BOOT，任何一项与预期不符就停在计划阶段，不写设备。

## 设备约束

1. 存储扩容过的机器，分区边界与校准数据可能被第三方改过。分区一律按自己设备的实时 GPT 计算，不套用固定数值。
2. 全盘无空位。`userdata` 占满盘尾，新分区只能从 `userdata` 尾部让出。
3. `/data` 是加密 dm 映射。上游的无损缩容前提不成立，首次分区必须清空 `userdata`。
4. 无 `/dev/kvm`。内核参数是 `kvm-arm.mode=protected`。虚拟化只能走 AVF/crosvm + Gunyah。

## 许可

自有代码采用 MIT 许可。SunUEFI 为文件级许可，内核为 GPL-2.0，固件许可不明且不入库。上游依赖按各自许可取用，不复制代码。
