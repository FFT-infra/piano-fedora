%global _annotated_build 0
%global debug_package %{nil}

Name:           piano-camera
Version:        0.1.0
Release:        1%{?dist}
Summary:        Camera pipeline and NEON-accelerated v4l2loopback daemon for Xiaomi Pad 8 Pro (piano)
License:        GPL-2.0-only AND Apache-2.0
URL:            https://github.com/FFT-infra/piano-fedora-test
Source0:        %{name}-%{version}.tar.gz

ExclusiveArch:  aarch64

BuildRequires:  gcc
BuildRequires:  systemd-rpm-macros
Requires:       systemd

Provides:       piano-camera-daemon = %{version}-%{release}

%description
Camera hardware bring-up service and resident user-space relay daemon (piano-camerad)
for Xiaomi Pad 8 Pro (SM8750 piano). Features handwritten 16-pixel parallel AArch64
NEON NV12->BGRx conversion pipeline delivering 1.9ms low-latency frames into
v4l2loopback nodes, bypassing Mesa GTK diagonal tearing artifacts.

%prep
%autosetup

%build
gcc %{optflags} -Isrc src/piano-camerad.c -lm -lpthread -o piano-camerad

%install
mkdir -p %{buildroot}%{_prefix}/lib/piano
cp -a usr etc %{buildroot}/
install -m 0755 piano-camerad %{buildroot}%{_prefix}/lib/piano/piano-camerad

%files
%{_prefix}/lib/piano/piano-camerad
%{_prefix}/lib/piano/camera-start
%{_unitdir}/piano-camerad.service
%{_unitdir}/piano-camera.service
%config(noreplace) %{_sysconfdir}/udev/rules.d/70-piano-camera.rules
%config(noreplace) %{_sysconfdir}/wireplumber/wireplumber.conf.d/50-piano-camera.conf

%post
%systemd_post piano-camerad.service piano-camera.service
systemctl enable piano-camerad.service piano-camera.service || :

%preun
%systemd_preun piano-camerad.service piano-camera.service

%postun
%systemd_postun_with_restart piano-camerad.service piano-camera.service

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- Initial package for Xiaomi Pad 8 Pro camera relay and NEON conversion
