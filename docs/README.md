# Xiaomi Pad 8 Pro (piano) 文档中心

按使用场景查找对应文档。每份文档只负责各自范围，避免跨页重复描述。

## 装机与日常使用

- [安装与分区](installation.md)：首次分区的两种方式、手动与一键刷写、SSH 公钥注入与常见故障排查
- [升级与回滚](upgrade.md)：`dnf upgrade` 升级内核与 COPR 包、`update-f2fs.py` 覆盖升级、UEFI 模块更新与双系统往返
- [桌面档位](desktops.md)：GNOME、KDE、niri、Hyprland、Denial 的会话登录、电源策略、自动旋转与 `extras` 说明
- [COPR 软件源](copr-repository.md)：20 个维护包与 `extras` 源的启用、降级回滚及内核 `%posttrans` 说明
- [已知问题](known-issues.md)：未解决限制、硬件工作边界与可忽略的开机错误日志

## 构建与系统参考

- [系统架构](architecture.md)：SunUEFI 与本仓库的分工、阶段边界、CI 工作流表与设备前提
- [交付物与流水线](artifacts.md)：`deploy-bundle` 与 Release 归档结构、F2FS 校验与清单绑定规则
- [本地构建与验证](build.md)：主机依赖、取源、`apply-patches.py --check`、`make test`、出货顺序与换 pin 步骤
- [维护与验收](internals.md)：Actions 触发门禁、真机取证脚本、SELinux 策略与 PR 验收门槛
- [设备树与 Overlay](device-tree.md)：三条 DT 血缘、24 个 overlay 与 `compare-vendor-dt.py` 对照检查
- [上游跟踪](upstream.md)：`sources.lock.json` 规则、`upstream-watch` 看板、补丁分叉与 16 GB/24 GB 内存边界
- [与上游的功能差距](upstream-parity.md)：与 Project SunUEFI 的子系统对比、架构分歧决策与基线核对命令
- [Gunyah 虚拟化](virtualization.md)：SM8750 Gunyah 内核、crosvm/librevm、RM capability 交接与实机验收范围

## 上游与署名

启动链、内核打包、设备树 overlay 与基础 UEFI 补丁源自 [Project SunUEFI](https://github.com/slwyts/Project-SunUEFI)。许可与第三方声明见 [NOTICE](../NOTICE) 以及上游仓库的 `LICENSE.md`、`THIRD_PARTY.md`。
