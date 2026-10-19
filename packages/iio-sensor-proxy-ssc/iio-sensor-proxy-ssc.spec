%global debug_package %{nil}

Name:           iio-sensor-proxy-ssc
Version:        3.9
Release:        2%{?dist}
Provides:       iio-sensor-proxy = %{version}-%{release}
Provides:       iio-sensor-proxy%{?_isa} = %{version}-%{release}
Obsoletes:      iio-sensor-proxy < %{version}-%{release}
Summary:        IIO sensors to D-Bus proxy (with Qualcomm SSC support for piano)
License:        GPL-3.0-or-later
URL:            https://gitlab.freedesktop.org/hadess/iio-sensor-proxy
Source0:        %{url}/-/archive/%{version}/iio-sensor-proxy-%{version}.tar.gz
Source1:        49-iio-sensor-proxy.rules

ExclusiveArch:  aarch64

BuildRequires:  gcc
BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  glib2-devel
BuildRequires:  libgudev-devel
BuildRequires:  polkit-devel
BuildRequires:  pkgconfig(udev)
BuildRequires:  dbus-devel
BuildRequires:  libssc
BuildRequires:  libqmi-devel
BuildRequires:  libmbim-devel
BuildRequires:  systemd-rpm-macros

Requires:       dbus
Requires:       glib2%{?_isa}
Requires:       libgudev%{?_isa}
Requires:       polkit%{?_isa}
Requires:       libssc%{?_isa}
Requires:       systemd

%description
iio-sensor-proxy built with Qualcomm Sensor Core (SSC) support enabled.
Proxies IIO sensor data to D-Bus, enabling accelerometer auto-rotation,
ambient light sensor, and proximity sensor on Xiaomi Pad 8 Pro (piano).

%prep
%autosetup -n iio-sensor-proxy-%{version}

%build
%meson \
    -Db_lto=true \
    -Dssc-support=enabled \
    -Dsystemdsystemunitdir=%{_unitdir}
%meson_build

%install
%meson_install
install -Dm644 %{SOURCE1} %{buildroot}%{_datadir}/polkit-1/rules.d/49-iio-sensor-proxy.rules

%files
%{_bindir}/monitor-sensor
%{_libexecdir}/iio-sensor-proxy
%{_unitdir}/iio-sensor-proxy.service
%{_udevrulesdir}/80-iio-sensor-proxy.rules
%{_datadir}/dbus-1/system.d/net.hadess.SensorProxy.conf
%{_datadir}/polkit-1/actions/net.hadess.SensorProxy.policy
%{_datadir}/polkit-1/rules.d/49-iio-sensor-proxy.rules

%post
%systemd_post iio-sensor-proxy.service
systemctl enable iio-sensor-proxy.service || :

%preun
%systemd_preun iio-sensor-proxy.service

%postun
%systemd_postun_with_restart iio-sensor-proxy.service

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 3.9-2
- Add Obsoletes to cleanly replace standard Fedora iio-sensor-proxy

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 3.9-1
- Formalize iio-sensor-proxy with native Qualcomm SSC enabled
