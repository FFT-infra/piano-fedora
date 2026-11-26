# 升级与维护指南

> 本指南适用于已装好 Fedora 的平板维护。全新分区与首次刷机见[安装指南](installation.md)。

## 一、 更新已有系统

### 1. 日常在线升级（含内核与适配包）

装好系统后，硬件适配包、图形栈与内核均通过 `xinyang26/piano-fedora` COPR 随日常更新走，无需重刷镜像或下载 bundle：

```sh
sudo dnf upgrade --refresh
```

`kernel-piano` 的 `%posttrans` 会在事务收尾时执行 `depmod`、重建 initramfs，并按 ESP 当前布局更新启动项（BLS 写 `/linux/<release>/` 与 loader 条目；单 UKI 用 `systemd-ukify` 重打 PE 并保留 `sunuefi.efi.bak`）。板级 DTB 来自新装上的 `kernel-piano`，升级日志写入 `/var/lib/piano/kernel-esp/<release>.json`。写入 ESP 前若失败，现有启动项保持不动。

**升级失败回滚**：内核按普通包覆盖安装（旧模块树会被替换），因此回滚不能只切启动项，需降级 RPM 或还原 `.bak`（详见 [COPR 软件源](copr-repository.md#启用与日常操作)）：

```sh
sudo dnf downgrade kernel-piano
# 若为单 UKI 布局，同时还原备份内核：
sudo cp /boot/efi/EFI/Linux/sunuefi.efi.bak /boot/efi/EFI/Linux/sunuefi.efi
```

### 2. 完整覆盖 ESP 与 F2FS root

若需把现有安装重置到新版 Release 归档的根文件系统，`update-f2fs.py` 只重写 `sunuefi_esp` 与 `sunuefi_root`，不动 GPT、Android `userdata` 与活动槽持久 BOOT（注意：Linux root 内个人文件会被替换，操作前先备份）。

1. 解压新版 Release 归档到暂存目录（见[准备安装材料](installation.md#三-准备安装材料)）。
2. 从可正常进入且带 root ADB 的原 Android 槽位提取当前 96 MiB 原厂 BOOT 并比对摘要（固件安装包里的 `boot.img` 仅当系统同版时一致，优先从设备现读）：

   ```sh
   SLOT=$(adb -s <serial> shell getprop ro.boot.slot_suffix | tr -d '\r')
   adb -s <serial> shell su -c "sha256sum /dev/block/by-name/boot${SLOT}"
   adb -s <serial> exec-out su -c "dd if=/dev/block/by-name/boot${SLOT} bs=1048576" \
     > stock-recovery/boot.img
   sha256sum stock-recovery/boot.img
   ```

3. 在暂存目录执行只读计划并核对 `/sys/firmware/fdt` 内存边界与分区容量（FDT 缺失或校验不通过时必须中止）：

   ```sh
   python3 boot-tools/installer/scripts/update-f2fs.py plan \
     --sunuefi boot-tools/installer/sunuefi --serial <serial> \
     --bundle . --root-install root-install \
     --device-boot device-boot --stock-boot stock-recovery/boot.img \
     --plan update-plan.json
   ```

4. 确认计划无误后执行写入：

   ```sh
   python3 boot-tools/installer/scripts/update-f2fs.py apply \
     --sunuefi boot-tools/installer/sunuefi --serial <serial> \
     --bundle . --root-install root-install \
     --device-boot device-boot --stock-boot stock-recovery/boot.img \
     --plan update-plan.json --session update-session \
     --execute --accept-root-replacement
   ```

5. `apply` 结束后设备停在 Fastboot，依次完成临时 BOOT 启动核验与硬件检查，全部通过后再决定是否升级持久 BOOT。中途意外中断请先检查 `update-session/result.json`，切勿盲目回滚 GPT。

### 3. 单独升级 UEFI 固件与引导助手

UEFI 固件位于活动槽合体 BOOT 内，不在 ESP 里，也不受 `dnf upgrade` 管理。

- **更新引导助手与固件核心**：从新版 Release 归档取 `sunuefi-module.zip`（含 `patches/uefi-ufs` 与 `patches/uefi-dwc3-halt` 补丁，勿与上游未打补丁模块混用），在 Android 的 KernelSU/SukiSU 中安装并重启生效。
- **单独刷写 ESP 镜像**：仅在当前 root 里 `/usr/lib/modules/` 已有与新版归档一致的内核版本时执行（否则 initramfs 找不到模块会掉进救援 shell）：

  ```sh
  sudo dnf upgrade --refresh          # 先让 root 里的 kernel-piano 对齐目标版本
  fastboot -S 128M flash sunuefi_esp sunuefi_esp.img
  ```

---

## 二、 双系统往返

双系统切换全部通过写活动槽合体 BOOT 末尾的请求页实现，不切换底层 A/B 槽，也不修改 GPT。

1. **Linux 到 Android**：点击桌面应用菜单里的「返回 Android」，或运行 `piano-next-boot android --reboot`。
2. **Android 到 Linux**：在 Android 侧「SunUEFI 引导助手」模块中选切换 Linux 并重启；若未安装模块，可在开机亮屏前 3 秒内按音量键进入 SunUEFI 菜单选择 Linux。
3. **临时菜单**：每次开机前 3 秒按音量上/下键随时呼出启动项选择器，不按则按持久请求页默认引导。
