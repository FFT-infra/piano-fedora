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

装好之后，内核跟着 `dnf upgrade` 走，不需要重跑刷写，也不需要 bundle。`kernel-piano` 按普通包升级，新包替换旧包；回滚要先 `dnf downgrade kernel-piano`，再恢复 UKI 的 `.bak`。

```sh
sudo dnf upgrade --refresh
```

升级事务收尾时 `kernel-piano` 的 `%posttrans` 会 `depmod`、用新内核重建 initramfs，再用 `systemd-ukify` 把新内核、新 initramfs、ESP 里现有 UKI 的 `.dtb` 与 `.cmdline`、新的 `.osrel` 与 `.uname` 组装成新的 `/boot/efi/EFI/Linux/sunuefi.efi`。重启后固件读到的是新内核。这条链路已在设备上验过：事务重建 initramfs，生成 `.bak` 并替换 UKI，重启正常进入桌面。

旧 `boot.img` 链上的 `7.2.9-1` → `7.2.9-3` 升级也验过一次。两种链的升级后重启、`uname -r` 与桌面加载都有真机记录。

任何一步失败时，旧 UKI 保持原样，设备继续用上一个内核启动。审计记录写在 `/var/lib/piano/kernel-esp/<release>.json`。

已经装好但起不来的内核没有自动回滚。回滚靠 `sunuefi.efi.bak`：

```sh
sudo cp /boot/efi/EFI/Linux/sunuefi.efi.bak /boot/efi/EFI/Linux/sunuefi.efi
```

`dnf history undo` 只回滚 RPM，不动 ESP。完整语义见 [COPR 软件源](copr-repository.md)。

---

## 二、 双系统往返

1. 从 Linux 切换回 Android：点击应用抽屉里的“返回 Android”图标，或在终端执行 `piano-next-boot android --reboot`。helper 把持久请求写成 Android 后正常重启，3 秒选择器在下次开机时进入 Android。已在真机验收。
2. 从 Android 切换回 Linux：安装 Android 的「[SunUEFI](https://github.com/slwyts/Project-SunUEFI) 引导助手」模块，在模块页面选择 Linux 并重启。没有引导助手时，开机前 3 秒按音量键，在 SunUEFI 菜单里选 Linux，选择会被保存。两条路径都不需要电脑，也不切活动槽。
3. 3 秒选择器：每次开机亮屏后的前 3 秒内按音量上/下键呼出。不按则按请求页里的持久选择启动。
