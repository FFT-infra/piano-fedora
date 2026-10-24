# Xiaomi Pad 8 Pro (piano) 安装指南

本指南说明如何在小米平板 8 Pro（代号 `piano`，骁龙 8 至尊版 SM8750）上安装 Fedora 44，以及如何更新、切换系统与排查故障。默认档位是 GNOME。其他档位的差异见[桌面档](desktops.md)。

安装有两条路。手动路径逐条给出命令，每一步都能单独核对，是主路径。一键脚本把同一条路径包成一次执行，是可选路径。两条路写入的分区和字节数相同。

---

## 一、 前提

1. 通过小米官方渠道解锁 Bootloader。刷写前把电量保持在 30% 以上。
2. 在电脑上装好 Android Platform Tools，`fastboot` 与 `adb` 在 `PATH` 里。Linux 主机还要配好设备 udev 规则，或者用带 `sudo` 的 shell。
3. 备份 Android 用户数据。首次分区会清空 `userdata`，数据无法恢复。

首次分区要用 Android 的 root 权限读写块设备。获取 root 的方法不在本仓库范围内。

### 电脑端工具

| 工具 | 用途 | 缺少时的后果 |
| --- | --- | --- |
| `sha256sum`、`tar` | 校验与解开归档 | 无法开始 |
| `zstd` | 解压归档 | 无法开始。GNU tar 的 `--zstd` 也是去调外部 `zstd`，没有内建支持 |
| `python3` | 一键脚本读 `root-install-manifest.json`，`make-device-boot.sh` 重打包 BOOT | 脚本在校验摘要前停下，不写设备 |
| `adb` | 读原厂 BOOT | `make-device-boot.sh` 无法生成 `device-boot/` |
| `mtools`（`mcopy`、`mtype`） | 注入 SSH 公钥，核对 ESP 里的内核版本 | 只能做手动路径，且不能注入公钥 |

Windows 的一键脚本用系统自带的 PowerShell，不需要 `python3`。Windows 上解 `.tar.zst` 用 7-Zip。安装命令：

```sh
# Fedora
sudo dnf install coreutils zstd python3 android-tools mtools
# Ubuntu / Debian
sudo apt install coreutils zstd python3 adb mtools
# macOS
brew install zstd android-platform-tools mtools   # python3 随 Command Line Tools 提供
```

---

## 二、 存储与分区架构原理

平板用 UFS 4.0 闪存，配合高通 A/B 槽位（Slot A / Slot B）：

```
                    ┌────────────────────────────────────────┐
                    │       UFS 4.0 物理闪存 (LUN 0-5)        │
                    └───────────────────┬────────────────────┘
                                        │
        ┌───────────────────────────────┴───────────────────────────────┐
        ▼                                                               ▼
  【Slot A（默认启动）】                                       【Slot B（保留原厂，不切槽）】
  - boot_a (合体 BOOT：原厂 Android 内核                        - boot_b
    + 3 秒选择器 + SunUEFI UEFI 核心)                          - dtbo_b
  - dtbo_a / init_boot_a                                        - init_boot_b
  - super / userdata
  - sunuefi_esp (512 MiB, FAT32，GPT 顶层分区)
  - sunuefi_root (剩余空间, F2FS，GPT 顶层分区)
```

合体 BOOT 把 Android 内核、3 秒选择器与 UEFI 核心装进同一个 `boot_a`。开机前 3 秒按音量键进入 SunUEFI 菜单。不按则按请求页里的持久选择进入 Android 或 Linux。系统不切换活动槽，`boot_b` 原样保留。

> **不要擦除 `dtbo_a` 或 `dtbo_b`。** 高通 ABL 启动时校验活动槽位的整条引导链。空 DTBO 会被拒绝，高通 ABL 报 `Failed to load/authenticate boot image: Device Error`，平板卡在 Fastboot。
>
> SunUEFI 的板级 DTB 来自 `vendor_boot_a` 底包，再叠加原厂 `dtbo_a` 的增量片段。原厂 `dtbo_a` 内容必须留在原位。

