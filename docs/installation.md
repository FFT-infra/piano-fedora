# Xiaomi Pad 8 Pro (piano) 安装、分区与双系统指南

本指南讲两件事：在**小米平板 8 Pro（代号 `piano`，骁龙 8 至尊版 SM8750）**上从零安装 Fedora 44 GNOME，以及对已有 Linux 分区做增量更新。

---

## 一、 前提要求与准备工作

1. **解锁 Bootloader**：通过小米官方渠道完成 BL 解锁。刷入前电量保持 **30% 以上**，建议 50%。
2. **电脑端工具**：装好 Android Platform Tools，`fastboot` 与 `adb` 在 PATH 里。Linux 主机还要配好设备 udev 规则，或者用有 `sudo` 权限的 shell。
   - Python 3：`flash-linux.sh` 用它读 `root-install-manifest.json`。脚本在校验摘要之前就检查 `python3`，缺了直接停下，不会写设备。

     Windows 脚本改用系统自带的 PowerShell，不需要 Python。Fedora `sudo dnf install python3`，Ubuntu/Debian `sudo apt install python3`，macOS 用系统自带的 `python3`。
   - `mtools` 提供 `mcopy` 与 `mtype`：注入 SSH 公钥、以及核对 ESP 里的内核版本都要用它。Fedora `sudo dnf install mtools`，Ubuntu/Debian `sudo apt install mtools`，macOS `brew install mtools`。

    不走手动刷机又不注入公钥时可以省掉。
3. **备份数据**：首次分区会把原厂 `userdata` 一分为二，一半留给 Android，一半给 Linux。**这一步会清空 Android 用户数据，先备份到电脑或外置存储。**

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
  【Slot A (原厂 Android)】                                       【Slot B (SunUEFI + Linux)】
  - boot_a                                                        - boot_b (SunUEFI 链式引导头)
  - dtbo_a                                                        - dtbo_b (保留原厂内容，供 ABL 校验)
  - init_boot_a                                                   - init_boot_b
  - super (Android 系统与厂商镜像)                                 - sunuefi_esp (512 MiB, FAT32)
  - userdata (原厂扣减 Linux 后的剩余空间)                         - sunuefi_root (468+ GiB, 高性能 F2FS)
```

> **不要擦除 `dtbo_b`。** 高通 ABL 启动时校验活动槽位的整条引导链，空 DTBO 会被拒绝，报 `Failed to load/authenticate boot image: Device Error`，平板卡在 Fastboot。
>
> SunUEFI 的板级 DTB 来自 `vendor_boot_b` 底包，再叠加原厂 `dtbo_b` 的增量片段。原厂内容必须留在原位。

* **`sunuefi_esp`（512 MiB，FAT32）**：SunUEFI EDK2 固件、合并设备树（DTB）、Linux 7.2 适配内核与 initramfs。
* **`sunuefi_root`（F2FS）**：Fedora 44 GNOME 根文件系统。F2FS 为闪存设计，能发挥 UFS 4.0 的顺序与随机读写性能。
* **引导稳定性（`qbootctl`）**：`qbootctl.service` 在每次 Linux 引导成功后向底层 GPT 写入 `Successful = 1`，ABL 的启动计数器不会归零。

---

## 三、 已有 Linux 分区的刷写（跨平台一键脚本）

适用于已解锁 BL、`install-f2fs.py apply` 已建好 GPT 分区并擦除 userdata、`sunuefi_esp` 与 `sunuefi_root` 都在的设备。全新盘不能直接跳到一键刷机。首次安装的主流程在第四节，本节的分区前提不能跳过。

### 1. 获取 Release 资产
从 [GitHub Releases 页面](https://github.com/FFT-infra/piano-fedora/releases) 下载最新发行包。仓库目前还没有 Release 与 tag，该链接是空的。发布前用 `build-bundle` 下载的完整 deploy-bundle，不要在本地重新打包历史 `out/` 目录充当发布资产。

- `piano-installer-*.zip`：只剩本地打包器会生成它，流水线不产出。没有 Release 时用仓库里的 `scripts/installer/`；
- `boot_sunuefi.img`：SunUEFI 引导头；
- `sunuefi_esp.img`：SunUEFI ESP 分区镜像；
- `pianoroot.f2fs.img.zst`：封存的基础盘，不能直接首装；
- `root-install/pianoroot.f2fs.sparse.img`：`prepare-install` 按本机容量扩容后的传输镜像；
- `SHA256SUMS`：deploy-bundle 内 ESP/BOOT 等资产的摘要；
- `root-install/SHA256SUMS`：root-install 内 sparse 镜像和清单的摘要。

首装必须先用同一个 deploy-bundle run 跑 `prepare-install.yml`，Android 槽位固定为 `_a`，得到 `root-install`。它的 sparse 镜像已扩到真实 `sunuefi_root` 容量，写入设备配置并恢复 SELinux 标签。

镜像里没有 `authorized_keys`。管理公钥在刷写时用 `--ssh-key` 注入，见下一节。

刷写脚本取自本仓库 `scripts/installer/`，随仓库检出：`flash-linux.sh`、`flash-linux.bat`、`README-INSTALL.txt`。`piano-installer-*.zip` 目前只由本地打包器 `scripts/package-release-bundle.sh` 生成，流水线不产出它。

没有 Release 时直接从仓库取这三个文件。

准备一个空的暂存目录（下面记作 `piano-install/`），把材料完整复制进去。不要从不同 run 各取一部分：

1. 把 `scripts/installer/` 里的 `flash-linux.sh`、`flash-linux.bat`、`README-INSTALL.txt` 复制到该目录；
2. 完整解压同一 run 的 `deploy-bundle/` 到同一层，保留其中全部普通文件；
3. 完整解压同一 deploy-bundle run 派生的 `root-install/` 到该目录下；
4. 不要把两个 artifact 的 `SHA256SUMS` 合并、改名或重新生成。

最终目录必须长这样：

```text
piano-install/
├── flash-linux.sh
├── flash-linux.bat
├── README-INSTALL.txt
├── boot_sunuefi.img
├── sunuefi_esp.img
├── SHA256SUMS
├── bundle-manifest.json
├── root-image-manifest.json
└── root-install/
    ├── pianoroot.f2fs.sparse.img
    ├── root-install-manifest.json
    └── SHA256SUMS
