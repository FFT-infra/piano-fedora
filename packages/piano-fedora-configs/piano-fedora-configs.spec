%global debug_package %{nil}

Name:           piano-fedora-configs
Version:        0.1.0
Release:        15%{?dist}
Summary:        System configuration, hardware fixes and power management for Xiaomi Pad 8 Pro (piano)
License:        MIT AND BSD-2-Clause-Patent AND GPL-2.0-only
URL:            https://github.com/FFT-infra/piano-fedora
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  systemd-rpm-macros
Requires:       systemd
Requires:       qbootctl
Requires:       util-linux
Requires:       f2fs-tools
Requires:       python3-evdev
# piano-power-button 用 gi.repository 的 Gio/GLib 走会话总线。
Requires:       python3-gobject

# Replaces the older standalone piano-runtime / piano-session-fixes packages.
# The bound is written as `< %{version}-%{release}` so it can never reach this
# package's own provide; a hard-coded number silently becomes wrong the next
# time the version is bumped.
Obsoletes:      piano-session-fixes < %{version}-%{release}
Provides:       piano-session-fixes = %{version}-%{release}
Obsoletes:      piano-runtime < %{version}-%{release}
Provides:       piano-runtime = %{version}-%{release}

%description
System integration, hardware platform fixes, and power management configuration
for Xiaomi Pad 8 Pro (SM8750 piano) running Fedora 44 / GNOME:
- qbootctl A/B slot stability: automatically marks slot Successful: 1 on boot
- f2fs-root-shutdown hook: safely flushes F2FS checkpoint and remounts root read-only
- PMIC RTC software clock floor restore and periodic background save
- Gunyah hypervisor DT node masking to restore GNOME battery & power panel
- GNOME auto-rotation fake tablet mode daemon (SW_TABLET_MODE)
- Power key and hall sensor udev rules plus the GNOME power-button user service
- Interconnect bwmon and RPMh stats module auto-loading
- Dual-boot SunUEFI / Android return launchers and polkit rules
- CPU schedutil 2ms rate limit tuning for instantaneous touch response
- TuneD profiles and the tuned-ppd power mode mapping for GNOME, bridged to
  uperf-linux's own power modes
- Chinese default locale and the pinyin input source for GNOME
- Static tablet chassis identification for systemd and desktop

%prep
%autosetup

%build
# Architecture-independent configurations and scripts; nothing to compile

