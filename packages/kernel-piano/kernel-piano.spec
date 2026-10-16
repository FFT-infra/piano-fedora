%undefine _debugsource_packages
%global debug_package %{nil}
%global KERNEL_VER 7.2.9
%global KERNEL_TAG piano-7.2.9
%global DEVICE_NAME piano
%global PLATFORM_NAME sm8750

Name:           kernel-piano
Version:        %{KERNEL_VER}
Release:        1.%{DEVICE_NAME}%{?dist}
Summary:        Mainline Linux kernel with hardware fixes for Xiaomi Pad 8 Pro (piano)
License:        GPL-2.0-only
URL:            https://github.com/blu-sharky/linux-piano
Source0:        %{url}/archive/refs/tags/%{KERNEL_TAG}.tar.gz#/%{name}-%{version}.tar.gz

ExclusiveArch:  aarch64

Provides:       kernel = %{version}-%{release}
Provides:       kernel-core = %{version}-%{release}
Provides:       kernel-modules = %{version}-%{release}
Provides:       kernel-modules-core = %{version}-%{release}

Requires:       dracut
Requires:       qbootctl
Requires:       f2fs-tools

%description
Mainline Linux kernel adapted for Xiaomi Pad 8 Pro (SM8750 piano):
- F2FS root filesystem and Fedora SELinux label support
- FourSemi FS19xx amplifier hardware volume clamp (capped at 215)
- DPU dual-INTF slave CTL reset deferral to eliminate screen shutdown tearing (0038)
- Iris VPU35 hardware video decoder key frame support (0037)
- Gunyah Type-1 hypervisor guest isolation profile
- NT36532E SPI frame read timeout error retention

%prep
%autosetup -p1 -n linux-piano-%{KERNEL_TAG}

%build
# Full kernel compilation invoked via standardized build-kernel pipeline or container
echo "Kernel build specification prepared for %{DEVICE_NAME}."

%install
# Kernel image, dtb and modules installation layout
mkdir -p %{buildroot}/boot %{buildroot}%{_prefix}/lib/modules

%files
/boot/*
%{_prefix}/lib/modules/*

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 7.2.9-1.piano
- Standalone kernel packaging spec with F2FS, DPU, Iris, and FS19xx hardware limit patches
