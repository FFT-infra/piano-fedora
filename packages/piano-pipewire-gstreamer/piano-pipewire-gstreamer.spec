%global debug_package %{nil}

# Fedora 45 的 pipewire-1.6.12-1 已带 1.6 分支收的 GStreamer 修复：SPA choice
# 的首选值进 caps（Snapshot 走 portal 不再拿到 16x4）、buffer_recycle 的加锁
# 顺序、首帧 keepalive 的空指针、32 位架构上的 modifier 截断。基准还是 1.6.11
# 时为这几处带的六个补丁已在 1.6.12 的源码里，再带会在 %prep 阶段套不上。
#
# 1.6.12 的 pipewiresrc 还缺三处，这份包现在只补它们：
#
# 1. caps 带列表时 "caps 没变就不重连" 的判定永远不成立，每次 reconfigure 都
#    断开重连，池子被拆掉时 sink 手里的缓冲还在被复制。上游 97699d70 改了
#    判定，1.6.12 没有带。
# 2. MemPtr chunk 指向的 PipeWire 映射会在流重协商时被移除，下游持有 buffer
#    引用也留不住，要拷贝后再交出。
# 3. 流的 caps 与当前格式没有交集时，fixate 之后仍是空 caps，断言直接返回，
#    wait_negotiated() 一直等；改走流的错误路径，只让这一条流失败。
#
# 三个补丁逐字节取自 SunUEFI patches/pipewire 的 1.6.11 序列（文件在 1.6.9
# 目录里，按系列引用，那边的编号是 0004、0006、0013）。上游序列还没有 1.6.12
# 目录；这三条在 1.6.12 上只有行号偏移，fuzz 0 能套上。Fedora 带上这三处之后
# 整份包删除。
#
# 这份包只替换 pipewire-gstreamer 这一个插件（它只含 libgstpipewire.so）。
# spa v4l2 的修复在 pipewire-libs 里，不在下发范围。
Name:           piano-pipewire-gstreamer
Version:        1.6.12
Release:        2%{?dist}
Summary:        GStreamer 1.0 plugin for PipeWire, with pipewiresrc fixes the release lacks
License:        MIT
URL:            https://gitlab.freedesktop.org/pipewire/pipewire
Source0:        %{url}/-/archive/%{version}/pipewire-%{version}.tar.gz
Patch0:         0001-gst-retain-running-caps.patch
Patch1:         0002-gst-copy-memptr.patch
Patch2:         0003-gst-report-incompatible-capture-format.patch

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
BuildRequires:  pkgconfig(systemd)
BuildRequires:  pkgconfig(openssl)
BuildRequires:  pkgconfig(libsystemd)
BuildRequires:  systemd-rpm-macros
BuildRequires:  pkgconfig(libdrm)

# 只声明这个包实际编译的那条路径：核心库加 GStreamer 插件。sndfile、
# libpulse、libcanberra、ncursesw、sdl2、libselinux、vulkan、fftw3f 与
# echo-cancel-webrtc 这些子系统或者被构建段关掉、或者本来就是可选探测，
# 声明它们只会让构建环境膨胀。webrtc 还有一层原因：Fedora 45 只提供
# webrtc-audio-processing-2，照抄上游 spec 的 -1 会永远装不上。

%description
This package replaces the distribution pipewire-gstreamer plugin with a build of
the same PipeWire release plus three pipewiresrc fixes that the release lacks.
Without them pipewiresrc restarts the stream on every reconfigure event although
the running caps still fit, hands downstream MemPtr chunks whose mapping goes
away when the stream renegotiates, and keeps waiting instead of failing when the
capture format does not intersect the stream caps.

%prep
%autosetup -p1 -n pipewire-%{version}

%build
# 只编需要的部分：GStreamer 插件依赖 pipewire 核心库，meson 没有只编插件的
# 开关，所以关掉所有与插件无关的子系统。选项名取自 1.6.12 的
# meson_options.txt（与 1.6.11 的逐字节相同），写错一个 meson 会在配置阶段
# 直接停。
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
  -Dx11-xfixes=disabled \
  -Draop=disabled \
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
  -Decho-cancel-webrtc=disabled \
  -Dfftw=disabled \
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
* Fri Nov 27 2026 FFT-infra <dev@piano.invalid> - 1.6.12-2
- 基准换到 1.6.12，跟上 Fedora 45 updates 的 pipewire-1.6.12-1。上一版声明了
  Conflicts: pipewire-gstreamer > 1.6.11-7，按名字要官方子包的 GNOME 与 KDE
  包组因此解不出安装事务
- 1.6.12 已带首选 caps、加锁顺序、首帧空指针与 32 位 modifier 截断的修复，
  对应的六个补丁删掉。在 1.6.11 上套完这六个补丁，插件源码与 1.6.12 只差
  一个空行和上游另外两处改动：协商时先取线程锁，设备枚举断开时摘掉 metadata
  监听
- 留下 1.6.12 没有的三个补丁，内容不变：97699d70 的不重连判定、MemPtr 拷贝、
  不兼容采集格式的错误路径

* Sun Nov 22 2026 FFT-infra <dev@piano.invalid> - 1.6.11-7
- 补丁序列改成逐字节取自 SunUEFI edcf272 的 patches/pipewire 1.6.11 序列：
  新增 0013 报告不兼容的采集格式，不重连的判定改为上游 97699d70 的原文；
  上游 0008 的 per-plane stride 已在 1.6.11 tag 里，不再重复带
- 仍保留两条上游序列没带的 1.6 分支提交：首帧空指针与 32 位 modifier 截断

* Sun Nov 15 2026 FFT-infra <dev@piano.invalid> - 1.6.11-6
- 加第 8 个补丁：MemPtr chunk 改成拷贝后再交出。MemPtr 指向的 PipeWire
  映射会在流重协商时被移除，下游持有 buffer 引用也留不住；fd 背书的内存
  仍走 share，只对真正共享的 chunk 保留父 buffer

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
