# piano-bluez

Fedora 45 的 `bluez-5.88-1` 源码，叠 SunUEFI `patches/bluez/5.88/` 的两条修复。
补丁逐字节取自那边，摘要由 `tests/check_bluez_package.py` 与 `series.json` 对齐。

- HoG 先读 Device Information 的 PnP ID 再建 HID 设备。设备信息档案可能晚于
  report map 完成，内核会先拿到 vendor 与 product 都为 0 的 HID 设备。
- mpris-proxy 用 GLib 的 Unix 信号源退出。信号处理里直接调 `g_main_loop_quit()`
  会抢 GLib 锁，信号打断持锁线程就死锁。

包名是 `piano-bluez`，二进制包仍以 `Provides`/`Obsoletes` 顶掉 Fedora 的
`bluez` 系列。根文件系统显式安装本包；只靠 `Provides` 时 dnf 按名字优先，
还是会选官方包。`Conflicts` 拦住 Fedora 更高的版本，让升级停下来等人判断
新版本带没带修复。