### 分区用途

* `sunuefi_esp`（512 MiB，FAT32）：SunUEFI EDK2 固件、合并设备树（DTB）、Linux 7.2 适配内核与 initramfs。
* `sunuefi_root`（F2FS）：Fedora 44 根文件系统。桌面按档位装配。F2FS 为闪存设计，能发挥 UFS 4.0 的顺序与随机读写性能。
* `boot_a`：合体 BOOT。Linux 与 Android 都从这里启动。

### 引导稳定性

`qbootctl.service` 在每次 Linux 引导成功后向底层 GPT 写入 `Successful = 1`。ABL 的启动计数器不会归零。

### 容量计算

容量不是固定值。每台设备按自己实时的 GPT 计算，512 GiB、1 TiB 或其他容量都走同一套规则：

1. 保留 Android 的全部固定分区（`boot_a`、`dtbo_a`、`super` 等）不动。
2. 从尾部缩短 `userdata`，让出 Linux 需要的空间。
3. 让出的空间放 `sunuefi_esp`（512 MiB）和 `sunuefi_root`（其余全部）。
4. `userdata` 与 `sunuefi_root` 默认按剩余空间对半分配。固定 Android 容量的方法见第四节。
5. 所有新分区起点按 1 MiB 对齐，两边相差不超过 2 MiB。

`product.json` 的 `root_size_mib` 是封存镜像的容量（8192 MiB），与设备无关。`sunuefi_root` 的实际容量从实时 GPT 算。文件系统在首启时长满 `sunuefi_root`。

### 首次分区的两条路

首次分区只有两条路：

- 用 `install-f2fs.py apply` 在已 root 的 Android 上写 GPT。
- 按第四节的手动步骤在 Android 上执行 `adb shell su -c`。

两条路都离不开 root。原厂 Fastboot 只能读分区容量、擦除和刷写，不能改分区表。

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

解完后，这个目录就是暂存目录。第五节与第七节的命令都在这里执行。再校验一次 prepared root：

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

| 文件 | 用途 | 刷到哪 |
| --- | --- | --- |
| `sunuefi_esp.img` | SunUEFI ESP 分区镜像 | `sunuefi_esp` |
| `root-install/pianoroot.f2fs.sparse.img` | 已写入设备配置的根文件系统 | `sunuefi_root` |
| `boot_sunuefi.img` | ESP 内部的 Linux 载荷 | 不刷任何分区 |
| `PianoUEFI-product.img`、`uefi-source.json` | 产品固件与来源记录 | 不刷入，首装校验用 |
| `flash-linux.sh`、`flash-linux.bat`、`README-INSTALL.txt` | 一键脚本与说明 | 不刷入 |
| `make-device-boot.sh`、`boot-tools/`、`host-boot-tool/`、`scripts/` | 生成设备专用 BOOT | 不刷入 |
| `SHA256SUMS` | 归档内全部文件的摘要 | 不刷入 |
| bundle 与组件 manifest | 来源绑定与内核、运行时记录 | 不刷入，供校验 |

`root-install/` 里的 sparse 镜像已写入设备配置并恢复 SELinux 标签。没有指定 `root_size_mib` 时，sparse 镜像保持封存容量，首次启动再扩满 `sunuefi_root`。Release 固定使用 Android Slot A（`_a`）。B 槽设备要用同一次 bundle run 自己跑 `prepare-install.yml` 的 `_b`。

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

归档里的 `make-device-boot.sh` 一次做完全部步骤：用 `adb` 读 `boot_a`、核对 96 MiB 与设备侧摘要、调 `scripts/prepare-device-boot.py` 重打包。

```sh
./make-device-boot.sh            # 只有一台设备连着时可以省略 --serial
./make-device-boot.sh --serial <序列号>
```

`make-device-boot.sh` 要求 Android 侧的 root ADB。读回不足 96 MiB 或两条摘要不一致时，脚本不写任何东西。产出的 `device-boot/` 已有同名目录时也会拒绝覆盖。

