# 2026-10-18 槽位 GUID 错位导致无法启动：定位与修复

## 结论

设备从 10-16 起两槽都起不来，原因是 UFS LUN4 的 A/B 槽位类型 GUID
归属反了：活动槽持有 generic GUID，非活动槽持有专属 GUID。ABL 的
`ValidateSlotGuids()` 判定可启动槽位没有有效 GUID，拒绝加载任何引导
镜像，对外只报 `Reason:LoadImageAndAuth Fail`。

修复方式是用 EDL 交换 8 个分区的类型 GUID 并重算 CRC。HyperOS
OS3.0.309.0 已能从 Slot A 正常启动，`sunuefi_esp`、`sunuefi_root`
及 Slot B 的引导组件均完好。Slot B 的 Linux 启动尚未验证。

## 故障时间线

关键一点：故障不是某次刷写造成的。从 Linux 正常运行到再也起不来之间，没有任何
`fastboot flash` 或 `erase`。

| 时间（CST） | 事件 |
| --- | --- |
| 10-16 13:54 | Linux 正常运行，`uname -r` 为 `7.2.9-piano-gnome-g8c0e589d8829` |
| 10-16 14:40 | SSH 开始超时，系统逐渐失去响应 |
| 10-16 17:43 | `/proc/cmdline` 仍可读，内核在跑，systemd 已不可交互 |
| 10-16 17:44 | `qbootctl -s b; qbootctl -m`，超时后迟到回显显示已执行 |
| 10-16 17:49 | `echo b > /proc/sysrq-trigger` 强制重启 |
| 10-16 17:51 | 回到 Fastboot：`slot-successful:b: yes`、`retry-count:b: 1` |
| 10-16 17:51:55 | `fastboot reboot`，17 秒后跌回 Fastboot |
| 10-16 17:52:12 | `fastboot continue` 报 `Device Error`，此后 40 余次全部相同 |

`qbootctl -m` 写的是 GPT 分区属性位。系统当时已处于 I/O 挂死状态，
写操作很可能只完成了一部分就被强制重启打断。

## 定位过程

### 一、先排除镜像内容

以下手段全部无效，且都返回 `OKAY`：

* 两槽分别 `set_active` 后冷启动
* 刷入完整原厂 309 六件套到 Slot A
* 用原厂 `vbmeta.img` 原文件改 `flags=2`、改 `rollback_index`
* 给 `boot_sunuefi.img` 补 `os_version` 并重新签名

Slot A 全原厂镜像仍报同一个错，`slot-retry-count:a` 从 7 降到 6。镜像内容
可以排除了。

### 二、`fastboot continue` 不能作为证据

`continue` 和 `fastboot boot` 恒报
`Failed to load image from partition: Device Error`。早期把它当成底层
损坏的证据，写了送修建议，这是误判。SM8750 的 ABL 在 Fastboot 模式下
对这两个命令有独立的失败路径，不能用它判断引导链状态。

### 三、读回设备 GPT

`fastboot fetch` 不支持（`Unable to get max-fetch-size`），`oem catrb`
不接受参数，所以 Fastboot 下拿不到分区内容。改用 EDL：

1. `fastboot oem edl` 进入 `05c6:9008`
2. 加载免认证 SM8750 programmer
3. 只读 LUN0 / LUN4 的 GPT

读回的 LUN4 主 GPT 头 CRC 与表项 CRC 均校验通过，说明 GPT 结构完好，
问题不在表本身。

### 四、比对槽位类型 GUID

原厂 `gpt_main4.bin` 的出厂状态：

```
abl_a   BD6928A1-4CE0-A038-4F3A-1495E3EDDFFB
boot_a  20117F86-E985-4357-B9EE-374BC1D8487D
vbmeta_a 4B7A15D6-322C-42AC-8110-88B7DA0C5D77
abl_b / boot_b / vbmeta_b / dtbo_b  77036CD4-03D5-42BB-8ED1-37E5A88BAA34
```

设备实际读回（`current-slot: a`）：

