%global _annotated_build 0
%global debug_package %{nil}

Name:           xiaomi-piano-thp
Version:        0.2.0
Release:        7%{?dist}
Summary:        Touch Host Processor (THP) and Xiaomi Focus Pen driver for Xiaomi Pad 8 Pro (piano)
License:        Apache-2.0 AND MIT
URL:            https://github.com/FFT-infra/piano-fedora
Source0:        %{name}-%{version}.tar.gz

ExclusiveArch:  aarch64

BuildRequires:  gcc-c++
BuildRequires:  json-devel
BuildRequires:  make
BuildRequires:  systemd-rpm-macros
Requires:       systemd
Requires:       python3
Requires:       python3-evdev
# 本包拥有 piano-touch.service，它启动的 pen-control.py 走 Gio/GLib 连 BlueZ。
Requires:       python3-gobject
Requires:       bluez

Provides:       piano-touch = %{version}-%{release}

%description
Touch Host Processor (THP) multitouch input daemon, 5-stage Kalman temporal
filtering, jitter lock (24px deadband), axis lock, non-linear physical grid
calibration (32000x21360 at 134x131 units/mm), and Xiaomi Focus Pen (P81C)
pressure/tilt input pipeline for Xiaomi Pad 8 Pro (SM8750 piano).

%prep
%autosetup

%build
make -C src all CXXFLAGS="%{optflags} -std=c++20"

%install
mkdir -p %{buildroot}%{_bindir}
cp -a usr etc %{buildroot}/
if [ -f src/build/piano-thp-live ]; then
    install -m 0755 src/build/piano-thp-live %{buildroot}%{_bindir}/piano-thp-live
fi

%files
# piano-touch-view, pen-control.py 与 piano-pen-offline 归 piano-runtime：
# SunUEFI 从 pin 的 debian-piano overlay 与 linux/userspace/pen 生成它们。
# 在这里再放一份同名文件就有两个所有者，内容一旦分叉 rpm 会整单回滚。
%{_bindir}/piano-thp-live
%{_unitdir}/piano-touch.service
%config(noreplace) %{_sysconfdir}/systemd/system/piano-touch.service.d/20-fast-restart.conf
%dir %{_datadir}/piano/touch
%{_datadir}/piano/touch/piano-csot.json
%{_datadir}/piano/touch/piano-boe.json
%dir %{_datadir}/piano/pen
%{_datadir}/piano/pen/p81c-radio.json
%dir %{_datadir}/piano/pen/calibration
%{_datadir}/piano/pen/calibration/piano-csot.ini
%{_datadir}/piano/pen/calibration/piano-boe.ini

%post
# unit 与上游一致，没有 [Install]：79-piano-touch.rules 在 /proc/nvt_thp_stream
# 出现时用 SYSTEMD_WANTS 拉起，不在这里 enable。
%systemd_post piano-touch.service

%preun
%systemd_preun piano-touch.service

%postun
%systemd_postun_with_restart piano-touch.service

%changelog
* Sun Nov 22 2026 FFT-infra <dev@piano.invalid> - 0.2.0-7
- piano-touch.service 跟到 SunUEFI edcf272：手指与笔改由内核解码，
  unit 只剩 BlueZ 笔初始化，启动 /usr/lib/piano/pen-control.py。
  已装设备上的旧 unit 指向已删除的 touch-input，不换 NEVRA 就升级不到

* Mon Nov 16 2026 FFT-infra <dev@piano.invalid> - 0.2.0-6
- 笔校准 INI 跟到上游 BSP 的普通 Focus Pen 参数，补 stylus_2 一组键值

* Sun Nov 15 2026 FFT-infra <dev@piano.invalid> - 0.2.0-5
- piano-touch 的快速重启 drop-in 由构建器直接写 rootfs 改为本包载荷，
  dnf upgrade 能送到已装设备，装配脚本不再覆盖 RPM 拥有的路径

* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 0.2.0-4
- p81c-radio.json 补上 `slide`：与上游 BSP 副本逐字节一致。piano-runtime
  的 pen-bluetooth 要求这个键，缺了它每次开机都抛 KeyError 退出

* Sun Nov 09 2026 FFT-infra <dev@piano.invalid> - 0.2.0-3
- piano-touch.service 跟上游 pin 对齐：去掉 touch-start 预启动与
  piano-display 依赖，交给 79-piano-touch udev 规则激活

* Mon Oct 20 2026 FFT-infra <dev@piano.invalid> - 0.2.0-2
- 交还 touch-start/touch-input/pen-bluetooth/piano-touch-view 给 piano-runtime，
  本包不再重复拥有这批由 SunUEFI 生成的运行时文件

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 0.2.0-1
- Modularize THP touch and pen stack with 5-stage Kalman filtering and 32000x21360 calibration
