# 构建与交付产物

目标是 Fedora 44（四档桌面）+ F2FS 的主机构建集合。构建阶段停在设备写入之前。平板启动、首次分区和本机 BOOT 包装另行验收。

| 产物 | 生产入口 | 集成关系 |
| --- | --- | --- |
| UEFI 产品 | build-uefi.yml | 使用固定 SunUEFI 配方。保留原始 manifest |
| Image、完整模块、SDK | build-kernel.yml | 内建 F2FS/SELinux/Gunyah，强制 rootfstype=f2fs。SDK 绑定同一配置和 Module.symvers |
| Fedora 矩阵 RPM | host-check 调用的 build-piano-rpms.yml | 构建九类硬件/运行时 RPM 和匹配内核的 kernel-piano。同一 job 产出对应 SRPM，供 COPR 从源码重建。workflow_call 复用件由已注册的 host-check 启动，artifact 仍由 piano-rpms job 产出 |
| Fedora 基础树 | build-rootfs.yml | 原生 DNF installroot，记录容器 ID 和实际 RPM，排除发行版 kernel |
| A830v1 Mesa RPM | build-mesa.yml | 固定 Fedora 源 RPM 与 piano 补丁，复用 Fedora 配方。从实际 RPM 核对 Gallium/Turnip 芯片表 |
| FFmpeg RPM | host-check 调用的 build-ffmpeg.yml | 固定 Fedora 源 RPM，沿用 SunUEFI BSP 补丁启用 H.264/HEVC v4l2m2m 解码器。build-ffmpeg 是 workflow_call 复用件，实际 run 的 workflow_id 是 host-check。原生构建及设备解码仍待验证 |
| 设备运行时 RPM | build-device-runtime.yml | Fedora 原生触控/笔、相机程序、传感器、BOOT 请求工具、拓扑与设备配置。另含 VA-API、crosvm、LibreVM 和平台配置 RPM。ABI 匹配的 `v4l2loopback` 不在这一侧，由 `build-piano-rpms.yml` 编进 `kernel-piano` |
| 设备 rootfs、initramfs、F2FS raw 镜像 | build-image.yml | 同一 job 安装设备、Mesa 与 FFmpeg RPM，核对已安装包及解码器注册。随后运行上游完整 rootfs 检查，配置服务，清理身份，设置 SELinux 标签，最后完整读回验证 |
| ESP、Linux boot.img、交付集合 | build-bundle.yml | 从集成记录取得准确内核，调用上游 DTB/mkbootimg/ESP 包装，核对全部文件和展开 root 镜像 |

## 一致性要求

每份 initramfs、运行时和最终 rootfs manifest 都引用完整 kernel manifest 的 SHA256。kernel release 相同不足以证明配置相同，最终组合必须匹配这份摘要。

F2FS 镜像同时绑定 rootfs、内核、运行时、Mesa、FFmpeg 和 initramfs 六份清单的 SHA256。rootfs 清单引用实际消费的运行时、Mesa 与 FFmpeg 清单，并记录实际 RPM 清单的摘要。

组件分目录解包。基础树、内核、UEFI、运行时、Mesa、FFmpeg 和 initramfs 不共享 manifest 路径。跨 run 查找只接受成功且未过期的 artifact。内核只允许标准 kernel-release-7.2.9。SDK 是独立 artifact，两者必须取自同一次成功 run。

镜像和 bundle 只解包准确命名的内核 archive，拒绝旧 `kernel-experimental-gunyah` 与 `piano-fedora-runtime-gunyah` 产物。Gunyah 已由标准内核和标准 runtime 消费，SDK 内携带源码派生证明。`source-runs.json` 记录 `kernel_run` 与 `kernel_artifact`，bundle 按记录下载并要求内核为标准档。

clean rootfs 的 `kernel-piano` 与内核压缩包必须来自同一次 kernel run。DNF 事务先安装 `kernel-piano`，所以 rootfs 本来就有唯一的 `/usr/lib/modules/<release>`。build-image 不要求该目录为空，而是要求恰好一个 release、归属 `kernel-piano`，并逐文件比对封存内核 manifest。release 字符串相同但模块字节不同仍会失败，避免 ESP 与 rootfs 跨 run 混装。

所有 `workflow_dispatch` 入口都必须先在 `main` 注册。`build-piano-rpms.yml` 是 `workflow_call` 复用件，由已注册的 `host-check` 启动。`build-rootfs` 按 `host-check.yml` 解析 host-check run 的 `piano-rpms` artifact，不能再直接查询一个只在 PR 分支存在的 workflow。

