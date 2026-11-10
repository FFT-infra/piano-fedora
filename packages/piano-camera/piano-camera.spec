%global _annotated_build 0
%global debug_package %{nil}

Name:           piano-camera
Version:        0.1.0
Release:        5%{?dist}
Summary:        Camera pipeline and v4l2loopback daemon for Xiaomi Pad 8 Pro (piano)
License:        MIT AND Apache-2.0
URL:            https://github.com/FFT-infra/piano-fedora
Source0:        %{name}-%{version}.tar.gz

ExclusiveArch:  aarch64

BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  libjpeg-turbo-devel
BuildRequires:  libyuv-devel
BuildRequires:  cargo
BuildRequires:  rust
BuildRequires:  pkgconf-pkg-config
BuildRequires:  systemd-rpm-macros
Requires:       systemd

Provides:       piano-camera-daemon = %{version}-%{release}

%description
Camera hardware bring-up service and resident user-space relay daemon (piano-camerad)
for Xiaomi Pad 8 Pro (SM8750 piano). Runs the sensors through the CAMSS hardware ISP
and exposes preview and still capture on the v4l2loopback cameras. The rear full-frame
mode keeps the whole Quad CFA sample from the sensor and converts it in software.

%prep
%autosetup -p1

%build
export CARGO_HOME=%{_builddir}/cargo-home
# src/ 是 SunUEFI linux/userspace/camera 的副本；quad/vendor/demosaic 是锁定的
# crates.io 0.3.0 加一份 QPPG 修正补丁，--offline 因此不需要网络。
make -C src OUT=%{_builddir}/%{name}-%{version}/camera %{?_smp_mflags} \
  CC=gcc CXX=g++ \
  CFLAGS="%{optflags} -Wall -Wextra" \
  CXXFLAGS="%{optflags} -Wall -Wextra" \
  LDFLAGS="%{build_ldflags}"

%install
mkdir -p %{buildroot}%{_prefix}/lib/piano
cp -a usr etc %{buildroot}/
install -m 0755 %{_builddir}/%{name}-%{version}/camera/piano-camerad \
  %{buildroot}%{_prefix}/lib/piano/piano-camerad
install -Dm0755 %{_builddir}/%{name}-%{version}/camera/piano-camera-ctl \
  %{buildroot}%{_bindir}/piano-camera-ctl
install -Dm0644 src/LICENSE-MIT \
  %{buildroot}%{_licensedir}/piano-camera/LICENSE-MIT
install -Dm0644 src/quad/LICENSE-MIT \
  %{buildroot}%{_licensedir}/piano-quad/LICENSE-MIT
install -Dm0644 src/quad/vendor/demosaic/LICENSE-MIT \
  %{buildroot}%{_licensedir}/demosaic/LICENSE-MIT

%files
%{_licensedir}/piano-camera/LICENSE-MIT
%{_licensedir}/piano-quad/LICENSE-MIT
%{_licensedir}/demosaic/LICENSE-MIT
%{_prefix}/lib/piano/piano-camerad
%{_bindir}/piano-camera-ctl
%{_prefix}/lib/piano/camera-start
%{_unitdir}/piano-camerad.service
%{_unitdir}/piano-camera.service
%config(noreplace) %{_sysconfdir}/udev/rules.d/70-piano-camera.rules
%config(noreplace) %{_sysconfdir}/wireplumber/wireplumber.conf.d/50-piano-camera.conf

%post
%systemd_post piano-camerad.service piano-camera.service
systemctl enable piano-camerad.service piano-camera.service || :

%preun
%systemd_preun piano-camerad.service piano-camera.service

%postun
%systemd_postun_with_restart piano-camerad.service piano-camera.service

%changelog
* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 0.1.0-5
- 相机用户态换成上游 linux/userspace/camera：守护进程按 v4l2loopback 的
  生产者协商处理 S_FMT/S_PARM，后摄 8160x6120 走 RDI 全幅 Quad CFA，由
  libpiano-quad（Rust，demosaic 0.3.0 加 QPPG 修正）在软件里转成 NV12
- 依赖 libyuv、libjpeg 与 Rust；quad/vendor/demosaic 随包，构建不联网
- 静态拍摄沿用控制口的 capture <camera> [--flash]：自起流时按传感器原生
  尺寸拍、等自动曝光收敛，--flash 在无预览流时用常亮补光，有已收敛的预览
  时走上游的帧同步频闪

* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 0.1.0-4
- 新增全分辨率静态拍摄：piano-camera-ctl capture <camera> [--flash]
  --output FILE|-，守护进程通过 /run/piano-camerad/control.sock 交付 NV12 帧，
  客户端编码 JPEG；--flash 打开后置补光灯并在拍摄结束后关闭
- rear 传感器模式从 4080x3072 改为驱动实际报告的 4080x3060，原值在 7.3 内核上
  每次 S_FMT 都被钳制，后摄预览无法启动
* Mon Nov 09 2026 FFT-infra <dev@piano.invalid> - 0.1.0-3
- camera-start 只在 IOMMU 组不是 DMA 时才写 type（7.3 上该属性已只读，
  已是 DMA 时写入返回 EPERM，旧逻辑让 piano-camera 在真机上 failed）

* Tue Oct 20 2026 FFT-infra <dev@piano.invalid> - 0.1.0-2
- 收纳 Fedora 侧两个 camerad 补丁（GCC 边界检查、相机自有 source pad 路由），
  运行时不再另建一份同名二进制

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- Initial package for Xiaomi Pad 8 Pro camera relay and NEON conversion