```

脚本把当前工作目录当作暂存目录。deploy-bundle 的 `SHA256SUMS` 覆盖该 artifact 的全部文件，`root-install/SHA256SUMS` 覆盖 sparse root 与 root-install manifest。脚本还核对 root-install 里的 bundle/root 清单摘要确实来自当前 deploy-bundle。

只复制几个文件会让清单校验失去意义。

一键脚本要求分区已由 `install-f2fs.py apply` 建立。原厂设备只有 Android 的 `userdata`，没有 `sunuefi_esp` / `sunuefi_root`，脚本会拒绝。必须先走第四节的首次分区。清空分区是首次安装的破坏性步骤，不能由刷写脚本代劳。

脚本固定把 Linux 写到 Slot B，所以运行前 Android 必须还在 Slot A。当前槽是 B 时，脚本在任何写入前就退出。

### 2. 进入 Fastboot 模式
1. 平板完全关机。
2. 按住 **【电源键 + 音量减键】**，直到屏幕亮起并显示米兔/FASTBOOT 图标。
3. 用原装或质量可靠的 USB-C 数据线连接电脑。

### 3. 执行一键刷机
先进入前面的暂存目录：

```bash
cd piano-install
```

- **Linux / macOS 主机**：
  ```bash
  chmod +x flash-linux.sh
  ./flash-linux.sh
  ```
  需要给 `piano` 用户装一把 SSH 公钥时：
  ```bash
  ./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub
  ```
- **Windows 主机**：
  直接双击运行 `flash-linux.bat`。

`--ssh-key` 只接受单行 `ssh-ed25519` 或 `ssh-rsa` 公钥，绝不传私钥。脚本在
两份 SHA256SUMS、bundle/root 来源、槽位和容量全部校验通过、且用户确认之后，
才用 `mcopy -o -i sunuefi_esp.img <公钥> ::/piano-ssh-key.pub` 把公钥写进 ESP
镜像，然后重新计算 ESP 摘要并打印。设备首次启动时
`piano-first-boot-ssh-key.service` 把它追加到 `piano` 的 `authorized_keys`
（目录 0700、文件 0600）并删除 ESP 上的副本。不传 `--ssh-key` 时脚本行为与
以前完全一致，系统只保留文档里的口令登录。

注入会改变 `sunuefi_esp.img` 的字节，它和 deploy-bundle 的 `SHA256SUMS` 就不再一致。重跑前重新解压一份 deploy-bundle。刷写后读回核对时，用脚本打印的新摘要，不要用清单里的旧值。

Windows 脚本不自动注入；需要时按 `flash-linux.bat` 的提示手工执行 `mcopy`。

脚本按顺序做这些事：
1. 检查 `fastboot` 是否可用、设备是否在线。
2. 确认产品代号是 `piano`，防止误刷。
3. 按 deploy-bundle 的 `SHA256SUMS` 与 `root-install/SHA256SUMS` 分别校验 BOOT/ESP 和 sparse root。
4. 打印安全告知，要求输入大写 `YES` 确认。
5. 执行标准刷写序列：
   - 切换活动槽位至 B：`fastboot set_active b`
   - 刷入引导头：`fastboot flash boot_b boot_sunuefi.img`
   - 刷入 ESP 分区：`fastboot flash sunuefi_esp sunuefi_esp.img`
   - 刷入系统根分区：`fastboot flash sunuefi_root pianoroot.f2fs.sparse.img`
   - 自动重启：`fastboot reboot`

   脚本不写 `dtbo_b`、`init_boot_b`、`vendor_boot_b`、`vbmeta_b`，也不擦除
   `userdata`。需要逐条手动执行同样步骤时，见下一节。

---

## 三之二、 手动刷机（不使用一键脚本）

一键脚本只是把下面这些命令包了一层。想自己控制每一步，或者脚本在你的平台上跑不起来时，直接照本节执行。写入内容与脚本相同，`dtbo_b` 一律不动。

### 1. 从 Release 取哪些文件

从 [GitHub Releases 页面](https://github.com/FFT-infra/piano-fedora/releases) 下载发行包。还没有 Release 时，用同一次成功 `build-bundle` run 的完整 `deploy-bundle`，以及该 run 派生的 `prepare-install` 的 `root-install`。不要拿历史 `out/` 目录拼装。

手动刷机至少需要这些资产：

| Release 资产 | 用途 | 刷到哪 |
| --- | --- | --- |
| `boot_sunuefi.img` | SunUEFI 链式引导头 | `boot_b` |
| `sunuefi_esp.img` | ESP 分区镜像（固件核心、DTB、内核、initramfs） | `sunuefi_esp` |
| `root-install/pianoroot.f2fs.sparse.img` | `prepare-install` 扩容并配置后的根文件系统稀疏镜像 | `sunuefi_root` |
| `SHA256SUMS` | deploy-bundle 内 BOOT/ESP 等资产的 SHA256 清单 | 不刷入，只用于校验 |
| `root-install/SHA256SUMS` | root-install 内 sparse 镜像与 manifest 的 SHA256 清单 | 不刷入，只用于校验 |

`piano-installer-*.zip` 和 `piano-switch-ksu-*.zip` 只有本地打包器 `scripts/package-release-bundle.sh` 会生成，流水线与 deploy-bundle 都不含它们；手动刷机不需要。不要把 deploy-bundle 里的
`pianoroot.f2fs.img.zst` 解压后直接刷；它不是按本机 `sunuefi_root` 容量派生的。

### 2. 校验下载到的文件

在 `piano-install/` 暂存目录执行（macOS 把 `sha256sum` 换成
`shasum -a 256`）：

```sh
sha256sum -c SHA256SUMS
(cd root-install && sha256sum -c SHA256SUMS)
```

两份清单属于两个独立 artifact。任意一行 `FAILED` 都停止，不要继续刷新
合并后的假清单。

手动核对单个文件（只查要用到的三个，不受其它行影响）：

```sh
sha256sum boot_sunuefi.img sunuefi_esp.img
(cd root-install && sha256sum pianoroot.f2fs.sparse.img)
```

把 BOOT/ESP 输出与 deploy-bundle 的 `SHA256SUMS` 比对，把 sparse root
输出与 root-install 的 `SHA256SUMS` 比对，三组哈希都必须一致。

### 3. 确认设备和槽位

平板关机后长按**【电源键 + 音量减键】**进入 Fastboot，插好数据线，然后：

```sh
fastboot devices
fastboot getvar product        # 必须是 piano
fastboot getvar current-slot   # 必须是 a；Linux 固定写到 Slot B
fastboot getvar unlocked       # 必须是 yes
fastboot getvar is-userspace   # 必须是 no
```

任一项不对就停下，不要写任何分区：`product` 不是 `piano`，`unlocked` 不是 `yes`，或者 `is-userspace` 是 `yes`（说明落在了 fastbootd，不是原厂 ABL）。

`current-slot` 必须是 `a`。是 `b` 时一键脚本会拒绝，手动流程也必须停，不能把 Linux 写进正在承载 Android 的 `boot_b`。

`boot_b` 要写的是 `prepare-device-boot.py` 用本机原厂 BOOT 重打包出的 `device-boot/boot_linux.img`，不是 `deploy-bundle` 里的 `boot_sunuefi.img`。后者是 ESP 内部的 Linux 载荷（Header v2、无 AVB、无请求页），只有 SunUEFI 固件能加载。把它刷进 `boot_b`，ABL 会拒绝整条链，平板停在 Fastboot。

手动路径也只接受同一条新流水线的材料：完整 `deploy-bundle`，以及由该 bundle run 派生、`android_slot` 为 `_a` 的 `root-install`。

刷写前读 `fastboot getvar partition-size:sunuefi_root`，把十六进制容量换算成字节。它必须与 `root-install-manifest.json` 的 `expanded_bytes` 完全相等，不一致就停。

### 4. 逐条刷写

要装 SSH 公钥的话，在刷 ESP 之前先写进 ESP 镜像。这一步必须在 `sha256sum -c` 通过之后做，因为它会改变镜像字节：

```sh
# 可选：把管理公钥写进 ESP（只接受 ssh-ed25519 / ssh-rsa 公钥）
mcopy -o -i sunuefi_esp.img ~/.ssh/id_ed25519.pub ::/piano-ssh-key.pub
sha256sum sunuefi_esp.img      # 记下新摘要，读回校验用这个值
```

设备首次启动时，`piano-first-boot-ssh-key.service` 把 `/boot/efi/piano-ssh-key.pub` 追加到 `piano` 的 `authorized_keys`，然后删掉 ESP 上的副本。跳过这一步，系统只保留口令登录，其他行为不变。

格式不对的公钥（多行、私钥、不是 `ssh-ed25519`/`ssh-rsa`）不会被导入，helper 直接失败。

```sh
# 1. 切到 B 槽（Android 保留在 A 槽）
fastboot set_active b