## F2FS 验证

导入使用 GNU tar 的 pax、全部 xattr namespace、ACL、numeric owner 与 SELinux 恢复选项。逐条核对文件内容、UID/GID、mode、mtime、软链接目标、硬链接关系、全部 xattr、capability 与目标 SELinux 标签。属性读取出错时检查失败。目录大小不参与跨文件系统比较。

镜像先卸载，再运行 `fsck.f2fs -f --dry-run`，核对检查前后镜像摘要不变。随后以 `ro,norecovery` 挂载读回。卸载后才生成最终镜像摘要。仅有 fsck 退出 0 或小 fixture 通过不证明完整交付通过。

通用交付包里的 F2FS 镜像与设备无关，同一份镜像能装到任何容量的设备上。文件系统在首次启动时由 initramfs 长满自己的分区。设备容量一律从实时 GPT 计算，`product.json` 的 `root_size_mib` 只是封存镜像的容量。

`prepare-install.yml` 是可选步骤：填了容量就把镜像离线扩到那个 MiB 数并完整读回，留空则直接用封存镜像。扩容副本只对那一个容量成立，Release 发布的始终是通用封存镜像。

## 交付内容

每档桌面各有一个 `build-bundle` run，artifact 名是 `deploy-bundle-<档位>`，bundle 内部文件名不随桌面变化：`verify-bundle.py`、`prepare-root-capacity.py` 与 `flash-linux.sh` 只认规范名。桌面维度只出现在 artifact 名与归档名上。

最终 deploy-bundle 包含 `sunuefi_esp.img`、`boot_sunuefi.img`、`PianoUEFI-product.img`、`pianoroot.f2fs.img.zst`、与封存镜像同内容的 `pianoroot.f2fs.sparse.img`、`initramfs.tar.zst`、`diagnostic-abl-storage.tar.zst`、对应 manifest、`source-runs.json` 与 `SHA256SUMS`。

`initramfs.tar.zst` 由 build-image 用固定时间戳与 owner 打出，四档逐字节相同。它是 `build-bundle` 解包后重新打进 `boot_sunuefi.img` 的中间产物，不进 Release 归档。

bundle 自带 sparse。Release 归档里带的是 `prepare-install` 产出的 root，两者内容相同，后者带着设备配置与 SELinux 标签。

上游 `esp-manifest.json` 里仍用内部键 `esp.img`、`boot.img`。`verify-bundle.py` 会显式映射，不要求发布包保留第二套文件名。`verify-bundle.py` 流式展开压缩 root，核对实际 raw 大小和摘要，再记录 `HOST_VERIFIED_FEDORA_F2FS_BUNDLE_NOT_DEVICE_VERIFIED`。

Linux boot.img 是 ESP 上的 Linux 载荷。合体 BOOT 含当前 Android 内核，必须用本机当前 stock BOOT、槽位和 generation 另行包装。通用构建产物不包含这些个人设备输入。

已装系统升级内核不需要 bundle。`kernel-piano` 提供 `kernel-core`。`dnf upgrade` 装上之后，包自己的 `%posttrans` 重建 initramfs，并重打 ESP 上的 `boot.img`。语义与回滚见 [COPR 软件源](copr-repository.md)。

## 参考来源

`docs/upstreams.json` 登记研究与对照仓库，只用来限定实现范围。源码直接依赖以 `sources.lock.json` 为准，参考文档不会自动进入生产依赖。下载和生成的文件放在被忽略的 `build/`、`out/`。固件、校准和密钥不进代码仓库。

## 发布链基线

当前基线是 `sources.lock.json` 锁定的那组上游的一次完整链，每一阶段都 success：

| 阶段 | 关键产物 |
| --- | --- |
| build-kernel | `kernel-release-7.2.9`、`kernel-sdk-7.2.9` |
| host-check（FFmpeg + RPM） | `piano-fedora-ffmpeg`、`piano-rpms` |
| build-device-runtime | `piano-fedora-runtime` |
| build-rootfs | `rootfs-<rel>-<desktop>` |
| build-image | `root-image-<releasever>-<desktop>` |
| build-bundle | `deploy-bundle-<档位>` |
| prepare-install | `root-install-<档位>`、`boot-tools`、`host-boot-tool` |

