# 2026-10-15 上游集成实装记录

本轮尚未完成全部设备验收。原有正式 Gunyah、VA-API 和相机服务保留；新增手指滤波、相机 NEON、平台导出适配、功放限制和 uperf 已进入标准构建链。代码、构建结果与安装结果分别记录，不能互相代替。

| 项目 | 已完成 | 剩余验收 |
| --- | --- | --- |
| 手指与笔 | 固定 yzddmr6 `1ab620f41730fd66732141d73d27d6ce93f8a92a` 手指算法；同一 THP reader 保留原笔 owner；原生静态二进制编译通过；校准、NTP1 边界/epoch/过期释放测试通过 | 新 RPM 实装、真实手指拖窗、笔与手掌切换、事件坐标与物理尺寸 |
| 触控单位 | 十倍坐标 32000×21360，按当前面板源码 239×163 mm 设置 resolution，fuzz 为一个面板像素 | libinput 实机能力检查；报告的 113 units/mm 无依据，不采用 |
| DPU | 正式 7.2.9 链新增 0038；包含从路先停止、主路等待、从路最终清理 | 整核构建完成、新内核启动与多次关屏 |
| SPI | 检查实际使用的原厂派生 NT36xxx 驱动，读帧失败解锁后返回，保留 IRQ | 负载下持续触控；不套用于另一套 misc THP 驱动的 e6c332c6 |
| Q6APM | 已有 0032 mutex/global 生命周期修复 | ADSP 重启压力回归；不重复套补丁 |
| 音频 | 四功放即时值从 235 降至 192；UCM 默认 192；正式内核 0039 上限 215；现有 DEC0 增益 124 已对应 +40 dB | 新内核确认控件上限、重启后默认值、录放实测 |
| 平台识别 | PID1 前遮蔽导出的根 compatible，保留原 sysfs 只读视图和完整 RM 子树；独立挂载命名空间测试通过，detect-virt=none，RM 属性摘要不变、/dev/gunyah 保留 | 新 initramfs 启动、systemd/GNOME 电源行为与真实 VM 复测 |
| 相机 | 保留本项目路由、帧完整性与 AE/AF 修复，加入固定上游 NEON NV12→BGRx；原生转换测试与静态摄像头服务编译通过；可用 PIANO_CAMERA_OUTPUT=nv12 回退 | BGRx loopback 实际格式、前后摄连续预览、桌面应用与本机耗时 |
| uperf | 固定 `edfb14a235c3708b4e9627c819dc28c68596d0f1`；daemon/CLI/GUI 编译；CPU 0–5/6–7 分组、GPU 路径与实机一致。实机复验发现并修复三处缺陷：ENODEV 死循环、热插拔不重扫、启动时无触屏即永久停用输入监控；三种情形已在设备上复验通过 | 触摸提频观感与温控长期曲线 |
| uperf 兼容 | EOPNOTSUPP 时明确记录内核缺少 uclamp，回退普通线程属性；其余错误不吞；修正上游温控测试与现有默认值不一致；平板配置采用 70/80/95/75°C 阈值 | 功能边界：当前内核不支持利用率 clamp；不得宣称已实测 1ms 响应 |
| 视频与虚拟化 | 已安装 VA-API、正式 Gunyah、crosvm 和 LibreVM；此前媒体及单 vCPU ext4 写/读回通过；virtio-net 已打通，VM 三轮启停与有界失败退出生效，hub 与 daemon 的僵尸/残留缺陷已修复 | 浏览器实际硬解与 GUI 实际操作仍需人手确认 |
| 双系统 | SukiSU 与匹配模块已安装，Android→Linux 自动启动通过；Linux→Android 的 `app_generation` 过期与 `PIANO_EARLY_CHOOSER` 3 秒按键覆盖两处根因已定位（前者已修复） | Android SELinux 持久策略、完整往返与 Recovery，需真机重启 |

正式内核源码提交为 `8c0e589d8829e31b837b04f5e11e96621f88b833`，树为 `6c52d0f65429834f18d58fcdf7a8b502e871393d`。构建最初发现旧 compiler worktree 归属另一个 Git common dir，已保留旧目录，由构建工具为当前固定来源创建新受管目录，未绕过归属检查。

手指死区上游 240 单位等于 24 像素；旧研究报告的 2.4 像素错误。相机 1.9ms 是上游测量，未作为本机成绩。校准只在私有载荷中从真实 LCD 的原厂 INI 提取，未入库。

标准入口是 `scripts/build-device-runtime.py`。设备增量入口 `scripts/package-device-integration.py` 要求明确 base RPM 摘要，保留原依赖契约并记录基包摘要；增量包不冒充完整新镜像。finalizer 验证固定来源、适配摘要、必需文件和实际安装字节，拒绝缺失或篡改载荷。

本轮设备原生构建与测试日志位于 `/home/piano/integration-20261015/`，平台及音量证据位于 `/home/piano/production-project/build/integration-20261015/`。此后设备已运行正式内核 `7.2.9-piano-gnome-g8c0e589d8829`，新增运行时（触控滤波、相机 NEON、功放限制、uperf、VA-API、crosvm/librevm）均已实装；`systemd-detect-virt` 为 `none`，四个用户态服务 active。2026-10-16 的稳定性复核（virtio-net、VM 多轮、suspend、DPU 关屏、双系统、uperf）证据归档在 `/home/piano/integration-20261015/stability-verify/`。

逐项结论以[设备验收](../docs/device-acceptance-20261015.md)与[虚拟化](../docs/virtualization.md)为准；用户已确认屏幕显示正常，DPU 残余的 underrun 快照按噪声记录，不作为可见故障。

其他交付边界见[完成清单](completion-checklist.md)及[设备验收](../docs/device-acceptance-20261015.md)。