```
abl_a    generic 77036CD4      abl_b    专属 BD6928A1
boot_a   generic 77036CD4      boot_b   专属 20117F86
vbmeta_a generic 77036CD4      vbmeta_b 专属 4B7A15D6
dtbo_a   generic 77036CD4      dtbo_b   专属 24D0D418
```

活动槽 `a` 持有的全是 generic GUID，非活动槽 `b` 反而持有专属 GUID，
与原厂规则正好相反。`vendor_boot_a` 和 `init_boot_a` 是对的，说明当时
的交换只做了一半。

ABL 二进制中存在这些字符串，与现象吻合：

```
ValidateSlotGuids: BootableSlot %s does not have valid guids
ValidateSlotGuids: No partition entry for %s
Boot lun: %x and BootableSlot: %s do not match
```

`set_active` 无法修复。实测执行后 `current-slot` 变了，但 GPT 里的
类型 GUID 一个字节都没动。反汇编 `SetActiveSlot` 可见它只更新
`devinfo` 中的活动标志，不做 GUID 交换。

## 修复

### 修改范围

只交换 `abl`、`boot`、`vbmeta`、`dtbo` 四对的类型 GUID，共 8 个表项
各 16 字节。分区起止、属性、名称均不动，然后重算表项 CRC 和头部 CRC。

写入前自检结果：

```
entries with changes outside type-GUID field: NONE
entries with changed type GUID: 8
header differing byte offsets: [16, 17, 18, 19, 88, 89, 90, 91]
new hdr CRC: OK
new entries CRC: OK
```

头部只有两个 CRC 字段变化（偏移 16 到 19，88 到 91），其余字节未动。

### 关键操作细节

**表头和表项必须在同一次 EDL 会话里写完。**

分两次写入（先写表项、再写表头）读回仍是旧状态。原因是 `qdl` 每次
执行结束都会发送 `power reset`，设备复位时 ABL 会把缓存的 GPT 写回
磁盘，覆盖前一次的写入。

正确做法是用一份 XML 描述两个写操作，一次会话完成：

```xml
<?xml version="1.0" ?>
<data>
  <program SECTOR_SIZE_IN_BYTES="4096" file_sector_offset="0" filename="w-hdr.bin"
           label="PrimaryGPT" num_partition_sectors="1" partofsingleimage="false"
           physical_partition_number="4" readbackverify="false" size_in_KB="4.0"
           sparse="false" start_byte_hex="0x1000" start_sector="1"/>
  <program SECTOR_SIZE_IN_BYTES="4096" file_sector_offset="0" filename="w-ent.bin"
           label="GPT" num_partition_sectors="24" partofsingleimage="false"
           physical_partition_number="4" readbackverify="false" size_in_KB="96.0"
           sparse="false" start_byte_hex="0x2000" start_sector="2"/>
</data>
```

```sh
qdl -s ufs -i "$T" "$LOADER" "$T/write-gpt.xml"
```

### EDL 认证

设备原厂 `xbl_s_devprg_ns.melf` 本身就是 SM8750 的 Firehose
programmer，不是放错了文件。它内置鉴权逻辑，加载后拒绝任何
`configure`：

```
ERROR: Only nop and sig tag can be recevied before authentication.
```

实际使用的是公开的免认证版本：

| 项 | 值 |
| --- | --- |
| 来源 | `bkerler/Loaders` → `xiaomi/0000000000030000_467f3020c4cc788d_fhprg_8E_noauth.bin` |
| 直链 | `https://raw.githubusercontent.com/bkerler/Loaders/main/xiaomi/0000000000030000_467f3020c4cc788d_fhprg_8E_noauth.bin` |
| 大小 | 1,697,740 字节 |
| SHA256 | `95bd33db724706db5da03882c65783d01338df6159ce563be0ce1b963d83668d` |

同一份文件在 `18xjul08/xiaomi_ubl` 的 `firehose/8750.melf` 和
`Iqinix/Qualcomm-firehoses` 的 `Xiaomi/No Auth/SNAPDRAGON 8 ELITE.melf`
里各有一份拷贝，字节完全一致。

该文件不含 `Only nop and sig`、`EDL Authenticated`、`Blob should init
first` 三条字符串，含 `SM8750`、`SM8750P`、`PAKALA`。

