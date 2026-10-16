%global debug_package %{nil}

Name:           sm8750-video-accel
Version:        1.0.0
Release:        1%{?dist}
Summary:        Qualcomm Iris (SM8750) V4L2 stateful VA-API driver backend
License:        MIT
URL:            https://github.com/radxa-pkg/libva-v4l2
Source0:        https://github.com/radxa-pkg/libva-v4l2/archive/0e4ce1e05da1d0a92b342efc2090b9e4987035e1/libva-v4l2-0e4ce1e.tar.gz

ExclusiveArch:  aarch64

BuildRequires:  meson >= 0.56.0
BuildRequires:  ninja-build
BuildRequires:  gcc-c++
BuildRequires:  pkgconfig(libva) >= 1.1.0
BuildRequires:  pkgconfig(libdrm) >= 2.4.52

Requires:       libva%{?_isa}
Requires:       libdrm%{?_isa}

Provides:       libva-v4l2-iris = %{version}-%{release}
Obsoletes:      libva-v4l2-iris < 1.0.0-2

%description
Qualcomm Iris (VPU35, SM8750) hardware video acceleration backend for Linux via
VA-API and stateful V4L2 M2M. Provides hardware-accelerated decode and encode
entrypoints (H.264, HEVC Main, HEVC Main10, VP9) for Chromium, Firefox,
ffmpeg, and Sunshine streaming on Xiaomi Pad 8 Pro (piano).

%prep
%autosetup -n libva-v4l2-0e4ce1e05da1d0a92b342efc2090b9e4987035e1

%build
%meson -Dfastcv=disabled
%meson_build

%install
%meson_install
rm -rf %{buildroot}%{_datadir}/doc/libva-v4l2 %{buildroot}%{_datadir}/licenses/libva-v4l2
# Ensure msm alias exists so libva resolves the driver by DRM driver name
if [ ! -e %{buildroot}%{_libdir}/dri/msm_drv_video.so ]; then
    ln -s v4l2_drv_video.so %{buildroot}%{_libdir}/dri/msm_drv_video.so
fi

%files
%license LICENSE
%doc README.md
%{_libdir}/dri/v4l2_drv_video.so
%{_libdir}/dri/msm_drv_video.so

%changelog
* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 1.0.0-1
- Formalize SM8750 Iris VA-API package specification from radxa-pkg/libva-v4l2
