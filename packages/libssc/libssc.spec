%global debug_package %{nil}

Name:           libssc
Version:        0.4.4
Release:        3%{?dist}
Summary:        Qualcomm Sensor Core userspace library for Xiaomi Pad 8 Pro (piano)
License:        GPL-3.0-or-later
URL:            https://codeberg.org/DylanVanAssche/libssc
Source0:        https://codeberg.org/DylanVanAssche/libssc/archive/v%{version}.tar.gz#/%{name}-%{version}.tar.gz
Patch0:         wait_for_qmi_service.patch
# The device sensor stack needs all three. Without them the package builds but
# the sensors report wrong property types, there is no raw vector API, and the
# piano vector client is missing. build-device-runtime used to carry its own
# copies; these are the same patches, applied here so the RPM matrix and the
# runtime producer cannot drift into two different libssc builds.
Patch1:         0001-fix-sensor-property-types.patch
Patch2:         0002-raw-vector-reports.patch
Patch3:         0003-piano-ssc-vector-cli.patch

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
# Fedora 45 renamed protobuf-c to protobuf3-c. The new package obsoletes the
# old name but only provides the unqualified "protobuf-c", so an explicit
# arch-qualified Requires (protobuf-c(aarch-64)) no longer resolves and dnf
# prints a solver problem on every upgrade. The soname require RPM generates
# from libprotobuf-c.so.1 covers both package names.
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
# Added by 0003-piano-ssc-vector-cli.patch.
%{_bindir}/piano-ssc-vector
%{_libdir}/libssc.so
%{_libdir}/libssc.so.*
%{_includedir}/libssc
%{_libdir}/pkgconfig/libssc.pc
%{_datadir}/vala/vapi/libssc.vapi
%{_datadir}/vala/vapi/libssc.deps
%exclude %{_libexecdir}/installed-tests
# upstream mocking/ssc_server is a test double, not device runtime.
%exclude %{python3_sitelib}/ssc_server
%exclude %{_datadir}/gir-1.0/SSC-2.gir
%exclude %{_libdir}/girepository-1.0/SSC-2.typelib

%changelog
* Sun Nov 15 2026 FFT-infra <dev@piano.invalid> - 0.4.4-3
- Drop the arch-qualified protobuf-c requires. Fedora 45 renamed the package
  to protobuf3-c, which obsoletes the old name without providing
  protobuf-c(aarch-64), so dnf reported a solver problem on every upgrade.
  The generated libprotobuf-c.so.1 soname require keeps both names working.

* Mon Oct 19 2026 FFT-infra <dev@piano.invalid> - 0.4.4-2
- Ship the property-type, raw-vector and piano vector-client patches so the
  RPM matrix matches the device runtime build

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.4.4-1
- Formalize libssc package with GLib async QMI non-blocking retry
