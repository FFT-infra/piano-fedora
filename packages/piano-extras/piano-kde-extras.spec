%global debug_package %{nil}

Name:           piano-kde-extras
Version:        0.1.0
Release:        1%{?dist}
Summary:        Optional KDE desktop patches for Xiaomi Pad 8 Pro (piano)
License:        MIT
URL:            https://github.com/FFT-infra/piano-fedora
BuildArch:      noarch

Requires:       kwin
Requires:       gtk3

%description
Installs the optional KDE extras for the Xiaomi Pad 8 Pro:

- kwin with the HDR backlight restore fix
- gtk3 with the X11 XI2 tool-atom fix

The default image keeps Fedora's stock packages. Install this package only
when you need the patched compositor, for example to test HDR output.

%install
install -d %{buildroot}%{_datadir}/piano-extras
echo "kde" > %{buildroot}%{_datadir}/piano-extras/kde

%files
%{_datadir}/piano-extras/kde

%changelog
* Wed Nov 11 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- Optional KWin HDR backlight restore and GTK3 XI2 extras
