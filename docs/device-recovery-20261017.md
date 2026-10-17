# 2026-10-17 设备状态与恢复记录

## 结论

平板当前的失败点不在本 PR 的 Release 资产，也不在 `dtbo_b` 内容或
`fastboot` 主机侧连接。设备停在原厂 ABL 的 Fastboot，所有加载路径都在
`LoadImageAndAuth` 阶段返回 `Device Error`：两个槽位、完整原厂 slot B
引导链、PR 的 `boot_sunuefi.img`、内存引导，以及断电冷启动后重试，结果
完全一致。

现有证据把故障边界收敛到 ABL 的槽位/GPT 校验或底层存储状态，而不是镜像
内容。设备目前已经关机，需要人工长按电源键重新开机；开机后若仍进入
Fastboot，只能走官方售后，或由具备小米签名 Firehose programmer 的服务端
恢复。**在设备恢复并跑通一次完整手动刷机之前，PR 的真机盲测合并门槛仍未
满足。**

## 当前状态

最后一次设备在线时（`2026-10-17 17:39 CST`）的只读状态如下：

| 项目 | 值 |
| --- | --- |
| USB | `18d1:d00d`，序列号 `12440448`，bus 1 port 12 |
| `current-slot` | `b` |
| `slot-successful:a` / `b` | `no` / `no` |
| `slot-unbootable:a` / `b` | `no` / `no` |
| `slot-retry-count:a` / `b` | `7` / `7` |
| `battery-voltage` | `3999` mV |
| `off-mode-charge` | `1` |
| `oem fbreason` | `Reason:LoadImageAndAuth Fail` |
| `getvar` / `flash` | 正常，所有写入均返回 `OKAY` |
| `continue` / `fastboot boot` | 一律 `Device Error` |

快照保存在桥接机 `builder:~/piano-boot/pre-poweroff-state-20261017.txt`。
`off-mode-charge=1` 是冷启动实验留下的设置；它不影响刷写，但会让设备在
USB 断电后直接关机，而不是自动回到 Fastboot。

## 决定性证据

### 1. 失败发生在 ABL 加载/认证阶段

`fastboot continue` 的完整报错是：

```text
Resuming boot                                      FAILED (remote: 'Failed to load image from partition: Device Error')
```

`fastboot boot <image>` 则在传输完成后报：

```text
Sending 'boot.img' (...)                           OKAY
Booting                                            FAILED (remote: 'Failed to load/authenticate boot image: Device Error')
```

两条路径都在真正读取镜像内容之前失败。`oem fbreason` 独立回读为
`LoadImageAndAuth Fail`，与报错一致。

### 2. 两个槽位同样失败

`set_active a`、`set_active b` 均返回 `OKAY`，但随后 `continue` 仍然
`Device Error`。失败与当前活动槽无关，排除了“只坏了某一个 boot 分区”。

### 3. 完整原厂 slot B 引导链也失败

从官方包 `piano_images_OS3.0.5.0.WPYCNXM_16.0` 解包后，按以下哈希写入
slot B，五个分区全部 `OKAY`：

| 分区 | SHA256 |
| --- | --- |
| `boot_b` | `67ecb06b0a3464fc9f0abbf620c8d667e21bd2436ea9ae1426cc0aea9bc80bc7` |
| `init_boot_b` | `5e1232e75ee0b2ac95c63d92a8734f7b18ec89aa48459eb7bc112ff6a506ef0d` |
| `dtbo_b` | `46ffad8e8705b52305c3c2b8b2d4b2e1b781c98693d4f905e98d7de612812aa0` |
| `vendor_boot_b` | `36befe8449e9d1e5be778f035165d6eab0ae22ac9e6006887fbdbaf9ef361e8b` |
| `vbmeta_b` | `9999e70f9f6c939f3e7c1f038eed388c8e5ae182143064b8570de8131a504174` |

随后 `continue` 仍为 `Device Error`。这是“与 PR 资产无关”的最直接证据：
即使把 ABL 需要的原厂整条链全部放回，加载仍在进入镜像之前失败。

### 4. 原厂 `boot.img` 内存引导同样失败

`fastboot boot` 分别尝试了 PR 资产、历史 Header v4 资产和原厂
`boot.img`（`67ecb06b…` / 读回 `019be6f9…`），全部在传输完成后报
`Failed to load/authenticate boot image: Device Error`。因此不是
`boot_b` 分区内容或 `boot_sunuefi.img` 的 Header/AVB 问题。

### 5. 断电冷启动无效

`oem off-mode-charge 1` 写入并回读成功；`oem poweroff` 后 USB 断开。
通过正确路径 `/sys/bus/usb/devices/usb1/1-0:1.0/usb1-port12/disable`
断电 10 秒再上电，设备真实重新枚举（Device 028），但 `continue` 仍报
`Device Error`。因此不是 ABL 运行时缓存或端点卡死。

