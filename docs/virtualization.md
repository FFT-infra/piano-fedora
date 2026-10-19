# Gunyah 正式集成与 VM 验收

Gunyah 已进入标准 Fedora 内核构建链，不再提供实验档输入或独立实验 job。
客体执行、磁盘和网络需要真机证据，内核编译成功不能代替 VM 验收。

## 正式构建链

| 环节 | 入口与合同 |
| --- | --- |
| 固定驱动 | `sources.lock.json` 的 `gunyah-driver`，commit `fd503a492d381687c143b7085ea79ebce58dcdc7`；32 个文件原样导入 |
| 源码准备 | `scripts/prepare-production-kernel.py` 重放 SunUEFI/Iris 后导入驱动；不依赖一份已编译的基础内核 |
| 来源证明 | `production-source-manifest.json` 记录最终源码身份；`kernel-gunyah-source.json` 记录父树、导入文件和派生树 |
| 标准配置 | `--fedora-policy` 自动要求驱动源码并启用全部八项 Gunyah 配置；缺源码或配置不完整即失败 |
| 内核与 SDK | `build-kernel.yml` 标准 job 先配置与单编驱动，再整核构建；标准 SDK 携带派生证明 |
| 外部模块 | `prepare-runtime-kernel.py` 默认重放 Gunyah 派生证明，要求源码、配置、Module.symvers 与内核清单匹配 |
| 镜像与交付 | 只接受标准 kernel/runtime artifact，拒绝旧实验档，防止混装模块 |

八项内置配置为 `GUNYAH`、`GUNYAH_HYPERCALLS`、`GUNYAH_TIMER`、
`GUNYAH_PLATFORM_HOOKS`、`GUNYAH_QCOM_PLATFORM`、`GUNYAH_QCOM_TRUSTED_VM`、
`GUNYAH_IRQFD` 与 `GUNYAH_IOEVENTFD`。固定源码、逐字节导入和重放一致性由
`check_gunyah_source.py`、`check_production_kernel.py`、`check_runtime_kernel_source.py`
覆盖，均纳入 `make test`。

补丁文件名 `0001-isolated-gunyah-profile.patch` 是历史名称，其内容已改成
Fedora 正式策略，未保留 `--experimental-gunyah` 开关。

## 用户态

`piano-crosvm` 原生构建锁定的 crosvm，使用 `gunyah,net,balloon` feature；
`librevm` RPM 包含 CLI、daemon 和 GTK GUI，消费仓库内的 Fedora 适配与完整
Cargo.lock。VM 配置默认 `disable_sandbox=false`；命令构造只在实际选择 Gunyah
时追加 `--disable-sandbox`。这是当前锁定的 Gunyah crosvm 后端对本目标
PAGE_FAULT exit（reason 3）的运行要求。KVM 路径保留 sandbox，回归测试覆盖
默认配置。内置驱动的就绪判断检查 `/dev/gunyah` 和内核注册路径，不依赖
`/proc/modules`。

`build-device-runtime.py` 自动构建并交付上述 RPM。finalizer 校验源码 pin、
适配和锁文件摘要，以及 rootfs 实际安装的二进制字节；仅存在包名不能通过。
同一链路还交付 VA-API 与旋转/充电/schedutil 平台配置。

## Gunyah RM capability 交接

`uefi-live-rm` 补丁修的是 raw 交接里的一处输入：payload DTB 的
`/hypervisor/qcom,resource-manager-rpc@…` 节点 `reg` 是构建镜像时那一轮启动的
TX/RX capability，ABL 每次启动重新分配并通过 inherited FDT 下发当次值。
驱动按 `compatible` 找节点、按两个 u64 读 `reg`，所以过期值的表现是第一次
RM 请求被 hypervisor 判 ARG_INVAL、`gunyah_rm_msgq_send` 的许可永不返回，
调用者停在 D 状态，`VM_START` 永远发不出去。

补丁在把 DTB 交给内核之前，用产品已经校验过的 inherited FDT 覆盖这 16 字节，
并且只在「两棵树各自恰好一个匹配节点、继承值正好 16 字节且两个 id 都非零、
目标属性形状就是驱动读的那种」时才改；任何缺失、歧义或读不动都保留 payload
原值并记日志。节点名里的 unit-address 不动，Linux 侧不读它。

验证分两层：主机侧把要发货的 C 原文抽出来配真实 libfdt（两个独立 1.8.1
环境）对真实抓取的 payload DTB 与 inherited FDT 重放，只有 RM `reg` 与
`uefi-live-memory` 的内存项发生变化，九条策略用例与三条反向改坏对照都符合
预期；实机结论必须由正常启动路径给出，见
[设备验收](device-acceptance-20261015.md)。`tests/check_uefi_live_rm.py`
在主机 `make test` 里用假 FdtLib 编译同一段 C，覆盖同一组接受/拒绝路径。

## 2026-10-15 构建与部署证据

