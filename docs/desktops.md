# 桌面档

镜像按桌面分档构建，同一份发布链出四档 root 文件系统。四档共用内核、ESP、
安装器与刷写脚本，只有 root 镜像与 `root-install/` 不同。桌面差异全部收在
[`scripts/profiles/fedora-desktops.json`](../scripts/profiles/fedora-desktops.json)，
由 [`scripts/piano_desktop.py`](../scripts/piano_desktop.py) 解释，构建脚本不再
自己写桌面字面量。

| 档位 | 会话 | 登录 | 电源策略 | 自动旋转 | 验收状态 |
|---|---|---|---|---|---|
| `gnome` | GNOME 50（Workstation 环境） | GDM 自动登录 | dconf（`00-power`） | 走 Mutter，已实机验证 | 已登录桌面，旋转已验 |
| `kde` | KDE Plasma | plasma-login-manager 自动登录 | kconfig（`powerdevilrc`） | 走 KWin，需 `qt6-qtsensors` | 未上真机 |
| `niri` | niri | greetd `[initial_session]` | 仅 logind | 无，需手动 `transform` | 未上真机 |
| `hyprland` | Hyprland（第三方 COPR） | greetd `[initial_session]` | 仅 logind | 无，需手动 rotate | 未上真机 |
| `server` | 无 | 无 | 仅 logind | — | 只用于测试装配，不进 Release |

## 登录方式

四档都自动登录到 `piano` 用户，首次开机不需要在登录界面输入密码。默认口令是
`piano123456`，桌面起来之后用 `passwd` 改。

- **GNOME**：`/etc/gdm/custom.conf` 写 `AutomaticLoginEnable=True` 与
  `AutomaticLogin=piano`，由 `gdm.service` 接管。
- **KDE**：`/etc/plasmalogin.conf` 写 `Session=plasma`。Fedora 44 的
  `kde-desktop` 组把登录管理器换成了 `plasma-login-manager`（`plasmalogin.service`），
  它是 SDDM 的衍生实现，`[Autologin]` 的键与按 `<session>.desktop` 查找会话的
  规则都沿用 SDDM。装配后 `validate_against_rootfs()` 会确认
  `/usr/share/wayland-sessions/plasma.desktop` 真的在树里；名字对不上时
  只在日志里留一句错误，退回登录界面。SDDM 与 plasmalogin 都会往
  `display-manager.service` 建别名，同装必冲突，所以 profile 里只装后者。
- **niri / Hyprland**：`/etc/greetd/config.toml` 的 `[initial_session]` 指向
  `niri-session` 或 `start-hyprland`，`[default_session]` 留 `tuigreet --time`
  兜底。会话崩溃或用户注销后回到 tuigreet 的文本选择界面，而不是黑屏 VT。

四个显示管理器都在 `piano-display.service` 之后启动（`Requires=` + `After=`
的 drop-in），面板没就绪时不会先起桌面。

## 电源行为

电源键与合盖由 `piano-fedora-configs` 里的 `piano-power-button` 用户服务统一
处理，四档一致：短按只关背光，再按恢复；合盖同上；长按弹出关机入口。

长按的关机入口按桌面依次回退：先调 GNOME 的
`org.gnome.SessionManager.Shutdown`，失败再调 KDE 的
`org.kde.Shutdown.logoutAndShutdown`，两者都没有时直接 `systemctl poweroff`。
每一步失败都会写日志，不会在第二种桌面上变成静默无响应。

三种电源策略的落点：

- **dconf**（GNOME）：`etc/dconf/db/local.d/00-power`，由
  `piano-fedora-configs` 这个 RPM 安装，构建器只核对不重写。两种自动睡眠都是
  `nothing`，空闲 300 秒关屏并锁屏。
- **kconfig**（KDE）：`etc/xdg/powerdevilrc`，由构建器落盘，没有包拥有它。
  AC / Battery / LowBattery 三个 profile 的 `AutoSuspendAction`、
  `PowerButtonAction`、`LidAction` 全是 `0`（NoAction），空闲 300 秒关屏并锁屏。
- **仅 logind**（niri / Hyprland / server）：没有桌面侧文件。logind 的
  `HandlePowerKey=ignore`、`HandleLidSwitch=ignore`、`IdleAction=ignore` 保证
  它不跟用户服务抢这两个输入。

挂起与唤醒本身是可用的：`rtcwake -m mem -s 20` 实测正常，桌面上做挂起/唤醒两轮
也都回到了原会话。四档仍然关掉自动睡眠，因为电源键与合盖已经由
`piano-power-button` 统一接管，桌面再自己睡一次会和它抢同一个事件。

