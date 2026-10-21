%undefine _debugsource_packages
%global debug_package %{nil}
# Ship the sealed modules byte for byte. rpm's brp-strip runs `strip -g` over
# every ELF in the buildroot, which rewrites .symtab inside each .ko; the RPM
# then no longer matches the kernel artifact that build-image verifies against
# (stage-rootfs-payload.same_module_tree). Pointing __strip at a no-op keeps
# the payload identical, and the modules already carry a build-id, so no link
# has to be regenerated.
%global __strip /bin/true
%global _build_id_links none
%global KERNEL_VER 7.2.9
%global KERNEL_TAG piano-7.2.9
%global DEVICE_NAME piano
%global PLATFORM_NAME sm8750

# The real payload is produced by the build-kernel pipeline (SunUEFI
# artifacts/kernels/release-7.2.9), not by this spec. It is handed to rpmbuild as
# a tar of the kernel tree: Image, System.map, config, dtb/ and lib/modules/.
#
# This used to be optional: `%install` only ran `touch` on empty files and the
# payload copy was guarded by `if [ -d kernel-payload ]`. build-rpms.sh never
# created that directory, so the matrix shipped a kernel-piano RPM whose
# vmlinuz and DTB were 0 bytes. A system built from it has no kernel at all.
# The payload is now mandatory and verified, so the failure is loud.
%global kernel_payload_tar kernel-payload-%{KERNEL_VER}.tar.zst

Name:           kernel-piano
Version:        7.2.9
Release:        1.%{DEVICE_NAME}%{?dist}
Summary:        Mainline Linux kernel with hardware fixes for Xiaomi Pad 8 Pro (piano)
License:        GPL-2.0-only
URL:            https://github.com/blu-sharky/linux-piano
# No Source0 on purpose: the kernel is not built here. See the payload note above.
Source0:        %{kernel_payload_tar}

ExclusiveArch:  aarch64

Provides:       kernel = %{version}-%{release}
Provides:       kernel-core = %{version}-%{release}
Provides:       kernel-modules = %{version}-%{release}
Provides:       kernel-modules-core = %{version}-%{release}

Requires:       dracut
Requires:       qbootctl
Requires:       f2fs-tools
# The root filesystem is F2FS; without the tools a rescue shell cannot repair it.
Requires:       linux-firmware
# %posttrans rebuilds the initramfs from the installed tree; that needs the
# interpreter, the static BusyBox it links applets to, blkid from util-linux,
# and depmod from kmod.
Requires:       python3
Requires:       kmod
Requires:       busybox
Requires:       util-linux

%description
Mainline Linux kernel adapted for Xiaomi Pad 8 Pro (SM8750 piano):
- F2FS root filesystem and Fedora SELinux label support
- FourSemi FS19xx amplifier hardware volume clamp (capped at 215)
- DPU dual-INTF slave CTL reset deferral to eliminate screen shutdown tearing (0038)
- Iris VPU35 hardware video decoder key frame support (0037)
- Gunyah Type-1 hypervisor guest isolation profile
- NT36532E SPI frame read timeout error retention

%prep
%setup -q -c -T
%{__tar} -I zstd -xf %{SOURCE0}

%build
# Nothing to compile: the kernel Image, DTB and modules arrive prebuilt from the
# pinned build-kernel pipeline. Rebuilding here would produce a different ABI
# from the one the external modules (piano-camera, uperf, thp) were built against.

%install
# Refuse to package an incomplete kernel. Every one of these has been observed
# missing at some point; a silent empty file is worse than a failed build.
test -s Image
test -s System.map
test -s config
test -d lib/modules

install -d %{buildroot}/boot/dtb/qcom
install -d %{buildroot}%{_prefix}/lib/modules

install -m 0644 Image    %{buildroot}/boot/vmlinuz-%{version}-%{release}
install -m 0644 System.map %{buildroot}/boot/System.map-%{version}-%{release}
install -m 0644 config   %{buildroot}/boot/config-%{version}-%{release}

