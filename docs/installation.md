# Xiaomi Pad 8 Pro (piano) 安装指南

> 日常系统与内核升级、双系统往返见[升级与回滚](upgrade.md)。

本指南说明如何在小米平板 8 Pro（`piano`，骁龙 8 至尊版 SM8750）上划出 Linux 分区、刷入 Fedora 45 并完成首次登录。默认档位是 GNOME，其余四档差异见[桌面档](desktops.md)。

手动路径逐条列出命令，是主路径；一键脚本做完全相同的写入，两者的分区与刷写内容一致。

---

## 一、 前提

1. 通过小米官方渠道解锁 Bootloader，刷机前保持电量 > 30%。
2. 电脑端装好 `adb`、`fastboot`（Android Platform Tools）及下表工具；Linux 宿主机需配置 USB udev 规则或使用 `sudo`。
3. 备份 Android 个人数据：默认首次分区会清空 `userdata`。首次分区需要 Android 的 root 权限（获取 root 不在本仓库讨论范围内）。

### 电脑端工具

| 工具 | 用途 | 缺少时的影响 |
| --- | --- | --- |
| `sha256sum`、`tar`、`zstd` | 校验并解包归档（GNU tar `--zstd` 仍依赖外部 `zstd`） | 无法解包 |
| `python3` | 一键脚本解析清单，`make-device-boot.sh` 重打包 BOOT | 一键脚本停在校验前（Windows 一键脚本用内置 PowerShell，解包可用 7-Zip） |
| `adb` | 读取设备原厂 BOOT | `make-device-boot.sh` 无法生成 `device-boot/` |
| `mtools`（`mcopy`、`mtype`） | 向 ESP 注入 SSH 公钥、核对 ESP 内核版本 | 只能手动刷写且不注入公钥 |

```sh
# Fedora
sudo dnf install coreutils zstd python3 android-tools mtools
# Ubuntu / Debian
sudo apt install coreutils zstd python3 adb mtools
# macOS
brew install zstd android-platform-tools mtools
```

---

## 二、 存储与分区架构原理

平板采用 UFS 4.0 闪存与高通 A/B 槽位（出厂设备多为 Slot A，OTA 到 HyperOS 4 后多为 Slot B）：

```text
                    ┌────────────────────────────────────────┐
                    │       UFS 4.0 物理闪存 (LUN 0-5)        │
                    └───────────────────┬────────────────────┘
                                        │
        ┌───────────────────────────────┴───────────────────────────────┐
        ▼                                                               ▼
  【活动槽（如 Slot A）】                                      【另一槽（保持原厂，不切槽）】
  - boot_a（合体 BOOT：Android 内核 + 3 秒选择器 + SunUEFI）     - boot_b / dtbo_b / init_boot_b
  - dtbo_a / init_boot_a / super / userdata
  - sunuefi_esp（512 MiB，FAT32，GPT 顶层分区）
  - sunuefi_root（剩余空间，F2FS，GPT 顶层分区）
```

> **不要擦除 `dtbo_a` 或 `dtbo_b`。** SunUEFI 的板级 DTB 由活动槽 `vendor_boot` 叠加原厂 `dtbo` 拼装；ABL 找不到活动槽 DTBO 会报 `Failed to load/authenticate boot image: Device Error` 并卡在 Fastboot。

- `sunuefi_esp`（512 MiB，FAT32）：存放 systemd-boot、合成板级 DTB、Linux 7.3 内核、initramfs 与启动条目。
- `sunuefi_root`（F2FS）：Fedora 45 根文件系统。
- `boot_a` 或 `boot_b`：活动槽合体 BOOT，Linux 与 Android 共用此入口启动；`qbootctl.service` 在每次 Linux 开机成功后写入 `Successful = 1`，防止 ABL 计数器归零掉进 Fastboot。

### 容量计算与分区方式

每台设备按自己实时的 GPT 计算分区（512 GiB、1 TiB 等均通用）：保留 Android 全部固定分区，从尾部缩短 `userdata`，划出 1 MiB 对齐的 `sunuefi_esp`（512 MiB）与 `sunuefi_root`（默认与 `userdata` 对半平分）。`product.json` 的 `root_size_mib`（8192 MiB）仅是封存镜像尺寸，首次启动时 initramfs 会将 F2FS 自动撑满 `sunuefi_root`。

由于原厂 Fastboot 不支持改分区表，首次分区只有两条路（均需 Android root）：

- 用 `install-f2fs.py apply` 在线计算并写入（第四节 1）。
- 按第四节 2 在 Android 上用 `adb shell su -c` 手动切表。

---

## 三、 准备安装材料

两条安装路径使用同一套材料。每个档位一个归档，只下自己那一档。

### 1. 取资产

