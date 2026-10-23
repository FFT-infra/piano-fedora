# Xiaomi Pad 8 Pro (piano) 安装、分区与双系统指南

本指南覆盖：在**小米平板 8 Pro（代号 `piano`，骁龙 8 至尊版 SM8750）**上从零安装 Fedora 44，对已有 Linux 分区做增量更新，以及双系统往返与故障排查。默认桌面是 GNOME，其余档位的差异见[桌面档](desktops.md)。

---

## 一、 前提要求与准备工作

1. **解锁 Bootloader**：通过小米官方渠道完成 BL 解锁。刷入前电量保持 **30% 以上**，50% 更好。
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
  【Slot A（默认启动）】                                       【Slot B（保留原厂，不切槽）】
  - boot_a (合体 BOOT：原厂 Android 内核                        - boot_b
    + 3 秒选择器 + SunUEFI UEFI 核心)                          - dtbo_b
  - dtbo_a / init_boot_a                                        - init_boot_b
  - super / userdata
  - sunuefi_esp (512 MiB, FAT32，GPT 顶层分区)
  - sunuefi_root (剩余空间, F2FS，GPT 顶层分区)
```

合体 BOOT 把 Android 内核、选择器与 UEFI 核心装进同一个 `boot_a`。开机前 3 秒
按音量键进入 SunUEFI 菜单，否则按请求页里的持久选择进入 Android 或 Linux；
系统不切换活动槽，`boot_b` 原样保留。

> **不要擦除 `dtbo_a` 或 `dtbo_b`。** 高通 ABL 启动时校验活动槽位的整条引导链，空 DTBO 会被拒绝，报 `Failed to load/authenticate boot image: Device Error`，平板卡在 Fastboot。
>
> SunUEFI 的板级 DTB 来自 `vendor_boot_a` 底包，再叠加原厂 `dtbo_a` 的增量片段。原厂内容必须留在原位。

* **`sunuefi_esp`（512 MiB，FAT32）**：SunUEFI EDK2 固件、合并设备树（DTB）、Linux 7.2 适配内核与 initramfs。
* **`sunuefi_root`（F2FS）**：Fedora 44 根文件系统，桌面按档位装配。F2FS 为闪存设计，能发挥 UFS 4.0 的顺序与随机读写性能。
* **引导稳定性（`qbootctl`）**：`qbootctl.service` 在每次 Linux 引导成功后向底层 GPT 写入 `Successful = 1`，ABL 的启动计数器不会归零。
* **容量按实时 GPT 计算。** 容量不是固定值，**每台设备按自己实时的 GPT 计算**，512 GiB、1 TiB 或其他容量都走同一套规则：

  1. 保留 Android 的全部固定分区（`boot_a`、`dtbo_a`、`super` 等）不动；
  2. `userdata` 从尾部缩短，让出 Linux 需要的空间；
  3. 让出的空间放 `sunuefi_esp`（512 MiB）和 `sunuefi_root`（其余全部）；
  4. Android `userdata` 与 `sunuefi_root` 默认按剩余空间对半分配，也可以固定 Android 的容量（`--userdata-mib`，见第四节）；
  5. 所有新分区起点按 1 MiB 对齐，两边相差不超过 2 MiB。

  `product.json` 的 `root_size_mib` 是封存镜像的容量（8192 MiB），与设备无关。`sunuefi_root` 的实际容量从实时 GPT 算，文件系统在首启时长满该分区。

  **Android 侧需要 root 才能读写块设备。** 获取 root 是本指南的前提，本仓库不提供方法。首次分区只有两条路：用 `install-f2fs.py apply` 在已 root 的 Android 上写 GPT，或者按本节的手动步骤在 Android 上执行 `adb shell su -c`。两者都离不开 root；原厂 Fastboot 只能读分区容量、擦除和刷写，不能改分区表。

---

## 三、 已有 Linux 分区的刷写（跨平台一键脚本）

适用于已解锁 BL、`install-f2fs.py apply` 已建好 GPT 分区并擦除 userdata、`sunuefi_esp` 与 `sunuefi_root` 都在的设备。全新盘不能直接跳到一键刷机。首次安装的主流程在第四节，这一节的分区前提不能跳过。

### 1. 获取 Release 资产
从 [GitHub Releases 页面](https://github.com/FFT-infra/piano-fedora/releases) 下载最新发行包。Release 由 tag 触发，内容是同一次 `build-bundle` 与它派生的 `prepare-install` 产物；失败后可以用同一个 workflow 手动重跑。不要在本地重新打包历史 `out/` 目录充当发布资产。

Release 资产是扁平的。装机只需要平台资产与所选档位的 `root-install-*`，其他档位的 root 各 1–3 GiB，不必下载。放进同一个空目录后执行 `bash unpack-release.sh --desktop <档位>`，脚本按 `RELEASE-SHA256SUMS` 校验目录里已有的资产，缺的档位不影响所选档位的还原。

一个 Release 可以带多档桌面（`gnome`、`kde`、`niri`、`hyprland`）。ESP、内核、
initramfs 与 UEFI 四档逐字节相同，只发布一份；`root-install/` 每档
各一份，名字带 `-<档位>` 后缀。`--desktop` 决定哪一档的 root 进入
`root-install/`；Release 里只有一档时脚本自动识别，多档时不传参数会报错并列出
可选项。各档有什么差别见[桌面档](desktops.md)。

`unpack-release.sh` 会合并大于 2 GiB 的分片、解压 zstd 传输镜像、把桌面资产还原
成下面的规范名，并把 `root-install/` 放回脚本要求的布局。完成后这个目录就是下面的
暂存目录。

- `piano-installer-<tag>.zip`：`flash-linux.sh`、`flash-linux.bat`、`README-INSTALL.txt` 与 `unpack-release.sh`；
- `piano-boot-tools-<tag>.tar.zst`：生成设备专用 `device-boot/boot_linux.img` 的脚本与 x86-64 工具；
- `sunuefi_esp.img`：SunUEFI ESP 分区镜像；
- `boot_sunuefi.img`：ESP 内部的 Linux 载荷，不刷任何分区；
- `pianoroot.f2fs.img.zst`（单档）或 `pianoroot-<档位>.f2fs.img.zst`（多档）：封存的基础盘，不能直接首装；
- `pianoroot.f2fs.sparse.img`：deploy-bundle 自带的可刷传输镜像；
- `root-install/pianoroot.f2fs.sparse.img`：`prepare-install` 写入设备配置后的传输镜像；
- `SHA256SUMS`：deploy-bundle 内资产的摘要；
- `root-install/SHA256SUMS`：root-install 内 sparse 镜像与清单的摘要。

`root-install` 里的 sparse 镜像已写入设备配置并恢复 SELinux 标签。没有指定 `root_size_mib` 时它保持封存容量，首次启动再扩满 `sunuefi_root`。Release 固定使用 Android Slot A（`_a`）；B 槽设备要用同一次 bundle run 自己跑 `prepare-install.yml` 的 `_b`，不能直接用 Release 的 root-install。

镜像里没有 `authorized_keys`。管理公钥在刷写时用 `--ssh-key` 注入，见下一节。

没有 Release 时，用同一次成功 `build-bundle` run 的完整 deploy-bundle，以及这个 run 派生的 `prepare-install` 的 `root-install`：

1. 把 `scripts/installer/` 里的 `flash-linux.sh`、`flash-linux.bat`、`README-INSTALL.txt` 复制到暂存目录；
2. 完整解压同一 run 的 `deploy-bundle/` 到同一层，保留其中全部普通文件；
3. 完整解压同一 deploy-bundle run 派生的 `root-install/` 到这个目录下；
4. 不要把两个 artifact 的 `SHA256SUMS` 合并、改名或重新生成。

用一个空目录（下面记作 `piano-install/`），把材料完整放在一起。用 Release 时，`unpack-release.sh` 已经把 bundle 文件放在这一层并解出 `root-install/`；再从 `piano-installer-<tag>.zip` 里取出三个刷写脚本放到同一层。不要从不同 run 各取一部分。

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
├── device-boot/
│   ├── boot_linux.img
│   └── boot-request.json
└── root-install/
    ├── pianoroot.f2fs.sparse.img
    ├── root-install-manifest.json
    └── SHA256SUMS
```

