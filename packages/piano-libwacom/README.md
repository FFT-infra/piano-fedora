# piano-libwacom

把 upstream libwacom 2.20.0 连同 SunUEFI 的 SPI 手写笔补丁打成四个 Fedora
替代包：`piano-libwacom`、`piano-libwacom-data`、`piano-libwacom-utils`、
`piano-libwacom-devel`。四个包各自 `Provides`/`Obsoletes` 发行版的同名包。

## 为什么

内核把这支笔报成 SPI 设备，vendor `0x0603`、product `0x6532`，名字是
`Novatek Focus Pen` 或 `Novatek Focus Pen Pro`（SunUEFI 的
`patches/linux/7.3/0225-LOCAL-input-nt36532e-pen.patch`）。Fedora 45 的
libwacom 2.19.0 只认
usb/bluetooth/i2c/serial，桌面设置里看不到这支笔。2.20.0 本身也没有 SPI
支持，补丁仍然必需。

补丁来自 SunUEFI `patches/libwacom/2.20/0001-spi-digitizers-and-xiaomi-pad.patch`，
sha256 `63017df6…` 与那边的 `series.json` 一致。它加 `WBUSTYPE_SPI`、
`spi` 的 DeviceMatch 解析、hwdb 的 `001C` 映射，以及
`data/xiaomi-pad-8-pro.tablet`。描述文件用的是无橡皮、带压感的笔。

## 源码与摘要

`Source0` 是上游 GitHub release tarball：

- sha256 `370b45b5e05a91960df0aeb9c9481ae05846aab92ab2d4ec66945a0da4216888`
- sha512 `b855bba9cac30da2eced51fe2e53201966a26207a5b2b2a798adcdaf92df0f94f5
  253c9a37012f9b78e0f92ad48b6b4287700b620f992d28c751578486a33c09`

解包内容与 SunUEFI 钉的 Debian orig tarball 逐字节相同。顶层目录不同
（`libwacom-2.20.0` 对 `libwacom-libwacom-2.20.0`），spec 按上游的目录名解包。

## ABI

soname 仍是 `libwacom.so.9`，`lt` 版本 9:0:0，与 Fedora 45 的 2.19.0 相同。
符号版本包含 `LIBWACOM_2.19`，按 2.19 编译的消费者可以直接换过来。

## 构建验证

`%check` 跑构建目录里的 `list-devices`，它读源码树 `data/`，断言输出里有

```
- { bus: 'spi', vid: '0x0603', pid: '0x6532', name: 'Xiaomi Pad 8 Pro', ... }
```

本地 mock（Fedora 45 aarch64）构建通过，这一行出现在 `%check` 日志里。这是
构建层证据；桌面设置是否能列出这支笔要在设备上验证。
