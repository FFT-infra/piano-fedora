%global debug_package %{nil}

# Fedora 45 的 pipewire-1.6.11-1 是 1.6.11 tag 的源码，缺 1.6 分支在那之后
# 收的全部 GStreamer 修复。三个问题串在一起：
#
# 1. 转 caps 时丢掉 SPA choice 的首选值，gst_caps_fixate() 对区间取最小值，
#    Snapshot 走 portal 拿到 16x4 而不是 1920x1440（90ddb7e6）。
# 2. buffer_recycle 的加锁顺序反了，回收的 SPA buffer 可能在 sink 里还在被
#    复制（4d3a3fc8）。
# 3. caps 带列表时 "caps 没变就不重连" 的判定永远不成立，每次 reconfigure 都
#    断开重连，池子被拆掉时 sink 手里的缓冲还在被复制（master 97699d70，
#    1.6 分支还没有）。
#
# 前 6 个补丁是 1.6 分支 tip（9376731）在 src/gst/ 下的全部提交，套完后两个
# 源文件与分支 tip 逐字节一致；第 7 个补上与 master 的差距。Fedora 跟上之后
# 整份包删除。
#
# 这份包只替换 pipewire-gstreamer 这一个插件（它只含 libgstpipewire.so）。
# spa v4l2 的修复（865c43d6、f528e254）在 pipewire-libs 里，不在下发范围。
Name:           piano-pipewire-gstreamer
Version:        1.6.11
Release:        5%{?dist}
Summary:        GStreamer 1.0 plugin for PipeWire, GStreamer fixes from the 1.6 branch tip
License:        MIT
URL:            https://pipewire.org/
Source0:        %{url}/-/archive/%{version}/pipewire-%{version}.tar.gz
Patch0:         0001-gst-clamp-a-fraction-that-does-not-fit-a-GstFraction.patch
Patch1:         0002-gst-keep-the-value-a-source-prefers-when-converting-.patch
Patch2:         0003-gst-fix-video-format-conversion-and-enumeration-matc.patch
Patch3:         0004-gstpipewiresrc-fix-locking-order.patch
Patch4:         0005-gstpipewiresrc-fix-NULL-gst_buffer_unref-on-first-bu.patch
Patch5:         0006-gst-fix-format-conversion-failure-on-32-bit-architec.patch
Patch6:         0007-gstpipewiresrc-skip-restart-when-running-caps-still-fit.patch

ExclusiveArch:  aarch64

# 顶掉 Fedora 的 pipewire-gstreamer：两者提供同一个 .so，同时装会文件冲突。
Provides:       pipewire-gstreamer = %{version}-%{release}
Provides:       pipewire-gstreamer%{?_isa} = %{version}-%{release}
Obsoletes:      pipewire-gstreamer < %{version}-%{release}
# 只靠 Obsoletes 的话，Fedora 哪天发一个更高版本的 pipewire-gstreamer，dnf 会
# 把它当成我们包的升级，悄悄换回官方版，相机又开始崩，而没人知道为什么。
# 让这种更新直接冲突失败，由人来看新版本到底带没带修复。
Conflicts:      pipewire-gstreamer > %{version}-%{release}

BuildRequires:  meson >= 0.59.0
BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  pkgconfig
BuildRequires:  pkgconfig(libudev)
BuildRequires:  pkgconfig(dbus-1)
BuildRequires:  pkgconfig(glib-2.0) >= 2.32
BuildRequires:  pkgconfig(gio-unix-2.0) >= 2.32
BuildRequires:  pkgconfig(gstreamer-1.0) >= 1.10.0
BuildRequires:  pkgconfig(gstreamer-base-1.0) >= 1.10.0
BuildRequires:  pkgconfig(gstreamer-plugins-base-1.0) >= 1.10.0
BuildRequires:  pkgconfig(gstreamer-net-1.0) >= 1.10.0
BuildRequires:  pkgconfig(gstreamer-allocators-1.0) >= 1.10.0
BuildRequires:  pkgconfig(sndfile)
BuildRequires:  pkgconfig(systemd)
BuildRequires:  pkgconfig(openssl)
BuildRequires:  pkgconfig(libpulse) >= 13.0
BuildRequires:  pkgconfig(libcanberra)
BuildRequires:  pkgconfig(ncursesw)
BuildRequires:  pkgconfig(sdl2)
BuildRequires:  pkgconfig(libselinux)
BuildRequires:  pkgconfig(libsystemd)
BuildRequires:  pkgconfig(vulkan)
BuildRequires:  pkgconfig(webrtc-audio-processing-1) >= 1.0
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3-docutils
BuildRequires:  pkgconfig(fftw3f)
BuildRequires:  pkgconfig(libdrm)

%description
This package replaces the distribution pipewire-gstreamer plugin with the one
from the 1.6 branch tip. The release Fedora ships resolves a SPA choice range to
its minimum in GStreamer caps, so a camera advertising 16x4 through 6528x4896
lands every client that expresses no preference of its own on 16x4 instead of
the native 1920x1440. The same plugin also mis-orders a lock in buffer recycling,
which can hand an unmapped buffer to a copy.

%prep
%autosetup -p1 -n pipewire-%{version}