`device-boot/` 不属于 deploy-bundle，要用 `scripts/prepare-device-boot.py` 在
本机生成一次。它不在两份 `SHA256SUMS` 里，脚本单独核对它的结构。

脚本把当前工作目录当作暂存目录。deploy-bundle 的 `SHA256SUMS` 覆盖 deploy-bundle 的全部文件，`root-install/SHA256SUMS` 覆盖 sparse root 与 root-install manifest。脚本还核对 root-install 里的 bundle/root 清单摘要确实来自当前 deploy-bundle。

只复制几个文件会让清单校验失去意义。

一键脚本要求分区已由 `install-f2fs.py apply` 建立。原厂设备只有 Android 的 `userdata`，没有 `sunuefi_esp` / `sunuefi_root`，脚本会拒绝。必须先走第四节的首次分区。清空分区是首次安装的破坏性步骤，不能由刷写脚本代劳。

脚本把合体 BOOT 写进当前活动槽，所以运行前 Android 必须在 Slot A；当前槽是 B 时，脚本在任何写入前就退出。

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
   - 保持活动槽 A，不切槽
   - 刷入合体 BOOT：`fastboot flash boot_a device-boot/boot_linux.img`
   - 刷入 ESP 分区：`fastboot flash sunuefi_esp sunuefi_esp.img`
   - 刷入系统根分区：`fastboot flash sunuefi_root pianoroot.f2fs.sparse.img`
   - 自动重启：`fastboot reboot`

   `device-boot/boot_linux.img` 由 `scripts/prepare-device-boot.py` 用本机原厂
   BOOT 重打包产生，不在 deploy-bundle 里，必须自己生成一次。脚本按
   `boot_linux.img`、`device-boot/boot_linux.img` 的顺序在暂存目录里找它，也
   可以用 `--boot` 指定。deploy-bundle 里的 `boot_sunuefi.img` 是 ESP 内部的
   Linux 载荷，脚本会明确拒绝它。

   脚本不写 `dtbo_a`、`init_boot_a`、`vendor_boot_a`、`vbmeta_a`，也不擦除
   `userdata`。需要逐条手动执行同样步骤时，见下一节。

