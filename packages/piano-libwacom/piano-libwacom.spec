# Fedora 45 自带 libwacom 2.19.0。SunUEFI 的 SPI 笔支持补丁按 2.20.0 的源码
# 编排（patches/libwacom/2.20/series.json），这里取同一个源码，套同一个补丁，
# 产出 libwacom、libwacom-data、libwacom-utils、libwacom-devel 四个替代包。
# 内核 0225 把笔报成 BUS_SPI/0603/6532 的 "Novatek Focus Pen"，2.20.0 的
# libwacom 不认识 SPI，桌面设置里就找不到这支笔。
%global debug_package %{nil}

Name:           piano-libwacom
Version:        2.20.0
Release:        1%{?dist}
Summary:        libwacom with SPI digitizer support for the Piano stylus
License:        HPND
URL:            https://github.com/linuxwacom/libwacom
# 上游 release tarball，解包内容与 SunUEFI series.json 里钉的 Debian orig
# 逐字节相同（diff -rq 无差异）。
# sha256 = 370b45b5e05a91960df0aeb9c9481ae05846aab92ab2d4ec66945a0da4216888
# sha512 = b855bba9cac30da2eced51fe2e53201966a26207a5b2b2a798adcdaf92df0f94f5
#          253c9a37012f9b78e0f92ad48b6b4287700b620f992d28c751578486a33c09
Source0:        %{url}/releases/download/libwacom-%{version}/libwacom-%{version}.tar.xz
# sha256 = 63017df69dc0454782b13c67d9b7c2ca02aabd912b02485321c64807e441e54b
Patch0:         0001-spi-digitizers-and-xiaomi-pad.patch

ExclusiveArch:  aarch64

BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  gcc
BuildRequires:  glib2-devel
BuildRequires:  libgudev-devel
BuildRequires:  libevdev-devel
# hwdb 由 meson custom_target 调 tools/libwacom-update-db.py 生成。
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros

# 替换 Fedora 的整套 libwacom。版本 2.20.0 高于 Fedora 45 的 2.19.0，Obsoletes
# 把四个二进制包一起换掉；只换其中一个会让其余旧包留着同路径的旧文件。
Provides:       libwacom = %{version}-%{release}
Provides:       libwacom%{?_isa} = %{version}-%{release}
Obsoletes:      libwacom < %{version}-%{release}
Requires:       %{name}-data = %{version}-%{release}

%description
libwacom with the SPI digitizer support the Piano stylus needs. The kernel
reports the pen as BUS_SPI with vendor 0x0603 and product 0x6532, named
"Novatek Focus Pen" or "Novatek Focus Pen Pro"; stock libwacom only knows
usb/bluetooth/i2c/serial and therefore ignores it. The package also ships a
tablet description and a pressure/tilt stylus without an eraser, so GNOME and
KDE tablet settings recognise the pen. It replaces Fedora's libwacom family.

%package data
Summary:        Tablet database with the Xiaomi Pad 8 Pro pen
BuildArch:      noarch
Provides:       libwacom-data = %{version}-%{release}
Obsoletes:      libwacom-data < %{version}-%{release}

%description data
Tablet descriptions, stylus definitions, layouts, the udev rule and the hwdb
generated from them. Adds /usr/share/libwacom/xiaomi-pad-8-pro.tablet.

%package utils
Summary:        libwacom command line tools
Requires:       %{name} = %{version}-%{release}
# libwacom-show-stylus 是 python 脚本。
Requires:       python3-libevdev
Requires:       python3-pyudev
Provides:       libwacom-utils = %{version}-%{release}
Obsoletes:      libwacom-utils < %{version}-%{release}

%description utils
The libwacom-list-devices and libwacom-show-stylus tools.

%package devel
Summary:        Development files for libwacom
Requires:       %{name} = %{version}-%{release}
Requires:       pkgconfig
Provides:       libwacom-devel = %{version}-%{release}
Obsoletes:      libwacom-devel < %{version}-%{release}

%description devel
Header, pkg-config file and the unversioned shared library link.

%prep
%autosetup -p1 -n libwacom-%{version}

%build
# 测试要 pytest 与 uinput，Fedora 的包也关掉；笔的数据由 %check 里的
# list-devices 实际解析一遍。
%meson -Dtests=disabled -Ddocumentation=disabled
%meson_build

%install
%meson_install

%check
# 非安装版 list-devices 读源码树里的 data/，用编译出来的库解析全部 Tablet
# 描述。它既能证明补丁的 SPI 分支编进去了，也能证明新描述文件可以被匹配。
./%{_vpath_builddir}/list-devices | grep -E "bus: 'spi',.*name: 'Xiaomi Pad 8 Pro'"

%files
%license COPYING
%doc README.md
%{_libdir}/libwacom.so.9*
%{_bindir}/libwacom-list-local-devices
%{_bindir}/libwacom-update-db
%{_mandir}/man1/libwacom-list-local-devices.1*

%files data
%doc COPYING
%dir %{_datadir}/libwacom
%{_datadir}/libwacom/*.tablet
%{_datadir}/libwacom/*.stylus
%dir %{_datadir}/libwacom/layouts
%{_datadir}/libwacom/layouts/*.svg
%{_udevrulesdir}/65-libwacom.rules
%{_udevhwdbdir}/65-libwacom.hwdb

%files utils
%{_bindir}/libwacom-list-devices
%{_bindir}/libwacom-show-stylus
%{_mandir}/man1/libwacom-list-devices.1*
%{_mandir}/man1/libwacom-show-stylus.1*

%files devel
%dir %{_includedir}/libwacom-1.0/
%dir %{_includedir}/libwacom-1.0/libwacom
%{_includedir}/libwacom-1.0/libwacom/libwacom.h
%{_libdir}/libwacom.so
%{_libdir}/pkgconfig/libwacom.pc

%changelog
* Sun Nov 22 2026 FFT-infra <dev@piano.invalid> - 2.20.0-1
- New package: upstream libwacom 2.20.0 with SunUEFI's SPI digitizer patch,
  replacing Fedora's libwacom family so desktop tablet settings see the
  Novatek Focus Pen
