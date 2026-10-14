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
| 直接 Gunyah VMM | 小米 piano-w-oss 厂商驱动、Qualcomm 公开提案、`/dev/gunyah`、crosvm/相应 QEMU | 优先核对本机官方驱动的 ABI 与移植依赖，公开提案作对照 |
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

2026-10-14 核对“换上游最新一份”是否可行：**主线至今没有合并 Gunyah 驱动**。
`torvalds/linux` master 的 `drivers/virt/` 是 acrn、coco、fsl_hypervisor.c、
nitro_enclaves、vboxguest、vmgenid.c；固定的 7.2.6 树里唯一带 gunyah 的文件是
`drivers/watchdog/gunyah_wdt.c`。两份公开源码反而更难用：v17 提案基于 ~6.8，
`eberman/gunyah` 基于 6.10-rc7，都要通用 guest_memfd（`include/linux/guest_memfd.h`
与 `mm/guest_memfd.c`），而 7.2.6 只有 KVM 内部的 `virt/kvm/guest_memfd.c`。
小米厂商驱动是 6.6.57 基线，但自带 pin-user-pages、不依赖 guest_memfd，且是
唯一对得上本机 RM 的官方实现，所以维持从它导入。

逐符号核对已确认：该 VMM 使用通用 `include/linux/guest_memfd.h` 与
`mm/guest_memfd.c` 的 `guest_memfd_alloc/grab_folio/make_inaccessible` 接口，
并选择 `CONFIG_GUEST_MEMFD`。当前固定内核只有 KVM 内部的
`virt/kvm/guest_memfd.c` 与 `CONFIG_KVM_GUEST_MEMFD`，不提供这些通用 API。
移植还需 arm64 hypercall/Kbuild 和驱动接入。真机读取后更正：节点名虽然为
`qcom,resource-manager-rpc@…`，其 compatible 列表已经包含
`gunyah-resource-manager`，并提供两个 64 位 capability ID 与两项 IRQ。
不能从节点名直接断言驱动不匹配。父 hypervisor 的 compatible 列表为
Qualcomm 两项及 `simple-bus`，未包含通用 `gunyah-hypervisor`；仍需核对
父初始化、版本探测与已有平台子设备的注册路径，不盲目覆盖 compatible。

小米官方 `piano-w-oss@45fb9bd6ae5ba2942fc1d53e4b6b46ef76992f71` 里也有完整
宿主驱动，其 UAPI 包含 `GH_VM_SET_USER_MEM_REGION`、`GH_VM_ANDROID_LEND_USER_MEM`
和 `GH_VM_ANDROID_SET_FW_CONFIG`，自带 pin-user-pages、不依赖通用 guest_memfd。
2026-10-13 到 10-14 曾按它做过一版导入，结果是 6.6.57 → 7.2 的逐处 API 漂移：
51 分钟那轮撞上 `folio_index()` 与 `of_node_to_fwnode()`，改写后在十分钟的预检里
又撞上 `vm_mgr_mem.c` 的 `PageSwapBacked()`。这条线不再继续手工移植，改为直接
消费上游已适配的驱动（见下），小米树降为研究来源。

已适配主线 7.2 的现成驱动在 `SuperTurtleDev/iceland-mainline-kernel-linux` 的
`pr/gunyah` 分支：驱动来自 Android common kernel `android17-6.18`，分支上三个提交
分别是导入、适配主线 7.2、以及 `tools/virt/gunyah/gh_vmtest.py` 冒烟测试与验证
记录。其 `Documentation/virt/gunyah/port_adaptations.md` 记录五处适配，其中
`of_node_to_fwnode()`→`of_fwnode_handle()` 与上面撞到的是同一类。分支作者在
OnePlus Pad 4（SM8850）上记录 `gunyah: Running under Gunyah hypervisor 51/v1`、
`/dev/gunyah` 出现、VM_START 约 2 ms；这是作者记录，未在本机复现。

## 执行入口（2026-10-14 现状）

驱动改为**原样搬运上游已适配的源码**，本仓库不再改写驱动字节：

