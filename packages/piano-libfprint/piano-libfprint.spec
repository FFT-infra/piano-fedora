%global debug_package %{nil}

# Fedora 的 libfprint-1.94.100 与 SunUEFI 的 pin 是同一份源码：v1.94.100 是
# 附注标签，解引用后就是 80a4b5ec。补丁把 piano_goodix 驱动编进来，并用
# piano-fingerprint 提供的源码归档在目标发行版 ABI 下直接编译后端。
Name:           piano-libfprint
Version:        1.94.100
Release:        3%{?dist}
Summary:        libfprint with the Piano Goodix signed-TA driver
License:        LGPL-2.1-or-later AND NIST-PD AND BSD-3-Clause AND (GPL-2.0-only WITH Linux-syscall-note)
URL:            https://gitlab.freedesktop.org/libfprint/libfprint
Source0:        %{url}/-/archive/v%{version}/libfprint-v%{version}.tar.gz
Source1:        20-piano.conf
Patch0:         0001-piano-goodix-uio.patch
# 后端源码跟着 libfprint 一起编：不再依赖跨发行版编译的静态库与 pkg-config。
Patch1:         0002-build-goodix-backend-from-source.patch
# meson 1.12 不再接受 foreach 单变量遍历 dict；关掉 introspection 时上游的
# else 分支一定会被求值，构建在编译前就停了。
Patch2:         0003-meson-dict-foreach.patch

ExclusiveArch:  aarch64

# 替换发行版同名包：Release 必须高于 Fedora 的 1.94.100-1，否则 dnf 会同时
# 装两份并因为文件冲突回滚。Fedora 以后更新 libfprint 版本时，这份包要跟着
# 重新对 pin。
Provides:       libfprint = %{version}-%{release}
Provides:       libfprint%{?_isa} = %{version}-%{release}
Obsoletes:      libfprint < %{version}-%{release}

BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  pkgconfig(glib-2.0) >= 2.50
BuildRequires:  pkgconfig(gusb) >= 0.3.0
BuildRequires:  pkgconfig(gudev-1.0)
BuildRequires:  pkgconfig(pixman-1)
BuildRequires:  pkgconfig(openssl) >= 3.0
# 后端源码归档与 TEE 头文件由 piano-fingerprint 提供；rpmb 后端还要
# scsi/scsi_bsg_ufs.h，这个来自发行版内核头。
BuildRequires:  piano-fingerprint
BuildRequires:  kernel-headers
BuildRequires:  systemd-rpm-macros

Requires:       glib2%{?_isa}
Requires:       libgusb%{?_isa}
Requires:       systemd

%description
Standard libfprint built with the Piano Goodix driver. The backend sources
from piano-fingerprint are compiled into the driver with this distribution's
ABI, so fprintd and GNOME can enroll and verify fingerprints on Xiaomi Pad 8
Pro (piano). The package replaces Fedora's libfprint; the only added file is
the fprintd drop-in that opens the UIO/TEE devices and the
/var/lib/piano-goodix state directory to the service.

%prep
%autosetup -p1 -n libfprint-v%{version}
ARCHIVE=%{_datadir}/piano-fingerprint/piano-goodix-sources.tar
test -f "$ARCHIVE"
mkdir -p libfprint/drivers/piano-goodix
tar -xf "$ARCHIVE" -C libfprint/drivers/piano-goodix

%build
# 与 SunUEFI 的构建参数一致。introspection 与 gtk-doc 关掉：设备上没有消费
# libfprint GIR 的程序，开着只会把 gobject-introspection 与 umockdev 拖进
# 构建依赖。udev hwdb 也关掉，自动挂起表里没有这颗传感器。
%meson \
    -Ddrivers=all \
    -Ddoc=false \
    -Dgtk-examples=false \
    -Dinstalled-tests=false \
    -Dintrospection=false \
    -Dudev_rules_dir=%{_udevrulesdir} \
    -Dudev_hwdb=disabled
%meson_build

%install
%meson_install
# 不提供 -devel：头文件、pkgconfig、gir 与开发用 .so 链接都不装。
rm -rf %{buildroot}%{_includedir} %{buildroot}%{_libdir}/pkgconfig \
    %{buildroot}%{_datadir}/gtk-doc %{buildroot}%{_datadir}/gir-1.0 \
    %{buildroot}%{_libdir}/libfprint-2.so
install -Dm0644 %{SOURCE1} %{buildroot}%{_unitdir}/fprintd.service.d/20-piano.conf
install -Dm0644 libfprint/drivers/piano-goodix/BSD-3-Clause.txt \
    %{buildroot}%{_licensedir}/%{name}/piano-goodix-BSD-3-Clause.txt
install -Dm0644 libfprint/drivers/piano-goodix/BSD-3-Clause-Clear.txt \
    %{buildroot}%{_licensedir}/%{name}/piano-goodix-BSD-3-Clause-Clear.txt
install -Dm0644 libfprint/drivers/piano-goodix/qcbor/LICENSE \
    %{buildroot}%{_licensedir}/%{name}/piano-goodix-QCBOR.txt
install -Dm0644 libfprint/drivers/piano-goodix/uapi/linux/tee.h \
    %{buildroot}%{_licensedir}/%{name}/piano-goodix-linux-tee.h

%post -p /sbin/ldconfig
%postun -p /sbin/ldconfig

%files
%license COPYING
%license %{_licensedir}/%{name}/*
%doc NEWS THANKS AUTHORS README.md
%{_libdir}/libfprint-2.so.*
%{_udevrulesdir}/70-libfprint-2.rules
%{_datadir}/metainfo/org.freedesktop.libfprint.metainfo.xml
%{_unitdir}/fprintd.service.d/20-piano.conf

%changelog
* Wed Nov 18 2026 FFT-infra <dev@piano.invalid> - 1.94.100-3
- 后端源码改用 piano-fingerprint 的归档在目标 ABI 下直接编译，去掉
  pkgconfig(piano-goodix) 与跨发行版静态库

* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 1.94.100-2
- 首个包：1.94.100 加 piano_goodix 驱动，静态链接 libpiano-goodix.a，
  附带 fprintd 的 UIO/TEE 设备与状态目录 drop-in
