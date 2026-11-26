# piano-fedora Agent Guide

本文件是权威约定。改仓库前先读它。

## 项目

小米平板 8 Pro（piano / 25091RP04C / SM8750）的 Fedora + HyperOS 双系统构建仓库，Linux 根分区用 F2FS。SunUEFI 按 commit 锁定为上游。本仓库只带四样增量：Fedora 后端、F2FS 全链路、首次分区，以及 `patches/uefi-*` 下的 UEFI 启动补丁。

仓库地址是 `FFT-infra/piano-fedora`。补丁 `From:` 行与文档里的仓库名用这个名字，不是本地目录名。

## 仓库地图

每个事实只在一处定义，别处引用它。

| 路径 | 拥有的真源 | 说明 |
| --- | --- | --- |
| `sources.lock.json` | 上游是谁、锁定在哪个 commit | 只列实际消费的上游 |
| `product.json` | 要构建什么 | 发行版、文件系统、尺寸、分区标签 |
| `scripts/build-clean-rootfs.py` | Fedora 装配后端 | plan / execute / audit 三条路径 |
| `scripts/piano_desktop.py` | 桌面差异解释器 | 登录、服务、包集合、电源策略与产物名 |
| `scripts/release-assets.py` | Release 资产名 | 哪些共用、哪些按桌面加 `-<desktop>` |
| `scripts/profiles/` | 发行版与桌面配方 | base 包、dnf 选项、桌面包组与电源文件 |
| `scripts/package-sets.json` | 包集合与构建顺序 | 矩阵、运行时、图形栈、extras；各脚本与 workflow 都从这里派生 |
| `tests/` | 离线检查 | `make test` 跑全部 |
| `docs/README.md` | 文档索引 | 用户文档与维护参考的入口 |
| `.github/workflows/` | 主机侧 CI | 不接触设备 |

## 文档边界与写法

改文档前先查下表，只在拥有该主题的文档里写一次，其他文档用相对链接引用，不跨文件整段复写操作步骤。

| 文档 | 唯一负责的内容 | 不要往里塞的内容 |
| --- | --- | --- |
| `README.md` / `README.en.md` | 硬件支持表、桌面概览、快速安装入口、文档索引 | 详细分区算术、整套 CI 内部参数、长篇硬件调试日记 |
| `docs/installation.md` | 从零分区、手动与一键刷写、首次登录与安装排障 | 日常 `dnf upgrade` 流程、CI 内部 PR 验收细节 |
| `docs/upgrade.md` | `update-f2fs.py` 覆盖升级、内核升级与回滚、UEFI 模块更新、双系统切换 | 首次分区完整步骤 |
| `docs/desktops.md` | 五档桌面配置、登录管理器、电源策略、自动旋转、`extras` 概览 | COPR 发布流程与具体启用/降级命令行 |
| `docs/copr-repository.md` | `xinyang26/piano-fedora` 与 `extras` 的启用、装卸、包清单、内核 `%posttrans` | 桌面外观、旋转与电源行为细节 |
| `docs/known-issues.md` | 当前未解决的问题、硬件限制、明确边界与无害日志噪声 | 已真机验证修复的历史问题、固定八股模板 |
| `docs/artifacts.md` | 各阶段产物、manifest 绑定、`deploy-bundle` 与 Release 资产布局 | 刷机步骤、历史“待某步验证”占位话术 |
| `docs/architecture.md` | SunUEFI 与仓库分工、阶段规划、CI 工作流清单、系统约束 | 重复展开 `artifacts.md` 或 `installation.md` 细节 |
| `docs/build.md` | 本地取源、补丁校验、`make test`、CI 触发顺序、换 pin 步骤 | 用户装机指引 |
| `docs/internals.md` | CI 约束、真机取证脚本、SELinux 策略与 PR 验收门槛 | 再次抄写一遍 `installation.md` 的首次安装步骤 |
| `docs/device-tree.md` | 三条 DT 血缘、24 个 overlay、`compare-vendor-dt.py` oracle 检查 | 面板与内存策略全文 |
| `docs/upstream.md` / `docs/upstream-parity.md` | 上游跟踪规则、核心分叉决策、与 SunUEFI 的功能差距 | 已经进默认文档的常规用法教程 |
| `docs/virtualization.md` | Gunyah 内核构建、crosvm/librevm 用户态、RM capability 交接与验收结果 | 非虚拟化驱动细节 |

写文档按 GitHub 工程项目风格走：