每一轮的 run id、产物摘要与验收结论记在对应 Release 的说明和会话记录里，文档不逐轮记录。

封存镜像的容量由 `product.json` 的 `root_size_mib` 固定，与设备无关。首次启动由 initramfs 长满 `sunuefi_root`。

首装不需要先跑 `prepare-install`，bundle 自带的 sparse 就是首装材料。`prepare-install` 仍会跑，因为 `flash-linux.sh` 的更新路径要求 `root-install/`。Release 直接带上这份产物。

`verify-bundle.py` 解码 `pianoroot.f2fs.sparse.img`，与 `pianoroot.f2fs.img.zst` 展开后的字节和摘要比对。initramfs manifest 的 `f2fs_tools_verified` 为 `true`，归档里有 `usr/sbin/fsck.f2fs` 与指向它的 `resize.f2fs`。ABL 诊断链的 manifest 是 `false`，那条链不做扩容。

root 镜像与设备容量脱钩。封存镜像是通用件，同一份镜像能装到任意容量的设备上。文件系统在首次启动时扩满本机分区。

## Release 发布

`.github/workflows/release.yml` 只搬运已验证的构建，不重新编译。tag 指向的 commit 必须同时有成功的 `build-bundle` 与 `prepare-install` run，每档一对。缺一个就在写入任何发布资产之前失败。

发布不由 tag push 触发。手工 dispatch 并填 tag，内容与 tag 指向的 commit 绑定。

默认不填 run id：`desktop` 留 `all`，四档的 run 都按 tag commit 解析。某个档位缺 run 时，用 `desktop_runs` 只发其余档（`gnome=<bundle>:<prepare>,kde=<bundle>:<prepare>`）。没写 run id 的条目仍按 commit 解析。

build-bundle 与 prepare-install 的 artifact 都带档位：`deploy-bundle-<档位>` 与 `root-install-<档位>`。`boot-tools` 与 `host-boot-tool` 四档同源，只取第一档的，其余各档与它逐字节比对。

### 每档一个归档

每档桌面打成一个自包含归档。解到空目录就是安装指南的暂存目录，用户不需要再从别处拼第二个。

```
piano-<tag>-<档位>.tar.zst            单文件，小于 2 GiB 时
piano-<tag>-<档位>.tar.zst.part-NN    超过 2 GiB 时按 1900 MiB 切片
piano-<tag>-<档位>.sha256             这一档已上传文件的摘要
```

归档里：

- 平台镜像 `sunuefi_esp.img`、`boot_sunuefi.img`、`PianoUEFI-product.img`。
- 这一档的 `root-install/`，含 sparse root、清单与摘要。
- 组件 manifest 与 bundle manifest。
- `flash-linux.sh`、`flash-linux.bat`、`README-INSTALL.txt`、`make-device-boot.sh`。
- `boot-tools/`、`host-boot-tool/`、`scripts/`。
- `SHA256SUMS`。

`SHA256SUMS` 在装配完之后按目录里的实际内容生成。它只列真实存在的文件，所以 `sha256sum -c SHA256SUMS` 在归档里与手动路径下都能直接过。清单里混进未发布的成员，手动路径就会恒返回 1。

打包固定 `--sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner`，同一档重跑出同样的字节。归档打完后先解回来与装配目录 `diff -r`，不一致就停下。

成员表只有一处定义：`scripts/release-assets.py` 的 `BUNDLE_MEMBERS`、`EXCLUDED_BUNDLE_FILES` 与 `ARCHIVE_FILES`/`ARCHIVE_DIRS`。`release.yml` 按它装配，`check-bundle` 拒绝成员表不认识的 bundle 文件。

### 不进归档的文件

deploy-bundle 里有四类文件不进归档。

- 完整镜像 `pianoroot.f2fs.img.zst` 与封存镜像 `pianoroot.f2fs.sparse.img` 和 `root-install` 里的 root 内容相同，也不能直接刷。同一份 root 发三种形态会让人分不清该下哪个，还把每档归档撑大。
- `initramfs.tar.zst` 是 `build-bundle` 解包用的中间产物，内容与 `boot_sunuefi.img` 里那份逐字节相同。
- `diagnostic-abl-storage.tar.zst` 是 ABL 只读探针的调试件，manifest 自标 `device_tested: false`，装机脚本不读它的内容。

四样都能从对应的 workflow artifact 取。