%build
# 只编需要的部分：GStreamer 插件依赖 pipewire 核心库，meson 没有只编插件的
# 开关，所以关掉所有与插件无关的子系统。选项名取自 1.6.11 的
# meson_options.txt，写错一个 meson 会在配置阶段直接停。
%meson \
  -Dgstreamer=enabled \
  -Dgstreamer-device-provider=enabled \
  -Dtests=disabled \
  -Dexamples=disabled \
  -Dman=disabled \
  -Ddocs=disabled \
  -Dffmpeg=disabled \
  -Droc=disabled \
  -Dbluez5=disabled \
  -Dudev=disabled \
  -Dsession-managers=[] \
  -Dlegacy-rtkit=false \
  -Ddbus=disabled \
  -Davahi=disabled \
  -Dselinux=disabled \
  -Dsystemd-system-service=disabled \
  -Dsystemd-user-service=disabled \
  -Dlogind=disabled \
  -Dx11=disabled \
  -Dlibusb=disabled \
  -Dreadline=disabled \
  -Dpw-cat=disabled \
  -Dlv2=disabled \
  -Dalsa=disabled \
  -Dvideoconvert=disabled \
  -Dvideotestsrc=disabled \
  -Daudiotestsrc=disabled \
  -Dvolume=disabled \
  -Dcontrol=disabled \
  -Dvulkan=disabled \
  -Dlibcamera=disabled \
  -Dpipewire-alsa=disabled \
  -Dpipewire-jack=disabled \
  -Dpipewire-v4l2=disabled \
  -Dlibpulse=disabled \
  -Dlibcanberra=disabled \
  -Dsdl2=disabled \
  -Dsndfile=disabled \
  -Dlibmysofa=disabled \
  -Dopus=disabled \
  -Dlibffado=disabled \
  -Djack=disabled \
  -Dv4l2=disabled \
  -Dtest=disabled \
  -Devl=disabled \
  -Dflatpak=disabled \
  -Dgsettings=disabled \
  -Dgsettings-pulse-schema=disabled \
  -Dsnap=disabled \
  -Debur128=disabled \
  -Donnxruntime=disabled
%meson_build

%install
%meson_install
# 只留下这个插件；其余子包由 Fedora 的 pipewire 提供。
rm -rf %{buildroot}%{_libdir}/pipewire-0.3
rm -rf %{buildroot}%{_libdir}/spa-0.2
rm -rf %{buildroot}%{_bindir}
rm -rf %{buildroot}%{_sysconfdir}
rm -rf %{buildroot}%{_unitdir}
rm -rf %{buildroot}%{_userunitdir}
rm -rf %{buildroot}%{_datadir}/pipewire
rm -rf %{buildroot}%{_datadir}/spa-0.2
rm -rf %{buildroot}%{_datadir}/dbus-1
rm -rf %{buildroot}%{_datadir}/gsettings-schemas
rm -rf %{buildroot}%{_datadir}/man
rm -rf %{buildroot}%{_includedir}
rm -rf %{buildroot}%{_datadir}/locale
rm -rf %{buildroot}%{_libdir}/pkgconfig
rm -rf %{buildroot}%{_libdir}/libpipewire-0.3.so*
rm -rf %{buildroot}%{_libdir}/libspa-0.2.so*

%files
%{_libdir}/gstreamer-1.0/libgstpipewire.so

%changelog
* Sat Nov 14 2026 FFT-infra <dev@piano.invalid> - 1.6.11-5
- 加 Conflicts: pipewire-gstreamer > 本版本。Obsoletes 只压得住旧版本，
  更高版本会被 dnf 当成升级把我们的插件换掉，相机静默退回崩溃；冲突
  能让这种更新停下来等人判断

* Sat Nov 14 2026 FFT-infra <dev@piano.invalid> - 1.6.11-4
- 加第 7 个补丁：master 97699d70 的手工 backport。caps 带列表时
  gst_pipewire_src_negotiate() 的 "caps 未变" 判定永远不成立，流每秒
  重连十几次，池子拆掉时 sink 里排队的缓冲已经解映射，
  gst_base_sink_drain 的深拷贝读它才段错误
- 设备实测：修前流在数秒内断开重连十余次、约一秒后段错误；修后连续
  预览四分钟以上，期间一次重连，无新 core，预览正常出图

* Sat Nov 14 2026 FFT-infra <dev@piano.invalid> - 1.6.11-3
- 由单个 90ddb7e6 改成 1.6 分支 tip 在 src/gst/ 下的 6 个提交：补上
  buffer_recycle 的加锁顺序（4d3a3fc8）与首帧 keepalive 的空指针
  （1efc5700），以及格式转换与枚举匹配的重写（04f0d3f7）
- 套完后两个源文件与分支 tip 9376731 逐字节一致
- 尺寸回到 1920x1440 之后 Snapshot 仍在 queue0:src 的
  gst_base_sink_drain memcpy 里段错误，怀疑是回收的 SPA buffer 被提前
  解映射，本次修复针对这一点

* Fri Nov 13 2026 FFT-infra <dev@piano.invalid> - 1.6.11-2
- 摘取上游 90ddb7e6 并按 1.6.11 源码手工适配：SPA choice 的 preference
  在转 GStreamer caps 时保留为列表首项，gst_caps_fixate() 因此落在
  1920x1440 而不是区间下界 16x4
- GNOME Snapshot 经 portal 取相机时不再拿到 16x4，GTK4 Vulkan 渲染器
  的 turnip VM_BIND 段错误随之消失
