%global debug_package %{nil}

Name:           piano-fedora-configs
Version:        0.1.0
Release:        2%{?dist}
Summary:        System configuration, hardware fixes and power management for Xiaomi Pad 8 Pro (piano)
License:        MIT AND Apache-2.0 AND GPL-2.0-only
URL:            https://github.com/FFT-infra/piano-fedora-test
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

BuildRequires:  systemd-rpm-macros
Requires:       systemd
Requires:       qbootctl
Requires:       util-linux
Requires:       f2fs-tools
Requires:       python3-evdev

Obsoletes:      piano-session-fixes < 0.3.0
Provides:       piano-session-fixes = %{version}-%{release}

%description
System integration, hardware platform fixes, and power management configuration
for Xiaomi Pad 8 Pro (SM8750 piano) running Fedora 44 / GNOME:
- qbootctl A/B slot stability: automatically marks slot Successful: 1 on boot
- f2fs-root-shutdown hook: safely flushes F2FS checkpoint and remounts root read-only
- PMIC RTC software clock floor restore and periodic background save
- Gunyah hypervisor DT node masking to restore GNOME battery & power panel
- GNOME auto-rotation fake tablet mode daemon (SW_TABLET_MODE)
- Power key and hall sensor udev, sleep, and user service rules
- Interconnect bwmon and RPMh stats module auto-loading
- Dual-boot SunUEFI / Android return launchers and polkit rules
- CPU schedutil 2ms rate limit tuning for instantaneous touch response
- Static tablet chassis identification for systemd and desktop

%prep
%autosetup

%build
# Architecture-independent configurations and scripts; nothing to compile

%install
mkdir -p %{buildroot}
cp -a usr etc %{buildroot}/

%files
%config(noreplace) %{_sysconfdir}/systemd/system/qbootctl.service.d/piano.conf
%config(noreplace) %{_sysconfdir}/systemd/sleep.conf.d/piano.conf
%config(noreplace) %{_sysconfdir}/modules-load.d/piano-bwmon.conf
%config(noreplace) %{_sysconfdir}/modules-load.d/piano-stats.conf
%config(noreplace) %{_sysconfdir}/udev/rules.d/70-piano-power-button.rules
%config(noreplace) %{_sysconfdir}/dconf/db/local.d/00-power

%{_unitdir}/piano-clock-restore.service
%{_unitdir}/piano-clock-save.service
%{_unitdir}/piano-clock-save.timer
%{_unitdir}/piano-fake-tablet-mode.service
%{_unitdir}/piano-physical-platform.service
%{_prefix}/lib/systemd/system-shutdown/f2fs-root-shutdown
%{_userunitdir}/piano-power-button.service

%{_libexecdir}/piano-clock-restore
%{_libexecdir}/piano-clock-save
%{_libexecdir}/piano-fake-tablet-mode
%{_libexecdir}/piano-physical-platform
%{_prefix}/lib/piano/power-button

%{_bindir}/piano-collect-boot
%{_bindir}/piano-next-boot
%{_bindir}/piano-next-boot-configure
%{_libexecdir}/piano-next-boot-helper
/usr/local/sbin/piano-boot-request

%{_prefix}/lib/tmpfiles.d/piano-machine-info.conf
%{_prefix}/lib/tmpfiles.d/cpufreq-schedutil.conf
%{_datadir}/piano/machine-info

%{_datadir}/applications/org.sunuefi.ReturnAndroid.desktop
%{_datadir}/applications/org.sunuefi.BootMenu.desktop
%{_datadir}/polkit-1/actions/org.sunuefi.boot-request.policy
%{_datadir}/polkit-1/rules.d/49-piano-boot-request.rules

%post
%systemd_post piano-clock-restore.service piano-clock-save.timer piano-fake-tablet-mode.service piano-physical-platform.service
%systemd_user_post piano-power-button.service
systemctl enable piano-clock-restore.service piano-clock-save.timer piano-fake-tablet-mode.service piano-physical-platform.service qbootctl.service || :
systemctl --global enable piano-power-button.service || :
if [ -x %{_bindir}/dconf ]; then
    %{_bindir}/dconf update || :
fi
systemd-tmpfiles --create %{_prefix}/lib/tmpfiles.d/piano-machine-info.conf %{_prefix}/lib/tmpfiles.d/cpufreq-schedutil.conf || :

%preun
%systemd_preun piano-clock-restore.service piano-clock-save.timer piano-fake-tablet-mode.service piano-physical-platform.service
%systemd_user_preun piano-power-button.service

%postun
%systemd_postun_with_restart piano-clock-restore.service piano-clock-save.timer piano-fake-tablet-mode.service piano-physical-platform.service

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-2
- Obsolete monolithic piano-session-fixes, add tmpfiles and machine-info tablet configs, drop conflicting plymouth bootlog

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- Initial package for Xiaomi Pad 8 Pro (piano)
