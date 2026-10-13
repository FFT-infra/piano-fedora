# VM 路线与验收

目标包括 Fedora 宿主内运行硬件加速客体，再验证存储、网络和管理器。
当前没有完成 VM 支持，不能将软件模拟或容器作为这项目标的验收替代。

## 本机状态（2026-10-13）

已通过桥接机 SSH 读取当前 Fedora：内核为
`7.2.9-piano-gnome-gd33a42990baf`，`/dev/kvm` 与 `/dev/gunyah` 均不存在。
设备树中的 Gunyah VM/RM 节点只是定位接口的线索，尚未取得本机 RM 握手、
VM 创建或客体执行成功证据。

## 两条公开实现

| 路线 | 源码和实际接口 | 当前取舍 |
| --- | --- | --- |
| 直接 Gunyah VMM | Qualcomm v17 驱动提案、`/dev/gunyah`、crosvm/相应 QEMU | 优先核对：接近 code002-2 的 librevm 实测路线，但须补齐完整宿主驱动及 ABI |
| KVM ABI 到 Gunyah 的桥 | Karim 的 34 补丁系列、配套 QEMU、`/dev/kvm` | 保留实验对照；原提案尚无 virtio/SMP，不能直接满足普通 libvirt/Incus VM |

`code002-2/librevm@4be3e42b963e95a7753e600613a46059ebdfcd04` 的获取脚本默认
克隆自身 main，查找 `modules/$(uname -r)/*.ko`，否则要求提供内核源码。
该提交的目录树没有这些预编译模块。v0.1.0/v0.2.0 的公开发布资产只有应用
deb 与对应摘要，没有宿主驱动。点击安装按钮并不构成可复现的 VM 构建流程。

作者在 `docs/virtio-on-gunyah.md` 记录 crosvm 使用 `--no-balloon` 后客体磁盘和
网络可用。这是作者记录，未在本机复现；同文档的 `--disable-sandbox` 仅作
实验复现参数，不作为发行版默认配置。

现有十文件摘录不完整，保留在 `linux/gunyah-overlay/`，尚未参与任何构建。
正式集成需要完整系列、许可证和来源摘要，以及独立的实验内核产物；不把
未验证模块注入当前可启动内核。当前 nftables 修复用于 Fedora 防火墙与
后续 VM 网络，不能使缺失的 hypervisor 后端自动出现。

完整源码入口已经找到：`CodeLinaro-mirror/linux-kernel_gunyah-linux` 的
`eberman/gunyah@a73f08e03671eed3c2e3917637eacff764feef90`，基于 Linux
6.10-rc7，包含 VM Manager、guest_memfd、vCPU、irqfd、ioeventfd 与 UAPI。
这推翻“公开驱动源码找不到”的旧结论；仍需审阅系列基线、SCM/内存接口与
本机 RM compatible，不能直接把旧树驱动复制成当前 7.2.9 的外部模块。

## 完成需要的证据

1. 固定完整宿主驱动源码，记录相对现有 piano 内核的差异和移植补丁；核对
   hypervisor/RM compatible、消息队列、doorbell、SCM 内存权限接口。
2. 使用与 Image 一致的配置、编译器、Module.symvers 和 UAPI 构建，检查
   vermagic、BTI、所有未解析符号；构建清单绑定模块与内核。
3. 真机取得 RM 握手、实际 VM 创建和单 vCPU 客体控制台；再测试退出/回收，
   不能仅以设备节点存在判定通过。
4. 验证 virtio-blk 读写、virtio-net、内存回收和重复启停；之后分别评估 SMP、
   客体 UEFI、图形与 libvirt/Incus 的具体接口，逐项记录失败与限制。

## 实读来源

- [Qualcomm v17 完整提案](https://lore.kernel.org/lkml/20240222-gunyah-v17-0-1e9da6763d38@quicinc.com/)
- [完整 VMM 源码树](https://github.com/CodeLinaro-mirror/linux-kernel_gunyah-linux/tree/a73f08e03671eed3c2e3917637eacff764feef90/drivers/virt/gunyah)
- [公开 VM Manager 与 UAPI 补丁](https://lists.infradead.org/pipermail/linux-arm-kernel/2024-January/894121.html)
- [KVM 桥接系列及作者限制说明](https://patchew.org/linux/20250424141341.841734-1-karim.manaouil@linaro.org/)
- [配套 QEMU](https://github.com/karim-manaouil/qemu-for-gunyah)
- [librevm 的模块获取脚本](https://github.com/code002-2/librevm/blob/4be3e42b963e95a7753e600613a46059ebdfcd04/scripts/librevm-get-gunyah.sh)
- [librevm 的 virtio 实验记录](https://github.com/code002-2/librevm/blob/4be3e42b963e95a7753e600613a46059ebdfcd04/docs/virtio-on-gunyah.md)