---

## 三之二、 手动刷机（不使用一键脚本）

一键脚本只是把下面这些命令包了一层。想自己控制每一步，或者脚本在你的平台上跑不起来时，直接照这一节执行。写入内容与脚本相同，`dtbo_a` 一律不动。

### 1. 从 Release 取哪些文件

从 [GitHub Releases 页面](https://github.com/FFT-infra/piano-fedora/releases) 下载平台资产与所选档位的 `root-install-*`（其他档位的 root 不必下）。资产是扁平的，`bash unpack-release.sh --desktop <档位>`（只有一档时可省略）负责校验、合并分片并还原成规范名；手动刷机只认解包后的文件名。还没有 Release 时，用同一次成功 `build-bundle` run 的完整 `deploy-bundle`，以及这个 run 派生的 `prepare-install` 的 `root-install`。不要拿历史 `out/` 目录拼装。

手动刷机至少需要这些资产：

| 资产 | 来源 | 用途 | 刷到哪 |
| --- | --- | --- | --- |
| `device-boot/boot_linux.img` | 本地生成：`prepare-device-boot.py` 用本机原厂 BOOT 重打包 | 合体 BOOT | `boot_a` |
| `sunuefi_esp.img` | Release / deploy-bundle | ESP 分区镜像（固件核心、DTB、内核、initramfs） | `sunuefi_esp` |
| `root-install/pianoroot.f2fs.sparse.img` | Release / `prepare-install` 派生 | 已写入设备配置的根文件系统稀疏镜像 | `sunuefi_root` |
| `boot_sunuefi.img` | Release / deploy-bundle | ESP 内部的 Linux 载荷，不刷任何分区 | 不刷入 |
| `SHA256SUMS` | Release / deploy-bundle | deploy-bundle 内资产的 SHA256 清单 | 不刷入，只用于校验 |
| `root-install/SHA256SUMS` | Release / `prepare-install` 派生 | root-install 内 sparse 镜像与 manifest 的 SHA256 清单 | 不刷入，只用于校验 |

Release 里的 `piano-installer-<tag>.zip` 只含脚本与说明，不含镜像；`piano-boot-tools-<tag>.tar.zst` 用来在本机生成 `device-boot/boot_linux.img`。`piano-switch-ksu-*.zip`（Android 引导助手模块）由 SunUEFI 的模块工具单独构建，不在 Release 里。不要把 deploy-bundle 里的
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
fastboot getvar current-slot   # 必须是 a；合体 BOOT 写进当前槽
fastboot getvar unlocked       # 必须是 yes
fastboot getvar is-userspace   # 必须是 no
```

任一项不对就停下，不要写任何分区：`product` 不是 `piano`，`unlocked` 不是 `yes`，或者 `is-userspace` 是 `yes`（说明落在了 fastbootd，不是原厂 ABL）。

`current-slot` 必须是 `a`。是 `b` 时一键脚本会拒绝，手动流程也必须停：首装布局只支持原厂 Android 所在的 A 槽。

`boot_a` 要写的是 `prepare-device-boot.py` 用本机原厂 BOOT 重打包出的 `device-boot/boot_linux.img`，不是 `deploy-bundle` 里的 `boot_sunuefi.img`。后者是 ESP 内部的 Linux 载荷（Header v2 容器，没有 `SUNUEFI-NEXTv1` 请求页），只有 SunUEFI 固件从 ESP 里加载。把它刷进 `boot_a`，ABL 会拒绝整条链，平板停在 Fastboot。

合体 BOOT 保持上游的 unsigned AVB NONE 元数据，`prepare-device-boot.py` 不补
`os_version`，也不重新签名。槽 A 的 ABL 直接加载这份镜像；已用同一
份未签名镜像在真机上完成 Android→Linux→Android 往返。不要自行补 `os_version`
或 RSA4096 footer：`piano-boot-repack` 的 probe/request/adopt 会把首块与包装器
里保存的原厂头逐字节比较，改了偏移 16，Android 侧的引导助手就认不出当前
BOOT，`switch linux` 会直接失败。

手动路径也只接受同一条新流水线的材料：完整 `deploy-bundle`，以及由这个 bundle run 派生、`android_slot` 为 `_a` 的 `root-install`。

刷写前读 `fastboot getvar partition-size:sunuefi_root`，把十六进制容量换算成字节。镜像不能大于这个分区：`root-install-manifest.json` 的 `expanded_bytes`，或者不带 `root-install` 时 `root-image-manifest.json` 的 `bytes`，都必须小于等于分区容量，否则就停。小于时首次启动会把文件系统扩满分区。

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
# 1. 合体 BOOT -> boot_a（活动槽保持 A，不切槽）
fastboot flash boot_a device-boot/boot_linux.img

# 2. ESP -> sunuefi_esp
fastboot flash sunuefi_esp sunuefi_esp.img

# 3. 根文件系统 -> sunuefi_root（sparse 只传有效块，需要几分钟）
fastboot flash sunuefi_root root-install/pianoroot.f2fs.sparse.img

# 4. 重启进系统
fastboot reboot
```

每条命令都必须打印 `OKAY`。出现 `FAILED` 就停下，不要继续后面的步骤。

**不要**执行 `fastboot erase dtbo_a`、`fastboot erase dtbo_b`、`fastboot erase boot_a`、
`fastboot erase userdata`、`fastboot -w`，也不要写 `abl`、`xbl`、`tz`、`modem`
等引导链分区。

### 5. 验证刷写结果

`fastboot flash` 的 `OKAY` 只说明数据发完了，不说明分区内容正确。重启前可以读回校验；`sunuefi_esp` 约 512 MiB，读回要几分钟：

```sh
fastboot getvar partition-size:sunuefi_esp     # 十六进制字节数
fastboot fetch sunuefi_esp esp-readback.img
```

`sunuefi_esp` 分区通常比 `sunuefi_esp.img` 大（按 512 MiB 规划分区，镜像
536870912 字节；实际分区字节数以自己的 `fastboot getvar` 结果为准）。`fetch`
读的是整个分区，尾部会带上分区里原有的数据，整份哈希必然不同。

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

能进所选桌面就是刷写成功最直接的证据。

这些检查要在从原始 GPT/UFS 重新分区的设备上做一次才有意义。在旧的 root 分区上热替换文件，或者用历史资产启动的机器，都不能说明这份镜像能装。

### 6. 回滚到 Android

Linux 起不来时，先从 SunUEFI 菜单选 Android：开机前 3 秒按音量键，或在 Android
正常时用 `piano-next-boot android --reboot`。合体 BOOT 本身损坏时，把备份的原厂
引导镜像刷回 `boot_a`，**不需要**改动分区或活动槽：

```sh
fastboot flash boot_a stock-recovery/boot.img
fastboot reboot
```

要重新刷 Linux，直接重复第 4 节。`userdata` 清空后无法恢复，所以不要擦除它。

---

## 四、 从原始 GPT/UFS 开始的首次安装

`root-install` 由 `prepare-install.yml` 生成，设备专用的 `device-boot` 由本机
`prepare-device-boot.py` 生成；实际操作由连接平板的 Linux 主机执行，Actions
不接触平板。构建成功、传输成功、Linux 启动成功三项分别记录。

已经有 `sunuefi_root` 与 `sunuefi_esp` 时走下面的更新入口。`install-f2fs.py apply` 会清空 userdata，不能用它做更新。

自动化的首装走 `install-f2fs.py plan` 与 `apply`，完成同一套分区、擦除与传输。先在已 root 的 Android 上生成只读计划：

```sh
python3 boot-tools/installer/scripts/install-f2fs.py plan \
  --sunuefi boot-tools/installer/sunuefi --serial <序列号> \
  --bundle deploy-bundle --root-install root-install --device-boot device-boot \
  --stock-boot stock-recovery/boot.img --plan first-install-plan.json
```

逐项复核计划后才执行。`apply` 会清空 userdata，必须同时给 `--execute`、`--accept-data-loss` 和一个尚不存在的 session 目录：

```sh
python3 boot-tools/installer/scripts/install-f2fs.py apply \
  --sunuefi boot-tools/installer/sunuefi --serial <序列号> \
  --bundle deploy-bundle --root-install root-install --device-boot device-boot \
  --stock-boot stock-recovery/boot.img --plan first-install-plan.json \
  --session sessions/first-install --execute --accept-data-loss
```

每步的输入契约、run id 记录与验收门槛见[维护与验收](internals.md)。

`device-boot/boot-request.json` 的 `boot_device` 是 `/dev/disk/by-partlabel/boot_a`。`app_generation` 要与 `deploy-bundle/PianoUEFI-product.img` 里的 APPv1 载荷一致。

选择器在“正在引导的那份镜像”末尾读请求页：ABL 只加载当前槽，`BootSelect.c` 按 `Base + align(original_image_size)` 取页。合体 BOOT 装进 A 槽，Linux 与 Android 都从同一份 `boot_a` 启动，请求页因此指向 `boot_a`；不切槽，也不存在第二份需要同步的页。

带了 `root-install` 时镜像里的 `etc/piano/boot-request.json` 就是这条配置；不带时镜像里没有这个文件，首启的 `piano-boot-request-autoconfig.service` 按当前槽重建。首启后也不要猜设备名或改槽。否则“返回 Android”和后续启动请求都会失败。

### 手动分区（不使用安装脚本）

不用安装脚本的路径只用设备上一定有的 `dd`，配合 `scripts/plan-first-partition.py` 离线算出新的整表。每一步都能单独核对，适合容量与默认分配不一致、或者想自己控制边界的设备。**这些命令会清空 userdata，执行前先备份 Android 数据。**

#### 1. 读取真实 GPT

设备必须停在原厂 Fastboot（不是 fastbootd），并且 `current-slot` 是 `a`：

```sh
fastboot getvar current-slot
fastboot getvar partition-size:userdata
fastboot getvar partition-size:sunuefi_esp
```

Android 侧需要 root，整盘是 `/dev/block/sda`，可写目录是 `/data/local/tmp`。下面这一段在**宿主 shell** 里跑完，变量留在宿主上，后面几步都要用。

`/sys/block/sda/size` 的单位是 512 字节扇区，GPT 用的是 4096 字节逻辑块，两者差 8 倍，不能直接当 LBA 总数用。总 LBA 数以主表头里的 `alternate_lba` 为准，它就是磁盘最后一个 LBA。表项数也由表头决定，两份 blob 的长度都从它算：

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

尾部不能按 `LAST-BLOCKS` 推：原厂 `gpt_backup0.bin` 是 5 个块，备份表之后留 2 个空块才到备份表头，`partition_entry_lba` 才是表的真实位置（这里是 `LAST-4`）。先读最后一块拿到表头，再按表头算起点和长度。

两份原始读数就是分区前的 GPT 基线。`plan-first-partition.py --head/--tail --sectors` 能直接解析它们。设备已经跑着 Linux 时跳过 `adb`，把 `DISK` 换成 `/dev/sda`、`TMP` 换成 `/tmp`，其余相同。

#### 2. 算容量

以 `L` 表示最后可用扇区、`U` 表示当前 `userdata` 的起始扇区，扇区大小是
`S` 字节：

```text
可用总扇区 = L - U + 1
ESP 扇区   = 512 * 1024 * 1024 / S        # 512 MiB，S=4096 时是 131072
root 扇区  = (可用总扇区 - ESP 扇区) / 2  # 对半；也可以自己指定
```

想给 Android 固定容量，把最后一行换成下面两行，其余不变。`307200` 是 300 GiB
的 MiB 数；`userdata` 起点不动，所以 Android 实际拿到的就是这个值：

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

1 MiB 对齐会把起始扇区往前推，实际容量只会大于等于请求值，Android 与 Linux 两侧相差不超过 2 MiB。

这套算术不用手算。把两份现场读数拉回本机，交给规划器，它写出新的整表与两份摘要：

```sh
python3 scripts/plan-first-partition.py --head gpt-head.raw --tail gpt-tail.raw \
  --sectors "$TOTAL" --userdata-mib 307200 \
  --output first-install-plan.json --emit-gpt gpt/
```

设备上已经有 Linux、能直接读整盘时，也可以跳过两份读数，直接 `--device /dev/sda`，其余参数相同。

`--emit-gpt` 产出四个文件：`gpt-head.bin`（保护性 MBR + 主表头 + 主分区表）、
`gpt-tail.bin`（备份分区表 + 空隙 + 备份表头）、`original-gpt-head.bin`、
`original-gpt-tail.bin`（分区前的原表）与 `SHA256SUMS`。尾部长度随现场表头，
原厂表是 `TAIL_BLOCKS` 个块。这些是**文件，还没写进设备**。
规划器在写出前会核对现场读数与计划是同一条表，不一致就停。

只有文本 dump 时也能规划，但不能 `--emit-gpt`。`sfdisk --dump` 的文本不带总扇区数与表项数，要自己补。`--sectors` 要的是 4096 字节 LBA 总数，不是 `/sys/block/sda/size`：

```sh
adb shell su -c "sfdisk --dump $DISK" > gpt.txt
python3 scripts/plan-first-partition.py --dump gpt.txt --sectors "$TOTAL" \
  --entries "$ENTRIES" --userdata-mib 307200
```

#### 3. 写 GPT

先回读核对：`gpt-head.bin` 与 `gpt-tail.bin` 推上设备后，必须与本地逐字节相同。

```sh
adb push gpt/gpt-tail.bin gpt/gpt-head.bin $TMP/
adb shell su -c "sha256sum $TMP/gpt-tail.bin $TMP/gpt-head.bin"
sha256sum gpt/gpt-tail.bin gpt/gpt-head.bin
```

再按**先尾后头**的顺序写入。尾部先落盘，中断后旧主表头的 `alternate_lba` 仍能找到新尾部；反过来会留下“头新尾旧”。尾部 blob 从 `TAIL_FIRST` 开始，占 `TAIL_BLOCKS` 个块；头部占 2 个块加同一张表：

```sh
adb shell su -c "dd if=$TMP/gpt-tail.bin of=$DISK bs=4096 seek=$TAIL_FIRST conv=fsync,notrunc"
adb shell su -c "dd if=$TMP/gpt-head.bin of=$DISK bs=4096 seek=0 conv=fsync,notrunc && sync"
```

写完立刻回读，两份都必须与源 blob 相同：

```sh
adb shell su -c "dd if=$DISK bs=4096 skip=$TAIL_FIRST count=$TAIL_BLOCKS" | sha256sum
adb shell su -c "dd if=$DISK bs=4096 count=$((BLOCKS+2))" | sha256sum
```

核对要点：`userdata` 的起始扇区与原来相同，`sunuefi_esp` 结束在 `L`，`sunuefi_root` 紧贴 ESP 之前。任一项不一致就用 `original-gpt-*.bin` 按同样顺序写回，不要继续。

写回的顺序与写入相反：先恢复头部，再恢复尾部。这两份是分区前从设备读到的原表，写回去等于撤销本次分区。

```sh
adb push gpt/original-gpt-head.bin gpt/original-gpt-tail.bin $TMP/
adb shell su -c "dd if=$TMP/original-gpt-head.bin of=$DISK bs=4096 seek=0 conv=fsync,notrunc"
adb shell su -c "dd if=$TMP/original-gpt-tail.bin of=$DISK bs=4096 seek=$TAIL_FIRST conv=fsync,notrunc && sync"
```

#### 4. 让 Android 重建 userdata

分区变短后，原来的加密 userdata 不再可信。回到 Fastboot 显式擦除：

```sh
fastboot erase userdata
fastboot reboot
```

实机上擦除不一定能让 Android 自动格式化：两次安装都停在
Recovery，屏幕底部显示 `Couldn't initialize user 0`，需要在 Recovery 里再选
一次“清除数据”，Android 才会重建加密分区并完成首次开机。看到这一行就按
Recovery 的提示清除数据，不要反复切槽或重写分区表。

等 Android 首次开机完成，`adb shell getprop sys.boot_completed` 返回 `1`。这一步不能跳过，否则 Android 会反复重启或加密失败。

#### 5. 刷入 Linux

Android 初始化完成后关机，按【电源键 + 音量减键】进入原厂 Fastboot，确认 `current-slot` 仍是 `a`，再按「三之二、手动刷机」的逐条命令刷入 `boot_a`、`sunuefi_esp`、`sunuefi_root`，最后 `fastboot reboot`。

> 手动路径没有 `install-f2fs.py apply` 的自动回读校验。每刷完一个分区，自己执行 `fastboot getvar partition-size:<name>` 和镜像的 `sha256sum`，把结果记进会话记录里。分区写错只能靠原厂包恢复，userdata 里的数据无法恢复。

## 更新已有 Linux 分区

`update-f2fs.py` 复用锁定上游的只读设备/GPT 解析、工厂 Fastboot 身份检查和本仓库的材料校验。它只传输 ESP 与 F2FS root，不写 GPT、userdata 与持久 BOOT。完整 root 会替换 Linux 文件，个人文件先备份。

先确认原 Android 能从当前活动槽完整启动并提供 root ADB；历史的 init_boot/DTBO 试验不算恢复完成的证据。计划会核对主备 GPT/CRC、分区类型、当前槽、原 BOOT、源码清单、管理配置与实际 sparse 容量，还只读取得 Android 的 `/sys/firmware/fdt`，检查 Linux RAM 子集与固定保留区，含义见[上游核对](upstream.md)。

缺 FDT 或比较失败就停下，不要跳过检查。

`stock-recovery/boot.img` 是设备当前槽的原厂 BOOT，执行器要求它 96 MiB 且摘要与设备一致。原厂 ABL 不实现 `fastboot fetch`，从 Android 侧读：

```sh
adb -s <serial> shell su -c 'sha256sum /dev/block/by-name/boot_a'
adb -s <serial> exec-out su -c 'dd if=/dev/block/by-name/boot_a bs=1048576' \
  > stock-recovery/boot.img
sha256sum stock-recovery/boot.img    # 与上一条相同
```

原厂 ROM 包里的 `images/boot.img` 也是 96 MiB，但只有设备还跑着那一版固件时才逐字节相同。用前比摘要，不一致以设备读出的为准。

在连接平板的 Linux 主机上执行。以下路径指已经解开的安装材料目录，`--serial` 是 `adb devices` 显示的 Android 序列号：

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

传输成功不等于读回或启动通过。下一步依次是临时 `boot_linux.img` 启动与设备读回、桌面与硬件验收、临时 Android 旁路。全部通过后才单独安装持久 BOOT。

中途中断时先读 session 的 result.json 和实际设备状态，不要自动重试或回滚 GPT。

### 只升级内核（已装系统）

装好之后内核跟着 `dnf upgrade` 走，不需要重跑刷写，也不需要 bundle。`kernel-piano` 提供 `kernel-core`，是 installonly 包：新内核与旧内核的模块树并存。

```sh
sudo dnf upgrade --refresh
```

升级事务收尾时 `kernel-piano` 的 `%posttrans` 会 `depmod`、用新内核重建 initramfs，再把新内核、新 initramfs 与 ESP 上原有的 `board.dtb` 重新打成 Android boot v2 容器，原子替换 `/boot/efi/EFI/Piano/stable/boot.img`。重启后固件读到的是新内核。

这条路径在真机上验证过：`7.2.9-1` 升到 `7.2.9-3` 之后重启，`uname -r` 是新内核，桌面正常起，`v4l2loopback` 从新模块树的 `updates/` 加载。

失败边界：任何一步失败，`boot.img` 保持原样，设备继续用上一个内核启动。审计记录写在 `/var/lib/piano/kernel-esp/<release>.json`。

已经装好但起不来的内核没有自动回滚，回滚靠 `boot.img.bak`：

```sh
sudo cp /boot/efi/EFI/Piano/stable/boot.img.bak /boot/efi/EFI/Piano/stable/boot.img
sudo cp /boot/efi/EFI/Piano/stable/Image.bak /boot/efi/EFI/Piano/stable/Image
sudo cp /boot/efi/EFI/Piano/stable/initramfs.bak /boot/efi/EFI/Piano/stable/initramfs
```

`dnf history undo` 只回滚 RPM，不动 ESP。完整语义见 [COPR 软件源](copr-repository.md)。

## 登录与诊断

桌面用户是 `piano`，默认密码 `piano123456`，与上游登录策略一致。桌面自动登录，首次使用后执行 `passwd` 改密。root 密码保持锁定。镜像里没有 `authorized_keys`。要密钥登录就在刷写时用 `flash-linux.sh --ssh-key` 注入，首启后由 `piano-first-boot-ssh-key.service` 导入。

正常 Fedora 用需要账户认证的 `serial-getty@ttyGS0.service`，initramfs 救援的串口 shell 另走一条。

NCM 用上游固定设备 MAC，NetworkManager 静态配置 `192.168.77.1/30`，不加默认路由。主机侧可用 `192.168.77.2/30`。这些入口已经配好，实际 USB 枚举和登录还要在设备上验证。

在现有 Fedora 上用 `stage-rootfs-payload.py --rootfs /` 增量安装时，脚本返回成功前会用本机 `setfiles -F` 恢复新模块、硬件准备程序、USB 诊断程序、systemd 单元和来源记录的 SELinux 标签。来源里的 `user_home_t` 不能用在系统模块上。

摘要一致不代表标签正确。离线 staging 的标签由 build-image 里的目标策略标记步骤处理。

ABL 只读对照把完整取证放在 RAM 的 `/run/piano-boot-evidence.txt`，进串口救援后读取；它不写 Linux 根分区。原生 BOOT 文件检查用 `verify-native-boot.py`，七项文件 guard 的报告与真实设备的槽位、当前来源、块设备写读回分开保存。未完成的 Android 模块不标为 ready。

---

## 五、 首次登录与桌面环境配置

刷完后平板自动重启，进入 120 Hz 的桌面。四档都自动登录到 `piano` 用户；GNOME 走 GDM，KDE 走 plasma-login-manager，niri 与 Hyprland 走 greetd 的 `tuigreet` 兜底。

* **默认用户**：`piano`
* **默认密码**：`piano123456`
* **改密**：打开终端执行 `passwd`。

### 默认状态
* **120 Hz 与触控**：Mesa 硬件加速默认开启；触控服务加载 5 阶自适应卡尔曼滤波与 24 像素死区防抖。
* **声学保护**：内核与 ALSA UCM2 把 FS19xx 四功放截断在 -15 dB；麦克风有 +40 dB 硬件增益。
* **电池与电源**：`/hypervisor` 节点已软隔离，桌面显示原生双电芯电量，支持合盖与深度挂起。休眠（hibernate）与混合睡眠默认关闭：`sleep.conf.d/piano.conf` 由 `scripts/finalize-fedora-rootfs.py` 写入，`logind.conf.d/piano.conf` 由 `piano-fedora-configs` 包提供。
* **触控提频**：`uperf-linux` 监听触控输入，触点按下后 1 ms 内拉高全大核频率。

---

## 六、 双系统往返

1. **从 Linux 切换回 Android**：点击应用抽屉里的**「返回 Android」**图标，
   或在终端执行 `piano-next-boot android --reboot`。helper 把持久请求写成
   Android 后正常重启，选择器在下次开机时进入 Android。已在真机验收。
2. **从 Android 切换回 Linux**：安装 Android 的「SunUEFI 引导助手」模块，在模块
   页面选择 Linux 并重启。没有模块时，开机前 3 秒按音量键，在 SunUEFI 菜单里
   选 Linux，选择会被保存。两条路径都不需要电脑，也不切活动槽。
3. **开机引导菜单**：每次开机亮屏后的**前 3 秒内**按音量上/下键呼出。不按则按
   请求页里的持久选择启动。

---

## 真机验收清单

刷完之后逐项核对，每项留一条命令输出或截图作为证据。任一项失败就按失败记录，
不要写成“未验证”。

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

浏览器放 H.264 视频另算一项，且三家结果不同：Google Chrome 与 Firefox 默认走
硬件解码；Fedora 的 Chromium 连 MP4 都打不开，因为它链接系统 `libavcodec`，
而 `ffmpeg-free` 去掉了软件 H.264 解码器。装 RPM Fusion 的
`libavcodec-freeworld` 后能播，但仍然软解。细节见
[已知问题](known-issues.md#触屏网络音频与相机)。

再完整重启一次，确认仍进所选桌面。双系统往返按下一节执行，往返后核对
`fastboot getvar current-slot`。

发布验收的标准比这里更严：必须从原始 GPT/UFS 重新分区，并使用同一条流水线新
产出的 bundle 与 root-install。门槛见[维护与验收](internals.md#pr-验收门槛)。

---

## 七、 常见故障排查

### Q1: 开机直接掉进 Fastboot 怎么解决？
高通 ABL 在开机时没收到明确的 `Successful = 1` 标记，会在连续重启后递减尝试次数，归零后掉进 Fastboot。

系统稳定启动后，确认 `qbootctl.service` 在运行，或手动执行 `sudo qbootctl -m` 把当前槽标记为 `Successful: 1`。设备始终从 A 槽启动；Linux 起不来时按第 6 节刷回原厂 `boot_a`。

### Q2: 误将 Android 数据格式化了？
Slot A 保留着完整的原厂 Android 分区（`super`、`userdata` 等）；合体 BOOT 只替换 `boot_a`，备份的原厂镜像可以随时刷回。

在 Fastboot 里执行 `fastboot erase userdata && fastboot reboot`，Android 会重建加密分区并走首次开机引导。若重启后停在 Recovery 并显示 `Couldn't initialize user 0`，在 Recovery 里选一次“清除数据”再继续。

### Q3: Type-C 有线网络 SSH 无法连接？
系统把 USB NCM 接口静态配置为 `192.168.77.1/30`。把电脑端对应网卡配到同一子网（例如 IP `192.168.77.2`，掩码 `255.255.255.252`），然后 `ssh piano@192.168.77.1`。
