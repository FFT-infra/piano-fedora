%global debug_package %{nil}

# 新的 TEE 传输直接用内核的 linux/tee.h 对象 uAPI，不再取 quic-teec 与
# minkipc。QCBOR 仍按上游 config/fingerprint.json 的提交取用。
%global qcbor_commit 590a23daf65af068cee81555c597063150e23539

Name:           piano-fingerprint
Version:        0.2.0
Release:        1%{?dist}
Summary:        Goodix under-display fingerprint client for Xiaomi Pad 8 Pro (piano)
License:        BSD-3-Clause AND BSD-3-Clause-Clear AND (GPL-2.0-only WITH Linux-syscall-note)
URL:            https://github.com/FFT-infra/piano-fedora
# 本地源码：src/ 是 SunUEFI linux/userspace/fingerprint 的副本，见 README.md。
Source0:        %{name}-%{version}.tar.gz
Source1:        https://github.com/laurencelundblade/QCBOR/archive/%{qcbor_commit}.tar.gz#/QCBOR-%{qcbor_commit}.tar.gz

ExclusiveArch:  aarch64

BuildRequires:  gcc
BuildRequires:  glibc-static
# 驱动头文件来自发行版内核；TEE 对象 uAPI 已经稳定，7.2 与 7.3 的 tee.h 相同。
BuildRequires:  kernel-headers

%description
Native Goodix client for the piano fingerprint sensor, speaking to the signed
TrustZone TA through the standard Linux TEE object uAPI. Provides
piano-fingerprint-ctl and the backend source archive that piano-libfprint
compiles directly into its Goodix driver.

%prep
%autosetup -N -n %{name}-%{version}
tar -xzf %{SOURCE1}

%build
QCBOR=QCBOR-%{qcbor_commit}
FLAGS="%{optflags} -Wno-error -fPIC -D_GNU_SOURCE -DUSE_QCBOR -I$QCBOR/inc"

mkdir -p obj
i=0
for source in \
    "$QCBOR/src/UsefulBuf.c" "$QCBOR/src/qcbor_encode.c" \
    "$QCBOR/src/qcbor_decode.c" "$QCBOR/src/qcbor_err_to_str.c" \
    "$QCBOR/src/ieee754.c" \
    src/piano-goodix-storage.c src/piano-goodix-events.c \
    src/piano-goodix-gatekeeper.c src/piano-goodix-operations.c \
    src/piano-goodix-rpmb.c src/piano-goodix-tee.c src/piano-goodix.c \
    src/piano-goodix-cli.c; do
    gcc $FLAGS -c "$source" -o "obj/$i.o"
    i=$((i + 1))
done
gcc %{optflags} -Wno-error -static obj/*.o -pthread -lm -o piano-fingerprint-ctl

# libfprint 在目标发行版的 ABI 下自己编这些源码，不再链接跨发行版的静态库。
# 归档内容与上游 build_piano_fingerprint.py 的 backend_archive 一致。
mkdir -p backend/uapi/linux
cp src/piano-goodix*.c src/piano-goodix*.h src/BSD-3-Clause*.txt backend/
cp /usr/include/linux/tee.h backend/uapi/linux/tee.h
cp -a "$QCBOR" backend/qcbor
tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner \
    -cf piano-goodix-sources.tar -C backend .

%install
install -Dm0755 piano-fingerprint-ctl %{buildroot}%{_sbindir}/piano-fingerprint-ctl
install -Dm0644 piano-goodix-sources.tar \
    %{buildroot}%{_datadir}/piano-fingerprint/piano-goodix-sources.tar
QCBOR=QCBOR-%{qcbor_commit}
install -Dm0644 src/BSD-3-Clause.txt %{buildroot}%{_licensedir}/%{name}/BSD-3-Clause.txt
install -Dm0644 src/BSD-3-Clause-Clear.txt %{buildroot}%{_licensedir}/%{name}/BSD-3-Clause-Clear.txt
install -Dm0644 "$QCBOR/LICENSE" %{buildroot}%{_licensedir}/%{name}/QCBOR-LICENSE.txt
install -Dm0644 /usr/include/linux/tee.h %{buildroot}%{_licensedir}/%{name}/linux-tee.h

%files
%{_sbindir}/piano-fingerprint-ctl
%{_datadir}/piano-fingerprint/piano-goodix-sources.tar
%license %{_licensedir}/%{name}/*

%changelog
* Wed Nov 18 2026 FFT-infra <dev@piano.invalid> - 0.2.0-1
- 跟到上游的 TEE 重写：直接走内核 linux/tee.h 对象 uAPI，删掉 quic-teec 与
  minkipc 依赖；为 libfprint 产出源码归档，不再安装 libpiano-goodix.a

* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 0.1.0-1
- 首个包：Goodix 原生客户端与 libpiano-goodix.a，源码来自 SunUEFI
  linux/userspace/fingerprint，依赖 quic-teec 与 minkipc 的固定提交