%install
mkdir -p %{buildroot}
cp -a usr etc %{buildroot}/
# Fedora is merged-usr: /usr/local/sbin is a symlink to bin (owned by the
# `filesystem` package). Listing both /usr/local/bin/X and /usr/local/sbin/X
# therefore names the same inode twice, and rpm 6 aborts the whole transaction:
#
#   file /usr/local/sbin/piano-debug-bootstrap conflicts between attempted
#   installs of piano-fedora-configs-0.1.0-4.fc44.noarch and
#   piano-fedora-configs-0.1.0-4.fc44.noarch
#
# Only the canonical /usr/local/bin paths are shipped. Callers that spell the
# rescue helper as /usr/local/sbin/... still work, because that directory is a
# symlink to bin.
chmod 0755 %{buildroot}%{_bindir}/* %{buildroot}%{_libexecdir}/*
chmod 0755 %{buildroot}/usr/local/bin/* %{buildroot}%{_prefix}/lib/piano/* || true

%files
# 这份 .repo 不带 noreplace：内核升级放开之后，已装设备上那份还带着
# exclude=kernel-piano，noreplace 会把它留成旧文件，升级永远拿不到内核。
# 与下面看门狗那条是同一个理由：要换掉的是文件内容，不是保护本地改动。
%config %{_sysconfdir}/yum.repos.d/piano-fedora-copr.repo
%config(noreplace) %{_sysconfdir}/systemd/system/qbootctl.service.d/piano.conf
%config(noreplace) %{_sysconfdir}/systemd/sleep.conf.d/piano.conf
%config(noreplace) %{_sysconfdir}/systemd/logind.conf.d/piano.conf
%config(noreplace) %{_sysconfdir}/modules-load.d/piano-bwmon.conf
%config(noreplace) %{_sysconfdir}/modules-load.d/piano-stats.conf
# 不加 noreplace，跟上面几份不同，这是有意的。早先的 initramfs 用 printf
# 直接写这个文件，绕过 rpm 的 SELinux 事务，留下的副本标签是 unlabeled_t，
# systemd-modules-load 每次启动都被拒。`%config(noreplace)` 遇到已存在的文件
# 会把新版本落成 .rpmnew 并保留旧文件，那份 unlabeled_t 永远换不掉——实测
# 从故障态升级到 0.1.0-7 后 rpm -qf 认了主，ls -Z 仍是 unlabeled_t。
# 改成 %config（无 noreplace），升级时把旧文件存成 .rpmorig、装上带正确
# 标签的新文件，实测标签变回 modules_conf_t。
%config %{_sysconfdir}/modprobe.d/piano-blacklist-watchdog.conf
%config %{_sysconfdir}/modprobe.d/dist-alsa.conf
%config(noreplace) %{_sysconfdir}/udev/rules.d/70-piano-power-button.rules
%config(noreplace) %{_sysconfdir}/dconf/db/local.d/00-power

%{_unitdir}/piano-clock-restore.service
%{_unitdir}/piano-clock-save.service
%{_unitdir}/piano-clock-save.timer
%{_unitdir}/piano-fake-tablet-mode.service
%{_unitdir}/piano-physical-platform.service
%{_unitdir}/piano-first-boot-ssh-key.service
%{_unitdir}/xiaomi-charger-mode.service
%{_prefix}/lib/systemd/system-shutdown/f2fs-root-shutdown
%{_unitdir}/piano-linux-debug.service
%{_userunitdir}/piano-power-button.service

%{_libexecdir}/piano-clock-restore
%{_libexecdir}/piano-clock-save
%{_libexecdir}/piano-fake-tablet-mode
%{_libexecdir}/piano-physical-platform
%{_libexecdir}/xiaomi-charger-mode
%{_prefix}/lib/piano/power-button
%{_prefix}/lib/piano/piano-power-mode
%{_prefix}/lib/piano/first-boot-ssh-key
%{_prefix}/lib/piano/piano-ram-hardware-prepare
%{_prefix}/lib/piano/piano-disk-hardware-prepare
%{_prefix}/lib/piano/piano_dma_routes.py
%{_prefix}/lib/piano/piano_dma_contexts.py
%{_prefix}/lib/piano/piano-boot-task-snapshot

%{_bindir}/piano-collect-boot
%{_bindir}/piano-next-boot
%{_libexecdir}/piano-next-boot-helper
/usr/local/bin/piano-debug-bootstrap
/usr/local/bin/piano-debug-stage

%{_prefix}/lib/tmpfiles.d/piano-machine-info.conf
%{_prefix}/lib/tmpfiles.d/cpufreq-schedutil.conf
%config(noreplace) %{_sysconfdir}/tuned/piano-ppd.conf
%config(noreplace) %{_sysconfdir}/locale.conf
%config(noreplace) %{_sysconfdir}/dconf/db/local.d/00-input-sources
%{_prefix}/lib/tuned/profiles/piano-power-saver/tuned.conf
%{_prefix}/lib/tuned/profiles/piano-power-saver/apply.sh
%{_prefix}/lib/tuned/profiles/piano-balanced/tuned.conf
%{_prefix}/lib/tuned/profiles/piano-balanced/apply.sh
%{_prefix}/lib/tuned/profiles/piano-performance/tuned.conf
%{_prefix}/lib/tuned/profiles/piano-performance/apply.sh
%{_unitdir}/tuned-ppd.service.d/50-piano-ppd.conf
%{_datadir}/piano/machine-info

%{_datadir}/applications/org.sunuefi.ReturnAndroid.desktop
%{_datadir}/applications/org.sunuefi.BootMenu.desktop
%{_datadir}/polkit-1/actions/org.sunuefi.boot-request.policy
%{_datadir}/polkit-1/rules.d/49-piano-boot-request.rules

%post
%systemd_post piano-clock-restore.service piano-clock-save.timer piano-fake-tablet-mode.service piano-physical-platform.service xiaomi-charger-mode.service
systemctl enable piano-clock-restore.service piano-clock-save.timer piano-fake-tablet-mode.service piano-physical-platform.service xiaomi-charger-mode.service qbootctl.service || :
# 电源键助手是 GNOME 专属的 user 单元，由装配脚本按桌面档挂进
# graphical-session.target.wants；包本身不 enable，KDE 等桌面不装它。
# 旧安装把它当 system 单元 enable 过，rpm 只会删 /usr/lib/systemd/system
# 下的文件，那条链接会指向一个不存在的单元。这段必须留在 %post，因为升级
# 时 %preun 跑的是旧包的脚本。
systemctl --no-reload disable piano-power-button.service >/dev/null 2>&1 || :
rm -f /etc/systemd/system/multi-user.target.wants/piano-power-button.service
if [ -x %{_bindir}/dconf ]; then
    %{_bindir}/dconf update || :
fi
systemd-tmpfiles --create %{_prefix}/lib/tmpfiles.d/piano-machine-info.conf %{_prefix}/lib/tmpfiles.d/cpufreq-schedutil.conf || :
# 0.1.0-10 起不再有 piano-boot-request-autoconfig.service。rpm 只删
# /usr/lib/systemd/system 下的 unit 文件，旧安装 enable 出来的
# /etc/systemd/system/multi-user.target.wants 链接会留下，指向一个不存在的
# unit。这段必须放在 %post：升级时 %preun 跑的是旧包的脚本，0.1.0-9 里没有
# 清理逻辑。
systemctl --no-reload disable piano-boot-request-autoconfig.service >/dev/null 2>&1 || :
rm -f /etc/systemd/system/multi-user.target.wants/piano-boot-request-autoconfig.service

%preun
%systemd_preun piano-clock-restore.service piano-clock-save.timer piano-fake-tablet-mode.service piano-physical-platform.service xiaomi-charger-mode.service

%postun
%systemd_postun_with_restart piano-clock-restore.service piano-clock-save.timer piano-fake-tablet-mode.service piano-physical-platform.service xiaomi-charger-mode.service

%changelog
* Sat Nov 14 2026 FFT-infra <dev@piano.invalid> - 0.1.0-15
- 电源键助手换成 SunUEFI 的 GNOME 实现：不再独占电源键，锁屏与熄屏走
  Mutter 的 PowerSaveMode 和 ScreenSaver 异步 D-Bus，随 graphical-session
  生命周期启停。单元改为 user 级，只在 GNOME 档启用

* Thu Nov 12 2026 FFT-infra <dev@piano.invalid> - 0.1.0-14
- 虚拟平板模式跟随上游 nanosic 的 `Nanosic WN8030 tablet mode` 节点。键盘盖
  接上时不把 `SW_TABLET_MODE` 置 ON，避免 libinput 屏蔽悬浮键盘触控板（#22）

* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 0.1.0-13
- 默认中文：`/etc/locale.conf` 写 `zh_CN.UTF-8`，GNOME 输入源默认带
  `ibus`/`libpinyin`，配合 base 包里的 `glibc-langpack-zh`

* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 0.1.0-12
- 补上 GNOME 的三档电源策略：三份 piano tuned profile 与 piano-ppd.conf，
  由 tuned-ppd.service.d 的 BindReadOnlyPaths 呈现给 tuned-ppd，不动 tuned
  自己拥有的 conffile

* Sun Nov 08 2026 FFT-infra <dev@piano.invalid> - 0.1.0-11
- 用同名 modprobe 配置遮住 alsa-lib 的 dist-alsa.conf，不再加载内核没有的 snd-seq

* Mon Oct 26 2026 FFT-infra <dev@piano.invalid> - 0.1.0-10
- 跟到 SunUEFI `0a8ec14`：上游在 `7dbabdc` 里删掉了 `/etc/piano/boot-request.json`
  整套机制，活动槽改为只读主备 GPT（两份头与条目 CRC 都校验）加高通 A/B 属性
  判定。`piano-next-boot` 与 `piano-next-boot-helper` 换成上游版本，
  `piano-next-boot-configure`、`piano-boot-request-autoconfig` 及其 service 一并
  删除。必须升 Release，否则 0.1.0-9 已装的设备不会失去这些文件

* Thu Oct 22 2026 FFT-infra <dev@piano.invalid> - 0.1.0-9
- udev 电源键规则改用真机上的盖子设备名：`hall_lid` 改成 `soc:piano-hall-ml`。
  旧名来自早期 DTS，规则从未匹配过任何设备，`event1` 因此没有 uaccess ACL；
  现在靠 piano 在 input 组的组权限才打得开。已经发布出去的同 NEVRA 包里内容
  不同，必须升 Release，否则 COPR 会对同一个 NVRA 给出两份不同的 RPM

* Wed Oct 22 2026 FFT-infra <dev@piano.invalid> - 0.1.0-8
- 放开内核在线升级：.repo 去掉 exclude=kernel-piano，改由 kernel-piano 的
  %posttrans 重建 initramfs 并重打 ESP 的 boot.img。这份 .repo 去掉 noreplace，
  否则已装设备上的旧文件会被留成 .rpmnew，排除规则永远消不掉

* Wed Oct 21 2026 FFT-infra <dev@piano.invalid> - 0.1.0-7
- 看门狗黑名单改由本包提供：initramfs 直接写 /etc/modprobe.d 会绕过 rpm 的
  SELinux 标签事务，留下 unlabeled_t 的无主文件，systemd-modules-load 被拒。
  已在 COPR 的 0.1.0-6 之后修复，必须升 Release 才能让 dnf upgrade 取到。
  这份文件不带 noreplace：已经处在故障态的设备升级时，noreplace 会把新版
  落成 .rpmnew、把 unlabeled_t 的旧文件留在原位，标签修不回来
* Tue Oct 20 2026 FFT-infra <dev@piano.invalid> - 0.1.0-6
- 收敛路径归属：收纳 xiaomi-charger-mode 与 logind 落点，交还 piano-boot-request
  给运行时（piano-next-boot-helper 要求的是那份原生实现），不再重复拥有
  SunUEFI 运行时派生的同一批文件
* Mon Oct 19 2026 FFT-infra <dev@piano.invalid> - 0.1.0-5
- Ship the COPR repository definition, with kernel-piano excluded from upgrades

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-4
- Package piano-ram-hardware-prepare, DMA route/context verifiers, and debug stage helpers into piano-fedora-configs

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-3
- Obsolete legacy piano-runtime, add boot-request autoconfig service and fix binary permissions

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-2
- Obsolete monolithic piano-session-fixes, add tmpfiles and machine-info tablet configs, drop conflicting plymouth bootlog

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- Initial package for Xiaomi Pad 8 Pro (piano)
