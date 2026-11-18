%global debug_package %{nil}

Name:           piano-droidloom-extra
Version:        0.1.0
Release:        1%{?dist}
Summary:        Optional Droidloom payload installer for piano
License:        MIT
URL:            https://github.com/FFT-infra/piano-fedora
ExclusiveArch:  aarch64
Source0:        piano-droidloom-extra

Requires:       bash
Requires:       glibc
Requires:       libgcc
Requires:       curl
Requires:       tar
Requires:       zstd
Requires:       systemd
Requires:       polkit
Requires:       util-linux
Requires:       mesa-libgbm
Requires:       libxkbcommon
Requires:       libwayland-server
Requires:       libdrm
Requires:       pipewire
Requires:       pipewire-libs
Requires:       e2fsprogs
Requires:       iproute
Requires:       nftables
Requires:       iptables-nft
Requires:       android-tools
Requires:       vulkan-loader

%description
Installs the optional Droidloom Android-in-Wayland runtime on piano.

The payload is not rebuilt in this repository. This package ships a small
installer that downloads the official aarch64 packages from the upstream
GitHub release, verifies the pinned SHA256 of both files, and lays the
verified tree under /usr. `piano-droidloom-extra remove` deletes exactly the
recorded files and keeps /etc/droidloom and the Android user data.

The default image and the default COPR do not install this package.

%prep

%build

%install
install -Dpm0755 %{SOURCE0} %{buildroot}%{_bindir}/piano-droidloom-extra

%post
echo "Droidloom payload is not installed yet."
echo "Run 'sudo piano-droidloom-extra install', then 'droidloomctl start' as your desktop user."

%preun
if [ "$1" -eq 0 ] && [ -x %{_bindir}/piano-droidloom-extra ]; then
    %{_bindir}/piano-droidloom-extra remove ||
        echo "warning: piano-droidloom-extra remove failed; payload files may remain" >&2
fi

%files
%{_bindir}/piano-droidloom-extra

%changelog
* Wed Nov 19 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- Optional Droidloom 0.1.0-46 payload installer pinned to the upstream
  release SHA256; keeps Android data on removal