# 2. 引导头 -> boot_b
fastboot flash boot_b boot_sunuefi.img

# 3. ESP -> sunuefi_esp
fastboot flash sunuefi_esp sunuefi_esp.img

# 4. 根文件系统 -> sunuefi_root（sparse 只传有效块，需要几分钟）
fastboot flash sunuefi_root root-install/pianoroot.f2fs.sparse.img

# 5. 重启进系统
fastboot reboot
```

每条命令都必须打印 `OKAY`。出现 `FAILED` 就停下，不要继续后面的步骤。

**不要**执行 `fastboot erase dtbo_b`、`fastboot erase boot_b`、
`fastboot erase userdata`、`fastboot -w`，也不要写 `abl`、`xbl`、`tz`、`modem`
等引导链分区。

### 5. 验证刷写结果

`fastboot flash` 的 `OKAY` 只说明数据发完了，不说明分区内容正确。重启前可以读回校验；`sunuefi_esp` 约 512 MiB，读回要几分钟：

```sh
fastboot getvar partition-size:sunuefi_esp     # 十六进制字节数
fastboot fetch sunuefi_esp esp-readback.img
```

这台平板的 `sunuefi_esp` 分区比 `sunuefi_esp.img` 大（实测分区 537899008 字节，镜像 536870912 字节；以自己的 `fastboot getvar` 结果为准）。`fetch` 读的是整个分区，尾部会带上分区里原有的数据，整份哈希必然不同。

只比对镜像长度那一段：

```sh
head -c "$(wc -c < sunuefi_esp.img)" esp-readback.img > esp-readback-head.img
sha256sum esp-readback-head.img sunuefi_esp.img
# 两行哈希必须相同
```

分区容量与镜像字节数完全相等时省掉 `head -c`，直接比对整份。容量比镜像小时说明分区写不下，立刻停下。

这台平板的原厂 ABL 不实现 `fetch`：主机 fastboot 37.0.0 能识别这个子命令，设备会回 `Device does not support fetch command`，宿主上还会留下一个 0 字节的读回文件。

遇到这条就跳过读回，改看下面的启动后验证；不要因此重刷。

如果第 4 节注入过 SSH 公钥，读回比对要用注入后重新算出的摘要，不能用 deploy-bundle `SHA256SUMS` 里的旧值。`mcopy` 就地改写 `sunuefi_esp.img`，两份摘要本来就不一样。搞错这一条会把正常的镜像判成损坏。

启动后验证（三项都要满足）：

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

`uname -r` 与 ESP 里的字符串不符，说明刷进去的不是这一版资产。`/usr/lib/modules/` 下没有同名目录，说明 rootfs 与 ESP 的内核不是一套；系统能起来，但一个驱动都加载不了。

能进 GNOME 桌面就是刷写成功最直接的证据。

上面的读回与启动后检查要在从原始 GPT/UFS 重新分区的设备上执行；构建成功、热修复启动、历史资产启动都不能当作 PR 验收证据。

### 6. 回滚到 Android

Linux 起不来时，把槽位切回 A 就能恢复 Android，**不需要**重刷任何分区：

```sh
fastboot set_active a
fastboot reboot
```

要重新刷 Linux，先把槽位按 `fastboot getvar current-slot` 的结果切回 `b`，再重复第 4 节。`userdata` 清空后无法恢复，所以不要擦除它。

---

## 四、 从原始 GPT/UFS 开始的首次安装

安装材料由 `prepare-install.yml` 生成，实际操作由连接平板的 Linux 主机执行；Actions 不接触平板。构建成功、传输成功、Linux 启动成功三项分别记录。

已经有 `sunuefi_root` 与 `sunuefi_esp` 时走下面的更新入口。`install-f2fs.py apply` 会清空 userdata，不能用它做更新。

自动化的首装走 `install-f2fs.py plan` 与 `apply`，完成同一套分区、擦除与传输。命令、run id 记录、产物契约与 PR 验收门槛见[维护与验收](internals.md)。

`device-boot/boot-request.json` 的 `boot_device` 必须指向首装 Android 所在的槽，即 `/dev/disk/by-partlabel/boot_a`。`app_generation` 要与 `deploy-bundle/PianoUEFI-product.img` 里的 APPv1 载荷一致。

请求页写在 Android 槽的合体 BOOT 内。首装把 Android 留在 A 槽，所以请求页在 `boot_a`，Linux 启动镜像才写 `boot_b`。把 `boot_device` 改成 `boot_b` 会让 helper 去 Linux 的 BOOT 上找请求页，写不进去。

`root-install` 已在 `etc/piano/boot-request.json` 写入同一份配置，首启后也不要猜设备名或改槽。否则“返回 Android”和后续启动请求都会失败。

### 手动分区（不使用安装脚本）

不用安装脚本的路径是原厂 Fastboot 加 `sgdisk`。每一步都能单独核对，适合容量与 `product.json` 不一致、或者想自己控制边界的设备。**这些命令会清空 userdata，执行前先备份 Android 数据。**

#### 1. 读取真实 GPT

设备必须停在原厂 Fastboot（不是 fastbootd），并且 `current-slot` 是 `a`：

```sh
fastboot getvar current-slot
fastboot getvar partition-size:userdata
fastboot getvar partition-size:sunuefi_esp
```

进入一个能读块设备的 shell。Android 侧用 `adb shell` 加 `su`；设备已经跑着 Linux 时，可以直接在本机读 `/dev/sda`。先确认扇区大小，后面的换算都依赖它：

```sh
cat /sys/block/sda/queue/physical_block_size   # 这台设备是 4096
sgdisk --print /dev/sda
```

`sgdisk --print` 给出总扇区数、First/Last usable sector 和每个分区的起止扇区。把完整输出存一份，作为本次安装的 GPT 基线。

#### 2. 算容量

以 `L` 表示最后可用扇区、`U` 表示当前 `userdata` 的起始扇区，扇区大小是
`S` 字节：

```text
可用总扇区 = L - U + 1
ESP 扇区   = 512 * 1024 * 1024 / S        # 512 MiB，S=4096 时是 131072
root 扇区  = (可用总扇区 - ESP 扇区) / 2  # 对半；也可以自己指定
```

三个分区的边界：

```text
sunuefi_esp  最后扇区 = L
sunuefi_esp  起始扇区 = 向下对齐 1 MiB(L - ESP 扇区 + 1)
sunuefi_root 最后扇区 = ESP 起始扇区 - 1
sunuefi_root 起始扇区 = 向下对齐 1 MiB(ESP 起始扇区 - root 扇区)
userdata     起始扇区保持不变，最后扇区改为 root 起始扇区 - 1
```

1 MiB 对齐会把起始扇区往前推，实际容量只会大于等于请求值，Android 与 Linux 两侧相差不超过 2 MiB。

可以离线核对这套算术：把 `sgdisk --print` 的输出存成文件，用 `scripts/plan-first-partition.py --dump` 跑一遍，它给出同样的边界和容量，不接触设备。

#### 3. 写 GPT

在设备上依次执行。`<n_root>` 和 `<n_esp>` 用 `sgdisk --print` 里尚未占用的
两个分区号：

```sh
# 缩短 userdata，只改末尾
sgdisk --delete=<n_userdata> /dev/sda
sgdisk --new=<n_userdata>:<U>:<new_userdata_last> /dev/sda
sgdisk --change-name=<n_userdata>:userdata /dev/sda
sgdisk --typecode=<n_userdata>:<原 userdata 的 type GUID> /dev/sda

