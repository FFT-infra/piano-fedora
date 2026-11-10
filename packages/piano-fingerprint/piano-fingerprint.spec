%global debug_package %{nil}

# 依赖提交与 SunUEFI pin 一致；迁移 pin 时三处一起改。
%global quic_teec_commit 736419e25a2036aac3292a10a93e394a90750ca3
%global minkipc_commit   ce145804d9e994e0a7913f8d224d52e84bce6608
%global qcbor_commit     590a23daf65af068cee81555c597063150e23539

Name:           piano-fingerprint
Version:        0.1.0
Release:        1%{?dist}
Summary:        Goodix under-display fingerprint client for Xiaomi Pad 8 Pro (piano)
License:        BSD-3-Clause
URL:            https://github.com/FFT-infra/piano-fedora
# 本地源码：src/ 是 SunUEFI linux/userspace/fingerprint 的副本，见 README.md。
Source0:        %{name}-%{version}.tar.gz
Source1:        https://github.com/quic/quic-teec/archive/%{quic_teec_commit}.tar.gz#/quic-teec-%{quic_teec_commit}.tar.gz
Source2:        https://github.com/qualcomm/minkipc/archive/%{minkipc_commit}.tar.gz#/minkipc-%{minkipc_commit}.tar.gz
Source3:        https://github.com/laurencelundblade/QCBOR/archive/%{qcbor_commit}.tar.gz#/qcbor-%{qcbor_commit}.tar.gz
Source4:        piano-goodix.pc
# minkipc 回调缓冲生命周期与 supplicant worker 退出，pin 之后的上游修复。
Patch0:         0001-callback-output-buffer-lifetime.patch
Patch1:         0002-supplicant-worker-root-release.patch

ExclusiveArch:  aarch64

BuildRequires:  gcc
BuildRequires:  glibc-static
BuildRequires:  patch

%description
Native Goodix client for the piano fingerprint sensor, speaking to the signed
TrustZone TA through quic-teec and minkipc. Provides piano-fingerprint-ctl and
the static libpiano-goodix.a that the patched libfprint links against.

%prep
%autosetup -N -n %{name}-%{version}
tar -xzf %{SOURCE1}
tar -xzf %{SOURCE2}
tar -xzf %{SOURCE3}
patch --batch --fuzz=0 -p1 -d minkipc-%{minkipc_commit} < %{PATCH0}
patch --batch --fuzz=0 -p1 -d minkipc-%{minkipc_commit} < %{PATCH1}

%build
QTEE=quic-teec-%{quic_teec_commit}
MINK=minkipc-%{minkipc_commit}
QCBOR=QCBOR-%{qcbor_commit}
INCLUDES="-I$QTEE/libqcomtee/include -I$QTEE/libqcomtee/src \
  -I$MINK/libminkadaptor/include -I$MINK/libminkadaptor/src \
  -I$MINK/listeners/libfsservice/gpfs -I$MINK/listeners/libfsservice/fs \
  -I$MINK/listeners/libfsservice/cmn -I$QCBOR/inc"
FLAGS="-O2 -g -fPIC -D_GNU_SOURCE -DUSE_QCBOR"

mkdir -p obj
i=0
for source in \
    $QTEE/libqcomtee/src/qcomtee_object.c \
    $QTEE/libqcomtee/src/objects/credentials_obj.c \
    $QTEE/libqcomtee/src/objects/mem_obj.c \
    $MINK/libminkadaptor/src/mink_adaptor.c \
    $MINK/libminkadaptor/src/supplicant.c \
    $MINK/libminkadaptor/src/syscall.S \
    $QCBOR/src/UsefulBuf.c $QCBOR/src/qcbor_encode.c $QCBOR/src/qcbor_decode.c \
    $QCBOR/src/qcbor_err_to_str.c $QCBOR/src/ieee754.c \
    src/piano-goodix-storage.c src/piano-goodix-events.c \
    src/piano-goodix-gatekeeper.c src/piano-goodix-operations.c \
    src/piano-goodix-rpmb.c src/piano-goodix.c src/piano-goodix-cli.c; do
    gcc $FLAGS $INCLUDES -c "$source" -o "obj/$i.o"
    i=$((i + 1))
done
# obj/17.o 是 CLI，其余 17 个对象就是 libfprint 链接的 libpiano-goodix.a。
ar rcs libpiano-goodix.a obj/0.o obj/1.o obj/2.o obj/3.o obj/4.o obj/5.o \
    obj/6.o obj/7.o obj/8.o obj/9.o obj/10.o obj/11.o obj/12.o obj/13.o \
    obj/14.o obj/15.o obj/16.o
gcc $FLAGS -static obj/*.o -pthread -lm -o piano-fingerprint-ctl

%install
install -Dm0755 piano-fingerprint-ctl %{buildroot}%{_sbindir}/piano-fingerprint-ctl
install -Dm0644 libpiano-goodix.a %{buildroot}%{_libdir}/piano-goodix/libpiano-goodix.a
install -Dm0644 src/piano-goodix.h %{buildroot}%{_includedir}/piano-goodix/piano-goodix.h
install -Dm0644 %{SOURCE4} %{buildroot}%{_libdir}/pkgconfig/piano-goodix.pc
install -d %{buildroot}%{_licensedir}/%{name}
install -m0644 licenses/*.txt %{buildroot}%{_licensedir}/%{name}/

%files
%{_sbindir}/piano-fingerprint-ctl
%{_libdir}/piano-goodix/libpiano-goodix.a
%{_includedir}/piano-goodix/piano-goodix.h
%{_libdir}/pkgconfig/piano-goodix.pc
%license %{_licensedir}/%{name}/*.txt

%changelog
* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- 首个包：Goodix 原生客户端与 libpiano-goodix.a，源码来自 SunUEFI
  linux/userspace/fingerprint，依赖 quic-teec 与 minkipc 的固定提交
