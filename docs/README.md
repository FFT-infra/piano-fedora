# Xiaomi Pad 8 Pro (piano) 文档中心

本项目文档按读者受众划分为三类：想装机的新用户、已经在使用的日常用户与参与维护的开发者。

---

## 想装机的人

面向准备在平板上安装 Fedora 的用户。包含硬件架构、首装准备、分区规划与镜像刷写。

- [系统架构](architecture.md)：Fedora、SunUEFI 与 Android 的边界与分区布局
- [安装与分区](installation.md)：首装准备、GPT 分区计算与检查、一键与手动刷写、首次登录
- [交付物与流水线](artifacts.md)：发布归档结构、分卷解压与离线材料校验标准

---

## 已经在用的人

面向已经进入 Fedora 系统、需要日常升级维护、配置桌面或排查故障的用户。

- [升级与回滚](upgrade.md)：已有分区的更新、内核 UKI 升级与回滚、双系统日常往返
- [COPR 软件源](copr-repository.md)：18 个包的在线升级链路、仓库优先级与失败回滚边界
- [桌面档位](desktops.md)：GNOME、KDE、niri、Hyprland 四档的会话配置、电源策略与可选组件
- [已知问题](known-issues.md)：未解决与观察中的问题目录与技术机理分析
- [Gunyah 虚拟化](virtualization.md)：SM8750 上的虚拟机生命周期与网络配置

---

## 参与维护的人

面向参与代码贡献、调试底层驱动、跟进上游变更或进行本地编译构建的维护者。

- [本地构建与验证](build.md)：编译工具清单、离线源码取源、构建顺序与换 pin 步骤
- [设备树与 23 个 Overlay](device-tree.md)：三条 DT 血缘、出货基线谱系与板级 Overlay 作用
- [维护与验收](internals.md)：CI 流水线入口、产物版本绑定与 PR 验收门槛
- [上游跟踪](upstream.md)：SunUEFI 与内核当前 pin、漂移看板与跟进规则
- [与上游的功能差距](upstream-parity.md)：截至 2026-11-12 对照 pin `3a7e5c1b` 的功能差距快照
- [公开分发前的缺口](public-release-gaps.md)：构建期仍绑定单机、单人输入的解耦清单