Bridge 完整编译正式内核 `7.2.9-piano-gnome-gaf0eac10c933`，源码 commit
`af0eac10c933cd21e130a39d954f8d271e54e7c1`，tree
`c105213de7cee43813e79ae3332e2745626422d6`。Image SHA256 为
`ab5429321fd4736a82c133defb10998b48c31f53a54bd958feafe1f385a1e5a5`，
内核清单 SHA256 为
`3c41bd00b4933259dd3993090943929a88fd6b9c167e07bc0a96b22676a316bb`。
八项 Gunyah 配置预检、驱动单编、整核构建和 1639 个模块的 vermagic 检查通过。

匹配的 v4l2loopback 用同一 config/Module.symvers 与 clang 19.1.7 构建。
ESP 更新已逐文件读回验证，board.dtb 与持久 BOOT 未变；旧内核、模块和完整
ESP 回滚包保留。启动与 VM 结果见[设备验收](device-acceptance-20261015.md)。

平板原生构建的 VA-API 测试 14 项通过，LibreVM core/CLI 共 57 项测试通过，
crosvm Gunyah 及 LibreVM 完整 workspace release 编译通过。四个运行时 RPM
已安装，旧的未受 RPM 管理的 LibreVM 启动器和宽松调试 override 已清理。
新 LibreVM RPM 通过包校验；daemon active、D-Bus 名称可见，`librevm info` 识别
Gunyah，CLI、daemon、GUI 的安装摘要与本轮构建产物一致。

## 当前验收结果与剩余门槛

| 项目 | 当前结果 | 剩余工作 |
| --- | --- | --- |
| 平台接口 | 正式内核、`/dev/gunyah`、实时 RM capability 交接及 SELinux Enforcing 通过 | 后续内核更新后复测 |
| 客体执行 | LibreVM Auto 选中 Gunyah；单 vCPU VM_START、客体正常退出通过 | 控制台交互体验仍需验收 |
| 数据路径 | virtio-blk 上 64 MiB ext4 写入和读回通过；virtio-net 连通通过（`NET_OK=1 NET_RX=2`，与宿主同网段伪桥，无 NAT） | 无 |
| 生命周期 | 三轮 disk+net 全通过、单轮无磁盘失败有界退出通过；每轮 crosvm 退出后进程、PID、socket 全清（0 hub、0 僵尸） | 无 |
| 管理器 | RPM、CLI、daemon、D-Bus 与安装二进制核验通过；KVM sandbox 回归通过 | GUI 实际创建/操作客体及完整 SELinux 流程待测；Gunyah 自动关闭 crosvm sandbox |
| 扩展 | — | SMP、UEFI、图形和 Incus 分别实现与验收，不由单 vCPU 结果推定 |

首次 RM 请求失败后的发送许可等待属于修复前记录，根因是构建期冻结的 RM
capability；`uefi-live-rm` 补丁已在正常启动路径生效。正确的项目 guest kernel
完成 64 MiB ext4 写入和读回，VM 返回码为 0，进程资源清理通过。旧实验产物、
错误 guest kernel 的失败尝试与最终通过结果应分开看；完整设备证据见
[设备验收](device-acceptance-20261015.md)。

## 2026-10-16 修复：virtio-net、失败有界退出与 hub 残留

### 客体网络

初始 ramdisk 把客体地址硬编码为 `10.9.0.2/24`、网关 `10.9.0.1`，而
librevm 的 tap 直接使用宿主真实网段（伪桥，无 NAT），因此 ping 必然失败。
`runtime/vm-guest/vmaccept-init` 现在从内核命令行读取 `vmaccept.guest=` 与
`vmaccept.gw=`，缺省仍是 `10.9.0.x`，独立 harness 行为不变。实测
`vmaccept.guest=192.0.2.201 vmaccept.gw=192.0.2.1` 时
`NET_OK=1 NET_RX=2`，磁盘摘要与 nonce 同时通过。

### VM 生命周期残留（本轮修复）

多轮回归暴露出两个真实缺陷，均已修复并复验：

1. **hub 进程不退出。** `hub::run` 先等控制台 EOF 再 `child.wait()`；只要还有
   任何写端持有该 socket，reader 就永不返回，于是 crosvm 已死成僵尸、hub 却
   一直活着，pid 文件还被看门狗线程反复重写。现在父进程的退出是唯一退出
   信号：先 `child.wait()`，用克隆句柄 `shutdown(Both)` 打断 reader，再 join、
   删除 pid 文件与 socket。
2. **daemon 不回收 hub。** `hub::spawn` 用 `Command::spawn` 启动 detach 的 hub
   后从不 `wait`，每停一台机器就留下一个 `[librevm] <defunct>`。现在 spawn
   之后由专用线程 `wait` 回收。

复验（内核 `7.2.9-piano-gnome-g8c0e589d8829`，2026-10-16）：三轮
disk+net（每轮约 6 s）与一轮无磁盘（12 s，有界失败）全部按预期结束，
`hub procs 0 / zombies 0 / crosvm 0 / pid files 0 / sockets 0`。
修复后的二进制摘要：`librevm` `dd221b56…`、`librevm-daemon` `ce9086d3…`、
`librevm-gui` `cc9500f9…`；证据在
`/home/piano/integration-20261015/stability-verify/librevm-fix-20261016/`。

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
