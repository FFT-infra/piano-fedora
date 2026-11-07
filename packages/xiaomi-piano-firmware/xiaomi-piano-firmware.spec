%global debug_package %{nil}
%global _firmwaredir %{_prefix}/lib/firmware
# 厂商固件叠层取自 SunUEFI 的 vendor/firmware/hyperos4-*：cdsp 与指纹固件
# 上游 piano-firmware 里没有，adsp 也是另一版 ROM。三份都带 manifest.json，
# 逐个文件记 bytes 与 sha256，由 firmware-overlays.py 在写盘前核对。
# 提交号与 sources.lock.json 的 sunuefi pin 相同，由 tests/check_firmware_overlays.py 守住。
%global sunuefi_commit afe922bc0d9f230dd05dc0dc9435ae6d6322a31b

Name:           xiaomi-piano-firmware
Version:        20261108
Release:        1%{?dist}
Summary:        Firmware binaries for Xiaomi Pad 8 Pro (piano)
# 厂商没有给出书面许可。这里按 Fedora 处理同类不可再分发固件的写法标注
# （`linux-firmware` 用的就是 LicenseRef-Callaway-Redistributable-no-modification-permitted），
# 只按“设备所有者自用”分发，详见仓库根目录的 NOTICE。
License:        LicenseRef-Callaway-Redistributable-no-modification-permitted
URL:            https://github.com/blu-sharky/piano-firmware
Source0:        https://github.com/blu-sharky/piano-firmware/archive/05fc37c09ca8146eecfc4920f8485479db140ae5/piano-firmware-05fc37c.tar.gz
Source1:        https://github.com/slwyts/Project-SunUEFI/archive/%{sunuefi_commit}/Project-SunUEFI-%{sunuefi_commit}.tar.gz
# 同目录的叠层安装脚本，进 SRPM，COPR 从源包重建时也能跑。
Source2:        firmware-overlays.py

BuildArch:      noarch
BuildRequires:  python3

Provides:       piano-firmware = %{version}-%{release}

%description
Firmware binaries for Xiaomi Pad 8 Pro (piano, SM8750):
- Qualcomm Adreno 830 GPU firmware (GMU, AQE, SQE, ZAP)
- Qualcomm Iris VPU35 video decoder firmware
- Qualcomm AudioReach DSP topology & FourSemi FS19xx amplifier DSP
- Novatek NT36532 touch controller firmware (BOE & CSOT panels)
- Qualcomm WCN7850 / PEACH WiFi (ath12k) and Bluetooth (qca)
- Qualcomm Hexagon ADSP and CDSP DSP firmware images and the Goodix
  fingerprint controller firmware, taken from the vendor ROM overlays

%prep
%autosetup -n piano-firmware-05fc37c09ca8146eecfc4920f8485479db140ae5
# 上游树自带 SHA256SUMS，99 个固件逐个核对。之前只有 Source0 的提交号，
# 拿到的 tarball 内容被换掉也发现不了。
(cd firmware && sha256sum --check --quiet SHA256SUMS)
# 叠层的目录放在 Source1 旁边，内容在 %install 里按各自的 manifest 核对。
tar -xf %{SOURCE1} -C %{_builddir}

%install
mkdir -p %{buildroot}%{_firmwaredir}
cp -a firmware/* %{buildroot}%{_firmwaredir}/
# Clean non-firmware metadata if present
rm -f %{buildroot}%{_firmwaredir}/SHA256SUMS 2>/dev/null || true
# 先叠上游的 99 个文件，再换成厂商 ROM 的那几份。adsp / cdsp / 指纹三份
# 都是整份覆盖，缺了 cdsp.mdt 设备上的 remoteproc1 直接起不来。
python3 %{SOURCE2} --sunuefi %{_builddir}/Project-SunUEFI-%{sunuefi_commit} \
        --firmware %{buildroot}%{_firmwaredir}
find %{buildroot}%{_firmwaredir} -type f -exec chmod 0644 {} \;

%files
%license LICENSES/*
%{_firmwaredir}/*

%changelog
* Sun Nov 08 2026 FFT-infra <dev@piano.invalid> - 20261108-1
- 叠加 SunUEFI 的三份厂商固件：ADSP 换 ROM、补上 CDSP 与 Goodix 指纹固件
- 叠层的每个文件按 manifest.json 的 bytes 与 sha256 核对后再写盘

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 20261015-1
- Initial standalone firmware package for Xiaomi Pad 8 Pro (piano)
