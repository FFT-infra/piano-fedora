%global debug_package %{nil}

Name:           alsa-xiaomi-piano
Version:        0.1.0
Release:        1%{?dist}
Summary:        ALSA UCM2 audio configuration for Xiaomi Pad 8 Pro (piano)
License:        BSD-3-Clause
URL:            https://github.com/FFT-infra/piano-fedora
Source0:        %{name}-%{version}.tar.gz
BuildArch:      noarch

Requires:       alsa-ucm
Requires:       alsa-lib >= 1.2.10

%description
ALSA Use Case Manager (UCM2) profile for Xiaomi Pad 8 Pro (SM8750 piano):
- High-fidelity quad-speaker configuration with FourSemi FS19xx amplifiers
- Safe amplifier hardware ceiling (192 default, capped at 215) to prevent physical transducer damage
- VA digital macro microphone boost (+40 dB) for clear voice capture
- Automatic profile selection on CardId matching Xiaomi Pad 8 Pro

%prep
%autosetup

%build
# Configuration files only; nothing to compile

%install
mkdir -p %{buildroot}
cp -a usr %{buildroot}/

%files
%{_datadir}/alsa/ucm2/Qualcomm/sm8750/Xiaomi-Pad-8-Pro/Xiaomi-Pad-8-Pro.conf
%{_datadir}/alsa/ucm2/Qualcomm/sm8750/Xiaomi-Pad-8-Pro/HiFi.conf
# UCM2 先按 /conf.d 里的同名条目找设备，再进 Qualcomm/ 目录读详细配置。
# 两处内容相同是有意的：删掉任何一份，设备要么不出现在 ucm2 列表里，要么
# 找不到自己的 HiFi.conf。不要在清理时合并它们。
"%{_datadir}/alsa/ucm2/conf.d/sm8750/Xiaomi Pad 8 Pro.conf"

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- Initial package for Xiaomi Pad 8 Pro (piano)
