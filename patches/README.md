# 补丁

上游按 commit 锁定，改动走这里的补丁，不 fork。

## gnome-settings-daemon

`gnome-settings-daemon/51.1/` 是 extras 构建用的本地补丁，不参与
`apply-patches.py`。自动亮度补丁与键盘背光补丁都在
`gsd_power_manager_stop()` 里插入清理代码，50.1 的原始键盘背光补丁叠在
51.1 的 ambient 补丁上时有一个 hunk 对不上。这里保存以 51.1 + ambient
补丁为基线重排后的版本，其余改动与 SunUEFI 的 50.1 补丁一致。

## kernel-f2fs

让内核内建 F2FS 支持，这是 F2FS 根分区能挂载的前提。

上游 `build_piano_full_kernel.py` 只接受脚本里登记过的配置覆盖组，其余一律拒绝。所以不能往 `linux/configs/piano-full.config` 里追加 `CONFIG_F2FS_FS=y`。根类型由 `config/release.json` 的 cmdline 指定，`CONFIG_CMDLINE` 是空的，改它不会生效。

`kernel-f2fs/` 里的补丁按上游自己的扩展点做三件事：

1. 新增一份可审阅的 F2FS 配置片段 `linux/configs/piano-f2fs.config`。
2. 在 `ALLOWED_OVERRIDES` 里登记这组选项，与 flash/early-CPUCP 同构。
3. 在 `build_piano_full_kernel.py` 里加载它，并让 `REQUIRED` 校验覆盖。

补丁针对 `sources.lock.json` 里锁定的 SunUEFI commit。上游更新后补丁可能
不再适用，`apply-patches.py` 会在应用前核对目标文件摘要，不匹配就报错退出。

## 应用方式

```sh
scripts/apply-patches.py --sunuefi DIR
```

工具按 `series.json` 的顺序应用，逐个核对上下文，失败即停并说明是哪个补丁、
哪个文件、哪一行。CI 的 `build-kernel.yml` 在构建前调用它。

`patches/piano-sensors/` 是例外：它改的是 SunUEFI 子模块里的设备侧 importer，
由 `scripts/build-device-runtime.py` 在复制出 `sensor-source` 之后用 `patch`
应用，排在上游自己的 `patches/piano-sensors` 之后。所以这一目录没有
`series.json`，`apply-patches.py` 不碰它。

## android-module

SunUEFI 的 KernelSU 模块把安装记录（`install-state.json`）绑定到当时的
BOOT：槽位、设备节点、ROM 与 boot 指纹、wrapper 摘要。系统安装会重写
BOOT，记录随之失效，之后模块里每次「切换系统」都会在身份校验处失败，
只能手动重装模块才能恢复。

补丁在 `common.sh` 增加 `piano_readopt_if_needed`：切换或已确认的请求之前，
先带安装记录跑只读 `status`；不一致时调用 `adopt`，用模块自己的载荷摘要
校验当前 BOOT，通过后重写安装记录。`adopt` 不写 BOOT，当前镜像不是本模块
的 wrapper 时它会拒绝，调用方保留原来的错误。

## piano-sensors

`piano-sensors-import` 过去只在文件缺失时导入。首次安装启动丢过 13 个 ODM
配置文件（全部大于 F2FS inline data 阈值），内容变成全零，而 PD 读的是
零长度之外的合法文件，于是解析出零个传感器。

补丁做四件事：

1. 每个文件读入后检查 `dump.erofs --cat` 的退出码与非空，写盘后 `sync`。
2. 导入目录旁写 `config.sha256`；`ensure_store` 每次启动校验，和清单不符就
   重导，三次仍失败则带明确日志退出。
3. `sns_reg_config` 用同样的清单校验；persist 的注册表与校准仍只导入一次，
   因为 ADSP 会正常改写它。
4. 从 ODM 动态传感器配置里读出 `sns_dynamic_libraries` 列出的库，从 vendor
   的 `/lib/rfsa/adsp` 导入 `/var/lib/piano-sensors/rfsa/adsp`；vendor 上
   不存在的库（如 `sns_tppe.so`）记录后跳过。

