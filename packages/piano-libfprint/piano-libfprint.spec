%global debug_package %{nil}

# Fedora 的 libfprint-1.94.100 与 SunUEFI 的 pin 是同一份源码：v1.94.100 是
# 附注标签，解引用后就是 80a4b5ec。补丁只做一件事——把 piano_goodix 驱动
# 编进来，并静态链接 piano-fingerprint 产出的 libpiano-goodix.a。
Name:           piano-libfprint
Version:        1.94.100
Release:        2%{?dist}
Summary:        libfprint with the Piano Goodix signed-TA driver
License:        LGPL-2.1-or-later AND NIST-PD AND BSD-3-Clause
URL:            https://gitlab.freedesktop.org/libfprint/libfprint
Source0:        %{url}/-/archive/v%{version}/libfprint-v%{version}.tar.gz
Source1:        20-piano.conf
Patch0:         0001-piano-goodix-uio.patch
# meson 1.12 不再接受 foreach 单变量遍历 dict；关掉 introspection 时上游的
# else 分支一定会被求值，构建在编译前就停了。
Patch1:         0002-meson-dict-foreach.patch

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
# piano-goodix.pc 与 libpiano-goodix.a 由 piano-fingerprint 提供。
BuildRequires:  pkgconfig(piano-goodix)
BuildRequires:  systemd-rpm-macros

Requires:       glib2%{?_isa}
Requires:       libgusb%{?_isa}
Requires:       systemd

%description
Standard libfprint built with the Piano Goodix driver. The driver talks to the
signed TrustZone TA through the static libpiano-goodix.a from
piano-fingerprint, so fprintd and GNOME can enroll and verify fingerprints on
Xiaomi Pad 8 Pro (piano). The package replaces Fedora's libfprint; the only
added file is the fprintd drop-in that opens the UIO/TEE devices and the
/var/lib/piano-goodix state directory to the service.

%prep
%autosetup -p1 -n libfprint-v%{version}

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

%post -p /sbin/ldconfig
%postun -p /sbin/ldconfig

%files
%license COPYING
%doc NEWS THANKS AUTHORS README.md
%{_libdir}/libfprint-2.so.*
%{_udevrulesdir}/70-libfprint-2.rules
%{_datadir}/metainfo/org.freedesktop.libfprint.metainfo.xml
%{_unitdir}/fprintd.service.d/20-piano.conf

%changelog
* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 1.94.100-2
- 首个包：1.94.100 加 piano_goodix 驱动，静态链接 libpiano-goodix.a，
  附带 fprintd 的 UIO/TEE 设备与状态目录 drop-in
