# 桌面档位

五档桌面由同一发布链构建，共用内核、ESP 与刷机脚本，仅 rootfs 与 `root-install/` 按档位区分。全部差异定义在 [`scripts/profiles/fedora-desktops.json`](../scripts/profiles/fedora-desktops.json)，由 [`scripts/piano_desktop.py`](../scripts/piano_desktop.py) 展开。

| 档位 | 会话 | 登录方式 | 电源策略 | 自动旋转 | 支持状态 |
| --- | --- | --- | --- | --- | --- |
| `gnome` | GNOME 51（Workstation） | GDM 自动登录 | dconf（`00-power`） | Mutter 原生 | ✅ 已登录桌面，旋转实机验证通过 |
| `kde` | KDE Plasma | `plasma-login-manager` | kconfig（`powerdevilrc`） | KWin（`qt6-qtsensors`） | ✅ 已登录桌面，旋转实机验证通过 |
| `niri` | niri | greetd `[initial_session]` | logind：短按挂起，长按关机 | 手动设置 `transform` | ⚠️ 桌面可用，无自动旋转 |
| `hyprland` | Hyprland（第三方 COPR） | greetd `[initial_session]` | logind：短按挂起，长按关机 | 手动执行 `hyprctl` | ⚠️ 桌面可用，无自动旋转 |
| `denial` | Denial（第三方 DNF 源） | greetd `[initial_session]`（`mobile` + `--start-locked`） | logind：短按挂起，长按关机 | 无（未接入传感器） | ⚠️ `0.6.10-1` 已冷启通过并验证 GPUI/Vulkan `denial-settings` |
| `server` | 无 | 无 | logind：短按挂起，长按关机 | 无 | 仅用于内部装配测试，不进 Release |

## 登录方式

五档均自动登录用户 `piano`（默认密码 `piano123456`，开机后用 `passwd` 改掉）。

- **GNOME**：通过 `/etc/gdm/custom.conf` 自动登录，`gdm.service` 拉起 GNOME 会话。
- **KDE**：通过 `/etc/plasmalogin.conf`（`Session=plasma`）自动登录。Fedora 45 的 KDE 使用 `plasma-login-manager`（`plasmalogin.service`），它会与 `sddm` 争夺 `display-manager.service` 别名，因此镜像只装前者。
- **niri / Hyprland / Denial**：通过 `/etc/greetd/config.toml` 的 `[initial_session]` 直接拉起对应合成器，并保留 `tuigreet --time` 作 `[default_session]` 兜底（崩溃或注销时退回文本选单）。注意 greetd 配置里的运行用户必须是 `greetd`（由 `greetd` sysusers 创建），写错成 `greeter` 会触发重启计数锁定导致无画面。

  排查命令：

  ```sh
  getent passwd greetd        # 应返回一行记录
  systemctl is-active greetd  # active
  ```

所有显示管理器直接随开机启动，不再强依赖旧的 `piano-display.service`（驱动随 modalias 自动加载）。

## 电源行为

五档均关闭空闲自动睡眠（支持手动合盖或 `rtcwake -m mem -s 20` 深度挂起），按键行为按桌面划分：

- **GNOME 与 KDE**：短按电源键与合盖均仅息屏锁屏、不挂起，长按弹出关机菜单，与出厂行为一致。分别由 `piano-fedora-configs` 里的用户服务 `piano-power-button`（调用 Mutter PowerSaveMode/ScreenSaver）与 `piano-plasma-power-button`（接管长按弹窗，避免 PowerDevil 先把屏黑掉盖住关机提示）处理。用户在桌面设置里改过电源键动作后，再选回“无操作”即可恢复默认。
- **niri / Hyprland / Denial / server**：没有桌面端按键助手，装配时通过 `etc/systemd/logind.conf.d/zz-piano-login.conf` 覆盖打包策略，由 logind 负责短按挂起、合盖挂起、长按关机。

配置文件落点：

- GNOME：`etc/dconf/db/local.d/00-power`（`piano-fedora-configs` 提供，300 秒空闲息屏锁屏）
- KDE：`etc/xdg/powerdevilrc`（构建器写入，`PowerButtonAction=0`、`LidAction=64`，300 秒空闲息屏锁屏）
- 其余档位：`etc/systemd/logind.conf.d/zz-piano-login.conf`

## KDE 默认值

KWin 与输入默认设置落在 `piano` 用户目录下（装配时设好属主以便 KWin 运行时改写）：

