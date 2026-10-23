# Gunyah 正式集成与 VM 验收

Gunyah 已进入标准 Fedora 内核构建链。构建链不再包含实验档输入或独立实验 job。
客体执行、磁盘和网络需要真机证据。内核编译成功不能代替 VM 验收。

## 正式构建链

| 环节 | 入口与合同 |
| --- | --- |
| 固定驱动 | `sources.lock.json` 的 `gunyah-driver`，commit `fd503a492d381687c143b7085ea79ebce58dcdc7`。32 个文件原样导入 |
| 源码准备 | `scripts/prepare-production-kernel.py` 重放 SunUEFI/Iris 后导入驱动。脚本不依赖已编译的基础内核 |
| 来源证明 | `production-source-manifest.json` 记录最终源码身份。`kernel-gunyah-source.json` 记录父树、导入文件和派生树 |
| 标准配置 | `--fedora-policy` 自动要求驱动源码并启用全部八项 Gunyah 配置。缺源码或配置不完整就失败 |
| 内核与 SDK | `build-kernel.yml` 标准 job 先配置驱动并单独编译，再构建整核。标准 SDK 携带派生证明 |
| 外部模块 | `prepare-runtime-kernel.py` 默认重放 Gunyah 派生证明，要求源码、配置、Module.symvers 与内核清单匹配 |
| 镜像与交付 | 只接受标准 kernel/runtime artifact。旧实验档一律拒绝，防止混装模块 |

八项内置配置包括 `GUNYAH`、`GUNYAH_HYPERCALLS`、`GUNYAH_TIMER`、`GUNYAH_PLATFORM_HOOKS`、`GUNYAH_QCOM_PLATFORM`、`GUNYAH_QCOM_TRUSTED_VM`、`GUNYAH_IRQFD` 与 `GUNYAH_IOEVENTFD`。
`check_gunyah_source.py`、`check_production_kernel.py` 与 `check_runtime_kernel_source.py` 校验固定源码、逐字节导入和重放一致性。三项脚本都纳入 `make test`。

补丁文件名 `0001-isolated-gunyah-profile.patch` 是历史名称。补丁内容已改为 Fedora 正式策略，不再包含 `--experimental-gunyah` 开关。

## 用户态

`piano-crosvm` 原生构建锁定的 crosvm，启用 `gunyah,net,balloon` feature。
`librevm` RPM 包含 CLI、daemon 和 GTK GUI，使用仓库内的 Fedora 适配与完整 `Cargo.lock`。
VM 配置默认包含 `disable_sandbox=false`。只有实际选择 Gunyah 时，命令行才追加 `--disable-sandbox`。锁定的 Gunyah crosvm 后端需要 `--disable-sandbox`，才能跑通 piano 的 `PAGE_FAULT exit`（reason 3）。
KVM 路径保留 sandbox。回归测试覆盖默认配置。
内置驱动的就绪判断检查 `/dev/gunyah` 和内核注册路径，不检查 `/proc/modules`。

`build-device-runtime.py` 自动构建并交付上述 RPM。
finalizer 校验源码 pin、适配与锁文件摘要，并校验 root 文件系统里实际安装的二进制字节。只有包名不算通过。
同一链路还交付 VA-API 与旋转、充电、schedutil 的平台配置。

## Gunyah RM capability 交接

`uefi-live-rm` 补丁修复 raw 交接里的一处输入。
payload DTB 的 `/hypervisor/qcom,resource-manager-rpc@…` 节点 `reg` 记录构建镜像时的 TX/RX capability。ABL 每次启动都重新分配 TX/RX capability，并通过 inherited FDT 下发当次的值。
驱动按 `compatible` 查找节点，并按两个 u64 读取 `reg`。
传入过期值时，hypervisor 判定首次 RM 请求为 ARG_INVAL。`gunyah_rm_msgq_send` 的许可永不返回，调用者停在 D 状态，`VM_START` 发不出去。

补丁在把 DTB 移交内核之前，用产品已校验的 inherited FDT 覆盖这 16 字节。
覆盖只在四个条件同时成立时执行：两棵树各自恰好有一个匹配节点，继承值正好 16 字节，两个 id 都非零，目标属性结构符合驱动读取要求。
节点缺失、匹配有歧义或读取失败时，补丁保留 payload 原值，并记录日志。
补丁不改节点名里的 unit-address，Linux 侧也不读这个字段。