# The board DTB may come as dtb/qcom/*.dtb (kernel build) or as a single
# board.dtb next to the kernel (SunUEFI packaging). Accept either.
if [ -d dtb ]; then
    cp -a dtb/. %{buildroot}/boot/dtb/
fi
if [ -f board.dtb ]; then
    install -m 0644 board.dtb %{buildroot}/boot/dtb/qcom/sm8750-xiaomi-piano.dtb
fi
test -s %{buildroot}/boot/dtb/qcom/sm8750-xiaomi-piano.dtb

# Modules keep the kernel's own release string (7.2.9-piano-gnome-g<hash>); the
# RPM version alone would not match the modules' vermagic.
cp -a lib/modules/. %{buildroot}%{_prefix}/lib/modules/
test -s "$(find %{buildroot}%{_prefix}/lib/modules -name 'modules.dep' | head -n 1)"

# Record which upstream commit this kernel came from, so a shipped image can be
# traced back without unpacking the RPM.
if [ -f manifest.json ]; then
    install -D -m 0644 manifest.json \
        %{buildroot}%{_datadir}/piano/kernel-manifest.json
fi

# 设备侧重建 initramfs 与重打 ESP 容器的那套工具。它们必须和内核同版本发布：
# 容器格式、manifest 判据都是跟这个内核一起演进的，分开升级会出现两套契约。
install -d %{buildroot}%{_prefix}/lib/piano
install -m 0755 piano-tools/f2fs-disk-bootstrap \
               piano-tools/piano-kernel-esp-update %{buildroot}%{_prefix}/lib/piano/
install -m 0644 piano-tools/build-initramfs.py \
               piano-tools/piano_artifacts.py \
               piano-tools/piano_boot_container.py %{buildroot}%{_prefix}/lib/piano/

%files
/boot/vmlinuz-%{version}-%{release}
/boot/System.map-%{version}-%{release}
/boot/config-%{version}-%{release}
/boot/dtb
# 整个模块树归本包所有，包括 updates/ 里的 v4l2loopback：它按本内核的 ABI
# 编译，模块树的名字就是本内核的 release。piano-runtime 不再提供第二份。
%{_prefix}/lib/modules/*
%{_datadir}/piano/kernel-manifest.json
%{_prefix}/lib/piano/f2fs-disk-bootstrap
%{_prefix}/lib/piano/piano-kernel-esp-update
%{_prefix}/lib/piano/build-initramfs.py
%{_prefix}/lib/piano/piano_artifacts.py
%{_prefix}/lib/piano/piano_boot_container.py

%posttrans
# 设备侧升级：depmod 之后重建 initramfs，再把新内核重新打成 ESP 上固件实际
# 读取的 boot.img。镜像构建（dnf --installroot）里没有 ESP 载荷，直接跳过；
# 判据是 board.dtb 在不在，而不是靠调用方传参，因为 %posttrans 拿不到上下文。
ESP=/boot/efi/EFI/Piano/stable
if [ ! -f "$ESP/board.dtb" ]; then
    echo "piano: no ESP payload at $ESP; skipping the boot.img rebuild"
    exit 0
fi
RELEASE=$(python3 -c 'import json;print(json.load(open("/usr/share/piano/kernel-manifest.json"))["kernel_release"])') || exit 1
depmod -a "$RELEASE" || exit 1
/usr/lib/piano/piano-kernel-esp-update --release "$RELEASE" || exit 1

%changelog
* Wed Oct 22 2026 FFT-infra <dev@piano.invalid> - 7.2.9-1.piano
- 内核纳入设备侧 dnf 升级：%posttrans 重建 initramfs 并重打 ESP 的 boot.img，
  失败时旧文件保持不动；v4l2loopback 改由本包拥有，外部模块跟着内核走

* Fri Oct 16 2026 FFT-infra <dev@piano.invalid> - 7.2.9-1.piano
- Standalone kernel packaging spec with F2FS, DPU, Iris, and FS19xx hardware limit patches