- **面向用户的文档**（`README*`、`installation`、`upgrade`、`desktops`、`known-issues`）：只写读者动手或排错需要知道的事，注意事项紧跟在对应动作后面。不要套用死板模板（例如每节机械拆成“现象 / 影响 / 技术根因 / 应对方法 / 状态”或“怎么做 / 为什么这样限制”），问题直接说清现象、影响或绕过方式与当前状态即可。
- **工程参考文档**（`architecture`、`artifacts`、`build`、`internals`、`device-tree`、`upstream*`、`virtualization`）：多用短表格、命令块和精准路径，少写铺垫段落和修辞。
- **硬限制**：正文禁用全角/半角破折号（U+2014 与 U+2013）；不在现行文档里写版本流水账（如“此前某版本曾……后来改为……”）；声称“已真机验证”时必须写清实际验过的范围，未在真机跑过的只写构建或静态检查状态；不要在 `docs/known-issues.md` 写出补丁 `0001` 的 `deferred_probe_timeout` 具体数字（`tests/check_boot_cmdline.py` 会拦截）。
- **测试约束**：修改 `README.md` 与 `README.en.md` 须保持 H1-H3 标题层级和 `docs/` 下文档的链接顺序一致（`tests/check_readme_parity.py`）；修改 `docs/installation.md` 不得破坏手动分区与手动刷写的命令块及测试锚点（`tests/check_manual_partition.py`、`tests/check_manual_flash_flow.py`、`tests/check_flash_script_guard.py`）。`docs/` 下所有 Markdown 文件须从根 README 两击内可达（`tests/check_links.py`）。

## 硬规则

- 上游不 fork。SunUEFI 按 `sources.lock.json` 的 commit 取用；本仓库的改动走自有脚本或补丁。
- 桌面差异只出现在 `scripts/profiles/`、`scripts/piano_desktop.py` 与桌面运行时探测里。其他层（workflow、发布、安装器）只消费派生出来的名字与字段，不手写桌面列表。
- 固件、校准数据、密钥不入库。
- 证据分层。作者记录、构建通过、设备可用是三件事，不互相代替。
- 未定的值留 null 并让工具报错，不猜。现在的 `product.json` 里没有 null，规则是为将来留的。
- 目录跟着实际交付走。没有文件就不建目录。
- 没有生成文件。若将来引入，源与产物分开，产物不入库。唯一的例外是 `rootfs/selinux/piano_hardware.pp`：设备上的 `semodule -i` 加载的是编译后的模块，`.te` 本身不会被读取，所以它必须入库。改 `.te` 之后跑 `make selinux` 重新生成（需要 `checkpolicy` 与 `policycoreutils`），否则镜像里装的还是旧策略。

## 命令

```sh
make test                  # 全部离线检查（scripts/run-checks.py 自动发现 tests/check_*.py）
make test-device DEVICE_HOST=piano-usb   # 平板上跑只读冒烟，报告落在 SMOKE_OUTPUT
make plan RELEASEVER=44    # 打印 Fedora rootfs 构建计划（默认桌面档）
make plan-desktop DESKTOP=kde RELEASEVER=44   # 另一档的装配计划
make selinux               # 从 .te 重新生成 piano_hardware.pp
```

## 长构建

编译类 workflow（内核、UEFI、镜像、rootfs）动辄几十分钟到几小时，失败一次的代价是整轮重跑。因此**首次运行前必须先把该 workflow 完整审一遍**：

- 逐个核对它调用的每个外部命令在 runner 上是否存在，不靠运行时才发现缺项。
- 上游若自带构建容器或依赖清单（如 `containers/Dockerfile`、`requirements-build.txt`），优先用它，不手工逐条补包。
- 核对产物路径、权限（容器以 root 写入时需要 sudo）、以及跨 run 取 artifact 的 `run-id`。
- 核对每个输入是否真的在仓库或上游里可取，特别是 vendor 目录与子模块。

审完再触发，并把审查发现写进提交信息。

## 验证

| 改了什么 | 跑什么 |
| --- | --- |
| `scripts/` | `make test` |
| `product.json`、`sources.lock.json` | `make test` |
| `docs/`、`README.md`、`README.en.md` | `make test`（含链接、README 对齐与手册命令检查） |
| `.github/workflows/` | `make test` |

声称某件事通过时，附上本轮的命令与输出。没跑的检查标为未跑，不写成通过。

## 提交

格式 `{type}: {description}`，type 取 `feat`、`fix`、`refactor`、`docs`、`chore`。

## 注释

少写注释，用命名、类型与结构表达意图。

- 写代码说不出的事：外部协议约束、硬件或上游行为、为什么不能用更直白的写法。
- 不写逐句翻译、修复流水、变更历史，也不写调用者一眼能读出的前置条件。
- 本仓库的注释会被后续所有 agent 当范例复制。一条解释坏设计的注释，会让这个设计再活十轮。

规格、理由与变更记录放在提交信息、`docs/` 或包的 changelog 里，不塞进源码。

## 表达

仓库文档用中文。`README.en.md` 是 README 的英文版，两版由 `tests/check_readme_parity.py` 锁定同一骨架，改一版就要同步另一版。

- 先写结论，再写做法。
- 不加粗打光，一段最多一个粗体。
- 不写“值得注意的是”“由此可见”“首先其次最后”“不是 A 而是 B”。
- 一句一个动作，条件紧跟动作。长句拆开，不用分号串联三件事。
- 中英文之间、中文与数字之间留一个空格（盘古之白）。
- 一个事实只写一处，别处引用。
