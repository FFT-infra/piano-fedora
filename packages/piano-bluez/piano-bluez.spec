%if 0%{?fedora} || 0%{?rhel} <= 8
%bcond_without deprecated
%else
%bcond_with deprecated
%endif

# To package an upstream git snapshot instead of a release, uncomment
# gitdate and shortcommit below (as %%global) and generate the tarball with
# the commands below. The Version then follows the snapshot guidelines,
# <base_version>^<number>.<revision> with the snapshot's commit date as
# the number (keep the base version at the last upstream release; the date
# must increase with every new snapshot):
# https://docs.fedoraproject.org/en-US/packaging-guidelines/Versioning/#_snapshots
# git config tar.tar.xz.command "xz -c"
# export VERSION=5.88 ; export DATE=`git log -1 --format=%cd --date=format:%Y%m%d HEAD` ; export SHA=`git rev-parse --short HEAD` ; git archive --format=tar.xz -o bluez-${VERSION}^${DATE}.${SHA}.tar.xz --prefix=bluez-${VERSION}^${DATE}.${SHA}/ HEAD
#global gitdate YYYYMMDD
#global shortcommit 0123abcde

# Fedora 45 的 bluez-5.88-1 是上游 5.88 tag。SunUEFI 的 patches/bluez/5.88
# 带两条设备上需要的修复：HoG 在 PnP ID 到达之前不建 HID 设备，mpris-proxy
# 在信号处理里不碰 GLib 锁。补丁逐字节取自那边，摘要由
# tests/check_bluez_package.py 与 series.json 对齐。
#
# 包名换成 piano-bluez，二进制包仍声明 Provides/Obsoletes 顶掉 Fedora 的同名
# 包。根文件系统显式安装 piano-bluez，dnf 事务里 Fedora 的 bluez 系列会被
# 淘汰；只靠 Provides 的话 dnf 按名字优先，还是会选官方包。
%global debug_package %{nil}

Name:    piano-bluez
Version: 5.88
Release: 2%{?dist}
Summary: Bluetooth utilities with the SunUEFI HoG and mpris-proxy fixes
License: GPL-2.0-or-later
URL:     http://www.bluez.org/

Source0: https://www.kernel.org/pub/linux/bluetooth/bluez-%{version}.tar.xz
Patch0:  0001-hog-read-uncached-pnp-id.patch
Patch1:  0002-mpris-proxy-handle-signals-in-main-loop.patch

BuildRequires: dbus-devel >= 1.6
BuildRequires: glib2-devel
BuildRequires: libell-devel >= 0.39
BuildRequires: libical-devel
BuildRequires: make
BuildRequires: readline-devel
# For bluetooth mesh
BuildRequires: json-c-devel
# For cable pairing
BuildRequires: systemd-devel
# For udev rules
BuildRequires: systemd
# For printing
BuildRequires: cups-devel
# For autoreconf
BuildRequires: libtool automake autoconf
# For man pages
BuildRequires: python3-docutils
BuildRequires: python3-pygments

Requires: dbus >= 1.6
Requires(post): systemd
Requires(preun): systemd
Requires(postun): systemd

Provides:       bluez = %{version}-%{release}
Provides:       bluez%{?_isa} = %{version}-%{release}
Obsoletes:      bluez < %{version}-%{release}
# 只靠 Obsoletes 时，Fedora 发一个更高的 bluez，dnf 会当成升级把我们换掉，
# 两条补丁静默消失。让这种更新直接冲突，由人来看新版本带没带修复。
Conflicts:      bluez > %{version}-%{release}

%description
Utilities for use in Bluetooth applications:
	- avinfo
	- bluemoon
	- bluetoothctl
	- bluetoothd
	- btattach
	- btmon
	- hex2hcd
	- mpris-proxy

The BLUETOOTH trademarks are owned by Bluetooth SIG, Inc., U.S.A.

%package cups
Summary: CUPS printer backend for Bluetooth printers
Requires: bluez%{?_isa} = %{version}-%{release}
Requires: cups
Provides: bluez-cups = %{version}-%{release}
Provides: bluez-cups%{?_isa} = %{version}-%{release}
Obsoletes: bluez-cups < %{version}-%{release}
Conflicts: bluez-cups > %{version}-%{release}

%description cups
This package contains the CUPS backend