原厂 ABL 不实现 `fastboot fetch`，所以归档不教这条命令。它只会返回一个 0 字节文件。

不要用 `boot_sunuefi.img` 顶替 `boot_linux.img`。前者是 ESP 内部的 Linux 载荷，ABL 无法加载。

### 6. 暂存目录

解包后的目录长这样：

```text
piano-install/
├── README-INSTALL.txt
├── SHA256SUMS
├── boot_sunuefi.img
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
│   ├── boot_linux.img
│   └── boot-request.json
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

## 四、 从原始 GPT/UFS 开始的首次安装

本节面向还没有 `sunuefi_esp` 与 `sunuefi_root` 的设备。设备已经有这两个分区时，跳过本节，直接看第七节的更新入口。

`install-f2fs.py apply` 会清空 `userdata`，不能用它做更新。

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

### 3. 请求页

`device-boot/boot-request.json` 的 `boot_device` 是 `/dev/disk/by-partlabel/boot_a`。`app_generation` 必须与归档里的 `PianoUEFI-product.img` 中的 APPv1 载荷一致。

3 秒选择器在“正在引导的那份镜像”末尾读请求页。ABL 只加载当前槽，`BootSelect.c` 按 `Base + align(original_image_size)` 取页。合体 BOOT 装进 Slot A，Linux 与 Android 都从同一份 `boot_a` 启动，请求页因此指向 `boot_a`。不切槽，也不存在第二份需要同步的页。

带了 `root-install` 时，root 文件系统里的 `etc/piano/boot-request.json` 就是这条配置。不带时 root 文件系统里没有这个文件，首启的 `piano-boot-request-autoconfig.service` 按当前槽重建。首启后不要猜设备名或改槽，否则“返回 Android”和后续启动请求都会失败。

---

## 五、 手动刷写 Linux

本节是主路径。第六节的一键脚本执行同样的写入。`dtbo_a` 一律不动。

### 1. 确认设备和槽位

平板关机后长按【电源键 + 音量减键】进入 Fastboot，插好数据线，然后：

```sh
fastboot devices
fastboot getvar product        # 必须是 piano
fastboot getvar current-slot   # 必须是 a，合体 BOOT 写进当前槽
fastboot getvar unlocked       # 必须是 yes
fastboot getvar is-userspace   # 必须是 no
```

任一项不对就停下，不要写任何分区。以下情况都算不对：`product` 不是 `piano`、`unlocked` 不是 `yes`、`is-userspace` 是 `yes`（说明落在 fastbootd，不是原厂 ABL）。

`current-slot` 必须是 `a`。当前槽是 `b` 时，手动流程也必须停：首装布局只支持原厂 Android 所在的 Slot A。

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
# 1. 合体 BOOT -> boot_a（活动槽保持 A，不切槽）
fastboot -S 128M flash boot_a device-boot/boot_linux.img

# 2. ESP -> sunuefi_esp
fastboot -S 128M flash sunuefi_esp sunuefi_esp.img

# 3. 根文件系统 -> sunuefi_root（sparse 只传有效块，需要几分钟）
fastboot -S 128M flash sunuefi_root root-install/pianoroot.f2fs.sparse.img

# 4. 重启进系统
fastboot reboot
```

`-S 128M` 让 fastboot 把每次传输切到不超过 128 MiB。钢琴 ABL 对单次 512 MiB 的裸 ESP 写入会停止响应，只能断电重来。三条 flash 命令都不要省这个参数。

每条命令都必须打印 `OKAY`。出现 `FAILED`，立刻停下，不要继续后面的步骤。

`boot_a` 要写的是 `prepare-device-boot.py` 用本机原厂 BOOT 重打包出的 `device-boot/boot_linux.img`，不是归档里的 `boot_sunuefi.img`。后者是 ESP 内部的 Linux 载荷（Header v2 容器，没有 `SUNUEFI-NEXTv1` 请求页），只有 SunUEFI 固件从 ESP 里加载。把它刷进 `boot_a`，ABL 会拒绝整条链，平板停在 Fastboot。