# Linux root（位于 ESP 之前）
sgdisk --new=<n_root>:<root_first>:<root_last> /dev/sda
sgdisk --change-name=<n_root>:sunuefi_root /dev/sda
sgdisk --typecode=<n_root>:8300 /dev/sda

# ESP（占据盘尾）
sgdisk --new=<n_esp>:<esp_first>:<L> /dev/sda
sgdisk --change-name=<n_esp>:sunuefi_esp /dev/sda
sgdisk --typecode=<n_esp>:EF00 /dev/sda

sync
```

`--delete` 会同时重写主 GPT 和备份 GPT。写完立刻回读核对；任何一项不一致就停下：

```sh
sgdisk --verify /dev/sda
sgdisk --print /dev/sda
```

核对要点：`userdata` 的起始扇区与原来相同。`sunuefi_esp` 结束在 `L`。`sunuefi_root` 紧贴 ESP 之前。三个分区互不重叠，且都在 usable 范围内。

#### 4. 让 Android 重建 userdata

分区变短后，原来的加密 userdata 不再可信。回到 Fastboot 显式擦除：

```sh
fastboot erase userdata
fastboot reboot
```

等 Android 首次开机完成，`adb shell getprop sys.boot_completed` 返回 `1`。这一步不能跳过，否则 Android 会反复重启或加密失败。

#### 5. 刷入 Linux

Android 初始化完成后关机，按【电源键 + 音量减键】进入原厂 Fastboot，确认 `current-slot` 仍是 `a`，再按「三之二、手动刷机」的逐条命令刷入 `boot_b`、`sunuefi_esp`、`sunuefi_root`，最后 `fastboot reboot`。

> 手动路径没有 `install-f2fs.py apply` 的自动回读校验。每刷一个分区后自己执行 `fastboot getvar partition-size:<name>` 和镜像的 `sha256sum`，把结果记进本次会话。分区写错只能靠原厂包恢复，userdata 里的数据无法恢复。

## 已有分区更新

`update-f2fs.py` 复用锁定上游的只读设备/GPT 解析、工厂 Fastboot 身份检查和本仓库的材料校验。它只传输 ESP 与 F2FS root，不写 GPT、userdata 与持久 BOOT。完整 root 会替换 Linux 文件，个人文件先备份。

先恢复配套 ROM 的 B 槽组件，确认原 Android 能完整启动并提供 root ADB；历史的 init_boot/DTBO 试验不算恢复完成的证据。计划会核对主备 GPT/CRC、分区类型、当前槽、原 BOOT、源码清单、管理配置与实际 sparse 容量，还只读取得 Android 的 `/sys/firmware/fdt`，检查 Linux RAM 子集与固定保留区，含义见[上游核对](upstream.md)。

缺 FDT 或比较失败就停下，不要跳过检查。

`stock-recovery/boot.img` 是设备当前槽的原厂 BOOT，执行器要求它 96 MiB 且摘要与设备一致。原厂 ABL 不实现 `fastboot fetch`，从 Android 侧读：

```sh
adb -s <serial> shell su -c 'sha256sum /dev/block/by-name/boot_a'
adb -s <serial> exec-out su -c 'dd if=/dev/block/by-name/boot_a bs=1048576' \
  > stock-recovery/boot.img