在 [Releases 页面](https://github.com/FFT-infra/piano-fedora/releases) 选一个 tag，再选一个档位。需要下载的文件以 `piano-<tag>-<档位>` 开头：

- `piano-<tag>-<档位>.sha256`，这一档的摘要清单。
- `piano-<tag>-<档位>.tar.zst`，或它的全部分片 `piano-<tag>-<档位>.tar.zst.part-NN`。

归档超过 2 GiB 时按 1900 MiB 切片。GNOME 与 KDE 通常有 `.part-00` 与 `.part-01` 两个分片，niri 与 Hyprland 只有一个文件。分片缺一个都解不开。

别的档位是各自的归档，装机不需要。

### 2. 解包

把下载到的文件放进同一个空目录，按顺序执行三条命令：

```sh
sha256sum -c piano-<tag>-<档位>.sha256
cat piano-<tag>-<档位>.tar.zst.part-* | zstd -dc | tar -x
sha256sum -c SHA256SUMS
```

第二行在没有分片时换成 `zstd -dc piano-<tag>-<档位>.tar.zst | tar -x`。GNOME 与 KDE 有分片，用上面那条。

解完后，这个目录就是暂存目录。第五节与 [升级与回滚](upgrade.md) 里的命令都在这里执行。再校验一次 prepared root：

```sh
(cd root-install && sha256sum -c SHA256SUMS)
```

也可以让仓库 `scripts/` 目录下的 `unpack-release.sh` 代做下载、校验与解包：

```sh
bash scripts/unpack-release.sh --tag <tag> --desktop <档位> --dir piano-install
```

脚本只拉这一档的文件，已经在盘上的跳过。`unpack-release.sh` 最后跑的校验与上面两条相同。

Windows 上用 7-Zip 打开 `.tar.zst`。分片先合并：`copy /b piano-<tag>-<档位>.tar.zst.part-00+piano-<tag>-<档位>.tar.zst.part-01 piano-<tag>-<档位>.tar.zst`。

### 3. 归档内容

归档解压后包含以下文件，对应刷写目标与用途划分如下：

| 文件 | 用途 | 刷到哪 |
| --- | --- | --- |
| `sunuefi_esp.img` | SunUEFI ESP 分区镜像 | `sunuefi_esp` |
| `root-install/pianoroot.f2fs.sparse.img` | 已写入设备配置的根文件系统 | `sunuefi_root` |
| `PianoUEFI-product.img`、`uefi-source.json` | 产品固件与来源记录 | 不刷入，首装校验用 |
| `flash-linux.sh`、`flash-linux.bat`、`README-INSTALL.txt` | 一键脚本与说明 | 不刷入 |
| `make-device-boot.sh`、`boot-tools/`、`host-boot-tool/`、`scripts/` | 生成设备专用 BOOT | 不刷入 |
| `SHA256SUMS` | 归档内全部文件的摘要 | 不刷入 |
| bundle 与组件 manifest | 来源绑定与内核、运行时记录 | 不刷入，供校验 |

`root-install/` 里的 sparse 镜像已写入设备配置并恢复 SELinux 标签。没有指定 `root_size_mib` 时，sparse 镜像保持封存容量，首次启动再扩满 `sunuefi_root`。Release 固定使用 Android Slot A（`_a`）。B 槽设备要用同一次 bundle run 自己跑 `prepare-install.yml` 的 `_b`。

Linux 内核不在归档里单独出现。它在 `sunuefi_esp.img` 里，由固件或 systemd-boot 加载。

镜像里没有 `authorized_keys`。管理公钥在刷写时用 `--ssh-key` 注入，手动路径见[第五节](#五-手动刷写-linux)，脚本路径见[第六节](#六-一键脚本)。

完整镜像、封存镜像、`initramfs.tar.zst` 与 `diagnostic-abl-storage.tar.zst` 不进归档。完整镜像与封存镜像和 `root-install` 里的 root 内容相同，不能直接刷。`initramfs.tar.zst` 与 `diagnostic-abl-storage.tar.zst` 只给构建链用。需要时从对应的 workflow artifact 取。

### 4. 没有 Release 时

用同一次成功 `build-bundle` run 的 `deploy-bundle-<档位>`，以及这个 run 派生的 `prepare-install` 的 `root-install-<档位>`：

1. 把同一 run 的 `deploy-bundle-<档位>` 全部普通文件解压到暂存目录。
2. 把 `root-install-<档位>` 解到 `root-install/`。
3. 从 `scripts/installer/` 复制 `flash-linux.sh`、`flash-linux.bat`、`README-INSTALL.txt`，从 `scripts/` 复制 `make-device-boot.sh`、`prepare-device-boot.py`、`piano_artifacts.py`、`piano_sparse.py`。
4. 不要让两个 artifact 的 `SHA256SUMS` 互相覆盖。

不要从不同 run 各取一部分。

### 5. 生成设备专用 BOOT

归档里没有 `device-boot/boot_linux.img`。合体 BOOT 含这台平板当前的 Android 内核，因此只有本机能生成。

归档里的 `make-device-boot.sh` 一次做完全部步骤：用 `adb` 读活动槽的 boot 分区（`--slot a|b`，默认从设备当前槽推断）、核对 96 MiB 与设备侧摘要、调 `scripts/prepare-device-boot.py` 重打包。

```sh
./make-device-boot.sh            # 只有一台设备连着时可以省略 --serial
./make-device-boot.sh --serial <序列号>
```

`make-device-boot.sh` 要求 Android 侧的 root ADB。读回不足 96 MiB 或两条摘要不一致时，脚本不写任何东西。产出的 `device-boot/` 已有同名目录时也会拒绝覆盖。

原厂 ABL 不实现 `fastboot fetch`，所以归档不教这条命令。它只会返回一个 0 字节文件。

活动槽的 boot 分区只写 `device-boot/boot_linux.img`。`sunuefi_esp.img` 是分区镜像，ABL 不认，刷进 boot 分区也放不下。

### 6. 暂存目录

解包后的目录长这样：

```text
piano-install/
├── README-INSTALL.txt
├── SHA256SUMS
├── sunuefi_esp.img
├── PianoUEFI-product.img
├── flash-linux.sh
├── flash-linux.bat
├── make-device-boot.sh
├── bundle-manifest.json
├── root-image-manifest.json
├── boot-tools/
├── host-boot-tool/
├── scripts/
├── device-boot/              # make-device-boot.sh 生成
│   ├── boot_android.img
│   ├── boot_linux.img
│   └── device-boot-manifest.json
└── root-install/
    ├── pianoroot.f2fs.sparse.img
    ├── root-install-manifest.json
    └── SHA256SUMS
```

组件 manifest（`kernel-manifest.json`、`esp-manifest.json` 等）与两个工具目录留在原位，一键脚本读它们。

`device-boot/` 不属于归档，一键脚本单独核对它的结构。

### 7. 校验材料

在暂存目录执行（macOS 把 `sha256sum` 换成 `shasum -a 256`）：

```sh
sha256sum -c SHA256SUMS
(cd root-install && sha256sum -c SHA256SUMS)
```

两份清单属于两个独立来源。任意一行出现 `FAILED`，立刻停止，不要继续。

归档的 `SHA256SUMS` 只列归档里真实存在的文件，这份命令不需要挑行。`root-install/SHA256SUMS` 覆盖 sparse root 与 root-install manifest。

一键脚本还会核对 root-install manifest 里的 `source_bundle_manifest_sha256` 与 `source_root_manifest_sha256` 是否指向归档里的 `bundle-manifest.json` 与 `root-image-manifest.json`。三者对不上，说明 root 来自别的构建。

> **完成判据**：两份 `sha256sum -c` 校验全部通过（输出均为 `OK`），`root-install/` 与 `boot-tools/` 已就位。

## 四、 从原始 GPT/UFS 开始的首次安装

本节面向还没有 `sunuefi_esp` 与 `sunuefi_root` 的设备。设备已经有这两个分区时，跳过本节，直接看 [升级与回滚](upgrade.md) 的更新入口。

`install-f2fs.py apply` 会清空 `userdata`，不能用它做更新。

### 0. 保留 Android 数据的首装（`--lossless`）

默认路径清空 `userdata`。需要保留 Android 数据时加 `--lossless`：先在 Android 上把解密后的 F2FS 缩到新容量，再写分区表，全程不擦除。前提是设备已 root、已装与当前 BOOT 匹配的「SunUEFI 引导助手」模块（`piano-resize-f2fs` 随模块提供），并且 `userdata` 的空闲块够用。

分区表要先是首装规划器接受的形态：没有 `sunuefi_root`、`sunuefi_esp`，`userdata` 是最后一个分区并顶到盘尾。设备上已经有 Linux 分区时，先在 Android 的「SunUEFI 引导助手」WebUI 存储页用 **删除并归还 Android 空间** 删掉 `sunuefi_esp`、`sunuefi_root` 并把 `userdata` 拉回盘尾；这一步需要支持早期 init 配置的 SukiSU，早期维护会在下次启动把加密 F2FS 扩到新容量。操作顺序见上游的 [Android 存储扩容](https://github.com/slwyts/Project-SunUEFI/blob/18274377d11e0a12288ffbb795b9982b6a9804f4/docs/devel/boot/android-storage.md)。

`plan` 与 `apply` 都要带 `--lossless`，且都不能带 `--accept-data-loss`。计划里会多出 `f2fs_shrink.userdata_bytes`，那是设备侧缩容的目标容量；新 `userdata` 长度按 F2FS section 对齐，section 大小由设备上的解密映射读出，宿主机读不到。

```sh
python3 boot-tools/installer/scripts/install-f2fs.py plan \
  --sunuefi boot-tools/installer/sunuefi --serial <序列号> \
  --bundle . --root-install root-install --device-boot device-boot \
  --stock-boot stock-recovery/boot.img --plan lossless-plan.json --lossless
```

执行顺序与默认路径相反：先 `piano-resize-f2fs shrink`，再写 GPT，然后直接刷镜像，没有 `fastboot erase userdata`。缩容成功而写表失败时，文件系统比分区小，是安全状态，Android 照常启动，只是少了空间。反过来永远不会发生。

失败时执行器不自动重试、不擦除、不重新分区，`result.json` 里记着原 GPT 的两段备份和恢复说明。先看 `f2fs_shrink` 字段有没有写进去，再决定是恢复 GPT 还是继续。

该无损安装路径已在 Slot B 设备验证，限制与说明见[已知问题](known-issues.md#无损首装)。默认推荐路径仍为清空安装。

### 1. 自动分区

在已 root 的 Android 上生成只读计划：

```sh
python3 boot-tools/installer/scripts/install-f2fs.py plan \
  --sunuefi boot-tools/installer/sunuefi --serial <序列号> \
  --bundle . --root-install root-install --device-boot device-boot \
  --stock-boot stock-recovery/boot.img --plan first-install-plan.json
```

逐项复核计划，再执行。`apply` 会清空 userdata，必须同时给 `--execute`、`--accept-data-loss` 和一个尚不存在的 session 目录：

```sh
python3 boot-tools/installer/scripts/install-f2fs.py apply \
  --sunuefi boot-tools/installer/sunuefi --serial <序列号> \
  --bundle . --root-install root-install --device-boot device-boot \
  --stock-boot stock-recovery/boot.img --plan first-install-plan.json \
  --session sessions/first-install --execute --accept-data-loss
```

每步的输入契约、run id 记录与验收门槛见[维护与验收](internals.md)。

### 2. 手动分区

手动路径只用设备上一定有的 `dd`，配合 `scripts/plan-first-partition.py` 离线算出新的整表。每一步都能单独核对。容量与默认分配不一致时，或者想自己控制边界时，用这条路径。

**这些命令会清空 userdata。执行前先备份 Android 数据。**

#### 2.1 读取真实 GPT

设备必须停在原厂 Fastboot（不是 fastbootd），并且 `current-slot` 是 `a`：

```sh
fastboot getvar current-slot
fastboot getvar partition-size:userdata
fastboot getvar partition-size:sunuefi_esp
```

Android 侧需要 root。整盘是 `/dev/block/sda`，可写目录是 `/data/local/tmp`。下面这一段在宿主 shell 里跑完。变量留在宿主上，后面几步都要用。

`/sys/block/sda/size` 的单位是 512 字节扇区，GPT 用的是 4096 字节逻辑块，两者差 8 倍。不能直接把 `/sys/block/sda/size` 当 LBA 总数用。总 LBA 数以主表头里的 `alternate_lba` 为准，`alternate_lba` 就是磁盘最后一个 LBA。表项数也由表头决定，两份 blob 的长度都从表头算：

```sh
DISK=/dev/block/sda
TMP=/data/local/tmp
adb shell su -c "dd if=$DISK of=$TMP/gpt-header.raw bs=4096 count=2"
adb pull $TMP/gpt-header.raw .
LAST=$(od -An -tu8 -j 4128 -N 8 gpt-header.raw | tr -d ' ')
ENTRIES=$(od -An -tu4 -j 4176 -N 4 gpt-header.raw | tr -d ' ')
TOTAL=$((LAST + 1))
BLOCKS=$(( (ENTRIES * 128 + 4095) / 4096 ))
echo "last LBA $LAST, total $TOTAL, $ENTRIES entries, $BLOCKS table blocks"
adb shell su -c "dd if=$DISK of=$TMP/gpt-head.raw bs=4096 count=$((2 + BLOCKS))"
adb shell su -c "dd if=$DISK of=$TMP/gpt-backup-header.raw bs=4096 skip=$LAST count=1"
adb pull $TMP/gpt-backup-header.raw .
TAIL_FIRST=$(od -An -tu8 -j 72 -N 8 gpt-backup-header.raw | tr -d ' ')
TAIL_BLOCKS=$((LAST - TAIL_FIRST + 1))
echo "backup table at LBA $TAIL_FIRST, $TAIL_BLOCKS blocks"
adb shell su -c "dd if=$DISK of=$TMP/gpt-tail.raw bs=4096 skip=$TAIL_FIRST count=$TAIL_BLOCKS"
adb pull $TMP/gpt-head.raw $TMP/gpt-tail.raw .
```

尾部不能按 `LAST-BLOCKS` 推算。原厂 `gpt_backup0.bin` 是 5 个块，备份表之后留 2 个空块才到备份表头。`partition_entry_lba` 是备份表的真实起始位置（这里是 `LAST-4`）。先读最后一块取到备份表头，再按备份表头算起点和长度。

两份原始读数就是分区前的 GPT 基线。`plan-first-partition.py --head/--tail --sectors` 能直接解析这两份读数。设备已经跑着 Linux 时，跳过 `adb`，把 `DISK` 换成 `/dev/sda`、`TMP` 换成 `/tmp`，其余相同。

#### 2.2 算容量

以 `L` 表示最后可用扇区，`U` 表示当前 `userdata` 的起始扇区，`S` 表示扇区大小（字节）：

```text
可用总扇区 = L - U + 1
ESP 扇区   = 512 * 1024 * 1024 / S        # 512 MiB，S=4096 时是 131072
root 扇区  = (可用总扇区 - ESP 扇区) / 2  # 对半。也可以自己指定
```

给 Android 固定容量时，把最后一行换成下面两行，其余不变。`307200` 是 300 GiB 的 MiB 数。`userdata` 起点不动，所以 Android 实际拿到的就是这个值：

```text
userdata 扇区 = 307200 * (1024 * 1024 / S)
root 扇区     = 可用总扇区 - ESP 扇区 - userdata 扇区
```

三个分区的边界：

```text
sunuefi_esp  最后扇区 = L
sunuefi_esp  起始扇区 = 向下对齐 1 MiB(L - ESP 扇区 + 1)
sunuefi_root 最后扇区 = ESP 起始扇区 - 1
sunuefi_root 起始扇区 = 向下对齐 1 MiB(ESP 起始扇区 - root 扇区)
userdata     起始扇区保持不变，最后扇区改为 root 起始扇区 - 1
```

1 MiB 对齐会把起始扇区往前推。实际容量大于等于请求值。Android 与 Linux 两侧相差不超过 2 MiB。

这套算术不用手算。把两份现场读数拉回本机，交给规划器。规划器写出新的整表与两份摘要：

```sh
python3 scripts/plan-first-partition.py --head gpt-head.raw --tail gpt-tail.raw \
  --sectors "$TOTAL" --userdata-mib 307200 \
  --output first-install-plan.json --emit-gpt gpt/
```

设备上已经有 Linux、能直接读整盘时，跳过两份读数，直接 `--device /dev/sda`，其余参数相同。

`--emit-gpt` 产出四个文件与一份清单：`gpt-head.bin`（保护性 MBR + 主表头 + 主分区表）、`gpt-tail.bin`（备份分区表 + 空隙 + 备份表头）、`original-gpt-head.bin`、`original-gpt-tail.bin`（分区前的原表）、`SHA256SUMS`。尾部长度随现场表头，原厂表是 `TAIL_BLOCKS` 个块。这些是文件，还没写进设备。规划器写出前会核对现场读数与计划是否来自同一张表，不一致就停。

只有文本 dump 时也能规划，但不能 `--emit-gpt`。`sfdisk --dump` 的文本不带总扇区数与表项数，要自己补。`--sectors` 要的是 4096 字节 LBA 总数，不是 `/sys/block/sda/size`：

```sh
adb shell su -c "sfdisk --dump $DISK" > gpt.txt
python3 scripts/plan-first-partition.py --dump gpt.txt --sectors "$TOTAL" \
  --entries "$ENTRIES" --userdata-mib 307200
```

#### 2.3 写 GPT

先回读核对：`gpt-head.bin` 与 `gpt-tail.bin` 推上设备后，必须与本地逐字节相同。

```sh
adb push gpt/gpt-tail.bin gpt/gpt-head.bin $TMP/
adb shell su -c "sha256sum $TMP/gpt-tail.bin $TMP/gpt-head.bin"
sha256sum gpt/gpt-tail.bin gpt/gpt-head.bin
```

再按先尾后头的顺序写入。尾部先落盘，中断后旧主表头的 `alternate_lba` 仍能找到新尾部。顺序反过来会留下“头新尾旧”的不一致状态。尾部 blob 从 `TAIL_FIRST` 开始，占 `TAIL_BLOCKS` 个块。头部占 2 个块加同一张表：

```sh
adb shell su -c "dd if=$TMP/gpt-tail.bin of=$DISK bs=4096 seek=$TAIL_FIRST conv=fsync,notrunc"
adb shell su -c "dd if=$TMP/gpt-head.bin of=$DISK bs=4096 seek=0 conv=fsync,notrunc && sync"
```

写完立刻回读。两份都必须与源 blob 相同：

```sh
adb shell su -c "dd if=$DISK bs=4096 skip=$TAIL_FIRST count=$TAIL_BLOCKS" | sha256sum
adb shell su -c "dd if=$DISK bs=4096 count=$((BLOCKS+2))" | sha256sum
```

核对三点：`userdata` 的起始扇区与原来相同，`sunuefi_esp` 结束在 `L`，`sunuefi_root` 紧贴 `sunuefi_esp` 之前。任一项不一致，立刻用 `original-gpt-*.bin` 按同样顺序写回，不要继续。

写回的顺序与写入相反：先恢复头部，再恢复尾部。`original-gpt-*.bin` 是分区前从设备读到的原表，写回去等于撤销这次分区。

```sh
adb push gpt/original-gpt-head.bin gpt/original-gpt-tail.bin $TMP/
adb shell su -c "dd if=$TMP/original-gpt-head.bin of=$DISK bs=4096 seek=0 conv=fsync,notrunc"
adb shell su -c "dd if=$TMP/original-gpt-tail.bin of=$DISK bs=4096 seek=$TAIL_FIRST conv=fsync,notrunc && sync"
```

#### 2.4 让 Android 重建 userdata

分区变短后，原来的加密 userdata 不再可信。回到 Fastboot，显式擦除 `userdata`：

```sh
fastboot erase userdata
fastboot reboot
```

实机上擦除不一定能让 Android 自动格式化。两次安装都停在 Recovery，屏幕底部显示 `Couldn't initialize user 0`。看到这一行，按 Recovery 的提示选“清除数据”，Android 才会重建加密分区并完成首次开机。不要反复切槽或重写分区表。

等 Android 首次开机完成，`adb shell getprop sys.boot_completed` 返回 `1`。这一步不能跳过，否则 Android 会反复重启或加密失败。

> **完成判据**：回读输出的扇区起止与上表计算值一致无重叠，Android 顺利重建 userdata 并开机，`sys.boot_completed` 确认为 `1`。

### 3. 请求页

`device-boot-manifest.json` 的 `active_slot` 须与当前设备活动槽一致（`_a` 或 `_b`），`app_generation` 必须与归档里的 `PianoUEFI-product.img` 中的 APPv1 载荷一致。

3 秒选择器在“正在引导的那份镜像”末尾读请求页。ABL 只加载当前槽，`BootSelect.c` 按 `Base + align(original_image_size)` 取页。合体 BOOT 装进活动槽，Linux 与 Android 都从同一份 `boot<槽>` 启动，请求页因此指向它。不切槽，也不存在第二份需要同步的页。

活动槽不写进镜像，也不写进 ESP：`piano-next-boot-helper` 每次请求时只读主备 GPT 和高通 A/B 属性现算，再读那份 BOOT 的实际 generation。所以换槽、OTA 之后都不需要重新配置，也不存在“猜错设备名”这条失败路径。

---

## 五、 手动刷写 Linux

本节是主路径。第六节的一键脚本执行同样的写入。活动槽的 `dtbo` 一律不动。

### 1. 确认设备和槽位

平板关机后长按【电源键 + 音量减键】进入 Fastboot，插好数据线，然后：

```sh
fastboot devices
fastboot getvar product        # 必须是 piano
fastboot getvar current-slot   # a 或 b，合体 BOOT 写进这个槽的 boot 分区
fastboot getvar unlocked       # 必须是 yes
fastboot getvar is-userspace   # 必须是 no
```

任一项不对就停下，不要写任何分区。以下情况都算不对：`product` 不是 `piano`、`unlocked` 不是 `yes`、`is-userspace` 是 `yes`（说明落在 fastbootd，不是原厂 ABL）。

`current-slot` 是 `a` 或 `b` 都行，合体 BOOT 写进这个槽的 boot 分区。`root-install-manifest.json` 的 `android_slot` 必须与它一致：出厂设备是 `_a`，OTA 到 HyperOS 4 的设备是 `_b`，后者的 super 里只有 `_b` 那一套逻辑分区。不一致时 `prepare-install` 生成的 root 镜像装上去也没有传感器配置，刷写前就停下。

刷写前读分区容量，把十六进制换算成字节：

```sh
fastboot getvar partition-size:sunuefi_root
```

`root-install-manifest.json` 的 `expanded_bytes` 必须小于等于分区容量。`expanded_bytes` 大于分区容量时立刻停。小于时首次启动会把文件系统扩满 `sunuefi_root`。

### 2. 写入设备

要装 SSH 公钥时，在刷 ESP 之前先把公钥写进 ESP 镜像。这一步必须在 `sha256sum -c` 通过之后做，因为注入公钥会改变 `sunuefi_esp.img` 的字节：

```sh
# 可选：把管理公钥写进 ESP（只接受 ssh-ed25519 / ssh-rsa 公钥）
mcopy -o -i sunuefi_esp.img ~/.ssh/id_ed25519.pub ::/piano-ssh-key.pub
sha256sum sunuefi_esp.img      # 记下新摘要，读回校验用这个值
```

设备首次启动时，`piano-first-boot-ssh-key.service` 把 `/boot/efi/piano-ssh-key.pub` 追加到 `piano` 的 `authorized_keys`，然后删掉 ESP 上的副本。如果跳过注入这一步，系统只保留口令登录。

格式不对的公钥（多行、私钥、不是 `ssh-ed25519`/`ssh-rsa`）不会被导入，helper 直接失败。

按顺序执行四条命令：

```sh
# 1. 合体 BOOT -> 活动槽的 boot 分区（不切槽）
SLOT=$(fastboot getvar current-slot 2>&1 | awk '/^current-slot:/{print $2}')
BOOT_PART="boot_${SLOT}"
fastboot -S 128M flash "${BOOT_PART}" device-boot/boot_linux.img

# 2. ESP -> sunuefi_esp
fastboot -S 128M flash sunuefi_esp sunuefi_esp.img

# 3. 根文件系统 -> sunuefi_root（sparse 只传有效块，需要几分钟）
fastboot -S 128M flash sunuefi_root root-install/pianoroot.f2fs.sparse.img

# 4. 重启进系统
fastboot reboot
```

`-S 128M` 让 fastboot 把每次传输切到不超过 128 MiB。钢琴 ABL 对单次 512 MiB 的裸 ESP 写入会停止响应，只能断电重来。三条 flash 命令都不要省这个参数。

每条命令都必须打印 `OKAY`。出现 `FAILED`，立刻停下，不要继续后面的步骤。

活动槽的 boot 分区要写的是 `prepare-device-boot.py` 用本机原厂 BOOT 重打包出的 `device-boot/boot_linux.img`。归档里没有另一个能直接刷的 Linux 内核文件：内核与 initramfs 都在 `sunuefi_esp.img` 里，由 SunUEFI 固件加载。`sunuefi_esp.img` 是整分区镜像，只能写 `sunuefi_esp`。写进 boot 分区会让 ABL 停在 Fastboot。

合体 BOOT 保持上游的 unsigned AVB NONE 元数据。`prepare-device-boot.py` 不补 `os_version`，也不重新签名。活动槽的 ABL 直接加载这份镜像。同一份未签名镜像已在真机上完成 Android→Linux→Android 往返。不要自行补 `os_version` 或 RSA4096 footer：`piano-boot-repack` 的 probe/request/adopt 会把首块与包装器里保存的原厂头逐字节比较。改了偏移 16，Android 侧的引导助手就认不出当前 BOOT，`switch linux` 会直接失败。

不要执行这些命令：

- `fastboot erase dtbo_a`、`fastboot erase dtbo_b`
- `fastboot erase boot_a`、`fastboot erase boot_b`
- `fastboot erase userdata`
- `fastboot -w`
- 写 `abl`、`xbl`、`tz`、`modem` 等引导链分区

### 3. 验证刷写结果

`fastboot flash` 的 `OKAY` 只说明数据发完了，不说明分区内容正确。重启前可以读回校验。`sunuefi_esp` 约 512 MiB，读回需要几分钟：

```sh
fastboot getvar partition-size:sunuefi_esp     # 十六进制字节数
fastboot fetch sunuefi_esp esp-readback.img
```

`sunuefi_esp` 分区通常比 `sunuefi_esp.img` 大（按 512 MiB 规划分区，镜像 536870912 字节）。实际分区字节数以 `fastboot getvar` 的结果为准。`fetch` 读的是整个分区，尾部会带上分区里原有的数据，整份哈希必然不同。

只比对镜像长度那一段：

```sh
head -c "$(wc -c < sunuefi_esp.img)" esp-readback.img > esp-readback-head.img
sha256sum esp-readback-head.img sunuefi_esp.img
# 两行哈希必须相同
```

分区容量与镜像字节数完全相等时省掉 `head -c`，直接比对整份。容量比镜像小时，分区写不下，立刻停下。

这台平板的原厂 ABL 不实现 `fetch`。主机 fastboot 37.0.0 能识别这个子命令，设备会回 `Device does not support fetch command`，主机上还会留下一个 0 字节的读回文件。遇到这条回应，跳过读回，改看下面的启动后验证。不要因此重刷。

注入过 SSH 公钥时，读回比对必须用注入后重新算出的摘要，不能用归档 `SHA256SUMS` 里的旧值。`mcopy` 就地改写 `sunuefi_esp.img`，注入前后两份摘要本来就不一样。用旧摘要比对会把正常的镜像判成损坏。

启动后验证三项，三项都要满足：

```sh
uname -r              # 必须等于 ESP 里那个内核的 release（见下）
findmnt /             # 根文件系统为 F2FS
systemctl --failed    # 输出为空
```

`uname -r` 的正确值是这一版 ESP 里实际装的内核，不是写死的旧版本号。两处可以查到，两边必须一致：

```sh
# 1) ESP 镜像里的内核版本（刷写前就能查，不需要设备）
mdir -i sunuefi_esp.img ::/linux/          # 只有一个 release 目录
mtype -i sunuefi_esp.img ::/linux/<release>/linux > Image
strings -a Image | grep -m1 -oE '[0-9.]+-piano[^ ]*g[0-9a-f]+'

# 2) 设备启动后的实际内核
uname -r

# 3) 模块目录必须与之同名，否则驱动根本不会加载
ls /usr/lib/modules/
```

`uname -r` 与 ESP 里的字符串不符，说明刷进去的不是这一版资产。`/usr/lib/modules/` 下没有同名目录，说明 rootfs 与 ESP 的内核不是一套，initramfs 会停在救援环境，无法进入系统。

能进所选档位的桌面，就是刷写成功最直接的证据。

这些检查要在从原始 GPT/UFS 重新分区的设备上做一次才有意义。在旧的 `sunuefi_root` 上热替换文件，或者用历史资产启动的机器，都不能说明这份镜像能装。

### 4. 回滚到 Android

Linux 起不来时，先从 3 秒选择器选 Android。开机前 3 秒按音量键进入菜单，或者在 Linux 正常运行时执行 `piano-next-boot android --reboot`。

合体 BOOT 本身损坏时，把备份的原厂引导镜像刷回活动槽的 boot 分区。不需要改动分区或活动槽：

```sh
SLOT=$(fastboot getvar current-slot 2>&1 | awk '/^current-slot:/{print $2}')
fastboot flash "boot_${SLOT}" stock-recovery/boot.img
fastboot reboot
```

要重新刷 Linux，直接重复本节。`userdata` 清空后无法恢复，所以不要擦除它。

---

## 六、 一键脚本

一键脚本执行第五节的全部步骤，并在写入前做更多检查。一键脚本要求分区已由 `install-f2fs.py apply` 或第四节建立。原厂设备只有 Android 的 `userdata`，没有 `sunuefi_esp` / `sunuefi_root`，一键脚本会拒绝执行。清空分区是首次安装的破坏性步骤，不能由一键脚本代劳。

一键脚本把合体 BOOT 写进当前活动槽，`a` 或 `b` 都行；`root-install` 的 `android_slot` 必须与当前槽一致，不一致时在任何写入前就退出。

### 1. 运行

先进入暂存目录：

```sh
cd piano-install
```

Linux / macOS 主机：

```sh
chmod +x flash-linux.sh
./flash-linux.sh
```

需要给 `piano` 用户装一把 SSH 公钥时：

```sh
./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub
```

Windows 主机直接双击运行 `flash-linux.bat`。

`--ssh-key` 只接受单行 `ssh-ed25519` 或 `ssh-rsa` 公钥，绝不传私钥。一键脚本在两份 `SHA256SUMS`、bundle/root 来源、槽位和容量全部校验通过、且用户确认之后，才用 `mcopy -o -i sunuefi_esp.img <公钥> ::/piano-ssh-key.pub` 把公钥写进 ESP 镜像，然后重新计算 ESP 摘要并打印。

注入会改变 `sunuefi_esp.img` 的字节，它和归档的 `SHA256SUMS` 就不再一致。重跑前重新解一次归档。刷写后读回核对时，用脚本打印的新摘要，不要用清单里的旧值。

Windows 脚本不自动注入。需要时按 `flash-linux.bat` 的提示手工执行 `mcopy`。

### 2. 脚本执行顺序

1. 检查 `fastboot` 是否可用、设备是否在线。
2. 确认产品代号是 `piano`，防止误刷。
3. 按归档的 `SHA256SUMS` 与 `root-install/SHA256SUMS` 分别校验 BOOT/ESP 和 sparse root。
4. 打印安全告知，要求输入大写 `YES` 确认。
5. 保持当前活动槽，不切槽。
6. 刷入合体 BOOT：`fastboot -S 128M flash boot<当前槽> device-boot/boot_linux.img`。
7. 刷入 ESP 分区：`fastboot -S 128M flash sunuefi_esp sunuefi_esp.img`。
8. 刷入系统根分区：`fastboot -S 128M flash sunuefi_root pianoroot.f2fs.sparse.img`。
9. 自动重启：`fastboot reboot`。

`device-boot/boot_linux.img` 由 `scripts/prepare-device-boot.py` 用本机原厂 BOOT 重打包产生。`device-boot/boot_linux.img` 不在归档里，必须自己生成一次。一键脚本按 `boot_linux.img`、`device-boot/boot_linux.img` 的顺序在暂存目录里找它，也可以用 `--boot` 指定。归档里没有可以直接刷的 Linux 内核文件，一键脚本不会把 `sunuefi_esp.img` 当 BOOT。

脚本不写 `dtbo`、`init_boot`、`vendor_boot`、`vbmeta`（两槽都不写），也不擦除 `userdata`。

---

## 七、 首次登录与基本验证

刷完自动重启后进入 120 Hz 桌面，五档均自动登录用户 `piano`（GNOME 走 GDM，KDE 走 plasma-login-manager，niri / Hyprland / Denial 走 greetd + tuigreet 兜底）。

| 账户 / 凭据 | 说明 |
| --- | --- |
| 用户名 | `piano` |
| 默认密码 | `piano123456`（登录后尽快在终端运行 `passwd` 修改） |
| root 账户 | 密码默认锁定 |
| SSH 公钥 | 镜像内不含 `authorized_keys`，需在刷写时通过 `--ssh-key` 注入 |

默认系统行为：内核直接负责手指矩阵与笔解码（`piano-touch-view` 自动让路，`piano-touch.service` 仅留作笔蓝牙初始化）；四扬声器走原厂 DAP 矩阵拓扑（功放上限 235）；双电芯电量与深度挂起正常（默认关闭自动休眠与空闲挂起）；电源模式默认平衡档；串口登录通过 `serial-getty@ttyGS0.service` 提供。

维护者专用的增量 SELinux 重标记、ABL 诊断包与取证脚本说明已集中到[维护与验收](internals.md)。

### 开机后快速检查

```sh
uname -r                      # 与 ESP 里的 Image 版本一致
findmnt /                     # 根文件系统为 f2fs
systemctl --failed            # 输出为空
ls /usr/lib/modules/          # 存在与 uname -r 同名目录
```

进一步核对：显示拖动与手写笔压感、扬声器与麦克风、相机预览与 Wi-Fi/蓝牙均正常；合盖与 `rtcwake -m mem -s 20` 唤醒后无 failed unit；完整重启一次确认仍能进桌面。Chrome 与 Firefox 默认走 Iris V4L2 M2M 硬解，Fedora 自带 Chromium 因 `ffmpeg-free` 仅软解（见[已知问题](known-issues.md#触屏网络音频与相机)）。正式发布的更严格 PR 门槛见[维护与验收](internals.md#pr-验收门槛)。

---

## 八、 故障排查

### 开机直接掉进 Fastboot

ABL 在启动计数耗尽或活动槽未标 `Successful = 1` 时会掉回 Fastboot。进入 Linux 后检查 `qbootctl.service` 运行状态或执行 `sudo qbootctl -m`；若 Linux 无法启动，按第五节的[回滚到 Android](#4-回滚到-android) 刷回原厂 BOOT。

### 误将 Android userdata 格式化

合体 BOOT 不动 `super` 等分区，可在 Fastboot 执行 `fastboot erase userdata && fastboot reboot` 让 Android 重建加密分区；若重启停在 Recovery 并提示 `Couldn't initialize user 0`，在 Recovery 菜单手动选一次「清除数据」即可恢复。
