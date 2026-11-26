# Gunyah 虚拟化与验收

Gunyah 已作为内置正式能力进入标准 Fedora 内核构建链，不再使用单独实验开关或独立实验分支，所有虚拟化成果均以真机实测为准。

## 内核与用户态集成

- **内核端**：由 `scripts/prepare-production-kernel.py` 重放 `patches/linux/7.3/series.json`（含源自 `code002-2/librevm@56918558e2c4` 的 `0006-gunyah.patch`），并通过 `--fedora-policy` 强制打开全部八项内建配置（`GUNYAH`、`GUNYAH_HYPERCALLS`、`GUNYAH_TIMER`、`GUNYAH_PLATFORM_HOOKS`、`GUNYAH_QCOM_PLATFORM`、`GUNYAH_QCOM_TRUSTED_VM`、`GUNYAH_IRQFD`、`GUNYAH_IOEVENTFD`）。`check_production_kernel.py` 与 `check_runtime_kernel_source.py` 在 `make test` 中校验来源唯一性。
- **用户态**（`build-device-runtime.py` 交付并校验安装二进制字节）：
  - `piano-crosvm`：基于锁定 crosvm 源码启用 `gunyah,net,balloon` 特性；仅选择 Gunyah 时注入 `--disable-sandbox` 以适配 `PAGE_FAULT exit`（reason 3），KVM 路径保持沙箱开启。
  - `librevm`：含 CLI、daemon 与 GTK GUI；就绪检测以 `/dev/gunyah` 及内核注册路径为准。

## Gunyah RM Capability 交接

ABL 每次重启都会重新分配 Gunyah 资源管理器（RM）的 TX/RX capability 并写入 inherited FDT；若把镜像构建期冻结的旧值直接交给 hypervisor，首次 RM 请求会返回 `ARG_INVAL` 并让 `gunyah_rm_msgq_send` 死等。

为此 `PianoDeviceTreeFixup.c`（`PianoProductBootObjectsApplyGunyah`）会在交接内核前改写 payload DTB `/hypervisor/qcom,resource-manager-rpc@...` 节点的 `reg`（16 字节 TX/RX capability）与 `interrupts`，改写前强制满足四个条件：两棵树均恰好匹配一个目标节点、继承值刚好 16 字节且两个 id 均非零、属性布局合法；否则保留原值并记日志。

## 实机验收状态

> 以下真机实测结果完成于先前的 `boot.img` 启动链；当前切换到 UKI 引导链后仅复验过内核升级本身，尚未重跑完整 VM 压测。

| 验证面 | 已通过内容 | 尚未覆盖 / 待跟进 |
| --- | --- | --- |
| 平台接口 | 正式内核、`/dev/gunyah`、动态 RM capability 交接与 SELinux Enforcing | 新内核版本后常规复测 |
| 客体执行 | LibreVM Auto 自动选中 Gunyah，单 vCPU `VM_START` 与正常退出通过 | 交互式控制台长期体验 |
| 数据路径 | `virtio-blk` 64 MiB ext4 读写一致性通过；`virtio-net` 同网段通信通过（`NET_OK=1 NET_RX=2`） | 无 |
| 生命周期 | 3 轮盘+网与 1 轮无盘失败有界退出均无残留 crosvm 进程/socket | 无 |
| 管理器与扩展 | RPM、CLI/D-Bus 与 KVM sandbox 回归通过 | GTK GUI 操作、Gunyah 沙箱开关与 SMP / UEFI / 图形客体 / Incus 支持 |

详见[已知问题](known-issues.md)。

## 参考来源

- [Qualcomm Gunyah v17 LKML 提案](https://lore.kernel.org/lkml/20240222-gunyah-v17-0-1e9da6763d38@quicinc.com/)
- [7.3 Gunyah 补丁来源（code002-2/librevm）](https://github.com/code002-2/librevm/tree/56918558e2c4303cda803a655ea716869c551b71) 与 [Gunyah 7.2 移植说明](https://github.com/code002-2/librevm/tree/9833d43c7caadfdfb8439306cd10641921d58e90/packaging/gunyah-driver-7.2)
- [小米 piano 官方内核 Gunyah 驱动](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/tree/45fb9bd6ae5ba2942fc1d53e4b6b46ef76992f71/drivers/virt/gunyah) 与 [CodeLinaro Gunyah 源码](https://github.com/CodeLinaro-mirror/linux-kernel_gunyah-linux/tree/a73f08e03671eed3c2e3917637eacff764feef90/drivers/virt/gunyah)
- [Linux ARM-Kernel VM Manager 补丁](https://lists.infradead.org/pipermail/linux-arm-kernel/2024-January/894121.html)、[KVM 桥接系列](https://patchew.org/linux/20250424141341.841734-1-karim.manaouil@linaro.org/)、[QEMU for Gunyah](https://github.com/karim-manaouil/qemu-for-gunyah)
- [librevm 模块脚本](https://github.com/code002-2/librevm/blob/4be3e42b963e95a7753e600613a46059ebdfcd04/scripts/librevm-get-gunyah.sh) 与 [virtio on Gunyah 实验记录](https://github.com/code002-2/librevm/blob/4be3e42b963e95a7753e600613a46059ebdfcd04/docs/virtio-on-gunyah.md)
