%global _annotated_build 0
%global debug_package %{nil}

Name:           piano-camera
Version:        0.1.0
Release:        4%{?dist}
Summary:        Camera pipeline and NEON-accelerated v4l2loopback daemon for Xiaomi Pad 8 Pro (piano)
License:        MIT AND Apache-2.0
URL:            https://github.com/FFT-infra/piano-fedora
Source0:        %{name}-%{version}.tar.gz
# 真机管线修复：GCC 边界检查与"相机拥有自己的 source pad"路由选择。运行时的
# 旧聚合包曾经在 SunUEFI 上游树上打这两个补丁再静态编译；现在它们属于这里，
# 由本 spec 统一构建，避免同名二进制出现两个 owner。
Patch0:         camerad-gcc-bounds.patch
Patch1:         camerad-owned-pix-route.patch

ExclusiveArch:  aarch64

BuildRequires:  gcc
BuildRequires:  libjpeg-turbo-devel
BuildRequires:  systemd-rpm-macros
Requires:       systemd

Provides:       piano-camera-daemon = %{version}-%{release}

%description
Camera hardware bring-up service and resident user-space relay daemon (piano-camerad)
for Xiaomi Pad 8 Pro (SM8750 piano). Features handwritten 16-pixel parallel AArch64
NEON NV12->BGRx conversion pipeline delivering 1.9ms low-latency frames into
v4l2loopback nodes, bypassing Mesa GTK diagonal tearing artifacts.

%prep
%autosetup -p1

%build
gcc %{optflags} -Isrc src/piano-camerad.c -lm -lpthread -o piano-camerad
gcc %{optflags} -Isrc src/piano-camera-ctl.c -ljpeg -o piano-camera-ctl

%install
mkdir -p %{buildroot}%{_prefix}/lib/piano
cp -a usr etc %{buildroot}/
install -m 0755 piano-camerad %{buildroot}%{_prefix}/lib/piano/piano-camerad
install -Dm0755 piano-camera-ctl %{buildroot}%{_bindir}/piano-camera-ctl

%files
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
* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 0.1.0-4
- 新增全分辨率静态拍摄：piano-camera-ctl capture <camera> [--flash]
  --output FILE|-，守护进程通过 /run/piano-camerad/control.sock 交付 NV12 帧，
  客户端编码 JPEG；--flash 打开后置补光灯并在拍摄结束后关闭
- rear 传感器模式从 4080x3072 改为驱动实际报告的 4080x3060，原值在 7.3 内核上
  每次 S_FMT 都被钳制，后摄预览无法启动
* Mon Nov 09 2026 FFT-infra <dev@piano.invalid> - 0.1.0-3
- camera-start 只在 IOMMU 组不是 DMA 时才写 type（7.3 上该属性已只读，
  已是 DMA 时写入返回 EPERM，旧逻辑让 piano-camera 在真机上 failed）

* Mon Oct 20 2026 FFT-infra <dev@piano.invalid> - 0.1.0-2
- 收纳 Fedora 侧两个 camerad 补丁（GCC 边界检查、相机自有 source pad 路由），
  运行时不再另建一份同名二进制

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- Initial package for Xiaomi Pad 8 Pro camera relay and NEON conversion
