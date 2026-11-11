%global debug_package %{nil}

Name:           piano-gnome-extras
Version:        0.1.0
Release:        1%{?dist}
Summary:        Optional GNOME desktop patches for Xiaomi Pad 8 Pro (piano)
License:        MIT
URL:            https://github.com/FFT-infra/piano-fedora
BuildArch:      noarch
Source0:        20-piano-ambient

Requires:       gnome-settings-daemon-piano
Requires:       gtk3
Requires:       dconf

%description
Installs the optional GNOME extras for the Xiaomi Pad 8 Pro:

- gnome-settings-daemon-piano with the piano ambient-light profile and
  keyboard backlight hotplug handling
- gtk3 with the X11 XI2 tool-atom fix

It also enables ambient brightness for the GNOME power plugin. The default
image keeps Fedora's stock packages and leaves ambient brightness off; this
package is the explicit opt-in.

%install
install -d %{buildroot}%{_sysconfdir}/dconf/db/local.d
install -Dpm0644 %{SOURCE0} \
    %{buildroot}%{_sysconfdir}/dconf/db/local.d/20-piano-ambient
install -d %{buildroot}%{_datadir}/piano-extras
echo "gnome" > %{buildroot}%{_datadir}/piano-extras/gnome

%post
if command -v dconf >/dev/null 2>&1; then
    dconf update
fi

%postun
if command -v dconf >/dev/null 2>&1; then
    dconf update
fi

%files
%config(noreplace) %{_sysconfdir}/dconf/db/local.d/20-piano-ambient
%{_datadir}/piano-extras/gnome

%changelog
* Wed Nov 11 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- Optional GNOME ambient brightness and keyboard backlight extras
