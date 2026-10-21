# 构建与交付产物

目标是 Fedora 44/GNOME + F2FS 的主机构建集合。构建阶段停在设备写入之前。平板启动、首次分区和本机 BOOT 包装另行验收。

| 产物 | 生产入口 | 集成关系 |
| --- | --- | --- |
| UEFI 产品 | build-uefi.yml | 使用固定 SunUEFI 配方；保留原始 manifest |
| Image、完整模块、SDK | build-kernel.yml | 内建 F2FS/SELinux/Gunyah，强制 rootfstype=f2fs；SDK 绑定同一配置和 Module.symvers |
| Fedora 矩阵 RPM | host-check 调用的 build-piano-rpms.yml | 构建九类硬件/运行时 RPM 和匹配内核的 kernel-piano；同一 job 产出对应 SRPM，供 COPR 从源码重建；workflow_call 复用件由已注册的 host-check 启动，artifact 仍由 piano-rpms job 产出 |
| Fedora 基础树 | build-rootfs.yml | 原生 DNF installroot，记录容器 ID 和实际 RPM，排除发行版 kernel |
| A830v1 Mesa RPM | build-mesa.yml | 固定 Fedora 源 RPM 与 piano 补丁，复用 Fedora 配方；从实际 RPM 核对 Gallium/Turnip 芯片表 |
| FFmpeg RPM | host-check 调用的 build-ffmpeg.yml | 固定 Fedora 源 RPM，沿用 SunUEFI BSP 补丁启用 H.264/HEVC v4l2m2m 解码器；build-ffmpeg 是 workflow_call 复用件，实际 run 的 workflow_id 是 host-check；原生构建及设备解码仍待验证 |
| 设备运行时 RPM | build-device-runtime.yml | Fedora 原生触控/笔、相机程序、传感器、BOOT 请求工具、拓扑与设备配置；外部模块匹配 SDK；另含 VA-API、crosvm、LibreVM 和平台配置 RPM |
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

通用交付包里的 8192 MiB F2FS 镜像与设备无关，同一份镜像能装到任何容量的设备上，文件系统在首次启动时由 initramfs 长满自己的分区。设备容量一律从实时 GPT 计算，`product.json` 的 `root_size_mib` 只是封存镜像的容量。

`prepare-install.yml` 是可选步骤：填了容量就把镜像离线扩到那个 MiB 数并完整读回，留空则直接用封存镜像。

## 交付内容

每档桌面各有一个 `deploy-bundle` run，bundle 内部文件名不随桌面变化：`verify-bundle.py`、`prepare-root-capacity.py` 与 `flash-linux.sh` 只认规范名。桌面维度在到 Release 时才展开成资产名。

最终 deploy-bundle 包含 `sunuefi_esp.img`、`boot_sunuefi.img`、`PianoUEFI-product.img`、`pianoroot.f2fs.img.zst`、与封存镜像同内容的 `pianoroot.f2fs.sparse.img`、对应 manifest、`source-runs.json` 与 `SHA256SUMS`。有了 bundle 自带的 sparse，从 Release 装机不需要再跑一次 Actions。发布资产名与 `fastboot flash sunuefi_esp` / `boot_a` 的目标一致。上游 `esp-manifest.json` 里仍用内部键 `esp.img`、`boot.img`，`verify-bundle.py` 会显式映射，不要求发布包保留第二套文件名。`verify-bundle.py` 会流式展开压缩 root，核对实际 raw 大小和摘要，再记录 `HOST_VERIFIED_FEDORA_F2FS_BUNDLE_NOT_DEVICE_VERIFIED`。

Linux boot.img 是 ESP 上的 Linux 载荷。包含当前 Android 内核的合体 BOOT 必须用本机当前 stock BOOT、槽位和 generation 另行包装；通用构建产物不包含这些个人设备输入。

## 参考来源

`docs/upstreams.json` 登记研究与对照仓库，只用来限定实现范围。源码直接依赖以 `sources.lock.json` 为准，参考文档不会自动进入生产依赖。下载和生成的文件放在被忽略的 `build/`、`out/`。固件、校准和密钥不进代码仓库。

## 发布链基线

当前基线是 SunUEFI pin `065b192` 的一次完整链，每一阶段都 success：

| 阶段 | 关键产物 |
| --- | --- |
| build-kernel | `kernel-release-7.2.9`、`kernel-sdk-7.2.9` |
| host-check（FFmpeg + RPM） | `piano-fedora-ffmpeg`、`piano-rpms` |
| build-device-runtime | `piano-fedora-runtime` |
| build-rootfs | `rootfs-<rel>-<desktop>` |
| build-image | `root-image-<releasever>-<desktop>` |
| build-bundle | `deploy-bundle` |
| prepare-install | `root-install`、`boot-tools`、`host-boot-tool` |

每一轮的 run id、产物摘要与验收结论记在对应 Release 的说明和会话记录里，文档不逐轮记录。

这一轮定下的容量约定：封存镜像固定 8192 MiB，与设备无关，首次启动由 initramfs 长满 `sunuefi_root`。首装不再要求先跑 `prepare-install`：bundle 自带的 sparse 就是首装材料。`prepare-install` 仍会跑，因为 `flash-linux.sh` 的更新路径要求 `root-install/`；Release 直接带上这份产物。

`verify-bundle.py` 解码 `pianoroot.f2fs.sparse.img`，与 `pianoroot.f2fs.img.zst` 展开后的字节和摘要比对；initramfs manifest 的 `f2fs_tools_verified` 为 `true`，归档里有 `usr/sbin/fsck.f2fs` 与指向它的 `resize.f2fs`。ABL 诊断链的 manifest 是 `false`，那条链不做扩容。

