# 上游跟踪

所有构建消费的提交均锁定在 `sources.lock.json`。SunUEFI 跟正式 release：锁条目带 `tag`，`commit` 是该 tag 指向的提交，两次 release 之间落在 `main` 上的提交不跟。其余条目各锁在 `ref` 所指分支上的一个提交。`docs/upstreams.json` 登记全部上游：带 `lock_key` 的行与锁条目一一对应，其余是研究对照仓库，不参与构建。修改文档或查看最新上游动态不会改变已发布镜像的来源。

## 对照基准 pin

`sources.lock.json` 中两条 pin 仅用于 CI 对照任务、禁止进入任何出货构建路径：

- `vendor-kernel`（`android_kernel_xiaomi_piano`）：仅用于 `compare-vendor-dt.py` 核对设备树 overlay 节点。
- `vendor-blobs`（`android_vendor_xiaomi_piano`）：覆盖 148 个厂商仓库文件，仅与出货固件（含 `runtime/matrix-owned-paths.txt` 99 个路径）比对摘要。

`packages/xiaomi-piano-firmware` 在 `%prep` 校验基础 tarball 自带 99 项 `SHA256SUMS`；其上层通过 SunUEFI `config/release.json` 的 `firmware.overlays`（当前 10 份、109 个文件，来自 OS4.0.6.0.XPYCNXM，含 `hyperos4-{adsp,cdsp,fingerprint}`、`piano-display` 及相关外设固件）按清单 SHA256 落盘，由 `tests/check_firmware_overlays.py` 强制锁定与 `sunuefi` pin 一致。

## 自动漂移检查与换 Pin 原则

`.github/workflows/upstream-watch.yml` 每周一运行 `scripts/audit-upstreams.py --execute` 并调用 `scripts/upstream-watch.py`，拿锁定的 `commit` 与上游当前提交比较：带 `tag` 的条目比最新正式 release 的 tag 所指提交，其余比 `ref` 分支头。

- 白名单纯用户态组件（`crosvm`、`librevm`、`uperf`、`piano-mesa`、`piano-sensors`）发生漂移时，在 `upstream-drift` issue 中附带建议 `upstream-watch.py bump` 命令，由维护者审阅后手工更新。
- 内核、UEFI、相机/触控、固件与 `debian-piano` 以及非白名单的厂商树漂移仅开 issue 报告；`upstreams.json` 里不带 `lock_key` 的对照仓库不进这份报告，通过本地手工执行 `audit-upstreams.py --execute [--record-baseline]` 人工复核。

每次跟进上游变更前：完整比对上游具体 commit 与代码差异，复核受影响的补丁 `series.json`、构建容器、DTB/内存交接合同及全链重编产物；绝对不能混用旧二进制或直接照搬上游 16 GB 机器上的设备结论。

## `v1.0.0` 固件的设备验证范围

`sunuefi` 锁到 `v1.0.0`（`a6c321d0`）之后，这个 pin 的 UEFI 固件只在一台参考平板上换过。平板上是经 COPR 就地升级上来的 `denial` 档系统，没有用 `v45.11` 镜像重装。换 pin 后下表作废，按新固件重做。

