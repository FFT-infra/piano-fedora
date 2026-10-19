%global debug_package %{nil}

Name:           uperf-linux
Version:        0.1.2
Release:        2%{?dist}
Summary:        Userspace performance scheduler for Snapdragon devices (Xiaomi Pad 8 Pro)
License:        GPL-3.0-only
URL:            https://github.com/code002-2/uperf-linux
Source0:        https://github.com/code002-2/uperf-linux/archive/edfb14a235c3708b4e9627c819dc28c68596d0f1/uperf-linux-edfb14a.tar.gz
Source1:        config.piano.json
Patch0:         fedora.patch

ExclusiveArch:  aarch64

BuildRequires:  cmake
BuildRequires:  gcc
BuildRequires:  pkgconfig(dbus-1)
BuildRequires:  pkgconfig(polkit-gobject-1)
BuildRequires:  systemd-rpm-macros

Requires:       polkit
Requires:       systemd

%description
Uperf-Linux userspace performance scheduler tuned for Qualcomm Snapdragon 8 Elite (SM8750)
on Xiaomi Pad 8 Pro (piano):
- Instantaneous touch-boost response from evdev touchscreen gestures
- Multi-cluster CPU (6 Perf + 2 Prime) and Adreno GPU power and frequency management
- Resilient hotplug and early-boot inotify monitoring
- Safe thermal throttling (70/80/95/75°C) and recovery hysteresis

%prep
%autosetup -p1 -n uperf-linux-edfb14a235c3708b4e9627c819dc28c68596d0f1

%build
%cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_INSTALL_LIBDIR=lib -DBUILD_GUI=ON
%cmake_build

%install
%cmake_install
install -D -p -m 0644 %{SOURCE1} %{buildroot}%{_sysconfdir}/uperf-linux/config.json

%files
%{_bindir}/uperf-linux
%{_bindir}/uperfctl
%{_bindir}/uperf-gui
%{_bindir}/uperf-wizard
%{_unitdir}/uperf-linux.service
%dir %{_sysconfdir}/uperf-linux
%config(noreplace) %{_sysconfdir}/uperf-linux/config.json
%config(noreplace) %{_sysconfdir}/uperf-linux/perapp_powermode
%{_sysconfdir}/uperf-linux/sm8550.json
%{_datadir}/applications/uperf-gui.desktop
%{_datadir}/dbus-1/system.d/org.uperflinux.Daemon.conf
%{_datadir}/polkit-1/actions/org.uperflinux.policy
%{_datadir}/uperf-linux/
%license LICENSE

%post
%systemd_post uperf-linux.service
systemctl enable uperf-linux.service || :

%preun
%systemd_preun uperf-linux.service

%postun
%systemd_postun_with_restart uperf-linux.service

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.2-2
- Upstream hotplug/inotify fix and piano-tuned 32000x21360 touch & thermal profiles