%if %{with deprecated}
%package deprecated
Summary: Deprecated Bluetooth applications
Requires: bluez%{?_isa} = %{version}-%{release}
Obsoletes: bluez < 5.55-2
Provides: bluez-deprecated = %{version}-%{release}
Provides: bluez-deprecated%{?_isa} = %{version}-%{release}
Obsoletes: bluez-deprecated < %{version}-%{release}
Conflicts: bluez-deprecated > %{version}-%{release}

%description deprecated
Bluetooth applications that have bee deprecated by upstream. They have been
replaced by functionality in the core bluetoothctl and will eventually
be dropped by upstream. Utilities include:
	- ciptool
	- hciattach
	- hciconfig
	- hcitool
	- meshctl
	- rfcomm
	- sdptool
%endif

%package libs
Summary: Libraries for use in Bluetooth applications
Provides: bluez-libs = %{version}-%{release}
Provides: bluez-libs%{?_isa} = %{version}-%{release}
Obsoletes: bluez-libs < %{version}-%{release}
Conflicts: bluez-libs > %{version}-%{release}

%description libs
Libraries for use in Bluetooth applications.

%package libs-devel
Summary: Development libraries for Bluetooth applications
Requires: bluez-libs%{?_isa} = %{version}-%{release}
Provides: bluez-libs-devel = %{version}-%{release}
Provides: bluez-libs-devel%{?_isa} = %{version}-%{release}
Obsoletes: bluez-libs-devel < %{version}-%{release}
Conflicts: bluez-libs-devel > %{version}-%{release}

%description libs-devel
bluez-libs-devel contains development libraries and headers for
use in Bluetooth applications.

%package hid2hci
Summary: Put HID proxying bluetooth HCI's into HCI mode
Requires: bluez%{?_isa} = %{version}-%{release}
Provides: bluez-hid2hci = %{version}-%{release}
Provides: bluez-hid2hci%{?_isa} = %{version}-%{release}
Obsoletes: bluez-hid2hci < %{version}-%{release}
Conflicts: bluez-hid2hci > %{version}-%{release}
# Order after udev when both are installed, without depending on it
OrderWithRequires(post): systemd-udev

%description hid2hci
Most allinone PC's and bluetooth keyboard / mouse sets which include a
bluetooth dongle, ship with a so called HID proxying bluetooth HCI.
The HID proxying makes the keyboard / mouse show up as regular USB HID
devices (after connecting using the connect button on the device + keyboard),
which makes them work without requiring any manual configuration.

The bluez-hid2hci package contains the hid2hci utility and udev rules to
automatically switch supported Bluetooth devices into regular HCI mode.

Install this package if you want to use the bluetooth function of the HCI
with other bluetooth devices like for example a mobile phone.

Note that after installing this package you will first need to pair your
bluetooth keyboard and mouse with the bluetooth adapter before you can use
them again. Since you cannot use your bluetooth keyboard and mouse until
they are paired, this will require the use of a regular (wired) USB keyboard
and mouse.

%package mesh
Summary: Bluetooth mesh
Requires: bluez%{?_isa} = %{version}-%{release}
Requires: bluez-libs%{?_isa} = %{version}-%{release}
Provides: bluez-mesh = %{version}-%{release}
Provides: bluez-mesh%{?_isa} = %{version}-%{release}
Obsoletes: bluez-mesh < %{version}-%{release}
Conflicts: bluez-mesh > %{version}-%{release}

%description mesh
Services for bluetooth mesh

%package obexd
Summary: Object Exchange daemon for sharing content
Requires: bluez%{?_isa} = %{version}-%{release}
Requires: bluez-libs%{?_isa} = %{version}-%{release}
Provides: bluez-obexd = %{version}-%{release}
Provides: bluez-obexd%{?_isa} = %{version}-%{release}
Obsoletes: bluez-obexd < %{version}-%{release}
Conflicts: bluez-obexd > %{version}-%{release}

%description obexd
Object Exchange daemon for sharing files, contacts etc over bluetooth

%prep
%autosetup -p1 -n bluez-%{version}

%build
autoreconf -vif
%configure --enable-tools --enable-library \
           --enable-external-ell --disable-optimization \
%if %{with deprecated}
           --enable-deprecated \
%endif
           --enable-sixaxis --enable-cups --enable-nfc --enable-mesh \
           --enable-hid2hci --enable-testing --enable-experimental \
           --enable-bap --enable-bass --enable-mcp --enable-micp \
           --enable-csip --enable-vcp \
           --with-systemdsystemunitdir=%{_unitdir} \
           --with-systemduserunitdir=%{_userunitdir}

