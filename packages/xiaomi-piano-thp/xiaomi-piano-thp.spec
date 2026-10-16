%global debug_package %{nil}

Name:           xiaomi-piano-thp
Version:        0.2.0
Release:        1%{?dist}
Summary:        Touch Host Processor (THP) and Xiaomi Focus Pen driver for Xiaomi Pad 8 Pro (piano)
License:        Apache-2.0 AND BSD-2-Clause-Patent AND GPL-2.0-only
URL:            https://github.com/FFT-infra/piano-fedora-test
Source0:        %{name}-%{version}.tar.gz

ExclusiveArch:  aarch64

BuildRequires:  systemd-rpm-macros
Requires:       systemd
Requires:       python3
Requires:       python3-evdev
Requires:       bluez

Provides:       piano-touch = %{version}-%{release}

%description
Touch Host Processor (THP) multitouch input daemon, 5-stage Kalman temporal
filtering, jitter lock (24px deadband), axis lock, non-linear physical grid
calibration (32000x21360 at 134x131 units/mm), and Xiaomi Focus Pen (P81C)
pressure/tilt input pipeline for Xiaomi Pad 8 Pro (SM8750 piano).

%prep
%autosetup

%build
# Precompiled native runtime binaries and calibration maps

%install
mkdir -p %{buildroot}
cp -a usr %{buildroot}/

%files
%{_bindir}/piano-touch-view
%{_prefix}/lib/piano/touch-start
%{_prefix}/lib/piano/touch-input
%{_prefix}/lib/piano/pen-bluetooth
%{_unitdir}/piano-touch.service
%dir %{_datadir}/piano/touch
%{_datadir}/piano/touch/piano-csot.json
%{_datadir}/piano/touch/piano-boe.json
%dir %{_datadir}/piano/pen
%{_datadir}/piano/pen/p81c-radio.json
%dir %{_datadir}/piano/pen/calibration
%{_datadir}/piano/pen/calibration/piano-csot.ini
%{_datadir}/piano/pen/calibration/piano-boe.ini

%post
%systemd_post piano-touch.service
systemctl enable piano-touch.service || :

%preun
%systemd_preun piano-touch.service

%postun
%systemd_postun_with_restart piano-touch.service

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.2.0-1
- Modularize THP touch and pen stack with 5-stage Kalman filtering and 32000x21360 calibration
