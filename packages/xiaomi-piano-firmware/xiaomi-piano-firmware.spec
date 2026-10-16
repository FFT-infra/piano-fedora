%global debug_package %{nil}
%global _firmwaredir %{_prefix}/lib/firmware

Name:           xiaomi-piano-firmware
Version:        20261015
Release:        1%{?dist}
Summary:        Firmware binaries for Xiaomi Pad 8 Pro (piano)
License:        Redistributable, no modification permitted
URL:            https://github.com/blu-sharky/piano-firmware
Source0:        https://github.com/blu-sharky/piano-firmware/archive/05fc37c09ca8146eecfc4920f8485479db140ae5/piano-firmware-05fc37c.tar.gz

BuildArch:      noarch

Provides:       piano-firmware = %{version}-%{release}

%description
Firmware binaries for Xiaomi Pad 8 Pro (piano, SM8750):
- Qualcomm Adreno 830 GPU firmware (GMU, AQE, SQE, ZAP)
- Qualcomm Iris VPU35 video decoder firmware
- Qualcomm AudioReach DSP topology & FourSemi FS19xx amplifier DSP
- Novatek NT36532 touch controller firmware (BOE & CSOT panels)
- Qualcomm WCN7850 / PEACH WiFi (ath12k) and Bluetooth (qca)
- Qualcomm Hexagon ADSP DSP firmware images

%prep
%autosetup -n piano-firmware-05fc37c09ca8146eecfc4920f8485479db140ae5

%install
mkdir -p %{buildroot}%{_firmwaredir}
cp -a * %{buildroot}%{_firmwaredir}/
# Clean non-firmware metadata if present
rm -f %{buildroot}%{_firmwaredir}/README* %{buildroot}%{_firmwaredir}/LICENSE* 2>/dev/null || true
find %{buildroot}%{_firmwaredir} -type f -exec chmod 0644 {} \;

%files
%{_firmwaredir}/*

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 20261015-1
- Initial standalone firmware package for Xiaomi Pad 8 Pro (piano)