| 环节 | 入口 | 边界 |
| --- | --- | --- |
| 驱动源码 | `sources.lock.json` 的 `gunyah-driver`（`SuperTurtleDev/iceland-mainline-kernel-linux@fd503a49`，分支 `pr/gunyah`） | 只有独立实验构建消费；正式内核不自动替换 |
| 派生源码 | `scripts/prepare-gunyah-source.py --kernel <封存内核> --kernel-source <源码树> --driver-source <驱动 checkout> --output build/kernel-worktrees/gunyah-experiment --execute` | 默认只打印计划；32 个导入文件逐个与锁定提交逐字节比对；不改写任何字节；输出锁在 `build/` 下；提交只落在新 worktree 的 detached HEAD |
| 检查 | `tests/check_gunyah_source.py`（已进 `make test`） | 覆盖隐藏修改拒绝、两次派生结果一致、导入文件与上游逐字节相同 |
| 配置 | `patches/kernel-gunyah`（`GUNYAH_OVERRIDES` + `--experimental-gunyah`） | 八个符号按上游 Kconfig 全选；显式旗标才选；正式档不带该配置 |
| 构建 | `build-kernel.yml` 的 `gunyah-kernel` job，输入 `experimental_kernel_run_id`（取该 run 的封存内核产物） | 与正式 `kernel` job 互斥；留空即不构建实验档；先单编驱动目录的预检，再整核构建 |
| 配套 runtime | `build-device-runtime.yml` 的 `experimental_kernel_run_id` | 与标准 `kernel_run_id` 互斥；从同一实验 artifact 取 Image 清单与 SDK，重放派生源码并比较全部来源清单后编译外部模块；不混用标准内核模块 |

预检这一步在 2026-10-14 首次生效：整核构建要五十多分钟，而驱动目录的问题
在配置完成后十分钟内就能判死。

2026-10-14，Actions run `38445077854` 的驱动预检、整核构建和 SDK 导出均成功。
本地对下载产物执行 `piano_artifacts.inspect_kernel`，校验 Image、配置、1636 个
模块及索引通过，内核为 `7.2.9-piano-gnome-gd770d520df28`。派生清单的
`PREPARED_NOT_BUILT_NOT_DEVICE_VERIFIED` 只描述源码准备步骤；构建结果另由内核
清单与这次 CI 证明。实验内核尚未部署，RM 握手和客体执行仍未验证，不能据此
声明 VM 可用。

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
- [已适配主线 7.2 的 Gunyah 驱动分支](https://github.com/SuperTurtleDev/iceland-mainline-kernel-linux/tree/fd503a492d381687c143b7085ea79ebce58dcdc7/drivers/virt/gunyah)
- [librevm 对该驱动的打包与移植说明](https://github.com/code002-2/librevm/tree/9833d43c7caadfdfb8439306cd10641921d58e90/packaging/gunyah-driver-7.2)
- [小米 piano 官方 Gunyah 驱动](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/tree/45fb9bd6ae5ba2942fc1d53e4b6b46ef76992f71/drivers/virt/gunyah)
- [完整 VMM 源码树](https://github.com/CodeLinaro-mirror/linux-kernel_gunyah-linux/tree/a73f08e03671eed3c2e3917637eacff764feef90/drivers/virt/gunyah)
- [公开 VM Manager 与 UAPI 补丁](https://lists.infradead.org/pipermail/linux-arm-kernel/2024-January/894121.html)
- [KVM 桥接系列及作者限制说明](https://patchew.org/linux/20250424141341.841734-1-karim.manaouil@linaro.org/)
- [配套 QEMU](https://github.com/karim-manaouil/qemu-for-gunyah)
- [librevm 的模块获取脚本](https://github.com/code002-2/librevm/blob/4be3e42b963e95a7753e600613a46059ebdfcd04/scripts/librevm-get-gunyah.sh)
- [librevm 的 virtio 实验记录](https://github.com/code002-2/librevm/blob/4be3e42b963e95a7753e600613a46059ebdfcd04/docs/virtio-on-gunyah.md)