合体 BOOT 保持上游的 unsigned AVB NONE 元数据。`prepare-device-boot.py` 不补 `os_version`，也不重新签名。Slot A 的 ABL 直接加载这份镜像。同一份未签名镜像已在真机上完成 Android→Linux→Android 往返。不要自行补 `os_version` 或 RSA4096 footer：`piano-boot-repack` 的 probe/request/adopt 会把首块与包装器里保存的原厂头逐字节比较。改了偏移 16，Android 侧的引导助手就认不出当前 BOOT，`switch linux` 会直接失败。

不要执行这些命令：

- `fastboot erase dtbo_a`、`fastboot erase dtbo_b`
- `fastboot erase boot_a`
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

`uname -r` 的正确值是这一版 ESP 里实际装的内核，不是随便一个 7.2.9。两处可以查到，两边必须一致：

```sh
# 1) ESP 镜像里的内核字符串（刷写前就能查，不需要设备）
mtype -i sunuefi_esp.img ::/EFI/Piano/stable/Image > Image.bin
strings -a Image.bin | grep -m1 -oE '[0-9.]+-piano[^ ]*g[0-9a-f]+'

# 2) 设备启动后的实际内核
uname -r

# 3) 模块目录必须与之同名，否则驱动根本不会加载
ls /usr/lib/modules/
```

`uname -r` 与 ESP 里的字符串不符，说明刷进去的不是这一版资产。`/usr/lib/modules/` 下没有同名目录，说明 rootfs 与 ESP 的内核不是一套。系统能起来，但一个驱动都加载不了。

能进所选档位的桌面，就是刷写成功最直接的证据。

这些检查要在从原始 GPT/UFS 重新分区的设备上做一次才有意义。在旧的 `sunuefi_root` 上热替换文件，或者用历史资产启动的机器，都不能说明这份镜像能装。

### 4. 回滚到 Android

Linux 起不来时，先从 3 秒选择器选 Android。开机前 3 秒按音量键进入菜单，或者在 Android 正常时执行 `piano-next-boot android --reboot`。

合体 BOOT 本身损坏时，把备份的原厂引导镜像刷回 `boot_a`。不需要改动分区或活动槽：

```sh
fastboot flash boot_a stock-recovery/boot.img
fastboot reboot
```

要重新刷 Linux，直接重复本节。`userdata` 清空后无法恢复，所以不要擦除它。

---

## 六、 一键脚本

一键脚本执行第五节的全部步骤，并在写入前做更多检查。一键脚本要求分区已由 `install-f2fs.py apply` 或第四节建立。原厂设备只有 Android 的 `userdata`，没有 `sunuefi_esp` / `sunuefi_root`，一键脚本会拒绝执行。清空分区是首次安装的破坏性步骤，不能由一键脚本代劳。

一键脚本把合体 BOOT 写进当前活动槽，运行前 Android 必须在 Slot A。当前槽是 B 时，一键脚本在任何写入前就退出。

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
5. 保持 Slot A，不切槽。
6. 刷入合体 BOOT：`fastboot -S 128M flash boot_a device-boot/boot_linux.img`。
7. 刷入 ESP 分区：`fastboot -S 128M flash sunuefi_esp sunuefi_esp.img`。
8. 刷入系统根分区：`fastboot -S 128M flash sunuefi_root pianoroot.f2fs.sparse.img`。
9. 自动重启：`fastboot reboot`。

`device-boot/boot_linux.img` 由 `scripts/prepare-device-boot.py` 用本机原厂 BOOT 重打包产生。`device-boot/boot_linux.img` 不在归档里，必须自己生成一次。一键脚本按 `boot_linux.img`、`device-boot/boot_linux.img` 的顺序在暂存目录里找它，也可以用 `--boot` 指定。归档里的 `boot_sunuefi.img` 是 ESP 内部的 Linux 载荷，一键脚本会明确拒绝它。

