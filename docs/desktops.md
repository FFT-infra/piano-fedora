# 桌面档

镜像按档位构建，同一份发布链出五档 root 文件系统。五档共用内核、ESP 与
一键脚本，只有 root 文件系统与 `root-install/` 不同。桌面差异全部收在
[`scripts/profiles/fedora-desktops.json`](../scripts/profiles/fedora-desktops.json)，
由 [`scripts/piano_desktop.py`](../scripts/piano_desktop.py) 解释，构建脚本不再
自己写桌面字面量。

| 档位 | 会话 | 登录方式 | 电源策略 | 自动旋转 | 支持状态 |
|---|---|---|---|---|---|
| `gnome` | GNOME 51（Workstation 环境） | GDM 自动登录 | dconf（`00-power`） | Mutter 原生处理 | ✅ 桌面就绪，完整支持手势与自动旋转 |
| `kde` | KDE Plasma | plasma-login-manager 自动登录 | kconfig（`powerdevilrc`） | KWin 原生处理（`qt6-qtsensors`） | ✅ 桌面就绪，支持桌面定制与自动旋转 |
| `niri` | niri | greetd `[initial_session]` | 仅 logind | 需在 output 声明 `transform` | ⚠️ 桌面就绪，需配置文件声明旋转 |
| `hyprland` | Hyprland（第三方 COPR） | greetd `[initial_session]` | 仅 logind | 需执行 `hyprctl keyword monitor` | ⚠️ 桌面就绪，需通过命令行控制旋转 |
| `denial` | Denial（第三方 DNF 仓库） | greetd `[initial_session]`，`denial-session --start-locked` | 仅 logind | — | ⚠️ 只在 Fedora 44 上有上游验证记录，Fedora 45 与 SM8750 未验 |
| `server` | 无 | 无 | 仅 logind | — | 仅用于构建测试装配，不发布镜像 |

## 登录方式

五档都自动登录到 `piano` 用户。首次开机不需要在登录界面输入密码。默认口令是
`piano123456`，桌面起来之后用 `passwd` 改。

- **GNOME**：`/etc/gdm/custom.conf` 写 `AutomaticLoginEnable=True` 与
  `AutomaticLogin=piano`，由 `gdm.service` 接管。
- **KDE**：`/etc/plasmalogin.conf` 写 `Session=plasma`。Fedora 45 的
  `kde-desktop` 组把登录管理器换成了 `plasma-login-manager`（`plasmalogin.service`）。
  它是 SDDM 的衍生实现，`[Autologin]` 的键与按 `<session>.desktop` 查找会话的
  规则都沿用 SDDM。装配后 `validate_against_rootfs()` 确认
  `/usr/share/wayland-sessions/plasma.desktop` 在树里。名字对不上时，
  登录管理器只在日志里写一条错误，然后退回登录界面。SDDM 与 plasmalogin 都会往
  `display-manager.service` 建别名，同装必冲突，所以 profile 里只装后者。
- **niri / Hyprland / Denial**：`/etc/greetd/config.toml` 的 `[initial_session]`
  指向 `niri-session`、`start-hyprland` 或 `denial-session --start-locked`，
  `[default_session]` 留 `tuigreet --time` 兜底。会话崩溃或用户注销后，greetd
  回到 tuigreet 的文本选择界面，不显示黑屏 VT。`--start-locked` 是 Denial 的
  官方参数，用于没有显示管理器认证过的直接会话。

  `[default_session]` 的 `user` 必须是 `greetd`，不是 `greeter`。这个账户由
  `greetd` 包里的 sysusers 片段建立（`u greetd - "greetd daemon"`），名字写错时
  greetd 每 1 秒重启一次。5 次后触发 `StartLimitBurst`，服务变成 failed。
  显示管理器起不来，桌面也进不去。现场核对：

  ```sh
  getent passwd greetd        # 应有一行，uid 994 左右
  systemctl is-active greetd  # active
  ```
  日志里出现 `configured default session user 'greeter' not found` 即是这个问题。

