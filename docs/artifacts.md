# 交付物与流水线产物

所有构建阶段均在 CI 主机端完成。跨 run 装配严格按 manifest SHA256 绑定：仅核对 `kernel release` 字符串不足以保证模块与内核一致，所有中间环节混用不同 run 产物均会被校验脚本拒绝。

## 主机构建产物清单

| 产物 | 产生入口 | 说明与绑定规则 |
| --- | --- | --- |
| UEFI 产品 | `build-uefi.yml` | 锁定 SunUEFI 配方输出 `PianoUEFI-product.img` 与原始 manifest |
| 内核与 SDK | `build-kernel.yml` | 内建 F2FS/SELinux/Gunyah，仅出 `kernel-release-7.3.0` 与绑定的 `kernel-sdk-7.3.0` |
| 矩阵 RPM 与 SRPM | `host-check.yml` (`build-piano-rpms.yml`) | 编译 14 个矩阵包（含匹配内核的 `kernel-piano`），SRPM 供 COPR 源码重建 |
| 图形栈 RPM | `build-mesa.yml` / `build-ffmpeg.yml` | `mesa-piano-*`（Adreno A830v1）与启用 v4l2m2m 的 `ffmpeg-piano` |
| 运行时 RPM | `build-device-runtime.yml` | `piano-runtime`、`piano-crosvm`、`librevm`（`v4l2loopback` 在矩阵内核包中编译） |
| 基础 rootfs | `build-rootfs.yml` | 按 `desktop=all` 展开五档，记录容器 ID 与实际 RPM 列表 |
| F2FS 镜像与 initramfs | `build-image.yml` | 装入各组 RPM，比对 `/usr/lib/modules/<release>` 与内核 manifest 逐字节匹配后写入 F2FS |
| 交付 Bundle | `build-bundle.yml` | 汇总 `sunuefi_esp.img`、`PianoUEFI-product.img`、引导助手、sparse 镜像与校验清单 |
| 扩容与安装工具 | `prepare-install.yml` | 产出 `root-install-<desktop>`、`boot-tools` 与 `host-boot-tool` |

F2FS 镜像写入时通过 GNU tar 完整保留 xattr、ACL、capability 与 SELinux 标签；随后卸载执行 `fsck.f2fs -f --dry-run` 并以 `ro,norecovery` 挂回比对每一项文件属性，全部一致才生成最终镜像 SHA256。

`product.json` 的 `root_size_mib`（8192 MiB）决定封存镜像体积，可刷入任意容量设备（首启由 initramfs 自动撑满 `sunuefi_root`）。`prepare-install.yml` 默认产出的 prepared root 保持 8 GiB 封存尺寸，仅在显式填写目标容量时离线扩容并读回校验。

## deploy-bundle 结构

每档拥有独立的 `deploy-bundle-<desktop>` artifact，但内部文件名统一不带档位后缀，由 `scripts/release-assets.py` 唯一定义成员表：

- `sunuefi_esp.img`：512 MiB FAT32 分区镜像，按 `esp-manifest.json` 含 systemd-boot、内核/initramfs/合成 `board.dtb`（BLS 布局）或 UKI PE 文件；由 `verify-bundle.py` 流式展开并校验，记录 `HOST_VERIFIED_FEDORA_F2FS_BUNDLE_NOT_DEVICE_VERIFIED`。
- `PianoUEFI-product.img`：纯 UEFI 产品镜像（合体 BOOT 仍需用户在本地运行 `make-device-boot.sh` 基于本机 96 MiB stock BOOT 生成，不进通用构建包）。
- `sunuefi-module.zip` 与 `module-manifest.json`：Android 侧引导助手模块（v45.10 起内置，`same_product.product_sha256` 绑定同一次构建的 `PianoUEFI-product.img`）。
- `pianoroot.f2fs.img.zst` 与 `pianoroot.f2fs.sparse.img`：完整压缩镜像及等价的 sparse 传输镜像（sparse 仅含 `RAW` 与零值 `FILL` 块，不用 `DONT_CARE`）。
- `initramfs.tar.zst`：`build-image` 用确定性时间戳打出的 initramfs 中间包（五档逐字节一致，仅在 `build-bundle` 中打包进 ESP，不进入最终 Release 归档）。
- `diagnostic-abl-storage.tar.zst`、`source-runs.json`、组件 manifest 与 `SHA256SUMS`。

## Release 发布

`.github/workflows/release.yml` 不触发编译，由维护者对打好的 tag 手工 dispatch 执行（自动按 tag commit 解析并逐档核对 `head_sha`，也可用 `desktop_runs` 单独发布部分档位）。发布前依次校验 `deploy-bundle` 与 `root-install` 清单、`verify-bundle.py`、`release-assets.py check-bundle`、`boot-tools` 摘要以及 `source_bundle_manifest_sha256` / `source_root_manifest_sha256` 绑定关系（仅收 `android_slot == "_a"`）。

每个桌面档打成一个自包含归档（超过 2 GiB 自动按 1900 MiB 切片以适配 GitHub 资产上限，使用确定性 tar 参数并在打包后回解比对）：

```text
piano-<tag>-<档位>.tar.zst[.part-NN]   # 桌面自包含归档（GNOME/KDE 约 2.1-2.8 GiB，其余约 1 GiB）
piano-<tag>-<档位>.sha256             # 对应分片校验文件
RELEASE-SHA256SUMS                    # 全量资产汇总哈希（供 actions/attest-build-provenance 认证）
```

常规五档全发布共包含 7 个归档分片、5 份 `.sha256` sidecar 与 `RELEASE-SHA256SUMS` 共 13 个基础资产（随 extras 归档一起发布时为 15 个）。用 `gh attestation verify <任一分片>` 可验证 provenance（`RELEASE-SHA256SUMS` 自身不作为 attestation subject）。

归档解压后即是一键/手动安装所需的暂存目录，内含平台镜像（保持未二次压缩以便脚本直接读取）、当前档 `root-install/`、`flash-linux.{sh,bat}`、`README-INSTALL.txt`、`make-device-boot.sh`、`boot-tools/`、`host-boot-tool/`、组件清单与精确匹配现有文件的 `SHA256SUMS`。不放入通用归档的三类文件（raw zst 镜像、根目录 duplicate sparse、`initramfs.tar.zst`、以及未做真机认证的 `diagnostic-abl-storage.tar.zst`）均可按需直接从 Actions artifact 下载。

> 参考仓库列表登记在 `docs/upstreams.json`，实际编译与打包消费的提交仅以 `sources.lock.json` 为准，下载或临时输出均放入 `.gitignore` 覆盖的 `build/`、`out/` 目录。