脚本不写 `dtbo_a`、`init_boot_a`、`vendor_boot_a`、`vbmeta_a`，也不擦除 `userdata`。

---

## 七、 更新已有安装

### 1. 更新 ESP 与 root

`update-f2fs.py` 复用锁定上游的只读设备/GPT 解析、工厂 Fastboot 身份检查和本仓库的材料校验。`update-f2fs.py` 只传输 ESP 与 F2FS root 文件系统，不写 GPT、userdata 与持久 BOOT。完整 root 文件系统会替换 Linux 文件，个人文件先备份。

先确认原 Android 能从当前活动槽完整启动并提供 root ADB。历史的 init_boot/DTBO 试验不算恢复完成的证据。计划阶段会核对主备 GPT/CRC、分区类型、当前槽、原 BOOT、源码清单、管理配置与实际 sparse 容量，并以只读方式取得 Android 的 `/sys/firmware/fdt`，检查 Linux RAM 子集与固定保留区。含义见[上游核对](upstream.md)。

缺 FDT 或比较失败就停下，不要跳过检查。

`stock-recovery/boot.img` 是设备当前槽的原厂 BOOT。执行器要求它 96 MiB 且摘要与设备一致。原厂 ABL 不实现 `fastboot fetch`，从 Android 侧读：

```sh
adb -s <serial> shell su -c 'sha256sum /dev/block/by-name/boot_a'
adb -s <serial> exec-out su -c 'dd if=/dev/block/by-name/boot_a bs=1048576' \
  > stock-recovery/boot.img
sha256sum stock-recovery/boot.img    # 与上一条相同
```

原厂 ROM 包里的 `images/boot.img` 也是 96 MiB，但只有设备还跑着那一版固件时才逐字节相同。用前比摘要，不一致以设备读出的为准。

在连接平板的 Linux 主机上执行。以下路径指暂存目录，`--serial` 是 `adb devices` 显示的 Android 序列号：

```sh
python3 boot-tools/installer/scripts/update-f2fs.py plan \
  --sunuefi boot-tools/installer/sunuefi --serial <serial> \
  --bundle . --root-install root-install \
  --device-boot device-boot --stock-boot stock-recovery/boot.img \
  --plan update-plan.json
```

复核计划并备份 Linux 文件后，使用同一组输入：

```sh
python3 boot-tools/installer/scripts/update-f2fs.py apply \
  --sunuefi boot-tools/installer/sunuefi --serial <serial> \
  --bundle . --root-install root-install \
  --device-boot device-boot --stock-boot stock-recovery/boot.img \
  --plan update-plan.json --session update-session \
  --execute --accept-root-replacement
```

执行前重新比较设备、完整 GPT 基线、内存描述、槽位与材料，保存 GPT 双副本和原 BOOT。进入原厂 bootloader 后，第一笔传输之前核对两个目标的实时容量。fastbootd、序列号不对或槽位不对都拒绝。只传输 `sunuefi_esp` 与 `sunuefi_root`，完成后停在 Fastboot，状态是 `UPDATE_TRANSFERRED_FIRST_BOOT_AND_READBACK_PENDING`。

传输成功不等于读回或启动通过。下一步依次执行：临时 `boot_linux.img` 启动与设备读回、桌面与硬件验收、临时 Android 旁路。全部通过后才单独安装持久 BOOT。

中途中断时先读 session 的 result.json 和实际设备状态。不要自动重试或回滚 GPT。

### 2. 只升级内核

装好之后，内核跟着 `dnf upgrade` 走，不需要重跑刷写，也不需要 bundle。`kernel-piano` 提供 `kernel-core`，是 installonly 包，新内核与旧内核的模块树并存。

```sh
sudo dnf upgrade --refresh
```

升级事务收尾时 `kernel-piano` 的 `%posttrans` 会 `depmod`、用新内核重建 initramfs，再把新内核、新 initramfs 与 ESP 上原有的 `board.dtb` 重新打成 Android boot v2 容器，原子替换 `/boot/efi/EFI/Piano/stable/boot.img`。重启后固件读到的是新内核。

