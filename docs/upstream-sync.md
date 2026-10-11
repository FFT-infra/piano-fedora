# 上游更新与产物边界

构建来源由 sources.lock.json 固定。上游 main 的进展用于审阅；更新文档不改变已构建镜像的来源，也不使旧二进制自动获得新实现。

## 本轮核对

2026-10-11 22:14（Asia/Shanghai），GitHub API 与 git ls-remote 对 main 的结果一致：`964459705190fb7b83809e5b059e90b9718f619d`，领先本仓库 pin `6a8763d010adb00b18911d6d12e6dd02869f98b9` 三个提交。

| 上游提交 | 内容 | 本仓库处理 |
| --- | --- | --- |
| 1b3c538 | piano 默认登录密码 | Fedora finalizer 采用相同的文档化初始密码，保留自动登录与 root 密码锁定；不把修改 pin 当作 Fedora 已实现 |
| d8f5026 | 从 ECR Public 拉取同一摘要的官方 Debian 基础镜像 | 属于构建下载来源；目前 UEFI 二进制和原编译容器不变，后续改 pin 时连同官方 Dockerfile 审查 |
| 9644597 | HMCL 的 JavaFX 加速说明 | 可选应用说明，不表示本项目已安装或测试 HMCL |

本轮没有发现启动核心、Linux DTB、内核或 Recovery 合同的新改动，因此仍保留当前构建 pin 与配套 UEFI。新部署包必须记录 UEFI run、生产提交、实际 checkout、项目补丁和产品摘要，并逐项核对，不能只选择“最新成功”文件。

同轮查询确认 linux-piano 的 piano-7.2.6 分支，以及 piano-firmware、piano-mesa、debian-piano、piano-sensors、piano-mipps-auth 的 main 均与 sources.lock.json 中对应 pin 一致；没有据此变更内核或设备运行时输入。

来源：[固定上游与当前 main 的完整比较](https://github.com/slwyts/Project-SunUEFI/compare/6a8763d010adb00b18911d6d12e6dd02869f98b9...964459705190fb7b83809e5b059e90b9718f619d)、[上游登录策略](https://github.com/slwyts/Project-SunUEFI/blob/964459705190fb7b83809e5b059e90b9718f619d/config/release.json)、[构建说明](https://github.com/slwyts/Project-SunUEFI/blob/964459705190fb7b83809e5b059e90b9718f619d/docs/devel/building.md)。

## 每次跟进上游

1. 先读取远端 main 的完整提交号，并与当前 pin 比较提交、文件和实际代码；发布说明只作索引。
2. 启动、交接、内存、DTB、模块或硬件准备有变时，重读相关合同及消费者，检查补丁 series 的 base 与目标摘要；不直接替换某一件旧产物。
3. 修改 pin 前核对所有消费路径、构建容器和依赖；必须重新生成受影响的内核、运行时、root、initramfs、ESP、BOOT 配套与清单。
4. 下载并核对实际产物，更新部署入口。构建、文件验证和实机行为分别记账，不能继承上游另一台设备的结果。

## 本机内存与面板

锁定上游的状态页只覆盖 16 GB、另一版 HyperOS、Debian/ext4 的设备；本机是改装 24 GB、Fedora/F2FS，不能沿用其全部设备结论。

当前 Linux DTB 的 16 个非零 RAM 区间合计 16,470,685,696 字节。Raw Linux 交接修改面板 compatible、启动参数和 initrd 地址，保留输入内存描述；自动面板选择不等于自动发现 RAM。上线后的只读更新计划必须验证 Linux 描述的 RAM 是本次 Android FDT 的子集，并且当前固定保留区与 Linux RAM 相交的部分仍受保护。比较失败时拒绝更新，不猜测扩容后的地址或放宽内存门槛。比较通过仍不证明物理 DDR 所有权或完整 24 GB 可用。

来源：[上游设备覆盖范围](https://github.com/slwyts/Project-SunUEFI/blob/6a8763d010adb00b18911d6d12e6dd02869f98b9/docs/status.md)、[面板选择合同](https://github.com/slwyts/Project-SunUEFI/blob/6a8763d010adb00b18911d6d12e6dd02869f98b9/docs/devel/panel-selection.md)、[实际 Raw Linux 交接](https://github.com/slwyts/Project-SunUEFI/blob/6a8763d010adb00b18911d6d12e6dd02869f98b9/uefi/core/PianoRawLinuxBoot.c)。
