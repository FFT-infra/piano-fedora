%global debug_package %{nil}

Name:           libssc
Version:        0.4.4
Release:        1%{?dist}
Summary:        Qualcomm Sensor Core userspace library for Xiaomi Pad 8 Pro (piano)
License:        GPL-3.0-or-later
URL:            https://codeberg.org/DylanVanAssche/libssc
Source0:        https://codeberg.org/DylanVanAssche/libssc/archive/v%{version}.tar.gz#/%{name}-%{version}.tar.gz
Patch0:         wait_for_qmi_service.patch

ExclusiveArch:  aarch64

BuildRequires:  gcc
BuildRequires:  meson
BuildRequires:  ninja-build
BuildRequires:  glib2-devel
BuildRequires:  protobuf-c-devel
BuildRequires:  protobuf-c-compiler
BuildRequires:  protobuf-compiler
BuildRequires:  libqmi-devel
BuildRequires:  python3-devel
BuildRequires:  vala

Requires:       glib2%{?_isa}
Requires:       protobuf-c%{?_isa}
Requires:       libqmi%{?_isa}

%description
Userspace library to expose Qualcomm Sensor Core (SSC) sensors via QMI on SM8750.
Provides the ssccli command-line tool and asynchronous GLib mainloop non-blocking
retry support to guarantee sensor enumeration without stalling boot.

%prep
%autosetup -p1 -n libssc

%build
%meson
%meson_build

%install
%meson_install

%files
%{_bindir}/ssccli
%{_libdir}/libssc.so
%{_libdir}/libssc.so.*
%{_includedir}/libssc
%{_libdir}/pkgconfig/libssc.pc
%{_datadir}/vala/vapi/libssc.vapi
%{_datadir}/vala/vapi/libssc.deps
%exclude %{_libexecdir}/installed-tests
%exclude %{python3_sitelib}/ssc_server
%exclude %{_datadir}/gir-1.0/SSC-2.gir
%exclude %{_libdir}/girepository-1.0/SSC-2.typelib

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.4.4-1
- Formalize libssc package with GLib async QMI non-blocking retry