验证分为两层。
主机侧提取待发布的 C 源码，搭配真实 libfdt（两个独立的 1.8.1 环境），对真实抓取的 payload DTB 与 inherited FDT 重放。重放后只有 RM `reg` 与 `uefi-live-memory` 的内存项发生变动。九条策略用例与三条反向改坏对照都符合预期。
实机结论只能由正常启动路径给出。验收状态见验收结果表格与 [已知问题](known-issues.md)。
`tests/check_uefi_live_rm.py` 在主机 `make test` 里使用假 FdtLib 编译同一段 C 代码，覆盖同一组接受与拒绝路径。

## 当前验收结果与剩余门槛

| 项目 | 当前结果 | 剩余工作 |
| --- | --- | --- |
| 平台接口 | 正式内核、`/dev/gunyah`、实时 RM capability 交接及 SELinux Enforcing 通过 | 后续内核更新后复测 |
| 客体执行 | LibreVM Auto 选中 Gunyah。单 vCPU VM_START 与客体正常退出通过 | 控制台交互体验仍需验收 |
| 数据路径 | virtio-blk 上 64 MiB ext4 写入和读回通过。virtio-net 连通通过（`NET_OK=1 NET_RX=2`，与宿主同网段伪桥，无 NAT） | 无 |
| 生命周期 | 三轮 disk+net 全通过，单轮无磁盘失败有界退出通过。每轮 crosvm 退出后进程、PID、socket 全清（0 hub、0 僵尸） | 无 |
| 管理器 | RPM、CLI、daemon、D-Bus 与安装二进制核验通过，KVM sandbox 回归通过 | GUI 实际创建与操作客体、完整 SELinux 流程待测；Gunyah 下自动关闭 crosvm sandbox 待验收 |
| 扩展 | — | SMP、UEFI、图形和 Incus 分别实现与验收，不由单 vCPU 结果推定 |

首次 RM 请求失败后卡在发送许可等待，属于修复前的记录。根本原因是构建期冻结的 RM capability。`uefi-live-rm` 补丁已在正常启动路径生效，已实测。
项目 guest kernel 完成 64 MiB ext4 写入和读回。VM 返回码为 0。进程资源清理通过。
旧实验产物、错误 guest kernel 的失败尝试和最终通过结果分开记录。
未完成的验收项见 [已知问题](known-issues.md)。

## 实读来源

- [Qualcomm v17 完整提案](https://lore.kernel.org/lkml/20240222-gunyah-v17-0-1e9da6763d38@quicinc.com/)
- [已适配主线 7.2 的 Gunyah 驱动分支](https://github.com/SuperTurtleDev/iceland-mainline-kernel-linux/tree/fd503a492d381687c143b7085ea79ebce58dcdc7/drivers/virt/gunyah)
- [librevm 对 Gunyah 驱动的打包与移植说明](https://github.com/code002-2/librevm/tree/9833d43c7caadfdfb8439306cd10641921d58e90/packaging/gunyah-driver-7.2)
- [小米 piano 官方 Gunyah 驱动](https://github.com/MiCode/Xiaomi_Kernel_OpenSource/tree/45fb9bd6ae5ba2942fc1d53e4b6b46ef76992f71/drivers/virt/gunyah)
- [完整 VMM 源码树](https://github.com/CodeLinaro-mirror/linux-kernel_gunyah-linux/tree/a73f08e03671eed3c2e3917637eacff764feef90/drivers/virt/gunyah)
- [公开 VM Manager 与 UAPI 补丁](https://lists.infradead.org/pipermail/linux-arm-kernel/2024-January/894121.html)
- [KVM 桥接系列及作者限制说明](https://patchew.org/linux/20250424141341.841734-1-karim.manaouil@linaro.org/)
- [配套 QEMU](https://github.com/karim-manaouil/qemu-for-gunyah)
- [librevm 的模块获取脚本](https://github.com/code002-2/librevm/blob/4be3e42b963e95a7753e600613a46059ebdfcd04/scripts/librevm-get-gunyah.sh)
- [librevm 的 virtio 实验记录](https://github.com/code002-2/librevm/blob/4be3e42b963e95a7753e600613a46059ebdfcd04/docs/virtio-on-gunyah.md)