`device-boot/boot_linux.img` 也不进归档。它由 `make-device-boot.sh` 用每台平板自己的 96 MiB stock BOOT 生成。

### 资产清单

体积取自一次四档发布的实测，随内核与套件更新浮动。

| 资产 | 数量 | 体积 | 用途 |
| --- | --- | --- | --- |
| `piano-<tag>-<档位>.tar.zst[.part-NN]` | 6 个分片 | 合计约 6.8 GiB | 每档一个归档，gnome 与 kde 各 2 个分片，niri 与 hyprland 各 1 个 |
| `piano-<tag>-<档位>.sha256` | 4 份 | 每份 1–2 行 | 这一档的分片摘要，`sha256sum -c` 直接喂 |
| `RELEASE-SHA256SUMS` | 1 份 | 几 KiB | 全部资产的哈希，`actions/attest-build-provenance` 的输入 |

四档 Release 合计 11 个资产。装 gnome 下 2 个分片加 1 份 sidecar，合计 2.1 GiB。

归档总量比扁平布局多 318 MiB。多出来的是平台镜像与 boot-tools 在四档里各存一份。用户端不多下。

### 平台镜像压缩

归档里的三份镜像保持原样，压缩由外层 `.tar.zst` 完成。ESP 是 512 MiB 的 ext4，绝大部分扇区是零，归档压完的整体比例因此很高。不做内层压缩，装机脚本读的就是规范名，不需要再解一次。

### 校验与 provenance

发布前按顺序校验五项：

1. bundle 的 `SHA256SUMS`。
2. `verify-bundle.py`。
3. `release-assets.py check-bundle`，确认 bundle 只有成员表认识的文件。
4. root-install 的 `SHA256SUMS`。
5. boot-tools 的 `installer/SHA256SUMS`。

另外核对 root-install manifest 里 `source_bundle_manifest_sha256` 与 `source_root_manifest_sha256` 确实指当前 bundle 与其 root 镜像。Release 只收 `android_slot == "_a"`。`_b` 的 root-install 留给 B 槽设备自己生成。

`RELEASE-SHA256SUMS` 覆盖实际发布的全部资产，一份覆盖所有桌面。每档的 sidecar 从它切出来。

发布前用 `actions/attest-build-provenance` 以 `RELEASE-SHA256SUMS` 为输入生成 provenance。它把该文件列出的每个资产都记成一条 subject，所以 `gh attestation verify <任一分片>` 都能验。`RELEASE-SHA256SUMS` 本身不在 subject 列表里，拿它去验会得到 404。

GitHub 的单个 release asset 必须小于 2 GiB，所以归档超限时按 1900 MiB 切片。sidecar 覆盖每个分片，缺一个分片在解开之前就会失败。

## 尚需验证

主机构建验收已通过，覆盖组件清单摘要、压缩 root 展开后的 8 GiB 大小与 SHA256，以及 ESP 载荷和 boot.img 组件。设备上的 Fedora 在那之后启动过。未完成的验收项见[已知问题](known-issues.md)。首次分区、恢复材料及 Android 往返仍需各自的设备证明。

上游安装器保留 ext4 合同。piano-fedora 新增独立的 F2FS 初装入口和本机 BOOT 文件准备入口。工具路径、Actions 材料、破坏性操作门禁和真机验收边界见[维护与验收](internals.md)与[首次安装](installation.md)。新增实现的构建、包装和设备验收状态分别以对应 manifest 与会话记录为准。

## 容量与 sparse 传输

执行器从实时、双副本 CRC 合格的完整 GPT 重算容量，镜像不能大于它。原 userdata 会被清空重建。GPT/BOOT 恢复文件不含 Android 用户数据。分区容量的分配规则见[安装指南的分区架构](installation.md#二-存储与分区架构原理)。

通用包里的 8 GiB F2FS 镜像保持封存。`prepare-root-capacity.py` 在私有工作副本里设置本机启动配置与传感器槽位，恢复匹配的 Fedora SELinux 标签，然后离线扩容。镜像不写任何 SSH 公钥，manifest 用 `ssh_public_key_in_image: false` 记录。

源树与扩容副本的文件、所有权、权限、链接、mtime、全部 xattr 逐项比较。只读 fsck 与 sparse 解码摘要必须通过。

sparse 文件用 RAW 与显式零 FILL，不用 DONT_CARE。展开后的完整字节与验证过的 F2FS 镜像一致。文件系统仍是 F2FS，sparse 只负责 Fastboot 传输。