写入前核对过它包含 KIOXIA 与 SAMSUNG、MICRON 的 UFS 支持字符串。
本机是铠侠 UFS，公开资料对这类 loader 的写入有风险提示，因此全程只用
它做 GPT 修复和只读读取。

本地保存路径：`builder:~/Lab/Bridge/tmp/trash/noauth-loader/fhprg_8E_noauth.bin`。
使用前建议先跑一次 `sha256sum` 核对，再按下面的命令加载：

```sh
qdl -s ufs "$LOADER" read '4/0+32' /path/to/out.bin
```

`-s ufs` 必须显式指定，否则 qdl 会按默认存储类型探测并可能失败。

## 修复后状态

```
HyperOS   OS3.0.309.0.WPYCNXM，从 Slot A 启动
内核      6.6.118-android15-8-gc44b714366cc-abogki519650608-4k
adb       shell 权限可用
current-slot: a
```

两槽内容：

| 槽位 | 内容 | 验证状态 |
| --- | --- | --- |
| Slot A | HyperOS 309 | 已启动验证 |
| Slot B | SunUEFI + Fedora 44 | 组件完整，未验证启动 |

Slot B 组件（从 EDL 读回后挂载确认）：

```
boot_b       96 MiB   SUNUEFI 标记存在，kernel_size 67,661,824，自签公钥 541ad83e
sunuefi_esp  513 MiB   FAT32，标签 SUNUEFI_ESP
  EFI/Piano/stable/Image      43 MB   7.2.9-piano-gnome-g184e09d96775
  EFI/Piano/stable/board.dtb  1.2 MB  FDT magic 正确
  EFI/Piano/stable/initramfs  2.5 MB  gzip
  EFI/Piano/stable/boot.img   45 MB
sunuefi_root 468.74 GiB  F2FS
```

`boot_a` 为 309 原厂镜像（SHA256 `7c444a2d…`，小米公钥 `de5be2a5`，
rollback 1788220800）。`init_boot_a` 与 309 原厂完全一致
（SHA256 `156400bb…`），无任何 root 痕迹。

## 复现与回滚

### 判断是否再次发生

```sh
fastboot oem getguid | grep -E 'guid:(boot|abl|vbmeta|dtbo)_[ab]:'
fastboot getvar current-slot
```

活动槽的分区类型 GUID 应当是对应分区的专属 GUID，不是
`77036CD4-03D5-42BB-8ED1-37E5A88BAA34`。

### 回滚

修复前的原始 GPT 已备份：

```
lun0-gpt-backup-original.bin    8 KiB
lun4-gpt-backup-original.bin  128 KiB
lun4-gpt-fixed.bin            128 KiB
```

回滚同样需要在一次 EDL 会话内写完表头与表项。

## 操作中踩到的坑

**EDL 会话会被复位打断。** `qdl` 每次执行末尾都发送 `power reset`，
设备随即重启。连续读取多个分区时，第二次会报
`SAHARA: failed to read request from device`。每次读写之间都要重新
执行 `fastboot oem edl` 或 `adb reboot bootloader` 再进 EDL。

**LUN4 备份表项区读不出来。** 读取 LBA 378873 起的 24 个扇区返回
`NAK`，但最后一个扇区的备份 GPT 头可读。本次修复只写主表，未改备份表。

**`oem poweroff` 不一定断电。** 设备插着 USB 时不会真正断电，需要
配合 `/sys/bus/usb/devices/usb1/1-0:1.0/usb1-port12/disable` 做端口级
断电才能让设备重新枚举。

**`18d1:4e11` 不是 Fastboot。** 这个 USB ID 是 HyperOS 的 ADB
接口，小米复用了高通的描述符。判断模式要看 `adb devices` 和
`fastboot devices`，不能只看 `lsusb`。

## 与 PR 的关系

故障与 PR #2 的 Release 资产无关，这一点现在有直接证据：设备在故障
期间从未被刷写过，故障原因是槽位元数据，不是任何镜像内容。

本次修复不改变 PR 的验收结论。真机盲测仍需在设备完全恢复后重新执行，
Slot B 的 Linux 启动尚未验证。
