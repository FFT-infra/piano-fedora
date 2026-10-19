# 2026-10-18 Linux 停在 initramfs 救援 shell：两处装配缺陷

## 结论

设备能引导 Linux，但 `init` 在 `chroot /sysroot
/usr/lib/piano/piano-disk-hardware-prepare` 这一步失败，走 `fail()` 停在
`~ #`。原因不是引导链、不是硬件，是纯净装配链产出的镜像本身缺东西：

1. 镜像里没有 `/etc/piano/root-policy.json`，也没有 udev 的 Android 分区
   保护规则；
2. `piano-ram-hardware-prepare` 里硬编码的 DMA 校验器 SHA256 与镜像里
   实际文件对不上。

两条都由本 PR 引入。第一条是纯净装配链漏调 `apply-rootfs-policy.py`；
第二条是 RPM 打包改写 shebang 导致的。

## 怎么确认的

### 引导链是好的

切到 Slot B 后设备枚举为 `1209:8751 Piano Linux Debug ACM NCM`，
`192.168.77.1` ping 通，`/dev/ttyACM0` 出现。SunUEFI 从 ESP 读
`EFI/Piano/stable/boot.img` 成功，内核与 initramfs 都执行了。失败点在
initramfs 里的 `init` 脚本。

ESP 里的 boot.img 头（v2，偏移 1648 的 `dtb_size`）：

```
kernel_size          43117056
ramdisk_size          2538923
dtb_size              1209618   >= 40，满足 PianoRawLinuxBoot 的 Parse 要求
header_version              2
```

### 第一条：root-policy.json 缺失

挂载 `pianoroot-fedora44-gnome-f2fs.img`（10-17 14:48 那份）直接看：

```
/etc/fstab                                        存在
/etc/udev/rules.d/01-piano-protect-android.rules  不存在
/etc/piano/                                       整个目录不存在
```

`piano-ram-hardware-prepare` 的 `require_ram()` 第一件事就是读
`/etc/piano/root-policy.json`：

```python
policy = json.loads(Path('/etc/piano/root-policy.json').read_text())
```

而 `main()` 里的顺序是 `require_ram()` → `checker()`，所以缺文件时连哈希
校验都还没走到。表现就是 `FileNotFoundError`。

生成这个文件的脚本 `scripts/apply-rootfs-policy.py` 只在旧流水线
`.github/workflows/build-image.yml:154` 被调用，新的纯净装配
`scripts/build-clean-rootfs.py` 里一次都没有出现。

### 第二条：DMA 校验器哈希不匹配

`74b061b`（10-16 23:31）把 `piano-ram-hardware-prepare` 和两个校验器从
"直接拷进 rootfs" 改成 "打进 RPM"。Fedora 的 `brp-mangle-shebangs` 在
打包时把 `#!/usr/bin/env python3` 改写成 `#!/usr/bin/python3`，文件差
4 字节，而 `piano-ram-hardware-prepare` 里硬编码的是源码版本的哈希。

镜像里实测：

```
piano_dma_routes.py     7385e834cce8d398...
piano_dma_contexts.py   b4fe92465e4b428a...
prepare 里的常量        1f2c26329c00b5b7... / 9731f4027c0656d5...
```

两套哈希对不上，`load_reviewed()` 必然抛 `reviewed DMA verifier hash
mismatch`。

`1dd6e1a`（10-18 07:31）把源码 shebang 改成 `/usr/bin/python3` 并把常量
更新为打包后的值。但那次提交之后没有重建 RPM，`out/rpms/` 里仍是
10-17 07:48 的 `piano-fedora-configs-0.1.0-4`，镜像更早。所以修复没有
进入任何交付物。

## 一处需要更正的旧判断

早先的记录说 `mount --move` 没把 `/proc` 移进 `/sysroot`。这个说法不对。
看 `init` 的 `fail()`：它会先把 `/sysroot/{dev,proc,sys,run}` 逐个
`mount --move` 搬回原路径，再起救援 shell。救援 shell 里看到
`/sysroot/proc` 是空的，是失败之后的后果，不是失败的原因。

真正的第一处失败是上面那条 `root-policy.json`。

## 修复

### 代码

`build-clean-rootfs.py`：

* 在清运行时目录之前调用 `apply-rootfs-policy.py`，让 fstab、
  `root-policy.json` 和 udev 保护规则进入镜像；
* 删掉装配脚本自己写的那份 fstab。它写 `compress_algorithm=zstd`，策略
  脚本写 `data_flush,fsync_mode=strict`，两处会漂移，现在只留策略脚本
  一个来源；
