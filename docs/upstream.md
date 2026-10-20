# 上游跟踪

构建来源由 `sources.lock.json` 固定。上游 main 的进展只用于审阅。更新文档不改变已构建镜像的来源，旧二进制也不会自动获得新实现。

带日期的核对记录留在本地日志，不随仓库发布。

## 当前 pin

构建消费的上游以 `sources.lock.json` 为准，当前 pin 的 commit 写在该文件里，本文不复述。其他消费源的 commit 也记在 `sources.lock.json`。研究与对照仓库登记在 `upstreams.json`，不参与构建。

## 自动跟踪

`.github/workflows/upstream-watch.yml` 每周一 03:23 UTC 运行一次 `scripts/audit-upstreams.py --execute`。执行结果由 `scripts/upstream-watch.py` 处理。处理流程按以下规则分流：

- 锁定 ref 指向的提交与 `sources.lock.json` 的 pin 不同时，算作漂移。漂移汇总进一个 `upstream-drift` issue。下次运行若没有漂移，关闭该 issue。
- 白名单（`crosvm`、`librevm`、`uperf`、`piano-mesa`、`piano-sensors`、`piano-mipps-auth`）里的漂移另开一个 draft PR。该 PR 仅修改 `sources.lock.json` 的 `commit`。`ref` 保持不动。这些项目属于纯用户态上游。`make test` 与 CI 能够覆盖检查。PR 由 `GITHUB_TOKEN` 创建。GitHub 不会为该 PR 自动运行 workflow。合并前需手动触发一次 `host-check`。
- 内核、UEFI、相机/触控、固件和 debian-piano 只作报告。离线 CI 无法代替真机验收此类改动。人工按后文“每次跟进上游”的规则评审 pin。

漂移判断只看 pin，不看 `docs/upstreams.json` 的 `reference_commit`。`reference_commit` 是人工基线，只在 `--record-baseline` 时推进。拿它判断，会把没有位移的上游报成更新。

## 每次跟进上游

1. 读取远端 main 的完整提交号。比对当前 pin 与远端的提交、文件和实际代码。发布说明仅作索引。
2. 启动、交接、内存、DTB、模块或硬件准备有变时，重读相关合同与消费者。检查补丁 series 的 base 和目标摘要。不要直接替换某一件旧产物。
3. 改 pin 之前核对所有消费路径、构建容器和依赖。重新生成受影响的内核、运行时、root、initramfs、ESP 和 BOOT。清单同步更新。
4. 下载并核对实际产物，更新部署入口。构建、文件验证和实机行为分别记账，不能继承上游另一台设备的结果。

## 内存与面板边界

锁定上游的状态页只覆盖 16 GB、另一版 HyperOS、Debian/ext4 的设备。改装到 24 GB 的 Fedora/F2FS 机器不能照搬它的全部设备结论。

当前 Linux DTB 的 16 个非零 RAM 区间合计 16,470,685,696 字节。Raw Linux 交接修改面板 compatible、启动参数和 initrd 地址。交接保留输入的内存描述。自动选面板不会自动发现 RAM。

上线后的只读更新计划要验两件事：Linux 描述的 RAM 是当次 Android FDT 的子集，固定保留区与 Linux RAM 相交的部分仍受保护。比较失败就拒绝更新。不要猜扩容后的地址，也不要放宽内存门槛。比较通过也不证明物理 DDR 所有权或完整 24 GB 可用。

来源：[上游设备覆盖范围](https://github.com/slwyts/Project-SunUEFI/blob/50f29a92a9f43a96e0e8aaf28e0fdbd5a7c64c73/docs/status.md)、[面板选择合同](https://github.com/slwyts/Project-SunUEFI/blob/50f29a92a9f43a96e0e8aaf28e0fdbd5a7c64c73/docs/devel/panel-selection.md)、[实际 Raw Linux 交接](https://github.com/slwyts/Project-SunUEFI/blob/50f29a92a9f43a96e0e8aaf28e0fdbd5a7c64c73/uefi/core/PianoRawLinuxBoot.c)。
