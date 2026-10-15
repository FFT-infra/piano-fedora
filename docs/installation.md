# 安装、已有分区更新与验收

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