* 审计项增加 `etc/piano/root-policy.json`、
  `etc/udev/rules.d/01-piano-protect-android.rules` 和 `etc/fstab`，
  缺任意一个就不发布镜像；
* 空 installroot 先建好 `var/tmp`、`var/log`、`run`、`tmp`。不建的话
  rpm scriptlet 在 unpack 阶段就报 `error creating temporary file
  /var/tmp/rpm-tmp.XXXX`，整个事务失败。

`tests/check_clean_rootfs.py` 增加断言：装配脚本必须调用策略脚本，必须
要求 `root-policy.json`，且不得再写自己的 fstab。

### 交付物

重建 `piano-fedora-configs` RPM，重新跑纯净装配与成像。新镜像要满足：

```
piano_dma_routes.py 的 sha256  ==  prepare 里的 CHECKER_SHA256
piano_dma_contexts.py 的 sha256 ==  prepare 里的 CONTEXT_CHECKER_SHA256
/etc/piano/root-policy.json 存在
/etc/udev/rules.d/01-piano-protect-android.rules 存在
/usr/lib/modules 恰好一个 release，与 ESP 的 kernel-release 一致
```

## 与 PR 的关系

这两条都是 PR #2 自己产出的镜像里的缺陷，不是设备问题。GPT 槽位 GUID
错位是另一件事，已在
[槽位 GUID 错位](slot-guid-corruption-20261018.md) 里修好。

修复后仍需在真机上重新做一次盲测：刷入新镜像、确认 `init` 走完
`chroot` 交接、systemd 起来、SSH 可达。

---

## 2026-10-19 补充：卡点是 Fedora systemd，不是 initramfs

前两节把失败定位在 initramfs 阶段，这个判断错了。

实机屏幕显示最后停在：

```
[  OK  ] Found device dev-disk-by\x2dlabel-SUNUEFI_ESP.device
```

这一行是 Fedora systemd 的 udev 单元，说明内核早已完成
`switch_root`，跑的是 rootfs 里的 systemd（`systemd-zram-setup`、
`plymouth-start`、`cryptsetup.target` 都是 Fedora 单元，initramfs 里
只有 busybox）。失败点在 systemd 启动阶段的文件系统挂载。

此前针对 initramfs 的改动（替换引导头、清 misc 的 BCB、加载黑名单）
都不在这个阶段，因此看不到效果。

当时 rootfs 的 fstab 由 `apply-rootfs-policy.py` 写入：

```
LABEL=PIANOROOT / f2fs defaults,noatime,data_flush,fsync_mode=strict 0 0
LABEL=SUNUEFI_ESP /boot/efi vfat umask=0077,nofail 0 2
```

其中 `data_flush`、`fsync_mode=strict` 是本项目自己加的挂载参数，
未经真机验证。systemd 卡在 ESP device 被发现之后，与
`boot-efi.mount`／`local-fs.target` 的行为一致。

判断依赖一张屏幕照片。没有它的时候只能从 rootfs 内容反推，代价是
连续多轮盲改——这也说明这类启动问题需要先把启动日志拿到手。

## 2026-10-19 再补充：initramfs 确实在执行，SELinux 是主障碍

用探针确定了链路：在 `init` 的 `export PATH` 之后立刻 `poweroff -f`，
设备 30 秒内完全关机且 USB 无任何枚举。说明 boot.img ramdisk 里的
init 一直在正常执行，此前"改了没效果"不是没跑到。

同一探针也证明：`switch_root` 前写 `/etc/selinux/config` 为 disabled
**不能**阻止 systemd 的 `Permission denied`。内核
`CONFIG_SECURITY_SELINUX_BOOTPARAM is not set`，启动参数也走不通。
enforcing 状态来自 selinuxfs 挂载时加载的内置 policy。

DNF 装配的 rootfs 缺的是旧流水线 `build-image.yml:192` 的 `setfiles` 一步。
对照 10-15 那份能启动的 rootfs：`/usr/bin/ls` 有 `bin_t`、393/400 有标签；
新装配的是 0。补上 78815 个标签后，systemd 越过了
`Failed to mount API filesystems`，但随即卡在
`Failed at step EXEC spawning /usr/sbin/modprobe: Permission denied`，
`systemd-udevd` 同样失败，导致 `dev-zram0.device` 等 45 秒超时。

USB gadget 也一直没起来（`udc_selection_ambiguous_or_absent`），
所以拿不到 ACM 串口日志；作为替代，让 initramfs 把诊断写进 misc 分区
再经 EDL 读回。这个办法本身也没成功：misc 里只有 `bootonce-bootloader`，
说明写盘时 `/dev/sda12` 尚不存在。
