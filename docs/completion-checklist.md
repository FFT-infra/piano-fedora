# 交付完成清单

当前可启动 Fedora 44/GNOME/F2FS，Gunyah 已纳入正式内核并在真机启动。
项目尚未完成全部验收；下表区分代码、构建和设备结果，详细证据统一见
[2026-10-15 设备验收](device-acceptance-20261015.md)。
本轮新上游集成的构建与待验收状态见[集成记录](integration-20261015.md)。

| 项目 | 当前证据 | 尚需完成 |
| --- | --- | --- |
| 系统与分区 | F2FS 根、ESP、GNOME、SELinux Enforcing 已启动；黑屏根因已定位为会话总线子包缺失，设备热修复后进入桌面 | **从原始 GPT/UFS 布局开始的完整 PR 冷启动验收**、Recovery 与完整恢复演练 |
| 正式内核 | Iris + 八项 built-in Gunyah 整核构建、模块核对、真机重启通过 | 更新后的标准 CI 与完整交付包重建 |
| Gunyah | 正式内核、/dev/gunyah、RM 实时交接、单 vCPU VM_START 与正常退出通过 | 控制台交互体验；后续内核更新后复测 |
| VM 数据与生命周期 | 64 MiB ext4 写入/读回通过；virtio-net 打通（`NET_OK=1 NET_RX=2`）；三轮 disk+net 与一轮有界失败退出通过；每轮无 hub 残留、无僵尸、PID/socket 全清 | 无（GUI 交互待人手操作） |
| VM 扩展 | 源码与接口研究已有记录 | SMP、客体 UEFI、图形、Incus 各自实现与验收 |
| 管理器 | 新 RPM 校验安装，CLI/daemon/GUI 摘要与构建产物一致，D-Bus 可见；KVM 默认 sandbox 回归通过 | Gunyah 路径按后端限制自动关闭 sandbox；GUI 实际创建与操作客体、SELinux 下完整流程 |
| 视频 | Iris 关键帧与解码顺序修复；H.264/HEVC 各 300 帧回归通过 | 当前正式内核媒体复核、长期应用负载 |
| Wi-Fi | FT 三轮重连、HTTPS、Wi-Fi SSH 通过 | 冷启动与休眠后重连、持续传输；旧握手失败根因未确认 |
| 充电 | 原装充电器 PD PPS，高电量采样有功率与温度 | 合适低电量条件下直充与最大功率、长期热稳定 |
| 手写笔与触控 | 笔尖、位置、倾斜、全范围压感事件捕获 | 绘图、防误触、按键/捏握、笔充电/重连、144 Hz |
| 蓝牙 | 手写笔 HID 节点已出现 | 外设连接、重连与音频 |
| 传感器 | 原生 libssc、加速度/光/接近/罗盘采集 | 自动旋转、亮度、遮挡交互验收 |
| 相机 | 两个虚拟相机节点短采集通过 | 前后摄、LED、连续预览与通话 |
| 性能调度 | uperf 已装且开机自启；ENODEV 死循环、热插拔重扫、启动竞争三项缺陷已修复并实机复验 | 触摸提频观感与温控长期曲线 |
| 音频 | 服务与设备配置在位 | 扬声器各通道、麦克风实际录放 |
| 键盘/霍尔/电源 | 输入节点和相关服务在位 | 实物键盘、合盖、各按键、充电状态切换 |
| 休眠 | 单次 `rtcwake` suspend/resume 通过，服务存活、Wi-Fi 约 3 s 自动重连、无 failed unit | 多周期与长期功耗曲线 |
| HDR/指纹/IR | 未证明可用 | 明确支持边界并完成对应实现/实测 |
| Android 往返 | SukiSU root/授权/读回通过、匹配模块实装且 BOOT 未改；Linux→Android 失败根因已定位：`app_generation` 过期（已修复）＋`PIANO_EARLY_CHOOSER` 3 秒音量键覆盖请求页；请求当前保持 target=2；A/B 引导保护由 `qbootctl.service` 自动标记 `Successful=1`，彻底根除 retry 归零掉入 Fastboot 缺陷 | Android SELinux 持久策略、完整双向往返与 Recovery（需真机重启） |
| 包与来源 | 固定 pin、完整 Cargo.lock、RPM 文件归属与 finalizer 字节核验；`dbus-daemon`/`systemd-pam` 已加入装配审计 | **从发布 RootFS/ESP 重新生成镜像并从 GPT 起点实际装载核验** |
| 回滚与清理 | 旧内核/模块/ESP 回滚保留，旧裸 LibreVM 启动器已清理 | 交付证据归档后清理重复构建与失效包 |

临时目录会定期清理。当前可用系统、匹配模块、原厂 BOOT/GPT 和回滚材料
在完成替代与恢复验证之前保留；失效或重复包不能作为可安装交付物。

2026-10-19 的黑屏热修复只证明缺失 `dbus-daemon` 与 `systemd-pam` 会让
GDM 在面板点亮后退出，不能作为 PR 交付验收。最终 PR 必须从原始
GPT/UFS 分区开始，使用本流水线重新生成的 RootFS 与 ESP 完成冷启动，
并按
[黑屏事故记录](display-session-black-screen-20261019.md#最终-pr-验收门槛)
保留桌面与重启证据。

目前现有 ESP 的 release 是 `7.2.9-piano-gnome-g07c1f92db62b`，而
`/var/tmp/piano-image/rootfs` 的模块目录是
`7.2.9-piano-gnome-g184e09d96775`。这两份资产不配套，不能直接组成
发布候选；最终验收必须使用同一次流水线产出、release 一致的 ESP 与
RootFS。
