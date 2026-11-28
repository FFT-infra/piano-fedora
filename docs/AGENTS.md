# 文档编写与分工约定

> 本文件仅在编写或重构 `README.md`、`README.en.md` 与 `docs/` 下文档时读取，不写入纯代码改动的默认上下文。

## 唯一归属表

一个主题只在指定文档完整写一次，其他文档用相对链接引用，不跨文件整段复写操作步骤。

| 文档 | 唯一负责的内容 | 不要往里塞的内容 |
| --- | --- | --- |
| `README.md` / `README.en.md` | 硬件支持表、桌面概览、快速安装入口、文档索引 | 详细分区算术、CI 内部参数、硬件排障长文 |
| `docs/README.md` | 文档索引，每份文档一行 | 正文内容、会跟着改动变的数字 |
| `docs/installation.md` | 从零分区、手动与一键刷写、首次登录与安装排障 | 日常 `dnf upgrade`、CI 内部 PR 验收明细 |
| `docs/upgrade.md` | `update-f2fs.py` 覆盖升级、内核升级与回滚、UEFI 模块、双系统往返 | 首次分区完整步骤 |
| `docs/desktops.md` | 五档配置、登录管理器、电源策略、自动旋转、`extras` 概览 | COPR 发布流程与具体启用/降级命令行 |
| `docs/copr-repository.md` | `piano-fedora` 与 `extras` 的装卸、包清单、内核 `%posttrans` | 桌面外观与旋转行为细节 |
| `docs/known-issues.md` | 当前未解决限制、硬件工作边界与可忽略日志噪声 | 已真机验证修复的历史问题、固定标签模板 |
| `docs/artifacts.md` | 构建阶段产物、manifest 绑定、`deploy-bundle` 与 Release 资产布局 | 刷机步骤、过时验证占位表述 |
| `docs/architecture.md` | SunUEFI 与仓库分工、阶段边界、CI 工作流表、设备前提 | 重复展开 `artifacts.md` 或 `installation.md` |
| `docs/build.md` | 本地取源、补丁校验、`make test`、CI 触发顺序、换 pin 步骤 | 用户装机指引 |
| `docs/internals.md` | CI 入口约束、真机取证脚本、SELinux 策略与 PR 验收门槛 | 再次抄写 `installation.md` 首装步骤 |
| `docs/device-tree.md` | 三条 DT 血缘、板级 overlay 清单、`compare-vendor-dt.py` 对照检查 | 面板与内存边界全文 |
| `docs/upstream.md` / `upstream-parity.md` | 上游跟踪规则、核心分叉决策、与 SunUEFI 的功能差距 | 常规用户使用教程 |
| `docs/virtualization.md` | Gunyah 内核构建、crosvm/librevm、RM capability 交接与实机验收 | 非虚拟化驱动细节 |

## 写作边界

- **用户文档**（`README*`、`installation`、`upgrade`、`desktops`、`known-issues`）：先写结论与命令，注意事项紧跟在动作后面，面向实际装机排障用户，避免内部流水线黑话。
- **工程参考**（`architecture`、`artifacts`、`build`、`internals`、`device-tree`、`upstream*`、`virtualization`）：多用表格和精确命令路径，少铺垫，不跨页复制已有段落。
- **排版**：中文与英文、中文与数字之间留空格。
- **禁区**：
  - 不用死板问题卡片模板（如机械拆成「现象 / 影响 / 技术根因 / 应对方法 / 状态」或「怎么做 / 为什么这样限制」）。
  - 不用 AI 腔过渡词（“值得注意的是”“由此可见”“首先其次最后”“不是 A 而是 B”），正文禁用全角/半角破折号（U+2014 与 U+2013），不写版本流水账（“此前曾……后来改为……”）。
  - 不虚构真机结论：只写实际验证过的硬件与范围；`docs/known-issues.md` 禁止直接出现补丁 `0001` 的 `deferred_probe_timeout` 具体数值。

## 测试锁定约束

改完文档后运行 `make test`，注意以下自动化断言：

- `tests/check_readme_parity.py`：中英文 README 的 H1-H3 标题层级和 `docs/` 链接文件顺序必须严格一致。
- `tests/check_links.py`：`docs/` 下所有 Markdown 文件不得为孤儿，且必须从根 `README.md` 与 `README.en.md` 两击内可达。
- `tests/check_manual_partition.py` / `check_manual_flash_flow.py` / `check_flash_script_guard.py` / `check_first_boot_ssh_key.py`：`docs/installation.md` 中的手动分区与手动刷写代码块会被直接提取或对桩 `fastboot` 执行，不可改动测试约定的节标题（如 `## 四、 ...`、`## 五、 ...`、`## 六、 一键脚本`、`### 2. 手动分区`）及 `-S 128M` / 校验命令顺序。
