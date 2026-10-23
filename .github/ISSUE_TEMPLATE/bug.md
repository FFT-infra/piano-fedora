---
name: 刷机问题
about: 安装、启动或刷写失败
title: ""
labels: ""
assignees: ""
---

## 设备与固件

- 设备型号（`fastboot getvar product` 的输出）：
- 存储容量：
- 原厂系统版本（HyperOS 版本号）：
- 是否已解锁 Bootloader：

## 刷机材料

- 使用的归档或 deploy-bundle-<档位> 来自哪个 tag，或哪次 `build-bundle` run（run id）：
- root-install 是否来自同一次 run：
- 镜像 manifest 里的 `kernel_release`：
- 是否核对过 `SHA256SUMS`：

## Fastboot 关键字段

贴命令原文，不要改写：

```text
$ fastboot getvar product
$ fastboot getvar current-slot
$ fastboot getvar unlocked
$ fastboot getvar is-userspace
$ fastboot getvar partition-size:sunuefi_esp
$ fastboot getvar partition-size:sunuefi_root
```

## 做了什么

按顺序写下执行的命令，以及在哪一步失败。第一次安装和已有分区更新要分开写。

```sh
# 命令
```

## 现象

- 屏幕上显示什么（拍照或描述）：
- 设备停在哪个状态（原厂 Fastboot、UEFI、内核、GDM）：
- 是否会自动重启或掉回 Fastboot：

## 日志

串口、UEFI 控制台、`dmesg` 或安装器输出的相关片段。贴文本，不要只发截图。

```text
# 日志
```

## 已经排除的项

列出你确认过没问题的部分，例如分区容量、哈希、槽位。