| 文件 | 默认内容 |
| --- | --- |
| `~/.config/kwinoutputconfig.json` | DSI-1 3200×2136@120，缩放 2，`autoRotation: InTabletMode`，亮度 0.8（同时提供空 EDID 与 `XMI 129 0 0 2025 0` 两条匹配项） |
| `~/.config/kcminputrc` | `Xiaomi Touchpad` 自然滚动 |
| `~/.config/kscreenlockerrc` | `Autolock=false`，`LockOnResume=true` |

`/etc/environment.d/60-piano-display.conf` 设置 `KWIN_DRM_FORCE_SCALING_MODE=FULL` 让 KWin 允许缩放 DRM 模式输出，刻意不设 `QT_SCALE_FACTOR=2`，以免在系统 2 倍缩放上二次放大界面。

## 自动旋转

`piano-fake-tablet-mode` 在检测到活动图形会话（非 greeter）时上报 `SW_TABLET_MODE`。由于硬件传感器走高通 SSC（`libssc` + `iio-sensor-proxy-ssc`），`/sys/bus/iio/devices` 为空是正常的。

- **GNOME**：Mutter 原生订阅 `iio-sensor-proxy` 自动旋转，实机验证通过。
- **KDE**：需要 Fedora 官方包里未声明的依赖 `qt6-qtsensors`，KDE 档位已显式装入以启用「自动」旋转。
- **niri / Hyprland / Denial**：合成器自身不读传感器 D-Bus。niri 在配置文件 `output` 段写 `transform "90"` 等，Hyprland 用 `hyprctl keyword monitor` 旋转。

## extras 可选组件

默认五档使用 Fedora 原版桌面组件，可选的补丁版本放在默认关闭的 `xinyang26/piano-fedora-extras` 仓库中，启用与安装命令见 [COPR 软件源](copr-repository.md#可选的-extras-软件源)。

| 桌面 | meta 包或升级项 | 包含组件 | 提供的能力 |
| --- | --- | --- | --- |
| GNOME | `piano-gnome-extras`，另按需升级 `gdm`、`gnome-control-center` | `gnome-settings-daemon-piano`、`gtk3`、`mutter`、`gdm`、`gnome-control-center` | `20-piano-ambient` 开启原厂曲线自动亮度、键盘背光热插拔、X11 手写笔 XI2 修复、mutter 触摸/gamma 守卫、gdm 等 KMS connector 就绪、控制中心指纹等待 |
| KDE | `piano-kde-extras` | `kwin`、`gtk3` | 修复退出 HDR/广色域后背光停在最大值的问题、X11 手写笔 XI2 修复 |

实机已验证自动亮度（`gsd-power` 加载 `piano.ini`，随 8-2527 lux 环境光平滑调整 `ktz8866-backlight`）与 KDE HDR 退出恢复背光；键盘背光热插拔与睡眠恢复尚需更多外设测试。

## 第三方软件源

仅有 Hyprland 与 Denial 在镜像构建期使用 `--repofrompath` 临时引入第三方源（均开 `gpgcheck=1` 并锁定公钥 SHA256，且不写进生成镜像的持久 `.repo`）：

- **Hyprland**：`lionheartp/Hyprland` COPR。
- **Denial**：`https://packages.denialwm.org/rpm/fedora/44/aarch64/`（上游目前仅发布 Fedora 44 仓库，故不随 `releasever` 变化；未开 `repo_gpgcheck` 以避开 dnf5 installroot 路径解析 bug，请求使用自定义 UA 以绕过主机的 `Python-urllib` 拦截）。

## 产物命名规律

`piano_desktop.py` 自动按档位派生产物名：

| 层级 | 命名格式 |
| --- | --- |
| 构建根树 / artifact | `build/clean-distros/fedora-<rel>-<desktop>` / `rootfs-<rel>-<desktop>` |
| 镜像与 bundle | `root-image-<rel>-<desktop>` / `deploy-bundle-<desktop>` / `root-install-<desktop>` |
| Release 归档 | `piano-<tag>-<desktop>.tar.zst`（超 2 GiB 分 `.part-NN`）与 `.sha256` |
| 本地解包 | `scripts/unpack-release.sh --tag <tag> --desktop <档位>` |

构建期核对示例：

```sh
python3 scripts/piano_desktop.py matrix
python3 scripts/piano_desktop.py show --desktop hyprland
make plan-desktop DESKTOP=niri RELEASEVER=45
```
