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
# CMakeLists.txt:2 的 project() 同时启用 C 与 CXX；只装 gcc 时干净 chroot 里
# 报 "Could not find the compiler specified in the environment variable CXX:
# g++"（COPR build 11181697）。本地矩阵容器一直装着 gcc-c++，所以从没暴露。
BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  pkgconfig(dbus-1)
BuildRequires:  pkgconfig(polkit-gobject-1)
BuildRequires:  systemd-rpm-macros
# 上游 CMakeLists 的 pkg_check_modules 要求 json-c、glib-2.0>=2.56，
# -DBUILD_GUI=ON 还要 gtk4>=4.6 与 libadwaita-1>=1.2。之前只有本地矩阵
# 容器手工装了这四个包，spec 自己没声明，COPR 的干净 chroot 会直接失败。
BuildRequires:  glib2-devel
BuildRequires:  json-c-devel
BuildRequires:  gtk4-devel
BuildRequires:  libadwaita-devel

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
