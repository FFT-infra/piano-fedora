# 2026-10-19 面板亮但黑屏：GNOME 会话总线缺失

## 结论

这次不是 DPU、DSI 面板、背光或 GDM 本体故障，而是纯净 RootFS 装配链
漏装了两个 Fedora 子包：

* `dbus-daemon` 提供 `/usr/bin/dbus-daemon` 和
  `/usr/bin/dbus-run-session`。GDM 的
  `/usr/libexec/gdm-wayland-session` 启动私有会话总线时直接执行后者。
* `systemd-pam` 提供 `pam_systemd.so`。PAM/logind 靠它创建用户运行时
  目录 `/run/user/1000` 和会话总线 `/run/user/1000/bus`。

DNF 在 `install_weak_deps=False` 时不会把这两个子包带进依赖闭包，因此
必须在 `scripts/profiles/fedora.json` 显式列出，并在最终镜像里逐文件
审计。根文件系统、内核、initramfs、显示驱动和自动登录本身都已正常。

## 真机现象

设备上电后 USB 枚举为 `1209:8751 Piano Linux Debug ACM NCM`，
`192.168.77.1` 可 ping，22 端口开放，`serial-getty@ttyGS0` 提供
认证登录。屏幕背光亮、内容全黑。串口登录后确认：

```text
Linux fedora 7.2.9-piano-gnome-g07c1f92db62b aarch64
systemctl is-system-running -> running
systemctl --failed          -> 空
piano-display.service       -> active (exited)
gdm.service                 -> active (running)
```

显示链路的只读状态：

```text
/sys/class/drm/card2-DSI-1/status   connected
/sys/class/drm/card2-DSI-1/enabled  enabled
/sys/class/drm/card2-DSI-1/dpms     On
/sys/class/drm/card2-DSI-1/modes    3200x2136
/sys/class/backlight/ktz8866-backlight/brightness      1390
/sys/class/backlight/ktz8866-backlight/max_brightness  2047
/sys/class/backlight/ktz8866-backlight/bl_power        0
```

`piano-display.service` 的五个阶段全部成功，DSI 输出已经启用，背光没有
被关闭。因此“黑屏发亮”发生在显示硬件接管之后，而不是引导或面板初始化
阶段。

## 根因证据

GDM 每次拉起会话都报同一句话并退出：

```text
gdm-wayland-session[...]: Unable to run session message bus
Gdm: GdmDisplay: Session never registered, failing
```

对应文件状态：

```text
/usr/bin/dbus-daemon                不存在
/usr/bin/dbus-run-session           不存在
/usr/lib64/security/pam_systemd.so  不存在
/run/user/1000                      不存在
/run/user/1000/bus                  不存在
```

系统总线 `dbus-broker.service` 本身正常，`/run/dbus/system_bus_socket`
也在；失败只发生在需要 PAM/logind 建立的用户会话总线。

历史基线镜像同样缺这两个包。此前 `594de48` 已在旧的 Fedora plan
里显式加入它们，但 PR 的纯净装配分支从旧基线重建后没有继承这条修复。
这不是设备状态变化，而是同一类装配回归再次出现在新流水线里。

## 已执行的设备恢复

为验证根因，直接从 Fedora 44 aarch64 仓库取得与设备同版本的 RPM：

```text
dbus-daemon-1.16.2-1.fc44.aarch64.rpm
sha256 898d05b54bf4e76ea77ed2294f5f92aa1b4c998e2271c5638ee69b2f8534b059

systemd-pam-259.9-1.fc44.aarch64.rpm
sha256 0ed45f2e175097cb59083b7a7a0d4511e147ac62f91fe0524b0681e2d33429e8
```

两个文件通过串口传到设备后，先在设备上核对 SHA256，再执行：

```sh
sudo rpm -Uvh dbus-daemon-1.16.2-1.fc44.aarch64.rpm \
              systemd-pam-259.9-1.fc44.aarch64.rpm
sudo systemctl restart gdm.service
```

重启后 GDM 保持 active，`/run/user/1000` 与
`/run/user/1000/bus` 均由 `piano:piano` 创建，用户确认进入 GNOME
桌面。

随后又做了一次完整重启回归，确认热修复跨启动持久生效：

```text
uname -r                        7.2.9-piano-gnome-g07c1f92db62b
systemctl is-system-running     running
systemctl --failed              空
gdm.service                     active (running)
piano-display.service           active (exited), 五阶段 status=0/SUCCESS
loginctl                        seat0 user session + ttyGS0 session
/run/user/1000/bus              piano:piano
/run/user/1000/wayland-0        piano:piano
card2-DSI-1                     connected / enabled / On / 3200x2136
ktz8866 backlight               brightness 1727, max 2047, bl_power 0
gnome-shell                     以 --mode=user 运行
```

这证明两个 RPM 已经写入设备 RootFS，不再依赖当前会话的手工启动；但
它仍不是发布资产验收，原因见下一节。

这是一次设备热修复验证，不是 PR 发布资产的最终验收：当前根文件系统
来自历史可启动基线，两个 RPM 是后来补进去的。PR 必须重新生成完整
镜像，再从原始 GPT/UFS 布局开始做冷启动验收。

当前工作区的配套关系也还不自洽：

```text
现有 ESP / bundle: 7.2.9-piano-gnome-g07c1f92db62b
/var/tmp/piano-image/rootfs: 7.2.9-piano-gnome-g184e09d96775
```

前者是今晚设备恢复使用的历史 ESP，后者是 PR 装配链留下的 rootfs；
两者不能作为同一套发布候选。最终验收前必须重新构建，使 ESP 的
`kernel-release`、`/usr/lib/modules` 与镜像 manifest 完全一致。

## 代码与审计修复

`scripts/profiles/fedora.json` 已显式包含：

```json
"systemd-pam",
"dbus-daemon",
```

`scripts/build-clean-rootfs.py` 的最终审计增加了包与文件的硬门槛：

* RPM 数据库必须包含 `dbus-daemon` 与 `systemd-pam`；
* 必须存在 `/usr/bin/dbus-daemon`；
* 必须存在 `/usr/bin/dbus-run-session`；
* 必须存在 `/usr/lib64/security/pam_systemd.so`；
* 审计结果新增 `missing_session_packages`，缺任意一项即
  `audit_passed=false`，不得发布镜像。

`tests/check_clean_rootfs.py` 固定检查上述字符串和审计字段。
`tests/check_rootfs_plan.py` 继续检查 plan 中显式包含两个子包。

## 最终 PR 验收门槛

黑屏修复不能只在已有 rootfs 上补 RPM 后判断成功。合成后的 PR 必须按
以下顺序完成：

1. 从发布的 RootFS 与 ESP 资产重新生成镜像，审计结果为
   `audit_passed=true`，且两个会话包和三个关键文件齐全；
2. 从设备原始 GPT/UFS 布局开始，按安装文档重新分区、写 ESP 和
   `sunuefi_root`，不沿用今晚的热修复 rootfs；
3. 冷启动进入 Linux，串口确认 `systemd`、`gdm`、
   `/run/user/1000/bus` 和 `piano-display` 均为正常状态；
4. 屏幕实际显示 GNOME 桌面，并且重启一次后仍可复现；
5. 保留启动日志、`systemctl --failed`、DRM/backlight 状态和画面证据。

只有完成这五步，才能把 PR 标记为“设备验收通过”。当前记录只能证明
根因与热修复有效，不能替代发布资产的 GPT 起点测试。
