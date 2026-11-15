# Xiaomi Pad 8 Pro (piano) 文档

## 想装机的人

- [系统架构](architecture.md)：Fedora、[SunUEFI](https://github.com/slwyts/Project-SunUEFI) 与 Android 的边界和分区布局
- [安装与分区](installation.md)：首装准备、分区计算、一键与手动刷写、首次登录
- [交付物与流水线](artifacts.md)：发布归档结构、分卷解压与文件校验

---

## 已经在用的人

- [升级与回滚](upgrade.md)：已有分区更新、内核 UKI 升级与回滚、双系统往返
- [COPR 软件源](copr-repository.md)：18 个包的在线升级、仓库优先级与回滚
- [桌面档位](desktops.md)：GNOME、KDE、niri、Hyprland 四档配置、电源与可选组件
- [已知问题](known-issues.md)：未解决与观察中的问题排障记录与硬件细节
- [Gunyah 虚拟化](virtualization.md)：SM8750 上的虚拟机运行与网络配置

---

## 参与维护的人

- [本地构建与验证](build.md)：构建依赖、源码准备、构建顺序与换 pin 步骤
- [设备树与 23 个 Overlay](device-tree.md)：三条 DT 来源、出货基线谱系与各 overlay 说明
- [维护与验收](internals.md)：CI 流水线、产物绑定与 PR 验收项
- [上游跟踪](upstream.md)：SunUEFI 与内核当前 pin、差异看板与跟进规则
- [与上游的功能差距](upstream-parity.md)：对照上游基线 pin `3a7e5c1b` 的功能差距与说明，当前构建 pin 见 `sources.lock.json`

---

## 上游与署名

本项目的启动链、内核编排、rootfs 组装与打包来自 [Project SunUEFI](https://github.com/slwyts/Project-SunUEFI)。
引用其代码、数据与结论的页面在首次提及处链接回上游。许可与第三方材料见 [NOTICE](../NOTICE) 与上游仓库的 `LICENSE.md`、`THIRD_PARTY.md`。