%{make_build}

%install
%{make_install}

# "make install" fails to install avinfo
# Red Hat Bugzilla bug #1699680
install -m0755 tools/avinfo $RPM_BUILD_ROOT%{_bindir}

# "make install" fails to install avinfo
install -m0755 tools/btsnoop $RPM_BUILD_ROOT%{_bindir}

# btmgmt is not installed by "make install", but it is useful for debugging
# some issues and to set the MAC address on HCIs which don't have their
# MAC address configured 
install -m0755 tools/btmgmt $RPM_BUILD_ROOT%{_bindir}
rst2man doc/btmgmt.rst --no-datestamp --no-generator $RPM_BUILD_ROOT%{_mandir}/man1/btmgmt.1

# Remove libtool archive
find $RPM_BUILD_ROOT -name '*.la' -delete

# Remove the cups backend from libdir, and install it in /usr/lib whatever the install
if test -d ${RPM_BUILD_ROOT}/usr/lib64/cups ; then
	install -D -m0755 ${RPM_BUILD_ROOT}/usr/lib64/cups/backend/bluetooth ${RPM_BUILD_ROOT}%_cups_serverbin/backend/bluetooth
	rm -rf ${RPM_BUILD_ROOT}%{_libdir}/cups
fi

rm -f ${RPM_BUILD_ROOT}/%{_sysconfdir}/udev/*.rules ${RPM_BUILD_ROOT}/usr/lib/udev/rules.d/*.rules
install -D -p -m0644 tools/hid2hci.rules ${RPM_BUILD_ROOT}/%{_udevrulesdir}/97-hid2hci.rules

install -d -m0755 $RPM_BUILD_ROOT/%{_localstatedir}/lib/bluetooth
install -d -m0755 $RPM_BUILD_ROOT/%{_localstatedir}/lib/bluetooth/mesh

mkdir -p $RPM_BUILD_ROOT/%{_libdir}/bluetooth/

#copy bluetooth config files
install -D -p -m0644 src/main.conf ${RPM_BUILD_ROOT}/etc/bluetooth/main.conf
install -D -p -m0644 mesh/mesh-main.conf ${RPM_BUILD_ROOT}/etc/bluetooth/mesh-main.conf
install -D -p -m0644 profiles/input/input.conf ${RPM_BUILD_ROOT}/etc/bluetooth/input.conf
install -D -p -m0644 profiles/network/network.conf ${RPM_BUILD_ROOT}/etc/bluetooth/network.conf

# Install the HCI emulator, useful for testing
install emulator/btvirt ${RPM_BUILD_ROOT}/%{_libexecdir}/bluetooth/

#check
#make check

%ldconfig_scriptlets libs

%post
%systemd_post bluetooth.service

%preun
%systemd_preun bluetooth.service

%postun
%systemd_postun_with_restart bluetooth.service

%post hid2hci
# udevadm is not installed in minimal roots (containers, mock)
if command -v udevadm >/dev/null; then
    # retrigger is best-effort, e.g. /sys may be read-only in containers
    udevadm trigger --subsystem-match=usb || :
fi

%post mesh
%systemd_post bluetooth-mesh.service

%preun mesh
%systemd_preun bluetooth-mesh.service

%postun mesh
%systemd_postun_with_restart bluetooth-mesh.service

%post obexd
%systemd_user_post obex.service

%preun obexd
%systemd_user_preun obex.service

%files
%license COPYING
%doc AUTHORS ChangeLog
# bluetooth.service expects configuration directory to be read only
# https://github.com/bluez/bluez/issues/329#issuecomment-1102459104
%attr(0555, root, root) %dir %{_sysconfdir}/bluetooth
%config(noreplace) %{_sysconfdir}/bluetooth/main.conf
%config(noreplace) %{_sysconfdir}/bluetooth/input.conf
%config(noreplace) %{_sysconfdir}/bluetooth/network.conf
%{_bindir}/avinfo
%{_bindir}/bluemoon
%{_bindir}/bluetoothctl
%{_bindir}/btattach
%{_bindir}/btmgmt
%{_bindir}/btmon
%{_bindir}/hex2hcd
%{_bindir}/mpris-proxy
%{_mandir}/man1/bluetoothctl.1.*
%{_mandir}/man1/bluetoothctl-*.1.*
%{_mandir}/man1/btmgmt.1.*
%{_mandir}/man1/btattach.1.*
%{_mandir}/man1/btmon.1.*
%{_mandir}/man8/bluetoothd.8.*
%dir %{_libexecdir}/bluetooth
%{_libexecdir}/bluetooth/bluetoothd
%{_libdir}/bluetooth/
# bluetooth.service expects StateDirectoryMode to be 700.
%attr(0700, root, root) %dir %{_localstatedir}/lib/bluetooth
%dir %{_localstatedir}/lib/bluetooth/mesh
%{_datadir}/dbus-1/system.d/bluetooth.conf
%{_datadir}/dbus-1/system-services/org.bluez.service
%{_unitdir}/bluetooth.service
%{_userunitdir}/mpris-proxy.service
%{_datadir}/zsh/site-functions/_bluetoothctl

%if %{with deprecated}
%files deprecated
%{_bindir}/ciptool
%{_bindir}/hciattach
%{_bindir}/hciconfig
%{_bindir}/hcitool
%{_bindir}/meshctl
%{_bindir}/rfcomm
%{_bindir}/sdptool
%{_mandir}/man1/ciptool.1.*
%{_mandir}/man1/hciattach.1.*
%{_mandir}/man1/hciconfig.1.*
%{_mandir}/man1/hcitool.1.*
%{_mandir}/man1/rfcomm.1.*
%{_mandir}/man1/sdptool.1.*
%endif

%files libs
%{!?_licensedir:%global license %%doc}
%license COPYING
%{_libdir}/libbluetooth.so.*

%files libs-devel
%doc doc/*txt
%{_bindir}/btsnoop
%{_bindir}/isotest
%{_bindir}/l2test
%{_bindir}/l2ping
%{_bindir}/rctest
%{_mandir}/man1/isotest.1.*
%{_mandir}/man1/l2ping.1.*
%{_mandir}/man1/rctest.1.*
%{_mandir}/man5/org.bluez.*.5.*
%{_mandir}/man7/btsnoop.7.*
%{_mandir}/man7/hci.7.*
%{_mandir}/man7/iso.7.*
%{_mandir}/man7/l2cap.7.*
%{_mandir}/man7/mgmt.7.*
%{_mandir}/man7/rfcomm.7.*
%{_mandir}/man7/sco.7.*
%{_libdir}/libbluetooth.so
%{_includedir}/bluetooth
%{_libdir}/pkgconfig/bluez.pc
%dir %{_libexecdir}/bluetooth
%{_libexecdir}/bluetooth/btvirt

%files cups
%_cups_serverbin/backend/bluetooth

%files hid2hci
/usr/lib/udev/hid2hci
%{_mandir}/man1/hid2hci.1*
%{_udevrulesdir}/97-hid2hci.rules

%files mesh
%config(noreplace) %{_sysconfdir}/bluetooth/mesh-main.conf
%{_bindir}/mesh-cfgclient
%{_bindir}/mesh-cfgtest
%{_datadir}/dbus-1/system.d/bluetooth-mesh.conf
%{_datadir}/dbus-1/system-services/org.bluez.mesh.service
%{_libexecdir}/bluetooth/bluetooth-meshd
%{_unitdir}/bluetooth-mesh.service
%{_localstatedir}/lib/bluetooth/mesh
%{_mandir}/man8/bluetooth-meshd.8*

%files obexd
%{_libexecdir}/bluetooth/obexd
%{_datadir}/dbus-1/services/org.bluez.obex.service
/usr/lib/systemd/user/dbus-org.bluez.obex.service
%{_datadir}/dbus-1/system.d/obex.conf
%{_userunitdir}/obex.service

%changelog
* Sun Nov 22 2026 FFT-infra <dev@piano.invalid> - 5.88-2
- New package: Fedora 45 的 bluez 5.88 源码，套 SunUEFI patches/bluez/5.88 的
  两条修复，二进制包仍以 Provides/Obsoletes 顶掉官方 bluez 系列
- HoG 先读 PnP ID 再建 HID 设备，否则内核拿到 vendor/product 都为 0 的
  HID 设备，蓝牙键盘的输入与配对都要重来
- mpris-proxy 用 GLib 的 Unix 信号源退出，不再在信号处理里抢 GLib 锁