库目录通过在 `adsprpcd-sensorspd.service` 的 `30-adsp-library.conf` 里设置
`ADSP_LIBRARY_PATH` 暴露给 file service。

## 选项为什么是这几个

`CONFIG_F2FS_FS=y` 内建，不是模块。根分区在 initramfs 阶段就要挂载，模块
形式会让早期挂载依赖模块加载顺序，而 F2FS 正是被挂载的那个文件系统。

XATTR / POSIX_ACL / SECURITY 三项对应镜像制作时用 `tar --xattrs --acls`
写入的元数据。少任何一项，Fedora rootfs 的 capability 或 ACL 就会在挂载后
读不回来。这三项与上游参考配置一致；参考仓库登记在 `docs/upstreams.json`。

`CONFIG_F2FS_CHECK_FS` 不开：它是运行时一致性检查，代价是性能，镜像制作阶段
已用 `fsck.f2fs` 单独校验。

`CONFIG_F2FS_FS_COMPRESSION` 不开：首版不需要，开了会改变磁盘格式特性，
让镜像与未开启该特性的内核不兼容。

## uefi-ufs

修复 SunUEFI 在 1TB UFS 设备（HyperOS 3.0.305）上冷启动或从 Android 重启时陷入休眠死循环的缺陷。

### 现象与根因

上游 `PianoUfsReadOnlyDma.c` 在 UniPro 链路从 Hibern8 恢复后，仅在“设备描述符读取返回 `EFI_DEVICE_ERROR`”时才向 WLUN `0xD0` 发送 `START_STOP_UNIT` 唤醒 UFS 设备。在 1TB UFS 闪存上，描述符在 Sleep 态（`0x22`/`0x33`）下依然可被成功读取，导致上游完全跳过 `ResumeActive`。紧接着执行的 SCSI `REPORT_LUNS` 因 UFS 设备尚未就绪返回 `CHECK CONDITION: NOT READY (key=02 asc=04 ascq=00)`，且没有任何延时重试，直接触发 `CpuDeadLoop()` 挂起。

### 补丁改动

1. 无论描述符是否读取成功，始终核对当前电源模式；若非 Active（`0x11`），强制发起 `PianoUfsBuildResumeActive` 唤醒设备。
2. 为 `REPORT_LUNS` 增加 5 次有界重试，单次延时 50ms 并重试唤醒。
3. 为 `READ_CAPACITY_16` 增加 5 次有界重试，单次延时 20ms。


## uefi-ebs-identity

修复 systemd-stub 258 以上直入内核时，SunUEFI 拒绝 UKI 启动的问题。

### 现象与根因

菜单选 Linux 后立即回到菜单，提示「Linux 启动镜像格式不受支持，请更新镜像」。
日志里是 `PIANO_EBS_CALL` 之后紧跟 `PIANO_EBS_REFUSE stage=before-exit
status=Access Denied`，菜单再把 `EFI_LOAD_ERROR` 显示成这句话。

systemd-stub 258 起，没有 shim 时不再把 UKI 内嵌的内核 `LoadImage()` 成子镜像，
而是直接跳内核入口。跳转前它临时把 UKI 自己的 `EFI_LOADED_IMAGE_PROTOCOL`
的 `ImageBase`/`ImageSize` 改成内核的地址和大小，返回后恢复。`ExitBootServices`
发生在跳转内部，SunUEFI 的 `ValidateInstalledEfiImage()` 拿 `StartImage` 前记下的
`BaseIdentity`/`SizeIdentity` 去比，看到被改写的值就返回 `uki-identity` 与
`EFI_ACCESS_DENIED`，EBS 因此被拒绝。

上游碰不到：它的 rootfs 是 Debian trixie，systemd 257.13，`linux_exec()`
无条件 `LoadImage()` 内核，父镜像字段始终不动。stub 到 258 以上才会触发，
上游换 rootfs 后同样会坏。

### 补丁改动

