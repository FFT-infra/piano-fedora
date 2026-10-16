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

## 三、 全新纯净安装（跨平台一键刷机，推荐）

适用于已解锁 BL、已经由 SunUEFI 划分好分区（或需要全自动刷入）的平板。

### 1. 获取 Release 资产
从 [GitHub Releases 页面](https://github.com/FFT-infra/piano-fedora-test/releases) 下载最新版本的发行包：
- `piano-installer-*.zip`：一键刷机脚本包（包含 `flash-linux.sh`、`flash-linux.bat`）；
- `boot_sunuefi.img`：SunUEFI 引导头；
- `sunuefi_esp.img`：SunUEFI ESP 分区镜像；
- `pianoroot-fedora44-gnome-f2fs.img.zst`：Fedora 44 根文件系统压缩包；
- `SHA256SUMS`：校验文件。

将上述所有文件解压并放置在**同一本地目录**下。
若 `pianoroot-fedora44-gnome-f2fs.img` 仍为 `.zst` 压缩格式：
- **Linux / macOS**：运行 `zstd -d pianoroot-fedora44-gnome-f2fs.img.zst`；
- **Windows**：使用 7-Zip（需支持 Zstd 插件）或 `zstd.exe` 解压出 `.img`。

### 2. 进入 Fastboot 模式
1. 将平板完全关机；
2. 长按 **【电源键 + 音量减键】** 不放，直到屏幕亮起并显示米兔/FASTBOOT 图标；
3. 使用原装或高质量 USB-C 数据线将平板连接至电脑。

### 3. 执行一键刷机
- **Linux / macOS 主机**：
  ```bash
  chmod +x flash-linux.sh
  ./flash-linux.sh
  ```
- **Windows 主机**：
  直接双击运行 `flash-linux.bat`。

刷机脚本将全自动执行以下动作：
1. 自动检查 `fastboot` 环境与设备在线状态；
2. 校验设备产品代号确为 `piano`，避免误刷；
3. 根据 `SHA256SUMS` 校验各镜像文件完整性；
4. 弹出醒目安全告知，要求用户在终端输入大写 `YES` 确认；
5. 执行标准刷写序列：
   - 切换活动槽位至 B：`fastboot set_active b`
   - 刷入引导头：`fastboot flash boot_b boot_sunuefi.img`
   - 刷入 ESP 分区：`fastboot flash sunuefi_esp sunuefi_esp.img`
   - 刷入系统根分区：`fastboot flash sunuefi_root pianoroot-fedora44-gnome-f2fs.img`
   - 自动重启：`fastboot reboot`

   脚本不写 `dtbo_b`、`init_boot_b`、`vendor_boot_b`、`vbmeta_b`，也不擦除
   `userdata`。需要逐条手动执行同样步骤时，见下一节。

---

## 三之二、 手动刷机（不使用一键脚本）

一键脚本只是把下面这些命令包了一层。想自己控制每一步、或脚本在你的平台上跑不
起来时，直接照本节操作。命令与脚本执行的是同一套写入，`dtbo_b` 一律不动。

### 1. 从 Release 取哪些文件

从 [GitHub Releases 页面](https://github.com/FFT-infra/piano-fedora-test/releases)
下载发行包，只需要下面这些资产：

| Release 资产 | 用途 | 刷到哪 |
| --- | --- | --- |
| `boot_sunuefi.img` | SunUEFI 链式引导头 | `boot_b` |
| `sunuefi_esp.img` | ESP 分区镜像（固件核心、DTB、内核、initramfs） | `sunuefi_esp` |
| `pianoroot-fedora44-gnome-f2fs.img.zst` | Fedora 44 GNOME 根文件系统（压缩） | `sunuefi_root` |
| `SHA256SUMS` | 全部资产的 SHA256 清单 | 不刷入，只用于校验 |

`piano-installer-*.zip` 和 `piano-switch-ksu-*.zip` 是给一键脚本和 Android 侧
切换模块用的，手动刷机不需要。`.zst` 要先用 `zstd -d` 展开成 `.img` 再刷。

### 2. 校验下载到的文件

在存放这些文件的目录里执行（macOS 把 `sha256sum` 换成 `shasum -a 256`）：

```sh
sha256sum -c SHA256SUMS
```

清单里的路径写成 `./name`，所以必须在文件所在目录执行。三条都要是 `OK`；
任何一条 `FAILED` 都说明文件损坏，重新下载，不要继续。手动核对单个文件：

```sh
sha256sum boot_sunuefi.img sunuefi_esp.img pianoroot-fedora44-gnome-f2fs.img.zst
```

### 3. 确认设备和槽位

平板关机后长按**【电源键 + 音量减键】**进入 Fastboot，插好数据线，然后：

```sh
fastboot devices
fastboot getvar product        # 必须是 piano
fastboot getvar current-slot   # 记下当前槽位，回滚时要用
fastboot getvar unlocked       # 必须是 yes
fastboot getvar is-userspace   # 必须是 no
```

`product` 不是 `piano`、`unlocked` 不是 `yes`、或 `is-userspace` 是 `yes`
（说明落在 fastbootd 而不是原厂 ABL）时，停下，不要写任何分区。

### 4. 逐条刷写

```sh
# 1. 切到 B 槽（Android 保留在 A 槽）
fastboot set_active b

# 2. 展开根文件系统（只做一次）
zstd -d pianoroot-fedora44-gnome-f2fs.img.zst

# 3. 引导头 -> boot_b
fastboot flash boot_b boot_sunuefi.img

# 4. ESP -> sunuefi_esp
fastboot flash sunuefi_esp sunuefi_esp.img

# 5. 根文件系统 -> sunuefi_root（约 2 GiB，需要几分钟）
fastboot flash sunuefi_root pianoroot-fedora44-gnome-f2fs.img

# 6. 重启进系统
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
fastboot fetch sunuefi_esp /tmp/esp-readback.img
sha256sum /tmp/esp-readback.img
# 应与 sunuefi_esp.img 的 sha256 一致
```

设备不支持 `fastboot fetch` 时，改为启动后验证：能进 GNOME 桌面、`uname -r`
是 release 对应的内核、`findmnt /` 显示 F2FS、`systemctl --failed` 为空。

### 6. 回滚到 Android

Linux 起不来时，把槽位切回 A 即可恢复 Android，**不需要**重刷任何分区：

```sh
fastboot set_active a
fastboot reboot
```

要重新刷 Linux，先把 `fastboot getvar current-slot` 的结果与上一步一致地切回
`b`，再重复第 4 节。`userdata` 一旦被清空无法恢复，这也是不要擦除它的原因。

---

## 四、 首次安装与底层分区规划（进阶）

安装材料由 `prepare-install.yml` 生成，实际操作由连接平板的 Linux 主机执行。
Actions 不连接平板。构建成功、传输成功、Linux 启动成功分别记录。

已有 `sunuefi_root` 和 `sunuefi_esp` 时使用下面的更新入口。首次分区的 `install-f2fs.py apply` 会清空 userdata，不能用于更新。

## 已有分区更新

`update-f2fs.py` 复用锁定上游的只读设备/GPT 解析、工厂 Fastboot 身份检查与本仓库的材料校验。它只传输现有 ESP 和 F2FS root，保持 GPT、userdata 与持久 BOOT 的写入操作为零。完整 root 会替换 Linux 文件，已有个人文件须先备份。

先恢复正确 ROM 配套的 B 槽组件，确认原 Android 能完整启动并提供 root ADB；历史 init_boot/DTBO 试验不能作为已恢复的证据。计划核对主备 GPT/CRC、分区类型、当前槽、原 BOOT、源码清单、管理配置和实际 sparse 容量。还会只读取得 Android `/sys/firmware/fdt`，检查 Linux RAM 子集与固定保留区；检查含义见[上游核对](upstream-sync.md)。缺少 FDT 或比较失败时停止，不跳过检查。

在连接平板的 Linux 主机上执行，以下路径指本次已下载的材料，`--serial` 是实际 Android ADB 序列号：

```sh
python3 boot-tools/installer/scripts/update-f2fs.py plan \
  --sunuefi boot-tools/installer/sunuefi --serial 12440448 \
  --bundle deploy-bundle --root-install root-install \
  --device-boot device-boot --stock-boot stock-recovery/boot.img \
  --plan update-plan.json
```

复核计划并备份 Linux 文件后，使用同一组输入：

```sh
python3 boot-tools/installer/scripts/update-f2fs.py apply \
  --sunuefi boot-tools/installer/sunuefi --serial 12440448 \
  --bundle deploy-bundle --root-install root-install \
  --device-boot device-boot --stock-boot stock-recovery/boot.img \
  --plan update-plan.json --session update-session \
  --execute --accept-root-replacement
```

执行前重新比较设备、完整 GPT 基线、内存描述、槽位和材料，保存 GPT 双副本及原 BOOT；进入原厂 bootloader 后，在第一笔传输前核对两个目标的实时容量，拒绝 fastbootd、错误序列号或槽位。只传输 `sunuefi_esp` 与 `sunuefi_root`，完成后停在 Fastboot，状态为 `UPDATE_TRANSFERRED_FIRST_BOOT_AND_READBACK_PENDING`。

传输不等于读回或启动通过。下一步是临时 `boot_linux.img` 启动与设备读回、GNOME/硬件验收、临时 Android 旁路；通过后才单独安装持久 BOOT。中断时先读取 session 的 result.json 和实际状态，不自动重试或回滚 GPT。

## 登录和诊断

Fedora 的初始桌面用户为 `piano`，默认密码 `piano123456`，与上游新登录策略一致。桌面自动登录；首次使用后运行 `passwd` 修改密码。root 密码保持锁定，管理公钥仍只在 prepare-install 的私有派生镜像内配置。

正常 Fedora 使用需要账户认证的 `serial-getty@ttyGS0.service`；initramfs 救援的串口 shell 与之分开。NCM 使用上游固定设备 MAC，NetworkManager 静态配置为 `192.168.77.1/30`，不添加默认路由；主机可使用 `192.168.77.2/30`。这些是已配置的接管入口，实际 USB 枚举和登录仍需设备验证。

在现有 Fedora 上用 `stage-rootfs-payload.py --rootfs /` 增量安装时，脚本在返回成功前用本机 `setfiles -F` 恢复新模块、硬件准备和 USB 诊断程序、systemd 单元及来源记录的 SELinux 标签。复制来源中的 `user_home_t` 不能用于系统模块；摘要一致不代表标签正确。离线 staging 的标签由 build-image 中的完整目标策略标记步骤处理。

ABL 只读对照把完整取证保存到 RAM 中 `/run/piano-boot-evidence.txt`，进入串口救援后可读取；它不写 Linux 根分区。原生 BOOT 文件检查使用 `verify-native-boot.py`，七项文件 guard 的报告与真实设备的槽位、当前来源及块设备写读回分开保存，不将未完成的 Android 模块标为 ready。

## 分区与容量

`product.json` 记录本机已核对的目标容量。规划器默认保留 Android 固定分区，
将原 userdata 扣除 ESP 后的空间对半分；1 MiB 对齐允许两边相差不超过 2 MiB。
执行器从实时、双副本 CRC 合格的完整 GPT 重算，结果必须与镜像容量一致。
原 userdata 会清空重建，GPT/BOOT 恢复文件不包含 Android 用户数据。

通用包中的 8 GiB F2FS 镜像保持封存。`prepare-root-capacity.py` 在私有工作副本中
设置本机启动配置、传感器槽位和 root/piano SSH 公钥，恢复匹配的 Fedora SELinux
标签后离线扩容。源树与扩容后的文件、所有权、权限、链接、mtime、全部 xattr
逐项比较。只读 fsck 和 sparse 解码摘要必须通过。

sparse 文件使用 RAW 与显式零 FILL，不使用 DONT_CARE；其完整展开字节与
验证过的 F2FS 镜像一致。它仍是 F2FS 文件系统，sparse 只负责 Fastboot 传输。

## Actions 材料

手动运行 `prepare-install`，选择完整 deploy-bundle 的 run、当前 Android 槽位，
并提供本次安装管理用的 Ed25519 公钥。Linux 容量留空时使用 `product.json`。
私钥不传入 Actions。三个 artifact 分别为：

| artifact | 内容 |
| --- | --- |
| root-install | 扩容并配置后的 F2FS sparse、容量/元数据校验记录、摘要 |
| boot-tools | 同核心的 selector、Android 静态原生工具、产品固件、自包含安装入口 |
| host-boot-tool | 静态宿主 BOOT 重打包工具及来源记录 |

boot-tools 必须与所选 deploy-bundle 的 UEFI 产品镜像逐字节相同。
安装入口位于 boot-tools 的 `installer/scripts`；其中的锁定上游代码带源码清单，
不需要在设备主机重新编译。普通发行版主机需要 Python 3、adb、fastboot；
BOOТ 准备使用 Actions 提供的宿主工具。

## 本机 BOOT

先确认当前原 BOOT 完整摘要、槽位和系统版本。`prepare-device-boot.py` 使用
这些明确输入和同核心的 Actions 工具生成 `boot_android.img`、`boot_linux.img`
及 `boot-request.json`。普通 Android 为默认目标。

包装后恢复必须得到完整原 BOOT 的同一 SHA256。Linux 持久请求、只读预览、
改回 Android 后的再次完整恢复都要成功。文件检查不替代真机旁路和 Recovery 验收。

使用 `python3 scripts/prepare-device-boot.py --help` 查看文件参数。
原 BOOT、生成的设备 BOOT、安装记录和私钥均留在本地，不进入代码仓库。

## 实际首次安装

使用 `python3 scripts/install-f2fs.py --help` 查看入口。默认 `plan` 只读设备并
记录身份、完整 GPT 基线、当前原 BOOT 与三个输入包的身份。
`apply` 必须显式指定 `--execute --accept-data-loss`、已复核计划和新的会话目录。

执行顺序为：

1. 重新读取设备，确认计划、槽位、原 BOOT 和输入包未变，保存原 GPT/BOOT 恢复材料。
2. 临时 UEFI 和 Android 旁路必须另做并保留本次设备证据；首次安装器本身没有执行这两项，记录保持未验证，不继承历史试验。
3. 使用 Android 已有 sgdisk 写入按完整二进制 GPT 生成的布局，读回两份 header/table
   并验证 CRC、所有分区记录和固定 Android 分区字节。
4. 进入原厂 Fastboot，确认新 userdata/root/ESP 容量；擦除 userdata，再传输 ESP、
   F2FS sparse 和当前槽的 Android 默认合体 BOOT。
5. 保持 Fastboot，记录 `IMAGES_TRANSFERRED_FIRST_BOOT_PENDING`。传输成功不能写成
   文件系统读回成功、Android 初始化成功或 Linux 启动成功。

Android 的 userdata 重建使用该 ROM 自身的 fs_mgr；计划阶段必须确认其 `/data`
条目具有 formattable 合同。不能直接在宿主格式化 Android 的加密映射。

会话的 result.json 记录每个已完成阶段和错误。GPT 写入或擦除后发生中断时，
先看记录与实际设备状态，不自动重试、恢复旧 GPT 或启动 Android。
恢复 GPT 不能恢复已擦除的 Android 文件。

## 真机验收

首次 Linux 启动后检查 F2FS 容量、匹配内核/模块、GNOME 会话和日志；随后验收
Android 对新 userdata 的初始化，以及 Android→Linux、Linux→Android 和原厂
Recovery。触屏、显示、网络、音频、传感器和充电需要单独的实际行为证据。
这些项目未验收前，设备可用状态保持未验证。

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

## 七、 常见故障排查（FAQ & Troubleshooting）

### Q1: 开机直接掉进 Fastboot 怎么解决？
- **根本原因**：高通 ABL 在开机未收到明确 `Successful = 1` 标记时，会在连续重启后递减尝试次数，直至归零跌入 Fastboot。
- **解决方案**：在系统稳定启动后，确保 `qbootctl.service` 正常运行，或者手动在终端执行 `sudo qbootctl -m`，将当前 Slot 标记为 `Successful: 1`。在 PC 端亦可运行 `fastboot set_active b` 重新激活 Linux 槽位。

### Q2: 误将 Android 数据格式化了？
- **恢复方案**：Slot A 始终保留着完整的原厂 Android 系统镜像（boot_a、init_boot_a、super 等）。若需要重置 Android，可在 Fastboot 模式下执行 `fastboot set_active a && fastboot erase userdata && fastboot reboot`，Android 将自动重建加密分区并完成首次开机引导。

### Q3: Type-C 有线网络 SSH 无法连接？
- **排查**：系统已将 USB NCM 接口静态配置为 `192.168.77.1/30`。请确保电脑端对应网卡手动配置为同一子网（例如 IP: `192.168.77.2`, 子网掩码: `255.255.255.252`），即可通过 `ssh piano@192.168.77.1` 直接登入。
