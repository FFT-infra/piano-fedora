# 2026-10-17 设备状态与恢复记录

本文记录平板当前停在 Fastboot 的事实、已确认的根因边界、已经排除的路径，以及
恢复所需的人工动作。**本文件不代表设备已验收**；设备在恢复前不可用于任何盲测。

## 当前状态

* 平板在桥接机（`builder`，192.168.5.11）USB 上持续枚举为
  `18d1:d00d`，序列号 `12440448`，`fastboot devices` 能列出设备。
* 但所有 **bulk** 传输（EP `0x01` / `0x81`）超时，`fastboot getvar`、
  `fastboot boot`、`fastboot continue`、`fastboot reboot` 全部无响应。
* 控制传输（EP0）正常：设备描述符可读，接口 `ff/42/03`、两个 bulk 端点。
* 结论：**平板没有变砖**，ABL 仍在运行，但它的 fastboot 命令循环已卡死。

## 根因边界（已用 strace + 裸 usbfs 实测确认）

`strace -f -e trace=ioctl,read,write,openat fastboot getvar product` 显示：

1. 打开 `/dev/bus/usb/001/00X`，读取配置描述符成功；
2. `USBDEVFS_CLAIMINTERFACE` 成功；
3. `USBDEVFS_SUBMITURB` 成功（命令已下发）；
4. 随后 `USBDEVFS_REAPURB` **永久阻塞**，设备端从不回应。

也就是说：设备端 fastboot 处理器不再服务端点，而不是主机侧权限、udev、驱动或
线缆问题。裸 `USBDEVFS_BULK` 读写同样 `ETIMEDOUT`，与 fastboot 工具无关。

## 已尝试且无效的主机侧恢复

| 手段 | 结果 |
| --- | --- |
| 清理残留 `fastboot` 进程后重试 | 无效 |
| `USBDEVFS_RESET`（正确的 `_IO('U',20)` = `0x5514`） | 返回成功，端点仍不通 |
| `usb1-port12/disable` 切换（含 60 秒长断电） | 重新枚举，端点仍不通 |
| `authorized` 0/1 切换 | 无效 |
| 驱动 `unbind`/`bind`、`remove` + xhci 控制器重绑 | 重新枚举，端点仍不通 |
| `SET_CONFIGURATION(1)`（`USBDEVFS_CONTROL` 与 `USBDEVFS_SETCONFIGURATION`） | 返回成功，端点仍不通 |
| `CLEAR_HALT` 两个端点 | 返回成功，端点仍不通 |
| 关闭 `adb` server 后重试 | 无效 |

**结论：主机侧已无可用手段，必须人工断电重启平板。**

## 需要的动作（人工，一次）

1. 长按**电源键约 15 秒**强制关机，等 5 秒；
2. 长按**【电源键 + 音量减键】**进入 Fastboot，确认屏幕出现 FASTBOOT 标识；
3. 桥接机上运行 `fastboot getvar product`，应打印 `product: piano`。

后台的 `wait-for-device.sh` 会一直等到端点恢复，并在成功时把
`state-after-recovery.txt` 写到 `~/Lab/Bridge/tmp/trash/piano-recovery-20261017/`。

## 设备重新可用之后

1. 先用只读命令核对状态：`getvar current-slot`、两个槽的
   `slot-retry-count`/`slot-unbootable`/`slot-successful`、`battery-voltage`；
2. 再按 [手动刷机](installation.md#三之二-手动刷机不使用一键脚本) 逐条执行，
   全程只用 `boot_b` / `sunuefi_esp` / `sunuefi_root`，**不碰 `dtbo_b`**；
3. 刷写完成后按同一节的读回与启动后检查验收，记录真实输出。

## 与本次 PR 的关系

本次 PR 的一键脚本此前带 `fastboot erase dtbo_b`，与实测结论相反（ABL 会因此
拒绝整条引导链）。该命令已删除，并新增 `tests/check_flash_script_guard.py`
防止回归。手动刷机章节与禁止命令清单已补入 `docs/installation.md` 与
`scripts/installer/README-INSTALL.txt`。

上述修复的**脚本路径**已用桩设备验证（无 `erase`、写入序列与预检顺序符合预期），
但**尚未在真机上执行完整刷机流程**。在设备恢复并跑通一次完整手动刷机之前，
PR 的“纯净真机盲测”合并门槛仍未满足。
