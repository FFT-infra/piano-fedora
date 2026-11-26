# Xiaomi Pad 8 Pro (piano) 升级与维护指南

> 本文档适用于已装好系统后的维护。全新安装与首次分区请参见 [安装与分区](installation.md)；升级所需的材料解压与暂存目录准备参见 [准备安装材料](installation.md#三-准备安装材料)。

## 一、 更新已有安装

### 1. 更新 ESP 与 root

`update-f2fs.py` 复用锁定上游的只读设备/GPT 解析、工厂 Fastboot 身份检查和本仓库的材料校验。`update-f2fs.py` 只传输 ESP 与 F2FS root 文件系统，不写 GPT、userdata 与持久 BOOT。完整 root 文件系统会替换 Linux 文件，个人文件先备份。

先确认原 Android 能从当前活动槽完整启动并提供 root ADB。历史的 init_boot/DTBO 试验不算恢复完成的证据。计划阶段会核对主备 GPT/CRC、分区类型、当前槽、原 BOOT、源码清单、管理配置与实际 sparse 容量，并以只读方式取得 Android 的 `/sys/firmware/fdt`，记录设备当前的 RAM 区间与固定保留区。安装侧不拿交付件的 DTB 去比这份地图：固件在每次启动时用 `EFI_DT_FIXUP_PROTOCOL` 把 `/memory` 换成设备当次布局，并按容量与固定保留区校验。记录只用于计划与审计。含义见[上游跟踪](upstream.md)。

缺 FDT 或解析失败就停下，不要跳过检查。

`stock-recovery/boot.img` 是设备当前槽的原厂 BOOT。执行器要求它 96 MiB 且摘要与设备一致。原厂 ABL 不实现 `fastboot fetch`，从 Android 侧读；槽位从 `ro.boot.slot_suffix` 取，出厂设备是 `boot_a`，OTA 到 HyperOS 4 的设备是 `boot_b`：

```sh
SLOT=$(adb -s <serial> shell getprop ro.boot.slot_suffix | tr -d '\r')
adb -s <serial> shell su -c "sha256sum /dev/block/by-name/boot${SLOT}"
adb -s <serial> exec-out su -c "dd if=/dev/block/by-name/boot${SLOT} bs=1048576" \
  > stock-recovery/boot.img
sha256sum stock-recovery/boot.img    # 与上一条相同
```

原厂 ROM 包里的 `images/boot.img` 也是 96 MiB，但只有设备还跑着那一版固件时才逐字节相同。用前比摘要，不一致以设备读出的为准。

在连接平板的 Linux 主机上执行。以下路径指暂存目录（解压步骤见[准备安装材料](installation.md#三-准备安装材料)），`--serial` 是 `adb devices` 显示的 Android 序列号：

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

装好之后，内核跟着 `dnf upgrade` 走，不需要重跑刷写，也不需要 bundle。`kernel-piano` 按普通包升级，新包替换旧包，旧内核的模块树随之删除。

```sh
sudo dnf upgrade --refresh
```

升级事务收尾时 `kernel-piano` 的 `%posttrans` 会 `depmod`、用新内核重建 initramfs，再按 ESP 上的布局写启动项。BLS 布局写 `/linux/<release>/` 与 `/loader/entries/linux-<release>.conf`，内核与板级 DTB 都来自刚装上的包。UKI 布局用 `systemd-ukify` 把新内核、新 initramfs、包里的 DTB、ESP 里现有的 `.cmdline`、新的 `.osrel` 与 `.uname` 重打成 PE 文件：有 systemd-boot 时写版本化文件名，没有时原地替换 `/boot/efi/EFI/Linux/sunuefi.efi`，旧文件留成 `.bak`。重启后固件加载新内核。

系统支持从早期 `boot.img` 引导链路及当前 UKI 引导链路平滑升级至最新内核与适配包。

写 ESP 之前的任何一步失败，ESP 不动。审计记录写在 `/var/lib/piano/kernel-esp/<release>.json`。

已经装好但起不来的内核没有自动回滚。BLS 布局的旧条目还在，但旧模块树已被新包替换，要先 `dnf downgrade kernel-piano` 再选旧条目；单 UKI 布局回滚靠 `sunuefi.efi.bak`：

```sh
sudo cp /boot/efi/EFI/Linux/sunuefi.efi.bak /boot/efi/EFI/Linux/sunuefi.efi
```

`dnf history undo` 只回滚 RPM，不动 ESP。完整语义见 [COPR 软件源](copr-repository.md)。

### 3. 只升级 UEFI 固件与引导助手

`dnf upgrade` 管不到 UEFI 固件。固件在活动槽的合体 BOOT 里，由 Android 侧的引导助手模块更换。ESP 里是 systemd-boot、内核、initramfs 与板级 DTB，没有固件。

归档里的 `sunuefi_esp.img` 是 ESP 的整分区镜像。单独刷它会把这四样换成归档里的版本，不碰 root，也不换固件：

```sh
fastboot -S 128M flash sunuefi_esp sunuefi_esp.img
```

第 1 节的 `update-f2fs.py` 会同时换 ESP 与 root，完整替换 Linux 文件。

ESP 里的内核与 root 里的 `/usr/lib/modules/` 必须同一版。单独刷 ESP 之前，先用 `sudo dnf upgrade --refresh` 把 `kernel-piano` 升到与归档一致。root 里没有对应的模块目录时，initramfs 停在救援环境，不进系统。

引导助手模块装在 Android 的 KernelSU/SukiSU 里，刷 ESP 装不到它。归档里的 `sunuefi-module.zip`（v45.10 起才有）与 `PianoUEFI-product.img` 由同一次构建产出，`module-manifest.json` 的 `same_product.product_sha256` 就是那份产品的摘要，`verify-bundle.py` 在发布前核对。在模块管理器里选中这个 ZIP 安装，重启即生效。

本仓库的固件带 `patches/uefi-ufs` 与 `patches/uefi-dwc3-halt` 两条补丁，上游模块不含它们。装上游模块换不到这两处修复。

---

## 二、 双系统往返

1. 从 Linux 切换回 Android：点击应用抽屉里的“返回 Android”图标，或在终端执行 `piano-next-boot android --reboot`。helper 把持久请求写成 Android 后正常重启，3 秒选择器在下次开机时进入 Android。已在真机验收。
2. 从 Android 切换回 Linux：安装 Android 的「[SunUEFI](https://github.com/slwyts/Project-SunUEFI) 引导助手」模块，在模块页面选择 Linux 并重启。没有引导助手时，开机前 3 秒按音量键，在 SunUEFI 菜单里选 Linux，选择会被保存。两条路径都不需要电脑，也不切活动槽。
3. 3 秒选择器：每次开机亮屏后的前 3 秒内按音量上/下键呼出。不按则按请求页里的持久选择启动。