| 验证面 | 已通过内容 | 尚未覆盖 |
| --- | --- | --- |
| 固件产物 | 这个 pin 上 `build-uefi` 产出的 `PianoUEFI-product.img`，SHA256 `b89b37056aa12ce50f43430d97a8a657b434d936f8cd326919895c529a4686e9`，与 `v45.11` 五档 `build-bundle` 记录的 `product_sha256` 相同；换固件用的是同一次构建的模块载荷，模块清单声明的 `product_sha256` 也是这个值 | 无 |
| 换固件（主机离线） | 对 `boot_b` 的备份执行 `piano-boot-repack upgrade`；换固件前后两份合体 BOOT 各自 `restore` 出的原厂 BOOT 逐字节相同 | 在 Android 里安装 `sunuefi-module.zip` 的路径，见[升级](upgrade.md#3-单独升级-uefi-固件与引导助手) |
| 写入 | 在 Linux 下直接写 `boot_b`，落盘并清页缓存后回读，SHA256 与换固件后的镜像相同 | 从原始 GPT 开始的首次安装 |
| 启动 | `systemctl reboot` 后回到 `denial` 会话，`uname -r` 为 `7.3.3-piano-sunuefi-cachy-optimize-b1795512178-g384787daa271`，与这个 pin 上 `build-kernel` 产物的 `kernel_release` 相同；`systemctl --failed` 为空 | 关机后的冷启动；其余四档 |
| 固件表 | `/sys/firmware/efi/systab` 有 `ACPI20` 项；`/sys/firmware/acpi/tables` 不存在，内核仍按 DTB 启动 | ACPI 表的内容 |
| 请求页 | 换固件保留了原有请求；启动后 `piano-next-boot status` 报 `target: 2`（Linux）、`sequence: 2`、`original_kernel_changed: false`，`boot_device` 指向 `boot_b` | 切到 Android 再切回的往返与原厂 Recovery |
| 只读冒烟 | 登录用户运行 `scripts/verify-device-smoke.py`，8 项通过 | 冒烟不看的相机出图、触控采样与视频硬解，见[真机取证脚本](internals.md#真机取证脚本) |

[PR 验收门槛](internals.md#pr-验收门槛)要求的首次分区安装、同一流水线镜像的冷装与 Android ↔ Linux 往返都不在上表范围内，这组结果不能记作通过真机设备验收。

## 核心补丁分叉与策略

本仓库内核补丁集（`patches/kernel/`）在封存上游 290 条补丁之后追加自有 36 条适配补丁。各份累积补丁依次改写 SunUEFI 的 `patches/linux/7.3/series.json`，重放完成后该文件的 `target_tree` 字段就是目标树；`patches/kernel/series.json` 记录的是每份补丁改写后各文件的 sha256，由 `apply-patches.py --check` 核对。其余 Fedora 临时重编包的移除条件记录于 `scripts/fedora-overlays.json`。主要策略包括：

- **内核与触控解码**：启用 LLVM 23.1.5 + ThinLTO + `-O3` + `CONFIG_ARM64_PIANO_OPTIMIZE`；跟进上游 `0399` / `0431` / `0432` 在内核 IRQ 线程做手指矩阵与笔解码，`/proc/nvt_thp_status` 输出 `kernel_input: enabled` 时由用户态 `piano-touch-view` 自动退避（两者仅在 BlueZ 笔初始化保留交互）。
- **电源与充电**：`0415`-`0422` 接入 4.0.6 循环充电、MCA/SC8541 FFC 与 PPS；GNOME PPD 通过 `piano-power-mode` 桥接到 `uperfctl` 而不直接改 sysfs 频点。
- **启动与固件补丁**：同步支持 BLS（`/linux/<release>/`）与单 UKI 布局，`EFI_DT_FIXUP_PROTOCOL` 启动期重写 `/memory`；`patches/uefi-ufs` 与 `patches/uefi-dwc3-halt` 补充 UFS 唤醒及 USB 复位容错。
- **桌面与命令行**：GSD 移植原厂 108 点 lux 曲线与启动锁屏状态读取；默认启动命令行（`patches/kernel/0001-linux-73-series.patch`）设置正整数 `deferred_probe_timeout` 缓解首次冷启 GPU 未绑定问题。

## 内存与面板边界

上游状态基线面向 16 GB 机型与 Debian/ext4；本仓库运行于 24 GB 机型与 F2FS。当前出货 DTB 描述的 16 段 RAM 总计 16,470,685,696 字节，启动期 `Raw Linux` 交接仅改写面板 compatible 与 cmdline/initrd 而不会自动发现新增物理内存。

装机/更新校验脚本会检查 Linux 描述的内存区间是否为当前 Android `/sys/firmware/fdt` 的子集，且与固定保留区无冲突。切勿手动放宽内存校验门槛；通过校验也仅证明交接安全，不等于全量 24 GB 已纳入实机压测范围。面板标识由原厂 ABL `msm_drm.dsi_display0` 下发，UEFI 据此在两个面板 profile（CSOT `42_02_0a` / BOE `35_02_0b`）间自动匹配。