这条路径已在真机上验证：`7.2.9-1` 升到 `7.2.9-3` 之后重启，`uname -r` 是新内核，桌面正常起，`v4l2loopback` 从新模块树的 `updates/` 加载。

任何一步失败时，`boot.img` 保持原样，设备继续用上一个内核启动。审计记录写在 `/var/lib/piano/kernel-esp/<release>.json`。

已经装好但起不来的内核没有自动回滚。回滚靠 `boot.img.bak`：

```sh
sudo cp /boot/efi/EFI/Piano/stable/boot.img.bak /boot/efi/EFI/Piano/stable/boot.img
sudo cp /boot/efi/EFI/Piano/stable/Image.bak /boot/efi/EFI/Piano/stable/Image
sudo cp /boot/efi/EFI/Piano/stable/initramfs.bak /boot/efi/EFI/Piano/stable/initramfs
```

`dnf history undo` 只回滚 RPM，不动 ESP。完整语义见 [COPR 软件源](copr-repository.md)。

---

## 八、 首次登录与诊断

刷完后平板自动重启，进入 120 Hz 的桌面。四个档位都自动登录到 `piano` 用户：GNOME 走 GDM，KDE 走 plasma-login-manager，niri 与 Hyprland 走 greetd 的 `tuigreet` 兜底。

### 1. 默认状态

| 项 | 值 |
| --- | --- |
| 用户 | `piano` |
| 密码 | `piano123456` |
| 改密 | 打开终端执行 `passwd` |
| root | 密码锁定 |
| `authorized_keys` | 镜像里没有，刷写时用 `--ssh-key` 注入 |

首次使用后执行 `passwd` 改密。

### 2. 桌面默认配置

* 120 Hz 与触控：Mesa 硬件加速默认开启。触控服务加载 5 阶自适应卡尔曼滤波与 24 像素死区防抖。
* 声学保护：内核与 ALSA UCM2 把 FS19xx 四功放截断在 -15 dB。麦克风有 +40 dB 硬件增益。
* 电池与电源：`/hypervisor` 节点已软隔离，桌面显示原生双电芯电量，支持合盖与深度挂起。休眠（hibernate）与混合睡眠默认关闭。`sleep.conf.d/piano.conf` 由 `scripts/finalize-fedora-rootfs.py` 写入，`logind.conf.d/piano.conf` 由 `piano-fedora-configs` 包提供。
* 触控提频：`uperf-linux` 监听触控输入，触点按下后 1 ms 内拉高全大核频率。

### 3. 串口与网络

正常 Fedora 用需要账户认证的 `serial-getty@ttyGS0.service`。initramfs 救援的串口 shell 另走一条。

NCM 用上游固定设备 MAC。NetworkManager 静态配置 `192.168.77.1/30`，不加默认路由。主机侧可用 `192.168.77.2/30`。以上配置已写入镜像，实际 USB 枚举和登录还要在设备上验证。

### 4. 维护者用的诊断

在现有 Fedora 上用 `stage-rootfs-payload.py --rootfs /` 增量安装时，脚本返回成功前会用本机 `setfiles -F` 恢复新模块、硬件准备程序、USB 诊断程序、systemd 单元和来源记录的 SELinux 标签。来源里的 `user_home_t` 不能用在系统模块上。

摘要一致不代表标签正确。离线 staging 的标签由 build-image 里的目标策略标记步骤处理。

ABL 只读对照把完整取证放在 RAM 的 `/run/piano-boot-evidence.txt`，进串口救援后读取。取证文件不写 Linux root 文件系统。这条链的刷写件（`boot-abl-storage.img`、`init_boot-abl-storage.img`、`dtbo-abl-storage.img`）不随 Release 发布：它是调试材料，manifest 自标未做真机验证。需要时从 `build-image` 的 `diagnostic-abl-storage.tar.zst` workflow artifact 取。原生 BOOT 文件检查用 `verify-native-boot.py`。七项文件 guard 的报告与真实设备的槽位、当前来源、块设备写读回分开保存。未完成的引导助手模块不标为 ready。