各档的显示管理器都直接随开机启动，不挂 `piano-display.service` 依赖。驱动按
标准 modalias 加载，旧的分子系统手动 bring-up 单元已随 [SunUEFI](https://github.com/slwyts/Project-SunUEFI) 上游删除；给显示管理器
挂 `Requires=` 只会在依赖永远不满足时把桌面一起拖死。

## 电源行为

电源键和合盖都只关屏并锁屏，不把机器挂起，与出厂系统的动作一致。长按弹出关机
入口。五档都不开自动挂起。

GNOME 档由 `piano-fedora-configs` 的 `piano-power-button` 用户服务补这段策略。
它不独占输入设备，按键事件照常流向 compositor；熄屏走 Mutter 的
`PowerSaveMode`，锁屏走 `org.gnome.ScreenSaver`，长按走
`org.gnome.SessionManager.Shutdown`。单元随 `graphical-session.target` 启停，
只在 GNOME 档挂进 `graphical-session.target.wants`。

GNOME 设置里的「电源按钮」选项没有上锁，用户可以自己改。改成挂起之类的动作
后，助手和 gnome-shell 会各做各的：助手照旧熄屏锁屏，shell 再执行用户选的
动作。选回「无操作」（`power-button-action='nothing'`，出厂值）就恢复出厂
行为，不需要 root。

KDE 档由同一个包的 `piano-plasma-power-button` 用户服务补这段策略。短按调
PowerDevil 自己的 `Turn Off Screen` 动作，锁屏与 DPMS 还是 PowerDevil 的通路；
长按走 `org.kde.LogoutPrompt` 的关机对话框。PowerDevil 的动作在按下那一刻就
触发，长按到 800 毫秒时屏幕已经黑掉、会话也锁上，对话框落在锁屏后面看不见，
所以 KDE 的 `PowerButtonAction` 是 `0`（NoAction），电源键整条交给助手。合盖
仍由 PowerDevil 负责。

KDE 系统设置里的「电源按钮」同样可以改。改成具体动作后短按会重复触发一次，
既关屏又执行用户选的动作。改回「无操作」就恢复。

niri、Hyprland、Denial 与 server 没有桌面侧文件，靠 logind 的 `HandlePowerKey=ignore`、
`HandleLidSwitch=ignore`、`IdleAction=ignore` 保证它不抢这两个输入。

三种电源策略的落点：

- **dconf**（GNOME）：`etc/dconf/db/local.d/00-power`，由
  `piano-fedora-configs` 这个 RPM 安装，构建器只核对不重写。两种自动睡眠都是
  `nothing`，空闲 300 秒关屏并锁屏。
- **kconfig**（KDE）：`etc/xdg/powerdevilrc`，由构建器落盘，没有包拥有它。
  AC / Battery / LowBattery 三个 profile 的 `AutoSuspendAction` 与
  `PowerButtonAction` 是 `0`（NoAction），`LidAction` 是 `64`（TurnOffScreen），
  空闲 300 秒关屏并锁屏。
- **仅 logind**（niri / Hyprland / Denial / server）：没有桌面侧文件，电源键与合盖都不
  触发动作。

挂起与唤醒（`rtcwake -m mem -s 20`）运行正常，唤醒后平稳恢复至原会话。那是
用户主动发起的挂起，默认策略里没有任何一条会自动睡下去。

## KDE 默认值

KWin 的显示、输入与锁屏设置只在 `~/.config` 下，没有 `/etc/xdg` 级别的落点。
profile 的 `user_config` 列出文件与目标，装配时按 home 目录的属主落盘，KWin
运行时能改写的文件不会被 root 持有：

| 文件 | 内容 |
| --- | --- |
| `~/.config/kwinoutputconfig.json` | DSI-1 用 3200x2136@120、scale 2、`autoRotation: InTabletMode`、亮度 0.8 |
| `~/.config/kcminputrc` | `Xiaomi Touchpad` 自然滚动 |
| `~/.config/kscreenlockerrc` | `Autolock=false`、`LockOnResume=true` |

`kwinoutputconfig.json` 里同一个连接器写两条，一条 `edidIdentifier` 为空、一条
为 `XMI 129 0 0 2025 0`。KWin 先按 EDID 标识查，查不到再按连接器名查，两条
覆盖面板 EDID 可读与不可读两种情况。

`/etc/environment.d/60-piano-display.conf` 写 `KWIN_DRM_FORCE_SCALING_MODE=FULL`。
KWin 默认只缩放自己生成模式的输出；DSI-1 的模式来自 DRM，没有这个变量时它拒绝
按 scale 2 放大。上游同一时期还写 `QT_SCALE_FACTOR=2`，这里没有跟：输出的
scale 与 `QT_SCALE_FACTOR` 会叠加，界面会放大两次。

## 自动旋转

`piano-fake-tablet-mode` 把 `SW_TABLET_MODE` 暴露给桌面，判定依据是 logind
里是否存在活动的图形会话（`Class=user`、`Type=wayland|x11`、`Active=yes`、用户名
不是 greeter），不再扫 `gnome-shell` 进程名。

**只有 GNOME 与 KDE 走桌面自身的旋转路径。**

- GNOME（Mutter）订阅 `iio-sensor-proxy` 的 `AccelerometerOrientation`，平板模式
  打开时自动旋转。已在真机验证：平板从平放转成 `left-up`，逻辑显示器的
  `transform` 从 0 变成 1，屏幕跟着转。
- KDE（KWin）依赖 `qt6-qtsensors`。Fedora 45 的 `kwin` 包声明了
  `iio-sensor-proxy`，没声明 `qt6-qtsensors`。这个包不在位时，Plasma 6 不出现
  「自动」旋转选项，所以 KDE 档的包集合里单独列了它。
- niri 与 Hyprland 的合成器不读传感器。niri 要在 `output` 段里手动写
  `transform "90"` 之类的值。Hyprland 用 `hyprctl keyword monitor` 或配置里的
  `transform`。两档没有自动旋转是预期行为，不是缺陷。

传感器走 SSC 后端，不经过 IIO 子系统。`/sys/bus/iio/devices` 为空是正常的。
装上 `libssc` 与 `iio-sensor-proxy-ssc`，并加载 `qrtr_smd`/`qrtr_mhi`/`mhi`
之后，代理能直接拿到加速度计、环境光与接近传感器。

桌面旋转之外，`iio-sensor-proxy` 仍提供光感与接近传感器，五档都装了。

## extras 可选组件

五档默认镜像都用 Fedora 原版桌面组件，extras 的包不进镜像，也不进默认 COPR。需要使用补丁版桌面组件时，按[COPR 软件源](copr-repository.md#可选的-extras-软件源)启用默认关闭的 `xinyang26/piano-fedora-extras`。

| 桌面 | meta 包 | 带进来的组件 | 用途 |
| --- | --- | --- | --- |
| GNOME | `piano-gnome-extras` | `gnome-settings-daemon-piano`、`gtk3`、`mutter`，另手动升级 `gdm` | 自动亮度设备 profile、键盘背光热插拔、GTK3 XI2 工具原子、空 KMS gamma LUT 与触摸 grab 空指针两个守卫、登录前等 KMS connector |
| KDE | `piano-kde-extras` | `kwin`、`gtk3` | KDE HDR/广色域关闭后背光恢复、GTK3 XI2 工具原子 |

GNOME meta 写入 `20-piano-ambient`，只把 `org.gnome.settings-daemon.plugins.power` 的 `ambient-enabled` 改成 `true`。默认的 `00-power` 仍是 `false`，卸载 extras 后按文件名顺序回到默认值。不启用 extras 的 GNOME 不读自动亮度，KDE 的 HDR 与广色域保持默认关闭。

KDE HDR 依赖补丁版 KWin 修复退出 HDR 后的背光恢复问题。实机开关与背光验证数据、配置要求及当前限制见[已知问题](known-issues.md#hdr)。

自动亮度经两轮验证：`gsd-power` 加载 `piano.ini` 的 ambient profile，13–17 lux 下换算 66.7% 目标亮度，`ktz8866-backlight` 从 1023 调至 1360（上限 2047）；光照在 8–2527 lux 间变化时背光在 1600–2047 跟随调整，延迟 1–2 秒无抖动。目标亮度基于手动调光基准相对换算，基准在暗光处时暗端落点偏高。键盘背光需配合悬浮键盘外设，待后续验收。

`mutter` 与 `gdm` 仅作用于 GNOME 档：KDE、niri 与 Hyprland 分别使用 `plasmalogin` 和 `greetd`；mutter 仅由 `gnome-shell` 链接。`gdm` 不进 meta 的硬依赖，GNOME 用户可在安装 meta 前手动 `dnf upgrade gdm`。安装后切换至 GDM 的 GNOME 会话运行稳定，GSD 正常加载 ambient profile。触摸手势与键盘背光需要物理外设，待后续验收。

默认 GNOME 档安装发行版基础组件（`gnome-settings-daemon-51.1-1.fc45`、`kwin-6.8.3-1.fc45`、`gtk3-3.24.52-6.fc45`），默认不安装 extras meta 包，`ambient-enabled` 缺省为 `false`。

## 第三方软件源

Hyprland 与 Denial 两档依赖第三方仓库，Fedora 45 官方仓库里没有这两个桌面。
构建时仓库都以 `--repofrompath` 临时加入，不写进镜像的持久 `.repo`。

- **Hyprland**：`copr-hyprland`
  （`download.copr.fedorainfracloud.org/results/lionheartp/Hyprland/`）提供
  Hyprland 与配套组件。`gpgcheck=1`，公钥先下载到构建目录，按 profile 里的
  `gpgkey_sha256` 校验，之后才交给 dnf。哈希对不上时构建直接失败。
- **Denial**：官方签名 DNF 仓库
  `https://packages.denialwm.org/rpm/fedora/44/aarch64/`。上游只为 Fedora 44
  发布仓库，45 的 repodata 实测 404，所以 baseurl 固定到 `fedora/44`，不跟
  `releasever` 走；这等于在 Fedora 45 上装上游按 44 验证的包。公钥
  `denial-repo-key.asc` 的 sha256 钉在 profile 里，仓库经 `gpgcheck=1` 校验 RPM
  签名。仓库元数据的 `repo_gpgcheck` 没有开：dnf5 在 `--installroot` 下会把
  本地 gpgkey 路径当成仓库源解析，`repo_gpgcheck=1` 时直接失败；这与 Hyprland
  COPR 的信任级别一致，覆盖包签名而不覆盖元数据签名。这个主机按 UA 拒绝
  `Python-urllib/*`，取公钥的请求带项目自己的 UA，不伪装成浏览器。
- 其余三档（GNOME、KDE、niri）不启用任何第三方源。

本地未签名的 `piano-local` 仓库单独设 `gpgcheck=0`，不再用全局 `--nogpgcheck`。

## 文件名

每档的产物名由 `piano_desktop.py` 生成，不手写：

| 层 | 名字 |
|---|---|
| rootfs 目录 | `build/clean-distros/fedora-<rel>-<desktop>` |
| rootfs artifact | `rootfs-<rel>-<desktop>` |
| 镜像 artifact | `root-image-<rel>-<desktop>` |
| bundle artifact | `deploy-bundle-<desktop>`（每档各一个 run，内部文件名不变） |
| root-install artifact | `root-install-<desktop>` |
| Release 资产 | `piano-<tag>-<desktop>.tar.zst`（超过 2 GiB 时切成 `.part-NN`）与 `piano-<tag>-<desktop>.sha256` |
| 本地还原 | `scripts/unpack-release.sh --tag <tag> --desktop <档位>`，解出暂存目录 |

查看当前矩阵与某一档的展开结果：

```sh
python3 scripts/piano_desktop.py matrix
python3 scripts/piano_desktop.py show --desktop hyprland
make plan-desktop DESKTOP=niri RELEASEVER=45
```

## 已知缺口

- **上屏与旋转之外的行为没有真机验收记录。** niri 与 Hyprland 两档连上屏都还需要
  重建镜像，见上面的表格。装配后的
  `validate_against_rootfs()` 只能证明登录单元、会话文件、会话二进制与电源
  文件在树里，证明不了这些文件在设备上真的生效。
- **niri / Hyprland 没有自动旋转**，见上一节。
- **KDE 的自动旋转依赖 `qt6-qtsensors`**，这一条来自 Plasma 6 的已知行为，
  未在真机设备上实际验证。
- **Hyprland 依赖第三方 COPR。** 仓库消失或公钥更换时，构建硬失败。替代方案是自己打包，评估后未采用。
- **Denial 没有 Fedora 45 的官方验证，叠加测试也没有起来。** 上游只为
  Fedora 44 发布仓库，并在 Fedora 44 Workstation + GDM 上验证；本镜像在
  Fedora 45 + SM8750 上装配。2026-11-17 在 KDE 镜像上叠加 0.6.6 实测，
  `denial-session --check` 全部通过，但临时切到 greetd 后 `deniald` 在 GPU
  初始化阶段 SIGABRT，会话没有起来，已回滚到 KDE；详情见
  [已知问题](known-issues.md#denial-桌面)。构建通过不等于会话可用。构建时用
  官方默认的 `desktop` shell profile，没有开 `DENIAL_SHELL_PROFILE=mobile`。
- **Denial 合成器不读传感器**，平板上没有自动旋转，与 niri / Hyprland 相同。
- **Denial 的包不在本仓库的发布链里。** `denial` 与 `denial-flutter-engine`
  直接来自上游仓库，本仓库不镜像、不重新打包，也不进 COPR。
- **内核在 COPR 里，五档共用。** 装机用归档里的 `kernel-piano`。
  装好之后由 `dnf upgrade` 升级，`kernel-piano` 的 `%posttrans` 重建 initramfs
  并重打 ESP 的 `/EFI/Linux/sunuefi.efi`。失败与回滚边界见 [COPR 软件源](copr-repository.md)。
