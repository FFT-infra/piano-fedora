# Xiaomi Pad 8 Pro (piano) 安装、分区与双系统指南

本指南适用于在**小米平板 8 Pro（Xiaomi Pad 8 Pro / 代号 `piano` / 骁龙 8 至尊版 SM8750）**上从零全新安装 Fedora 44 GNOME 桌面系统，或者对已有 Linux 分区进行增量更新。

---

## 一、 前提要求与准备工作

1. **设备已解锁 Bootloader**：
   - 需通过小米官方社区申请并完成 BL 解锁流程；
   - 刷入前请确保平板电量在 **30% 以上**（建议 50% 以上）。
2. **电脑端工具链**：
   - 电脑（Windows / macOS / Linux）已配置好 `fastboot` 与 `adb` 命令行工具环境变量（Android Platform Tools）；
   - Linux 用户请确保配置了 Android 设备 udev 规则，或使用具备 `sudo` 权限的 shell。
   - 需要安装期注入 SSH 公钥时，另装 `mtools`（提供 `mcopy`）：Fedora
     `sudo dnf install mtools`，Ubuntu/Debian `sudo apt install mtools`，
     macOS `brew install mtools`。不注入公钥时不需要它。
3. **重要数据备份**：
   - 首次对平板进行分区调整时，原厂 Android 的 `userdata` 分区将被安全拆分为两部分（一半保留给 Android，一半分配给 Linux），**初次分区会清空 Android 用户数据，请提前完成外部备份**！

---

## 二、 存储与分区架构原理

Xiaomi Pad 8 Pro 拥有极速的 UFS 4.0 物理闪存，采用现代高通 A/B 槽位（Slot A / Slot B）架构：

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

> **不要擦除 `dtbo_b`。** 高通 ABL 在启动时会校验活动槽位的整条引导链，空
> DTBO 会被拒绝为 `Failed to load/authenticate boot image: Device Error`，
> 实测会把平板卡在 Fastboot。SunUEFI 的板级 DTB 来自 `vendor_boot_b` 底包，
> 叠加原厂 `dtbo_b` 增量片段，因此原厂内容必须留在原位。

* **`sunuefi_esp` (512 MiB, FAT32)**：存放 SunUEFI EDK2 固件核心、合并设备树（DTB）、Linux 7.2 主线适配内核及 Initramfs；
* **`sunuefi_root` (F2FS)**：存放 Fedora 44 GNOME 根文件系统。采用闪存友好的 F2FS 格式，彻底发挥高达数 GB/s 的 UFS 4.0 顺序与随机读写潜能；
* **引导稳定性保障 (`qbootctl`)**：系统内置 `qbootctl.service`，每次 Linux 引导成功后自动向底层 GPT 标记 `Successful = 1`，ABL 启动计数器永不归零，**从根源彻底消除掉进 Fastboot 的硬件通病**。

---

## 三、 已有 Linux 分区的刷写（跨平台一键脚本）

适用于已解锁 BL、已经由 `install-f2fs.py apply` 完成首次 GPT 分区并擦除
userdata，而且 `sunuefi_esp` / `sunuefi_root` 已存在的平板。全新盘不能直接
跳到一键刷机。真实首次安装的唯一主流程在第四节；本节之前的分区前提不可跳过。