## 自动旋转

`piano-fake-tablet-mode` 把 `SW_TABLET_MODE` 暴露给桌面，判定依据是 logind
里是否存在活动的图形会话（`Class=user`、`Type=wayland|x11`、`Active=yes`、用户名
不是 greeter），不再扫 `gnome-shell` 进程名。

**只有 GNOME 与 KDE 走桌面自身的旋转路径。**

- GNOME（Mutter）订阅 `iio-sensor-proxy` 的 `AccelerometerOrientation`，平板模式
  打开时自动旋转。已在真机验证：平板从平放转成 `left-up`，逻辑显示器的
  `transform` 从 0 变成 1，屏幕跟着转。
- KDE（KWin）依赖 `qt6-qtsensors`。F44 的 `kwin` 包声明了 `iio-sensor-proxy`，
  没有声明 `qt6-qtsensors`，所以 Plasma 6 上自动旋转要这个包在位才出现
  「自动」选项。
- niri 与 Hyprland 的合成器不读传感器。niri 要在 `output` 段里手动写
  `transform "90"` 之类的值；Hyprland 用 `hyprctl keyword monitor` 或配置里的
  `transform`。没有自动旋转是预期行为，不是缺陷。

传感器走 SSC 后端，不经过 IIO 子系统。`/sys/bus/iio/devices` 为空是正常的；
`libssc` 与 `iio-sensor-proxy-ssc` 装上、`qrtr_smd`/`qrtr_mhi`/`mhi` 加载之后，
代理能直接拿到加速度计、环境光与接近传感器。

桌面旋转之外，`iio-sensor-proxy` 仍提供光感与接近传感器，四档都装了。

## 第三方软件源

只有 Hyprland 档启用第三方仓库 `copr-hyprland`
（`download.copr.fedorainfracloud.org/results/lionheartp/Hyprland/`），Fedora 44
官方仓库没有 Hyprland 及其配套组件。构建时：

- 仓库以 `--repofrompath` 临时加入，不写进镜像的持久 `.repo`；
- `gpgcheck=1`，公钥先下载到构建目录，按 profile 里的 `gpgkey_sha256` 校验后
  才交给 dnf，哈希对不上直接失败；
- 其余三档不启用任何第三方源。

本地未签名的 `piano-local` 仓库单独设 `gpgcheck=0`，不再用全局 `--nogpgcheck`。

## 文件名

每档的产物名由 `piano_desktop.py` 生成，不手写：

| 层 | 名字 |
|---|---|
| rootfs 目录 | `build/clean-distros/fedora-<rel>-<desktop>` |
| rootfs artifact | `rootfs-<rel>-<desktop>` |
| 镜像 artifact | `root-image-<rel>-<desktop>` |
| bundle artifact | `deploy-bundle`（每档各一个 run，内部文件名不变） |
| Release 桌面资产 | `pianoroot-<desktop>.f2fs.img.zst` 等，平台资产不带后缀 |
| 本地还原 | `unpack-release.sh --desktop <档位>`，还原成上面这些规范名 |

查看当前矩阵与某一档的展开结果：

```sh
python3 scripts/piano_desktop.py matrix
python3 scripts/piano_desktop.py show --desktop hyprland
make plan-desktop DESKTOP=niri RELEASEVER=44
```

## 已知缺口

- **KDE、niri、Hyprland 都没有真机验收记录。** 装配后的
  `validate_against_rootfs()` 只能证明登录单元、会话文件、会话二进制与电源
  文件在树里，证明不了桌面上屏。
- **niri / Hyprland 没有自动旋转**，见上一节。
- **KDE 的自动旋转依赖 `qt6-qtsensors`**，这一条来自 Plasma 6 的已知行为，
  本轮没有在设备上验证。
- **Hyprland 依赖第三方 COPR。** 仓库消失或公钥更换时构建会硬失败；升级路径
  是自己打包，本轮评估后没有采用。
- **`denial` 是占位档位**，`buildable: false`。`build-clean-rootfs.py --plan`
  对它返回 `INPUTS_REQUIRED` 并以非零退出，不会产出镜像。
- **内核在 COPR 里，四档共用。** 装机用 release bundle 里的 `kernel-piano`；
  装好之后由 `dnf upgrade` 升级，包自己的 `%posttrans` 重建 initramfs 并重打
  ESP 的 `boot.img`。失败与回滚边界见 [COPR 软件源](copr-repository.md)。
