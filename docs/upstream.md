# 上游跟踪

构建来源由 `sources.lock.json` 固定。上游 main 的进展只用于审阅：更新文档不改变已构建镜像的来源，旧二进制也不会自动获得新实现。

带日期的核对记录留在本地日志，不随仓库发布。

## 当前 pin

构建消费的上游以 `sources.lock.json` 为准，当前 `sunuefi` pin 是 `50f29a92a9f43a96e0e8aaf28e0fdbd5a7c64c73`。其他消费源的 commit 也记在 `sources.lock.json`；研究与对照仓库登记在 `upstreams.json`，不参与构建。

## 自动跟踪

`.github/workflows/upstream-watch.yml` 每周一 03:23 UTC 跑一次
`scripts/audit-upstreams.py --execute`，结果交给 `scripts/upstream-watch.py` 处理：

- 锁定 ref 指向的提交与 `sources.lock.json` 的 pin 不同就算漂移，汇总进一个 `upstream-drift` issue；下次没有漂移就关掉该 issue。
- 白名单（`crosvm`、`librevm`、`uperf`、`piano-mesa`、`piano-sensors`、
  `piano-mipps-auth`）里的漂移会另开一个 draft PR，只改 `sources.lock.json` 的
  `commit`，`ref` 不动。这些是纯用户态上游，`make test` 与 CI 能兜住。PR 由
  `GITHUB_TOKEN` 创建，GitHub 不会为它自动跑 workflow，合并前要手动触发一次
  `host-check`。
- 内核、UEFI、相机/触控、固件和 debian-piano 只报告。这些改动没法用离线 CI 代替真机验收，pin 由人按下面的“每次跟进上游”评审。

判断漂移只看 pin，不看 `docs/upstreams.json` 的 `reference_commit`。后者是人工基线，只在 `--record-baseline` 时推进；用它判断会把没有位移的上游报成更新。

## 每次跟进上游

1. 先读远端 main 的完整提交号，与当前 pin 比较提交、文件和实际代码。发布说明只作索引。
2. 启动、交接、内存、DTB、模块或硬件准备有变时，重读相关合同与消费者，检查补丁 series 的 base 和目标摘要。不要直接替换某一件旧产物。
3. 改 pin 之前核对所有消费路径、构建容器和依赖。受影响的内核、运行时、root、initramfs、ESP 和 BOOT 必须重新生成并配套清单。
4. 下载并核对实际产物，更新部署入口。构建、文件验证和实机行为分别记账，不能继承上游另一台设备的结果。

## 内存与面板边界

锁定上游的状态页只覆盖 16 GB、另一版 HyperOS、Debian/ext4 的设备。改装到 24 GB 的 Fedora/F2FS 机器不能照搬它的全部设备结论。

当前 Linux DTB 的 16 个非零 RAM 区间合计 16,470,685,696 字节。Raw Linux 交接修改面板 compatible、启动参数和 initrd 地址，保留输入内存描述；自动选面板不等于自动发现 RAM。上线后的只读更新计划必须验证 Linux 描述的 RAM 是当次 Android FDT 的子集，并且固定保留区与 Linux RAM 相交的部分仍受保护。比较失败就拒绝更新，不要猜扩容后的地址，也不要放宽内存门槛。比较通过也不证明物理 DDR 所有权或完整 24 GB 可用。

来源：[上游设备覆盖范围](https://github.com/slwyts/Project-SunUEFI/blob/6a8763d010adb00b18911d6d12e6dd02869f98b9/docs/status.md)、[面板选择合同](https://github.com/slwyts/Project-SunUEFI/blob/6a8763d010adb00b18911d6d12e6dd02869f98b9/docs/devel/panel-selection.md)、[实际 Raw Linux 交接](https://github.com/slwyts/Project-SunUEFI/blob/6a8763d010adb00b18911d6d12e6dd02869f98b9/uefi/core/PianoRawLinuxBoot.c)。
