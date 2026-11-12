# 架构

SunUEFI 提供启动链。本仓库增加四项：Fedora 后端、F2FS 全链路、首次分区、UEFI UFS 唤醒补丁。

## 分工

| 层 | 归属 |
| --- | --- |
| 合体 BOOT、UEFI、内核编排、打包、安装器 | SunUEFI（锁定） |
| UEFI UFS 休眠唤醒与 SCSI 重试补丁 | 本仓库（patches/uefi-ufs） |
| Fedora root 文件系统后端与配方 | 本仓库 |
| F2FS 全链路（内核策略、initramfs、fstab、udev、镜像、一键脚本） | 本仓库 |
| 首次分区（上游返回 `NEW_INSTALL_NOT_READY`） | 本仓库 |

本项目不 fork 上游。改动走自有脚本或补丁。

## 阶段

每个阶段独立可用。所有步骤都在主机上完成，不碰设备。

1. Fedora 后端。给上游增加 dnf5 路径，生成 Fedora root 文件系统树。参考 [fedora-sheng](https://github.com/mumuxiao722/fedora-sheng) 的 RPM spec 与 Actions 参数化。不迁移其中的 SM8550 硬件内容与 mkbootimg 启动链。
2. F2FS 全链路。处理六处 ext4 硬编码：内核配置策略、`release-disk-bootstrap`、fstab、udev 放行、`package_release.py` 的镜像制作、一键脚本 magic 与 SSH 注入。

   制作镜像不用 `sload -P`。上游 f2fs-tools 的 sload 只保留 owner/mode/setuid，不枚举源树的 xattr/ACL/capability。导入阶段会静默丢失元数据。改用挂载真实 F2FS，用 `tar --xattrs --acls --numeric-owner` 写入。写完再挂载读回，逐项比对。

   制镜像需要挂载 F2FS。GitHub runner 的内核默认不带 f2fs。装上 `linux-modules-extra` 之后，`modprobe f2fs` 才能用。挂载式往返验证已在 CI 上跑通（`IMAGE_VERIFIED`，xattr 核对通过，fsck 退出码 0）。

3. 首次分区。只读探测本机并生成计划。使用模拟 GPT 在主机上验证计划。
4. 安装与验收。实际刷写设备，验证启动，完成双向切换。

F2FS 排在分区之前：先跑通 F2FS 镜像的挂载式往返验证，再用实测的 root 文件系统容量定分区方案。镜像容量与设备容量已经脱钩。封存镜像的容量由 `product.json` 的 `root_size_mib` 定义，首次启动时由 initramfs 长满分区。

## CI 分工

| workflow | 触发 | 产出 |
| --- | --- | --- |
| `host-check.yml` | push / PR / 手动 | 离线检查与 `make test`。手动 dispatch 时按输入复用调用 `build-piano-rpms`、`build-ffmpeg` 与 `package-install` |
| `build-piano-rpms.yml` | `host-check` 调用 | 九类矩阵 RPM 与匹配内核的 `kernel-piano`，同一 job 产出 SRPM |
| `build-ffmpeg.yml` | `host-check` 调用 | 带 v4l2m2m 的 FFmpeg RPM |
| `build-rootfs.yml` | 手动 | 按配方展开档位矩阵，每档生成一棵 Fedora root 文件系统树与 `.tar.zst`（`desktop=all` 一次出齐） |
| `build-kernel.yml` | 手动 | F2FS/SELinux Image、完整模块与匹配 SDK |
| `build-uefi.yml` | 手动 | UEFI 产品与原始 manifest |
| `build-mesa.yml` | 手动 | Fedora 原生 A830v1 Mesa RPM 与实际驱动检查记录 |
| `build-device-runtime.yml` | 手动 | Fedora 原生设备运行时。ABI 匹配的 `v4l2loopback` 由 `build-piano-rpms.yml` 编进 `kernel-piano` |
| `build-initramfs.yml` | 手动 | 独立 initramfs 构建与检查。正式 bundle 消费 build-image 的 initramfs |
| `verify-f2fs.yml` | push / PR | 小 fixture 的 F2FS 元数据往返验证 |
| `build-image.yml` | 手动 | 单个档位：同一次集成的设备 rootfs、initramfs、F2FS 镜像及相互绑定的 manifest，产物名 `root-image-<rel>-<desktop>` |
| `build-bundle.yml` | 手动 | 汇总内核、UEFI、root 文件系统与镜像为 deploy-bundle。每档一个 `deploy-bundle-<desktop>`，内部文件名不随档位变 |
| `prepare-install.yml` | 手动 | 按 bundle run 扩容 root、写入设备配置，产出 `root-install-<desktop>` 与 `boot-tools`。`root-install-manifest.json` 记录档位 |
| `package-install.yml` | 手动 / `host-check` 调用 | 压缩已校验的 `root-install-<desktop>`，不重建文件系统 |
| `publish-copr.yml` | 手动 | 从矩阵 SRPM 在 COPR 重建用户态包 |
| `release.yml` | 手动 | 单个 Release 带一个或多个档位，每档一个归档。不填 run id，按 tag commit 解析 |
| `upstream-watch.yml` | 每周一 | 上游漂移看板，只写 issue，改 pin 由人执行 |

上表只列构建与发布链。`host-check.yml` 也会在 push 与 PR 上跑 `make test`。

各 workflow 显式上传产物。上传失败不计为交付完成。

产物清单与 deploy-bundle 结构见 [部署产物](artifacts.md)。

## 启动链

固件从 ESP 的 `\EFI\Linux\sunuefi.efi` 加载 Linux。这个文件是 UKI，`systemd-ukify` 把内核、initramfs、DTB、cmdline、os-release 与 uname 写进同一个 PE 文件，节区是 `.linux`、`.initrd`、`.dtb`、`.cmdline`、`.osrel`、`.uname`。ESP 上没有 systemd-boot、BLS 条目或 Android boot 容器。

启动时 DT 由固件改写。`PianoDeviceTreeFixup.c` 实现 `EFI_DT_FIXUP_PROTOCOL`，依次处理内存、面板、蓝牙地址、RTC 偏移与 Gunyah 资源管理器。`systemd-stub` 以 `APPLY_FIXUPS|RESERVE_MEMORY` 调它，载荷 DTB 的 `/memory` 被换成设备本次的布局，并按 `DramBytes` 与固定保留区校验。raw Linux 交接走同一个协议。

切系统仍靠请求页。`PianoBootPolicy.c` 的 `REQUEST_BOOT_STABLE` 与返回 Android 的 token 没有变，和内核载荷格式无关。

内核升级由 `kernel-piano` 的 `%posttrans` 维护。事务收尾时它在设备上重建 initramfs，用 `systemd-ukify`、ESP 上现有的 `.dtb` 和新内核重新组装 `/EFI/Linux/sunuefi.efi`，原子替换旧文件并留一份 `.bak`。细节见 [COPR 软件源](copr-repository.md)。

`c720568` 及更早的固件读 `\EFI\Piano\stable\boot.img`，要求 Android boot v2 容器。HEAD 删掉了这条路径，ESP 上只留 `/EFI/Linux/sunuefi.efi` 这一份 Linux 载荷。

## 从 nabu_fedora 取什么

档位矩阵与单个 Release 的形态参考 [jhuang6451/nabu_fedora](https://github.com/jhuang6451/nabu_fedora)，取的是结构，不是它的构建方式。

取：

- matrix job 扫配方文件展开，而不是扫脚本文件名。这里的配方是 `scripts/profiles/fedora-desktops.json`，workflow 不手写档位列表。
- 每个档位一个资产名，一个 Release 带齐全部档位，下载页按档位分组。这里是每档一个归档，ESP、内核与 initramfs 在四档里各存一份，用户只下自己那一档。
- 一个产品 README 加一张变体对照表。表格与入口结构已经落地。四档真机验收之后再补截图。

不取：

- 构建器镜像（Dockerfile → GHCR）。多一个镜像与一层缓存失效面。现有 podman 容器按 `releasever` 锁定，可复现性由 manifest 兜底。
- base root 文件系统 `cp -a` 再增量 dnf 的变体模型。这种模型会打断"一次审计事务、RPM 归属、SELinux 单次 relabel"这条链。四档在 arm runner 上并行，总耗时不会因此拉长。
- 每个变体一份独立脚本加全局 `--nogpgcheck`。这里是一份配方加一个解释器，四档共用一次有签名的安装事务。这一侧也有测试：每档的派生名、登录文件与运行时脚本都在 `make test` 里。

各档的登录、电源与旋转差异见[桌面档](desktops.md)。

## 前提

整套方案假设 SunUEFI 的启动交接在目标设备上成立。上游在 16 GB 机型和 OS3.0.309 上验证过。本项目在 24 GB / 1 TB 的同一机型上实测。首次分区前的只读探测会核对 GPT、槽位与原厂 BOOT。任何一项与预期不符就停在计划阶段，不写设备。

## 设备约束

1. 存储扩容过的机器，分区边界与校准数据可能被第三方改过。分区一律按自己设备的实时 GPT 计算，不套用固定数值。
2. 全盘没有空闲空间。`userdata` 占满盘尾，新分区只能从 `userdata` 尾部让出。
3. `/data` 是加密 dm 映射。F2FS 的在线 resize 能缩不能扩，所以无损缩容必须经 vold 建立的解密映射在线完成，扩容才需要卸载后跑 `resize.f2fs`。宿主机读不到明文超级块，section 大小只能在设备上取。默认首装路径仍然清空 `userdata`；`--lossless` 走设备侧在线缩容，见[安装指南](installation.md#0-保留-android-数据的首装--lossless)。
4. 无 `/dev/kvm`。内核参数是 `kvm-arm.mode=protected`。虚拟化只能走 AVF/crosvm + Gunyah。

## 许可

自有代码采用 MIT 许可。SunUEFI 为文件级许可。内核为 GPL-2.0。固件许可不明，不入库。上游依赖按各自许可取用，不复制代码。
