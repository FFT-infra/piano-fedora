%global debug_package %{nil}

Name:           sm8750-video-accel
Version:        1.1.1
Release:        1%{?dist}
Summary:        Qualcomm Iris (SM8750) V4L2 stateful VA-API driver backend
License:        MIT
URL:            https://github.com/radxa-pkg/libva-v4l2
Source0:        https://github.com/radxa-pkg/libva-v4l2/archive/0980a1decf26d34c9aa71cd0a4a0d25703a7a17f/qcom-va-driver-0980a1decf26d34c9aa71cd0a4a0d25703a7a17f.tar.gz

ExclusiveArch:  aarch64

BuildRequires:  meson >= 0.61.0
BuildRequires:  ninja-build
BuildRequires:  gcc-c++
BuildRequires:  pkgconfig(libva) >= 1.1.0
BuildRequires:  pkgconfig(libdrm) >= 2.4.52
# meson.build 的依赖列表共五项。本地矩阵容器一直是整包装的，只有 COPR
# 的干净 chroot 才暴露了后三项没写，报
# `meson.build:12:6: ERROR: Dependency "egl" not found`。
BuildRequires:  pkgconfig(egl)
BuildRequires:  pkgconfig(glesv2) >= 3.2
BuildRequires:  pkgconfig(gbm)

Requires:       libva%{?_isa}
Requires:       libdrm%{?_isa}

Provides:       libva-v4l2-iris = %{version}-%{release}
# `< %{version}-%{release}`, not a fixed bound. With `Obsoletes:
# libva-v4l2-iris < 1.0.0-2` against `Provides: libva-v4l2-iris = 1.0.0-1`, the
# package obsoleted its own provide and DNF tried to install it twice.
Obsoletes:      libva-v4l2-iris < %{version}-%{release}

%description
Qualcomm Iris (VPU35, SM8750) hardware video acceleration backend for Linux via
VA-API and stateful V4L2 M2M. Provides hardware-accelerated decode and encode
entrypoints (H.264, HEVC Main, HEVC Main10, VP9, AV1) for Chromium, Firefox,
ffmpeg, and Sunshine streaming on Xiaomi Pad 8 Pro (piano).

%prep
%autosetup -n qcom-va-driver-0980a1decf26d34c9aa71cd0a4a0d25703a7a17f

%build
# 交付的驱动必须自带编码入口。2026-10-15 装过一个只声明 VLD 的后端，ffmpeg
# 的 h264_vaapi/hevc_vaapi 静默失效。入口在 backend.cpp 的
# vaQueryConfigEntrypoints 里声明，实现落在 encoder.cpp，后者用的是
# VAEncSliceParameterBufferType / VAEncPictureParameterBufferType 这些渲染参数
# 类型，不会出现 VAEntrypointEncSlice 字面量，所以两个文件各查各自的特征。
grep -q VAEntrypointEncSlice src/backend.cpp
grep -q VAEncSliceParameterBufferType src/encoder.cpp
grep -q VAEncPictureParameterBufferType src/encoder.cpp
# 两个 VPP 后端都明确关掉，不靠 auto 碰运气：
# CDSP 需要高通 Hexagon 编译器 ghc 和三个 wrap（qaic/hexagon/fastrpc），
# Fedora 仓库没有 ghc，构建机会去 softwarecenter.qualcomm.com 下载；
# Vulkan VPP 需要 vulkan-devel 与 glslangValidator，编出来的路径还没在设备上
# 验过。上一版没有 VPP，关掉后编解码路径与上一版一致。
%meson -Dcdsp_vpp=disabled -Dvulkan_vpp=disabled
%meson_build

%install
%meson_install
rm -rf %{buildroot}%{_datadir}/doc/qcom-va-driver %{buildroot}%{_datadir}/licenses/qcom-va-driver
# 上游自己装了两个别名：msm_drv_video.so（libva 按 DRM 驱动名回退）和
# v4l2_drv_video.so（改名前的驱动名）。这里核对而不是重建，名字再变时
# 在构建期就暴露。
test -L %{buildroot}%{_libdir}/dri/msm_drv_video.so
test -L %{buildroot}%{_libdir}/dri/v4l2_drv_video.so
test "$(readlink %{buildroot}%{_libdir}/dri/msm_drv_video.so)" = qcom_drv_video.so

%files
%license LICENSE
%doc README.md
%{_libdir}/dri/qcom_drv_video.so
%{_libdir}/dri/msm_drv_video.so
%{_libdir}/dri/v4l2_drv_video.so

%changelog
* Fri Nov 06 2026 FFT-infra <dev@piano.invalid> - 1.1.1-1
- 跟上上游 0980a1de：项目改名 qcom-va-driver，驱动模块变成 qcom_drv_video.so，
  msm 与 v4l2 两个别名由上游安装；本轮明确关闭 CDSP 与 Vulkan VPP 后端

* Mon Oct 20 2026 FFT-infra <dev@piano.invalid> - 1.0.0-2
- 在 %%build 里断言源码自带编码入口；运行时不再另建一份同路径的 VA-API 驱动

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 1.0.0-1
- Formalize SM8750 Iris VA-API package specification from radxa-pkg/libva-v4l2