### 6. EDL 只读诊断被厂商签名拦截

`fastboot oem edl` 可以进入 `05c6:9008`，但用原厂包内的
`xbl_s_devprg_ns.melf` 启动 Firehose 后，programmer 只接受
`nop`/`sig`，在 `configure` 前返回：

```text
ERROR: Only nop and sig tag can be recevied before authentication.
```

因此无法在不具备小米签名认证材料的情况下只读设备 GPT。断电退出 EDL
后设备关机，未再自动回到 Fastboot。

## ABL 静态分析

设备原厂 `abl.elf` 解包后包含 `ValidateSlotGuids()`，其失败条件包括：

* GPT 中活动槽的 `boot_x` 与 `abl_x` 类型 GUID 异常相等；
* UFS Boot LUN 与 `BootableSlot` 不匹配；
* rollback index 读取失败。

原厂 `gpt_main4.bin` 的 header CRC 和 partition entry CRC 均正确。
slot A 的 `boot_a`（`20117f86…`）与 `abl_a`（`bd6928a1…`）类型 GUID
不同；slot B 的 `boot_b` 和 `abl_b` 在出厂表中都使用 generic GUID
`77036cd4…`。这说明设备上的实际 GPT 必须经过 `set_active` 的类型 GUID
交换，才能让活动槽通过校验；如果交换只完成了一半，或 UFS Boot LUN
切换没有真正生效，就会出现“两槽都 `Device Error`”的症状。

这属于**推断**，不是已经读回设备 GPT 后的事实；EDL 被签名拦截，正是
无法把推断升级为定论的原因。

## 已尝试且无效的手段

| 手段 | 结果 |
| --- | --- |
| 清理残留 `fastboot` 进程 | 恢复命令循环，但加载仍失败 |
| `USBDEVFS_RESET`（`0x5514`） | 端点恢复，加载仍失败 |
| 端口 `disable` 0/1 循环、长断电 | 设备重新枚举，加载仍失败 |
| `set_active a` / `set_active b` | `OKAY`，`continue` 仍失败 |
| 重刷原厂 `boot_b`、`init_boot_b`、`dtbo_b` | `OKAY`，仍失败 |
| 重刷原厂完整 slot B 五分区链 | `OKAY`，仍失败 |
| `fastboot boot` PR / 历史 / 原厂镜像 | 传输 `OKAY`，加载失败 |
| `oem poweroff` + 端口断电冷启动 | 重新枚举，仍失败 |
| `fastboot fetch` | 不支持（`Unable to get max-fetch-size`） |
| `oem uefilog` | `Failed to get logfs files` |
| `oem ramdump fat` | 只设置下一次崩溃的 ramdump 模式，不导出当前日志 |
| EDL + 原厂 Firehose programmer | 厂商签名认证拦截 |

## 与本次 PR 的关系

本次 PR 的 Release 资产在本地已经通过以下检查：

* `make test` 全绿；
* `sha256sum -c SHA256SUMS` 全部 `OK`；
* `boot_sunuefi.img` 为 96 MiB、`ANDROID!` Header v4、含 `AVB0`；
* 含 `SUNUEFI-SPLITv1` 与已验证的 live-memory `app_generation`；
* `SUNUEFI-NEXTv1` CRC 正确，默认 `target=2`（Linux）。

但设备级故障使真机端到端验收无法继续。这个失败**不能**归因于
`boot_sunuefi.img`、`sunuefi_esp.img`、rootfs 或一键脚本；也不能据此宣称
PR 已通过真机盲测。

## 恢复建议

按风险从低到高：

1. **人工重新开机**：长按电源键约 15 秒，再按电源键正常开机；若仍进入
   Fastboot，先只跑 `fastboot getvar all` 和 `oem fbreason`，不要写任何
   分区。
2. **官方售后 / 小米授权维修**：这是当前最稳妥的恢复路径。说明设备卡在
   Fastboot，`LoadImageAndAuth` 对所有镜像失败，且 EDL 需要签名
   programmer；不要要求维修人员先擦 `userdata`。
3. **具备签名 Firehose 材料的服务端**：只有在能提供与
   `piano_images_OS3.0.5.0.WPYCNXM_16.0` 匹配的签名 programmer 时，才考虑
   通过 EDL 只读核对 GPT/UFS Boot LUN，再决定是否修复。不要用来源不明的
   programmer 写 GPT。

## 禁止操作

在根因确认前不要执行：

* `fastboot erase dtbo_b`、`fastboot erase boot_b`、`fastboot erase userdata`；
* `fastboot -w`、`fastboot format`；
* 重刷 `gpt_main4.bin` / `gpt_backup4.bin`；
* 写 `devinfo`、`persist`、`misc`、`xbl`、`abl`、`tz`、`modem`；
* 使用非原厂或未签名的 Firehose programmer。

这些操作可能让当前仍可进入 Fastboot 的设备进一步失去可恢复性。