---

## 九、 双系统往返

1. 从 Linux 切换回 Android：点击应用抽屉里的“返回 Android”图标，或在终端执行 `piano-next-boot android --reboot`。helper 把持久请求写成 Android 后正常重启，3 秒选择器在下次开机时进入 Android。已在真机验收。
2. 从 Android 切换回 Linux：安装 Android 的「SunUEFI 引导助手」模块，在模块页面选择 Linux 并重启。没有引导助手时，开机前 3 秒按音量键，在 SunUEFI 菜单里选 Linux，选择会被保存。两条路径都不需要电脑，也不切活动槽。
3. 3 秒选择器：每次开机亮屏后的前 3 秒内按音量上/下键呼出。不按则按请求页里的持久选择启动。

---

## 十、 真机验收清单

刷完之后逐项核对，每项留一条命令输出或截图作为证据。任一项失败，按失败记录，不要写成“未验证”。

```sh
uname -r                      # 与 ESP 里的 Image 字符串一致
findmnt /                     # 根文件系统是 f2fs
systemctl --failed            # 输出为空
ls /usr/lib/modules/          # 有与 uname -r 同名的目录
```

硬件按五门检查：

| 门 | 检查内容 |
| --- | --- |
| 显示、触控、笔 | 拖动窗口不抖，手写笔压感与倾斜有响应 |
| 音频 | 扬声器放音、麦克风录音各一次 |
| 相机 | 相机应用能出画面，无斜条纹 |
| 无线 | Wi-Fi 连接、蓝牙扫描 |
| 电源与睡眠 | 合盖与 `rtcwake -m mem -s 20` 能唤醒，唤醒后无 failed unit |

浏览器放 H.264 视频另算一项，且三家结果不同：Google Chrome 与 Firefox 默认走硬件解码。Fedora 的 Chromium 连 MP4 都打不开，因为它链接系统 `libavcodec`，而 `ffmpeg-free` 去掉了软件 H.264 解码器。装 RPM Fusion 的 `libavcodec-freeworld` 后能播，但仍然软解。细节见[已知问题](known-issues.md#触屏网络音频与相机)。

再完整重启一次，确认仍进所选档位的桌面。双系统往返按第九节执行，往返后核对 `fastboot getvar current-slot`。

发布验收的标准比这里更严：必须从原始 GPT/UFS 重新分区，并使用同一条流水线新产出的 bundle 与 root-install。门槛见[维护与验收](internals.md#pr-验收门槛)。

---

## 十一、 故障排查

### Q1: 开机直接掉进 Fastboot 怎么解决？

高通 ABL 在开机时没收到明确的 `Successful = 1` 标记，会在连续重启后递减尝试次数，归零后掉进 Fastboot。

系统稳定启动后，确认 `qbootctl.service` 在运行，或手动执行 `sudo qbootctl -m` 把当前槽标记为 `Successful: 1`。设备始终从 Slot A 启动。Linux 起不来时按第五节的回滚步骤刷回原厂 `boot_a`。

### Q2: 误将 Android 数据格式化了？

Slot A 保留着完整的原厂 Android 分区（`super`、`userdata` 等）。合体 BOOT 只替换 `boot_a`，备份的原厂镜像可以随时刷回。

在 Fastboot 里执行 `fastboot erase userdata && fastboot reboot`，Android 会重建加密分区并走首次开机引导。重启后若停在 Recovery 并显示 `Couldn't initialize user 0`，在 Recovery 里选一次“清除数据”再继续。

### Q3: Type-C 有线网络 SSH 无法连接？

系统把 USB NCM 接口静态配置为 `192.168.77.1/30`。把电脑端对应网卡配到同一子网（例如 IP `192.168.77.2`，掩码 `255.255.255.252`），然后 `ssh piano@192.168.77.1`。