### 1. 获取 Release 资产
从 [GitHub Releases 页面](https://github.com/FFT-infra/piano-fedora/releases)
下载最新版本的发行包。注意仓库当前还没有任何 Release 与 tag，该链接暂时是空的；
发布前请使用 `build-bundle` 下载的完整 deploy-bundle。不要在本地重新打包
历史 `out/` 目录充当发布资产。

- `piano-installer-*.zip`：一键刷机脚本包（包含 `flash-linux.sh`、`flash-linux.bat`）；
- `boot_sunuefi.img`：SunUEFI 引导头；
- `sunuefi_esp.img`：SunUEFI ESP 分区镜像；
- `pianoroot.f2fs.img.zst`：封存的基础盘，不能直接首装；
- `root-install/pianoroot.f2fs.sparse.img`：`prepare-install` 按本机容量扩容后的传输镜像；
- `SHA256SUMS`：deploy-bundle 内 ESP/BOOT 等资产的摘要；
- `root-install/SHA256SUMS`：root-install 内 sparse 镜像和清单的摘要。

首次安装必须先用同一个 deploy-bundle run 跑 `prepare-install.yml`，并把
Android 槽位固定为 `_a`，取得 `root-install`。它的 sparse 镜像已经扩到真实
`sunuefi_root` 容量、写入设备配置并恢复 SELinux 标签。镜像本身不带任何
`authorized_keys`；管理公钥在刷写时用 `--ssh-key` 注入，见下一节。

准备一个空的安装暂存目录（下面记为 `piano-install/`），完整复制材料，
不要从不同 run 各取一部分：

1. 解压 `piano-installer-*.zip` 到该目录；
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

脚本以当前工作目录为暂存目录。deploy-bundle 的 `SHA256SUMS` 覆盖该
artifact 的全部文件，`root-install/SHA256SUMS` 覆盖 sparse root 和
root-install manifest；脚本再核对 root-install 里的 bundle/root 清单摘要
确实属于当前 deploy-bundle。只复制某几个文件会让清单校验失去意义。

这里的一键脚本前提是分区已经由 `install-f2fs.py apply` 建立。原始机器只有
Android 的 `userdata`，没有 `sunuefi_esp` / `sunuefi_root`；脚本会拒绝，必须
先走第四节的首次分区流程。分区的清空是首次安装的破坏性步骤，不能由这个
刷写脚本代替。脚本固定把 Linux 写到 Slot B，因此运行前 Android 必须仍在
Slot A；如果当前槽是 B，脚本会在任何写入前拒绝。

### 2. 进入 Fastboot 模式
1. 将平板完全关机；
2. 长按 **【电源键 + 音量减键】** 不放，直到屏幕亮起并显示米兔/FASTBOOT 图标；
3. 使用原装或高质量 USB-C 数据线将平板连接至电脑。

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

注入会改变 `sunuefi_esp.img` 的字节，因此它与 deploy-bundle 的 `SHA256SUMS`
不再一致。重跑前请重新解压一份 deploy-bundle；刷写后要读回核对时，用脚本
打印的新摘要，而不是清单里的旧值。Windows 脚本不自动注入，需要时按
`flash-linux.bat` 的提示手工 `mcopy`。

刷机脚本将全自动执行以下动作：
1. 自动检查 `fastboot` 环境与设备在线状态；
2. 校验设备产品代号确为 `piano`，避免误刷；
3. 分别按 deploy-bundle 的 `SHA256SUMS` 和 `root-install/SHA256SUMS`
   校验 BOOT/ESP 与 sparse root；
4. 弹出醒目安全告知，要求用户在终端输入大写 `YES` 确认；
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

一键脚本只是把下面这些命令包了一层。想自己控制每一步、或脚本在你的平台上跑不
起来时，直接照本节操作。命令与脚本执行的是同一套写入，`dtbo_b` 一律不动。

### 1. 从 Release 取哪些文件

从 [GitHub Releases 页面](https://github.com/FFT-infra/piano-fedora/releases)
下载发行包；Release 尚未发布时，使用同一次成功 `build-bundle` run 下载的
完整 `deploy-bundle` 与 `prepare-install` run 下载的 `root-install`，不要使用
历史 `out/` 目录拼装。手动刷机至少需要下面这些资产：

| Release 资产 | 用途 | 刷到哪 |
| --- | --- | --- |
| `boot_sunuefi.img` | SunUEFI 链式引导头 | `boot_b` |
| `sunuefi_esp.img` | ESP 分区镜像（固件核心、DTB、内核、initramfs） | `sunuefi_esp` |
| `root-install/pianoroot.f2fs.sparse.img` | `prepare-install` 扩容并配置后的根文件系统稀疏镜像 | `sunuefi_root` |
| `SHA256SUMS` | deploy-bundle 内 BOOT/ESP 等资产的 SHA256 清单 | 不刷入，只用于校验 |
| `root-install/SHA256SUMS` | root-install 内 sparse 镜像与 manifest 的 SHA256 清单 | 不刷入，只用于校验 |

`piano-installer-*.zip` 和 `piano-switch-ksu-*.zip` 是给一键脚本和 Android 侧
切换模块用的，手动刷机不需要。不要把 deploy-bundle 里的
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

`product` 不是 `piano`、`unlocked` 不是 `yes`、或 `is-userspace` 是 `yes`
（说明落在 fastbootd 而不是原厂 ABL）时，停下，不要写任何分区。
`current-slot` 必须是 `a`；如果是 `b`，一键脚本会拒绝，手动流程也必须停止，
不能把 Linux 写到正在承载 Android 的 `boot_b`。

手动路径同样只接受同一条新流水线产出的材料：完整 `deploy-bundle`，以及
由该精确 bundle run 派生、`android_slot` 为 `_a` 的 `root-install`。刷写前
读取 `fastboot getvar partition-size:sunuefi_root`，把返回的十六进制容量
换算为字节后，必须与 `root-install-manifest.json` 的 `expanded_bytes`
完全相等；容量不一致就停止。

### 4. 逐条刷写

如果你要装一把 SSH 公钥，在刷 ESP 之前先把它写进 ESP 镜像。这一步要在
`sha256sum -c` 通过之后做，因为它会改变镜像字节：

```sh
# 可选：把管理公钥写进 ESP（只接受 ssh-ed25519 / ssh-rsa 公钥）
mcopy -o -i sunuefi_esp.img ~/.ssh/id_ed25519.pub ::/piano-ssh-key.pub
sha256sum sunuefi_esp.img      # 记下新摘要，读回校验用这个值
```

设备首次启动时 `piano-first-boot-ssh-key.service` 会把
`/boot/efi/piano-ssh-key.pub` 追加到 `piano` 的 `authorized_keys`，然后删掉
ESP 上的副本。跳过这一步时系统只保留口令登录，其它行为不变。写坏的公钥
（多行、私钥、非 `ssh-ed25519`/`ssh-rsa`）不会被导入，helper 会直接失败。

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

`fastboot flash` 的 `OKAY` 只证明数据发完了，不证明分区内容正确。重启前可以
读回校验（`sunuefi_esp` 是 512 MiB，读回需要几分钟）：

```sh
fastboot fetch sunuefi_esp esp-readback.img
sha256sum esp-readback.img sunuefi_esp.img
# 两行哈希必须相同
```

该读回只在分区容量与镜像字节数相等时才有意义；先在设备上确认：

```sh
fastboot getvar partition-size:sunuefi_esp
```

结果应等于 `sunuefi_esp.img` 的字节数。容量更大时读回会带上分区尾部原有数据，
哈希不会相同，此时改用下面的启动后验证。设备或 ABL 不支持 `fastboot fetch`
时同样跳过这一步。

如果第 4 节注入过 SSH 公钥，读回比对要用注入后重新算出的摘要，而不是
deploy-bundle 的 `SHA256SUMS` 里的旧值。`mcopy` 会就地改写 `sunuefi_esp.img`，
两份摘要天然不同；记错这一条会把正常的镜像判成损坏。

启动后验证（三项都要满足）：

```sh
uname -r              # 必须等于 ESP 里那个内核的 release（见下）
findmnt /             # 根文件系统为 F2FS
systemctl --failed    # 输出为空
```

`uname -r` 的正确值不是随便一个 7.2.9，而是这一版 ESP 里实际装的内核。
它在两个地方可以查到，两边必须一致：

```sh
# 1) ESP 镜像里的内核字符串（刷写前就能查，不需要设备）
fastboot fetch sunuefi_esp esp.img      # 或直接用发布包里的 sunuefi_esp.img
mtype -i sunuefi_esp.img ::/EFI/Piano/stable/Image > Image.bin
strings -a Image.bin | grep -m1 -oE '[0-9.]+-piano[^ ]*g[0-9a-f]+'

# 2) 设备启动后的实际内核
uname -r

# 3) 模块目录必须与之同名，否则驱动根本不会加载
ls /usr/lib/modules/
```

如果 `uname -r` 与 ESP 里的字符串不符，说明刷进去的不是这一版资产；
如果 `/usr/lib/modules/` 下没有同名目录，说明 rootfs 与 ESP 的内核不是一套，
系统能起来但没有任何驱动。

能进入 GNOME 桌面是刷写成功的最直接证据。以上读回与启动后检查必须在从原始
GPT/UFS 重新分区的这台设备上执行；构建成功、热修复启动或历史资产启动都不能
当作 PR 验收证据。

### 6. 回滚到 Android

Linux 起不来时，把槽位切回 A 即可恢复 Android，**不需要**重刷任何分区：

```sh
fastboot set_active a
fastboot reboot
```

要重新刷 Linux，先把 `fastboot getvar current-slot` 的结果与上一步一致地切回
`b`，再重复第 4 节。`userdata` 一旦被清空无法恢复，这也是不要擦除它的原因。

---

## 四、 从原始 GPT/UFS 开始的首次安装

安装材料由 `prepare-install.yml` 生成，实际操作由连接平板的 Linux 主机执行。
Actions 不连接平板。构建成功、传输成功、Linux 启动成功分别记录。

已有 `sunuefi_root` 和 `sunuefi_esp` 时使用下面的更新入口。首次分区的 `install-f2fs.py apply` 会清空 userdata，不能用于更新。

自动化的首装路径由 `install-f2fs.py plan` 与 `apply` 完成同一套分区、擦除与
传输。命令、run id 记录、产物契约和 PR 验收门槛见[维护与验收](internals.md)。

`device-boot/boot-request.json` 必须记录首装 Android 所在的
`/dev/disk/by-partlabel/boot_a`，其 `app_generation` 必须与
`deploy-bundle/PianoUEFI-product.img` 的 APPv1 载荷一致。请求页写在
`boot_a` 的合体 BOOT 内，Linux 启动镜像才写入 `boot_b`；不要把请求配置
改成 `boot_b`。`root-install` 已在 `etc/piano/boot-request.json` 写入同一份
配置，首启后也不要猜测设备名或改槽。否则 Linux 的“返回 Android”和后续
启动请求会失败。

### 手动分区（不使用安装脚本）

脚本之外的路径就是原厂 Fastboot 加 `sgdisk`。下面每一步都可以单独核对，
适合容量与 `product.json` 不同、或者想自己控制边界的设备。**这些命令会
清空 userdata，执行前先备份 Android 数据。**

#### 1. 读取真实 GPT

设备必须停在原厂 Fastboot（不是 fastbootd），并且 `current-slot` 是 `a`：

```sh
fastboot getvar current-slot
fastboot getvar partition-size:userdata
fastboot getvar partition-size:sunuefi_esp
```

进入一个能读到块设备的 shell。Android 侧用 `adb shell` 加 `su`；如果设备
已经在 Linux，则可以在本机直接读 `/dev/sda`。先确认扇区大小，后面的所有
换算都依赖它：

```sh
cat /sys/block/sda/queue/physical_block_size   # 这台设备是 4096
sgdisk --print /dev/sda
```

`sgdisk --print` 会给出总扇区数、First/Last usable sector，以及每个分区的
起止扇区。把完整输出另存一份，作为本次安装的 GPT 基线。

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

1 MiB 对齐会把起始扇区往前推，所以实际容量只会大于等于请求值，Android 与
Linux 两侧相差不超过 2 MiB。用 `scripts/plan-first-partition.py --dump` 可以
离线核对这套算术：把 `sgdisk --print` 的输出保存成文件，它会给出同样的边界
和容量，不接触设备。

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

`--delete` 会同时重写主 GPT 和备份 GPT。写完立刻回读核对，任何一项不一致就
停下，不要继续往下走：

```sh
sgdisk --verify /dev/sda
sgdisk --print /dev/sda
```

核对要点：`userdata` 的起始扇区与原来相同；`sunuefi_esp` 结束在 `L`；
`sunuefi_root` 紧贴 ESP 之前；三个分区互不重叠，且都在 usable 范围内。

#### 4. 让 Android 重建 userdata

分区变短后，原加密 userdata 不再可信。回到 Fastboot 显式擦除：

```sh
fastboot erase userdata
fastboot reboot
```

等 Android 首次开机完成，`adb shell getprop sys.boot_completed` 返回 `1`。
这一步不能跳；跳过会出现 Android 反复重启或加密失败。

#### 5. 刷入 Linux

Android 初始化完成后关机，按【电源键 + 音量减键】进入原厂 Fastboot，确认
`current-slot` 仍是 `a`，然后按下面「三之二、手动刷机」的逐条命令刷入
`boot_b`、`sunuefi_esp`、`sunuefi_root`，最后 `fastboot reboot`。

> 手动路径不包含 `install-f2fs.py apply` 的自动回读校验。每一步之后自己执行
> `fastboot getvar partition-size:<name>` 和镜像的 `sha256sum`，把结果记进
> 本次会话；分区写错只能靠原厂包恢复，而 userdata 里的数据无法恢复。

## 已有分区更新

`update-f2fs.py` 复用锁定上游的只读设备/GPT 解析、工厂 Fastboot 身份检查与本仓库的材料校验。它只传输现有 ESP 和 F2FS root，保持 GPT、userdata 与持久 BOOT 的写入操作为零。完整 root 会替换 Linux 文件，已有个人文件须先备份。

先恢复正确 ROM 配套的 B 槽组件，确认原 Android 能完整启动并提供 root ADB；历史 init_boot/DTBO 试验不能作为已恢复的证据。计划核对主备 GPT/CRC、分区类型、当前槽、原 BOOT、源码清单、管理配置和实际 sparse 容量。还会只读取得 Android `/sys/firmware/fdt`，检查 Linux RAM 子集与固定保留区；检查含义见[上游核对](upstream.md)。缺少 FDT 或比较失败时停止，不跳过检查。

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

执行前重新比较设备、完整 GPT 基线、内存描述、槽位和材料，保存 GPT 双副本及原 BOOT；进入原厂 bootloader 后，在第一笔传输前核对两个目标的实时容量，拒绝 fastbootd、错误序列号或槽位。只传输 `sunuefi_esp` 与 `sunuefi_root`，完成后停在 Fastboot，状态为 `UPDATE_TRANSFERRED_FIRST_BOOT_AND_READBACK_PENDING`。

传输不等于读回或启动通过。下一步是临时 `boot_linux.img` 启动与设备读回、GNOME/硬件验收、临时 Android 旁路；通过后才单独安装持久 BOOT。中断时先读取 session 的 result.json 和实际状态，不自动重试或回滚 GPT。

## 登录和诊断

Fedora 的初始桌面用户为 `piano`，默认密码 `piano123456`，与上游新登录策略一致。桌面自动登录；首次使用后运行 `passwd` 修改密码。root 密码保持锁定。镜像不带任何 `authorized_keys`；需要密钥登录时，在刷写阶段用
`flash-linux.sh --ssh-key` 注入，首启后由 `piano-first-boot-ssh-key.service` 导入。

正常 Fedora 使用需要账户认证的 `serial-getty@ttyGS0.service`；initramfs 救援的串口 shell 与之分开。NCM 使用上游固定设备 MAC，NetworkManager 静态配置为 `192.168.77.1/30`，不添加默认路由；主机可使用 `192.168.77.2/30`。这些是已配置的接管入口，实际 USB 枚举和登录仍需设备验证。

在现有 Fedora 上用 `stage-rootfs-payload.py --rootfs /` 增量安装时，脚本在返回成功前用本机 `setfiles -F` 恢复新模块、硬件准备和 USB 诊断程序、systemd 单元及来源记录的 SELinux 标签。复制来源中的 `user_home_t` 不能用于系统模块；摘要一致不代表标签正确。离线 staging 的标签由 build-image 中的完整目标策略标记步骤处理。

ABL 只读对照把完整取证保存到 RAM 中 `/run/piano-boot-evidence.txt`，进入串口救援后可读取；它不写 Linux 根分区。原生 BOOT 文件检查使用 `verify-native-boot.py`，七项文件 guard 的报告与真实设备的槽位、当前来源及块设备写读回分开保存，不将未完成的 Android 模块标为 ready。

## 分区与容量

容量不是固定值。**每台设备按自己实时的 GPT 计算**，512 GiB、1 TiB 或其他
容量都走同一套规则：

1. 保留 Android 的全部固定分区（`boot_a`、`dtbo_a`、`super` 等）不动；
2. `userdata` 从尾部缩短，让出 Linux 需要的空间；
3. 让出的空间放 `sunuefi_esp`（512 MiB）和 `sunuefi_root`（其余全部）；
4. Android `userdata` 与 `sunuefi_root` 按剩余空间对半分配；
5. 所有新分区起点按 1 MiB 对齐，两边相差不超过 2 MiB。

`product.json` 里的 `root_size_mib` 只是某台设备实测的一次记录，用来给
`prepare-install` 提供默认值，**不能当成所有设备的约定**。换了容量就要用
下面的手动流程重新算，或者给 `prepare-install` 传自己的 `root_size_mib`。

执行器从实时、双副本 CRC 合格的完整 GPT 重算，结果必须与镜像容量一致。
原 userdata 会清空重建，GPT/BOOT 恢复文件不包含 Android 用户数据。

通用包中的 8 GiB F2FS 镜像保持封存。`prepare-root-capacity.py` 在私有工作副本中
设置本机启动配置和传感器槽位，恢复匹配的 Fedora SELinux 标签后离线扩容。
镜像不写任何 SSH 公钥，manifest 用 `ssh_public_key_in_image: false` 记录这一点。
源树与扩容后的文件、所有权、权限、链接、mtime、全部 xattr 逐项比较。只读 fsck
和 sparse 解码摘要必须通过。

sparse 文件使用 RAW 与显式零 FILL，不使用 DONT_CARE；其完整展开字节与
验证过的 F2FS 镜像一致。它仍是 F2FS 文件系统，sparse 只负责 Fastboot 传输。

---

## 五、 首次登录与桌面环境配置

刷机完成后平板自动重启，片刻后即可直接进入流畅的 120Hz Fedora 44 GNOME 桌面：

* **默认桌面用户**：`piano`
* **默认登录密码**：`piano123456`
* **首次改密**：登入桌面后，请按需要打开终端执行 `passwd` 设置个性化密码。

### 核心特性默认状态
* **120Hz 刷新率与触控**：默认开启 Mesa 硬件加速；触控服务已加载 5 阶自适应卡尔曼滤波与 24 像素死区防抖锁，窗口拖拽与书写零抖动；
* **声学安全保护**：已通过内核及 ALSA UCM2 将 FS19xx 四功放硬截断在 -15dB 安全上限内，VA 麦克风提供 +40dB 硬件级拾音增益；
* **电池与电源控制**：真机 `/hypervisor` 节点已通过软隔离屏蔽，GNOME 设置面板完美呈现原生双电芯电量百分比、休眠与合盖选项；
* **触控即时提频**：`uperf-linux` 服务在后台实时监听触控输入，手指触碰瞬间 1ms 内拉高全大核频率，带来跟手体验。

---

## 六、 双系统无缝往返指南

无需连接电脑，随时随地在 Linux 与 Android 之间快速往返：

1. **从 Linux 切换回 Android**：
   - 点击 GNOME 应用抽屉里的**「返回 Android」**图标；
   - 或在终端输入 `piano-next-boot android --reboot`，系统将安全同步数据并重启进入 HyperOS。
2. **从 Android 切换回 Linux**：
   - 在 Android (HyperOS) 中安装并启用 `piano-switch-ksu` (KernelSU / SukiSU) 模块；
   - 点击控制中心磁贴或模块界面中的**「进入 Linux」**，无需电脑介入即可切换回 Fedora。
3. **开机硬件选单**：
   - 开机屏幕亮起的 **前 3 秒内** 按下平板音量上/下键，可直接呼出 SunUEFI 图形引导菜单，随时自由选择。

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

## 七、 常见故障排查（FAQ & Troubleshooting）

### Q1: 开机直接掉进 Fastboot 怎么解决？
- **根本原因**：高通 ABL 在开机未收到明确 `Successful = 1` 标记时，会在连续重启后递减尝试次数，直至归零跌入 Fastboot。
- **解决方案**：在系统稳定启动后，确保 `qbootctl.service` 正常运行，或者手动在终端执行 `sudo qbootctl -m`，将当前 Slot 标记为 `Successful: 1`。在 PC 端亦可运行 `fastboot set_active b` 重新激活 Linux 槽位。

### Q2: 误将 Android 数据格式化了？
- **恢复方案**：Slot A 始终保留着完整的原厂 Android 系统镜像（boot_a、init_boot_a、super 等）。若需要重置 Android，可在 Fastboot 模式下执行 `fastboot set_active a && fastboot erase userdata && fastboot reboot`，Android 将自动重建加密分区并完成首次开机引导。

### Q3: Type-C 有线网络 SSH 无法连接？
- **排查**：系统已将 USB NCM 接口静态配置为 `192.168.77.1/30`。请确保电脑端对应网卡手动配置为同一子网（例如 IP: `192.168.77.2`, 子网掩码: `255.255.255.252`），即可通过 `ssh piano@192.168.77.1` 直接登入。