sha256sum stock-recovery/boot.img    # 与上一条相同
```

原厂 ROM 包里的 `images/boot.img` 也是 96 MiB，但只有设备还跑着那一版固件时才逐字节相同。用前比摘要，不一致以设备读出的为准。

在连接平板的 Linux 主机上执行，以下路径指本次已下载的材料，`--serial` 是实际 Android ADB 序列号：

```sh
python3 boot-tools/installer/scripts/update-f2fs.py plan \
  --sunuefi boot-tools/installer/sunuefi --serial <serial> \
  --bundle deploy-bundle --root-install root-install \
  --device-boot device-boot --stock-boot stock-recovery/boot.img \
  --plan update-plan.json
```

复核计划并备份 Linux 文件后，使用同一组输入：

```sh
python3 boot-tools/installer/scripts/update-f2fs.py apply \
  --sunuefi boot-tools/installer/sunuefi --serial <serial> \
  --bundle deploy-bundle --root-install root-install \
  --device-boot device-boot --stock-boot stock-recovery/boot.img \
  --plan update-plan.json --session update-session \
  --execute --accept-root-replacement
```

执行前重新比较设备、完整 GPT 基线、内存描述、槽位与材料，保存 GPT 双副本和原 BOOT。进入原厂 bootloader 后，第一笔传输之前核对两个目标的实时容量；fastbootd、序列号不对或槽位不对都拒绝。只传输 `sunuefi_esp` 与 `sunuefi_root`，完成后停在 Fastboot，状态是 `UPDATE_TRANSFERRED_FIRST_BOOT_AND_READBACK_PENDING`。

传输成功不等于读回或启动通过。下一步依次是临时 `boot_linux.img` 启动与设备读回、GNOME 与硬件验收、临时 Android 旁路。全部通过后才单独安装持久 BOOT。

中途中断时先读 session 的 result.json 和实际设备状态，不要自动重试或回滚 GPT。

## 登录和诊断

桌面用户是 `piano`，默认密码 `piano123456`，与上游登录策略一致。桌面自动登录，首次使用后执行 `passwd` 改密。root 密码保持锁定。镜像里没有 `authorized_keys`。要密钥登录就在刷写时用 `flash-linux.sh --ssh-key` 注入，首启后由 `piano-first-boot-ssh-key.service` 导入。

正常 Fedora 用需要账户认证的 `serial-getty@ttyGS0.service`，initramfs 救援的串口 shell 另走一条。

NCM 用上游固定设备 MAC，NetworkManager 静态配置 `192.168.77.1/30`，不加默认路由。主机侧可用 `192.168.77.2/30`。这些入口已经配好，实际 USB 枚举和登录还要在设备上验证。

在现有 Fedora 上用 `stage-rootfs-payload.py --rootfs /` 增量安装时，脚本返回成功前会用本机 `setfiles -F` 恢复新模块、硬件准备程序、USB 诊断程序、systemd 单元和来源记录的 SELinux 标签。来源里的 `user_home_t` 不能用在系统模块上。

摘要一致不代表标签正确。离线 staging 的标签由 build-image 里的目标策略标记步骤处理。

ABL 只读对照把完整取证放在 RAM 的 `/run/piano-boot-evidence.txt`，进串口救援后读取；它不写 Linux 根分区。原生 BOOT 文件检查用 `verify-native-boot.py`，七项文件 guard 的报告与真实设备的槽位、当前来源、块设备写读回分开保存。未完成的 Android 模块不标为 ready。

## 分区与容量

容量不是固定值。**每台设备按自己实时的 GPT 计算**，512 GiB、1 TiB 或其他容量都走同一套规则：

1. 保留 Android 的全部固定分区（`boot_a`、`dtbo_a`、`super` 等）不动；
2. `userdata` 从尾部缩短，让出 Linux 需要的空间；
3. 让出的空间放 `sunuefi_esp`（512 MiB）和 `sunuefi_root`（其余全部）；
4. Android `userdata` 与 `sunuefi_root` 按剩余空间对半分配；
5. 所有新分区起点按 1 MiB 对齐，两边相差不超过 2 MiB。

`product.json` 的 `root_size_mib` 只是一台设备的实测记录，给 `prepare-install` 当默认值，**不是所有设备的约定**。容量变了就按下面的手动流程重算，或者给 `prepare-install` 传自己的 `root_size_mib`。

执行器从实时、双副本 CRC 合格的完整 GPT 重算，结果必须与镜像容量一致。原 userdata 会被清空重建；GPT/BOOT 恢复文件不含 Android 用户数据。

通用包里的 8 GiB F2FS 镜像保持封存。`prepare-root-capacity.py` 在私有工作副本里设置本机启动配置与传感器槽位，恢复匹配的 Fedora SELinux 标签，然后离线扩容。镜像不写任何 SSH 公钥，manifest 用 `ssh_public_key_in_image: false` 记录。

源树与扩容副本的文件、所有权、权限、链接、mtime、全部 xattr 逐项比较；只读 fsck 与 sparse 解码摘要必须通过。

sparse 文件用 RAW 与显式零 FILL，不用 DONT_CARE；展开后的完整字节与验证过的 F2FS 镜像一致。文件系统仍是 F2FS，sparse 只负责 Fastboot 传输。

---

## 五、 首次登录与桌面环境配置

刷完后平板自动重启，进入 120 Hz 的 Fedora 44 GNOME 桌面。

* **默认用户**：`piano`
* **默认密码**：`piano123456`
* **改密**：打开终端执行 `passwd`。

### 默认状态
* **120 Hz 与触控**：Mesa 硬件加速默认开启；触控服务加载 5 阶自适应卡尔曼滤波与 24 像素死区防抖。
* **声学保护**：内核与 ALSA UCM2 把 FS19xx 四功放截断在 -15 dB；麦克风有 +40 dB 硬件增益。
* **电池与电源**：`/hypervisor` 节点已软隔离，GNOME 显示原生双电芯电量，支持合盖与深度挂起。休眠（hibernate）与混合睡眠默认关闭：`sleep.conf.d/piano.conf` 由 `scripts/finalize-fedora-rootfs.py` 写入，`logind.conf.d/piano.conf` 由 `piano-fedora-configs` 包提供。
* **触控提频**：`uperf-linux` 监听触控输入，触点按下后 1 ms 内拉高全大核频率。

---

## 六、 双系统往返

切换不需要电脑。

1. **从 Linux 切换回 Android**：
   - 点击 GNOME 应用抽屉里的**「返回 Android」**图标；
   - 或在终端输入 `piano-next-boot android --reboot`，系统同步数据后重启进入 HyperOS。
2. **从 Android 切换回 Linux**：
   - `piano-switch-ksu` (KernelSU / SukiSU) 匹配模块可以装，但只接管已有核心，不重写 BOOT；Android→Linux 的自动切换尚未验收，见[已知问题](known-issues.md#android-侧)；
   - 现阶段用开机前 3 秒的引导菜单，或从原厂 Fastboot 执行
     `fastboot set_active b` 后重启。
3. **开机引导菜单**：
   - 开机亮屏后的 **前 3 秒内** 按音量上/下键，呼出 SunUEFI 图形引导菜单。

---

## 真机验收

刷完之后逐项核对，每项留一条命令输出或截图作为证据。任一项失败就按失败记录，
不要写成"未验证"。

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

再完整重启一次，确认仍进 GNOME。双系统往返按下一节执行，往返后核对
`fastboot getvar current-slot`。

发布验收的标准比这里更严：必须从原始 GPT/UFS 重新分区，并使用同一条流水线新
产出的 bundle 与 root-install。门槛见[维护与验收](internals.md#pr-验收门槛)。

---

## 七、 常见故障排查

### Q1: 开机直接掉进 Fastboot 怎么解决？
高通 ABL 在开机时没收到明确的 `Successful = 1` 标记，会在连续重启后递减尝试次数，归零后掉进 Fastboot。

系统稳定启动后，确认 `qbootctl.service` 在运行，或手动执行 `sudo qbootctl -m` 把当前槽标记为 `Successful: 1`。在电脑端也可以执行 `fastboot set_active b` 重新激活 Linux 槽位。

### Q2: 误将 Android 数据格式化了？
Slot A 保留着完整的原厂 Android 分区（`boot_a`、`init_boot_a`、`super` 等）。

在 Fastboot 里执行 `fastboot set_active a && fastboot erase userdata && fastboot reboot`，Android 会重建加密分区并走首次开机引导。

### Q3: Type-C 有线网络 SSH 无法连接？
系统把 USB NCM 接口静态配置为 `192.168.77.1/30`。把电脑端对应网卡配到同一子网（例如 IP `192.168.77.2`，掩码 `255.255.255.252`），然后 `ssh piano@192.168.77.1`。
