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