root 镜像与设备容量脱钩。早期方案把 root 离线扩到目标容量，产物与某台设备绑死，同一份镜像装不到别的容量上；现在封存镜像是通用件。

## Release 发布

`.github/workflows/release.yml` 只搬运已验证的构建，不重新编译。tag 指向的 commit 必须同时有成功的 `build-bundle` 与 `prepare-install` run；缺一个就在写入任何发布资产之前失败。手动触发可以显式传 `bundle_run`、`prepare_run`，两者仍要绑定同一个 tag commit。

多档桌面的 Release 用 `desktop_runs` 给出每档一对 run（`gnome=<bundle>:<prepare>,kde=...`）。每档独立走一遍身份、状态与提交绑定校验，`root-install` 还要与它自己的 bundle 和 root 镜像逐摘要对齐。资产分两组：

- **平台资产**：`PianoUEFI-product.img`、`sunuefi_esp.img`、`boot_sunuefi.img`、`uefi-source.json`、`kernel-manifest.json`、`initramfs.tar.zst`、`initramfs-manifest.json`、`diagnostic-abl-storage.tar.zst` 与三份组件 manifest。名字不带后缀，四档逐字节相同，只上传第一档的副本，其余三档只做 `cmp`，不一致就在写 Release 之前失败。
- **桌面资产**：`pianoroot.f2fs.img.zst`、`pianoroot.f2fs.sparse.img.zst`、`root-install-manifest.json`、`root-install-SHA256SUMS`、`root-install-pianoroot.f2fs.sparse.img.zst`，以及每档的 `bundle-manifest.json`、`root-image-manifest.json`、`rootfs-manifest.json`、`source-runs.json` 与 `SHA256SUMS`。在第一处 `.` 前插入 `-<desktop>`（`root-install-SHA256SUMS` 没有扩展名，后缀接在末尾）。`esp-manifest.json` 也在这组：上游 `package_release.py` 把 `root.tar.zst` 的摘要写进 `files`，四档不同。

命名规则只在 `scripts/release-assets.py` 与 `scripts/unpack-release.sh` 各写一次，`tests/check_release_assets.py` 逐项比对两条实现。单档 Release 不带后缀，与多桌面之前的资产名一致。

发布前按顺序校验：bundle 的 `SHA256SUMS`、`verify-bundle.py`、root-install 的 `SHA256SUMS`、boot-tools 的 `installer/SHA256SUMS`，以及 root-install manifest 里 `source_bundle_manifest_sha256` 与 `source_root_manifest_sha256` 确实指当前 bundle。Release 只收 `android_slot == "_a"`；`_b` 的 root-install 留给 B 槽设备自己生成。

GitHub 的单个 release asset 必须小于 2 GiB，所以大镜像按 1,900 MiB 切片，两个 root transport 先流式 zstd 压缩。`initramfs.tar.zst` 与 `diagnostic-abl-storage.tar.zst` 用 `--sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner` 打包：tar 默认记录每个 job 自己的写入时刻，四档按字节比对会必然失败。`RELEASE-SHA256SUMS` 覆盖实际发布的扁平资产，一份覆盖全部桌面；`unpack-release.sh --desktop <档位>` 先校验它，再合并分片、解压、把桌面资产还原成规范名并解出 `root-install/`。只有一档时档位可省略，多档时缺参数报错并列出可选项。发布前用 `actions/attest-build-provenance` 对 `RELEASE-SHA256SUMS` 生成 provenance。

`boot_linux.img` 不进 Release：它由 `prepare-device-boot.py` 用每台平板自己的 96 MiB stock BOOT 生成。Release 附带 `piano-boot-tools-<tag>.tar.zst`，里面是同一 bundle 的 `boot-tools`、x86-64 `host-boot-tool` 和脚本。

## 尚需验证

2026-10-10 的主机构建验收已通过，覆盖组件清单摘要、压缩 root 展开后的 8 GiB 大小与 SHA256，以及 ESP 载荷和 boot.img 组件。那是更早一轮的构建，设备上的 Fedora 在那之后启动过。未完成的验收项见[已知问题](known-issues.md)。首次分区、恢复材料及 Android 往返仍需各自的设备证明。

上游安装器保留 ext4 合同。piano-fedora 新增独立的 F2FS 初装入口和本机 BOOT 文件准备入口。工具路径、Actions 材料、破坏性操作门禁和真机验收边界见[维护与验收](internals.md)与[首次安装](installation.md)。新增实现的构建、包装和设备验收状态分别以对应 manifest 与会话记录为准。

## 容量与 sparse 传输

执行器从实时、双副本 CRC 合格的完整 GPT 重算容量，镜像不能大于它。原 userdata 会被清空重建；GPT/BOOT 恢复文件不含 Android 用户数据。分区容量的分配规则见[安装指南的分区架构](installation.md#二-存储与分区架构原理)。

通用包里的 8 GiB F2FS 镜像保持封存。`prepare-root-capacity.py` 在私有工作副本里设置本机启动配置与传感器槽位，恢复匹配的 Fedora SELinux 标签，然后离线扩容。镜像不写任何 SSH 公钥，manifest 用 `ssh_public_key_in_image: false` 记录。

源树与扩容副本的文件、所有权、权限、链接、mtime、全部 xattr 逐项比较；只读 fsck 与 sparse 解码摘要必须通过。

sparse 文件用 RAW 与显式零 FILL，不用 DONT_CARE；展开后的完整字节与验证过的 F2FS 镜像一致。文件系统仍是 F2FS，sparse 只负责 Fastboot 传输。