`ValidateInstalledEfiImage()` 里把 `ImageBase`/`ImageSize` 的相等比较限定在
EBS 调用者不是 UKI 句柄的情况。直入内核时调用者就是 UKI 句柄；老的
`LoadImage()` 路径里调用者是内核子句柄，仍走严格比较。handle、`ParentHandle`、
`DeviceHandle`、镜像状态与父链检查一条没动。

### 验证与退役

固件重编刷机后看 `uki-identity` 是否消失、DT 与内存校验走到哪一步。真机验证
前不把这一节当成已修复。上游修好这个假设后整组删掉，并同步删掉
`docs/known-issues.md` 里对应条目。

## uefi-dwc3-halt

### 现象与根因

菜单选 Linux 后停在「未能启动系统 / 系统交接失败」，状态 `0x8000000000000012`
（`EFI_TIMEOUT`）。`uki-identity` 那一关已经过了，失败发生在资源交接
（`PianoProductOwnersRetire`）里 USB 服务停止这一步，日志是：

```
SUNUEFI_USB_SERVICE_CLEANUP stage=begin start_status=Success allocated=9 programmed=1 dctl=80F00000 dsts=0000C858
SUNUEFI_USB_SERVICE_CLEANUP stage=halt status=Time out dctl=00F00000 dsts=...
PIANO_START_FAILED stage=MENU handoff=linux-uki status=Time out
```

`PianoDwc3Device.c` 的 `Halt()` 直接清 `DCTL` 的 RunStop 位，然后只在 `DSTS`
上等 `DevCtrlHlt` 100 ms。上游碰不到，是因为它走 raw/boot.img 链，retire 在
`StartImage` 之前完成，那时链路刚被菜单释放、没有主机在枚举。两版
`PianoProductOwners.c` 与 `PianoUsbController.c` 逐字节相同，差别只在 retire
的时机。

2026-11-12 真机复测只套第一版补丁时仍然失败，日志换成：

```
SUNUEFI_USB_SERVICE_CLEANUP stage=begin start_status=Success allocated=9 programmed=1 dctl=80F00000 dsts=0000CF70
SUNUEFI_USB_SERVICE_CLEANUP stage=halt status=Time out dctl=00F00000 dsts=0011808B
```

`0x0011808B` 里 `DevCtrlHlt` 与 `CoreIdle` 都是 0，`USBLNKST=4`（Address）。
`begin` 时链路还没连上，`begin` 到 `halt` 之间一条 `SUNUEFI_USB_EPCMD` 都没有，
说明当时没有挂起传输，DEPENDXFER 无从发起。真正卡住 core 的是 event ring 里
未确认的事件：Linux 的 `dwc3_gadget_soft_disconnect()` 注释写明控制器要等事件
被确认才会 halt，靠中断处理程序在轮询期间完成；固件在 EBS 里空转 `gBS->Stall`，
service pump 不跑，没人确认。EP0 停在 setup 阶段之外也有同样效果，Linux 用
`dwc3_ep0_reset_state()` 把它拉回去再停。

### 补丁改动

`0001` 改 `Halt()`：先用现有的 `StopTransfer()` 结束每个挂起传输；HS/FS 时临时清
`GUSB2PHYCFG` 的 SUSPHY/ENBLSLPM 并在停止后恢复，与 `dwc3_gadget_run_stop()`
一致；等待预算从 100 ms 放宽到 2 s。

`0002` 补上真机缺的两步：轮询期间就地确认 event ring；EP0 不在 setup 阶段时
重新 `ArmSetup()`。同时打印 `SUNUEFI_USB_HALT_ENTRY` 与 `SUNUEFI_USB_HALT_RESULT`
（挂起端点、已确认事件数、循环次数、最终 `DSTS`），下一次失败可以直接从设备
日志读出卡在哪一步。停止顺序、清理证明与其它 owner 都没动。

### 验证与退役

离线检查只证明补丁能套。刷机后要看 `stage=halt` 是否变成 `status=Success`、
能否进入 Linux；如果还失败，看 `SUNUEFI_USB_HALT_ENTRY/RESULT` 的 `armed`、
`acked` 与 `dsts`。上游自己修正 halt 顺序后整组删掉，并同步删掉
`docs/known-issues.md` 里对应条目。
