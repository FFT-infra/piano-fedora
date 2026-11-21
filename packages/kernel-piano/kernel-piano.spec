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
%global KERNEL_VER 7.3.3
%global KERNEL_TAG piano-7.3.3
%global DEVICE_NAME piano
%global PLATFORM_NAME sm8750

# The real payload is produced by the build-kernel pipeline (SunUEFI
# artifacts/kernels/release), not by this spec. It is handed to rpmbuild as
# a tar of the kernel tree: Image, System.map, config, dtb/ and lib/modules/.
#
# This used to be optional: `%install` only ran `touch` on empty files and the
# payload copy was guarded by `if [ -d kernel-payload ]`. build-rpms.sh never
# created that directory, so the matrix shipped a kernel-piano RPM whose
# vmlinuz and DTB were 0 bytes. A system built from it has no kernel at all.
# The payload is now mandatory and verified, so the failure is loud.
%global kernel_payload_tar kernel-payload-%{KERNEL_VER}.tar.zst

Name:           kernel-piano
Version:        7.3.3
Release:        7.%{DEVICE_NAME}%{?dist}
Summary:        Mainline Linux kernel with hardware fixes for Xiaomi Pad 8 Pro (piano)
License:        GPL-2.0-only
URL:            https://github.com/blu-sharky/linux-piano
# No Source0 on purpose: the kernel is not built here. See the payload note above.
Source0:        %{kernel_payload_tar}

ExclusiveArch:  aarch64

# 不提供 kernel / kernel-core / kernel-modules / kernel-modules-core。DNF 的
# installonly 按这些 provides 匹配，会把新的 Release 当成再装一份内核；模块树
# 的目录只由编译期 release 决定（7.3.3-piano-sunuefi-cachy-optimize），两个
# Release 指向同一批文件，事务在安装阶段直接报 file conflict。这里按普通包
# 升级，%posttrans 仍在事务收尾时重建 initramfs 并为新内核写 systemd-boot
# 启动项（BLS 布局；ESP 是可选 UKI 布局时写版本化 UKI）。

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
# 出厂是 BLS 布局，写启动项不需要 ukify。ESP 若被改成可选 UKI 布局，
# %posttrans 用 systemd-ukify 重打 UKI，stub 由 systemd-boot-unsigned 提供；
# 缺这两个工具时那条路径放弃写 ESP，BLS 布局的升级不受影响。
Requires:       systemd-ukify
Requires:       systemd-boot-unsigned

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

# Modules keep the kernel's own release string (7.3.3-piano-sunuefi-cachy-optimize);
# the RPM version alone would not match the modules' vermagic.
cp -a lib/modules/. %{buildroot}%{_prefix}/lib/modules/
test -s "$(find %{buildroot}%{_prefix}/lib/modules -name 'modules.dep' | head -n 1)"

# Record which upstream commit this kernel came from, so a shipped image can be
# traced back without unpacking the RPM.
#
# The name still carries the package NVRA. A fixed
# /usr/share/piano/kernel-manifest.json collided when two packages owned it and
# rpm refused the whole transaction ("file ... conflicts with file from package
# kernel-piano-7.2.9-1"). The NVRA name also keeps leftover manifests
# distinguishable for piano-kernel-esp-update.
if [ -f manifest.json ]; then
    install -D -m 0644 manifest.json \
        %{buildroot}%{_datadir}/piano/kernel-manifest-%{version}-%{release}.json
fi

# 设备侧重建 initramfs 与重打 ESP UKI 的那套工具。它们必须和内核同版本发布：
# UKI 段布局、manifest 判据都是跟这个内核一起演进的，分开升级会出现两套契约。
install -d %{buildroot}%{_prefix}/lib/piano
install -m 0755 piano-tools/f2fs-disk-bootstrap \
               piano-tools/piano-kernel-esp-update %{buildroot}%{_prefix}/lib/piano/
install -m 0644 piano-tools/build-initramfs.py \
               piano-tools/piano_artifacts.py %{buildroot}%{_prefix}/lib/piano/

%files
/boot/vmlinuz-%{version}-%{release}
/boot/System.map-%{version}-%{release}
/boot/config-%{version}-%{release}
/boot/dtb
# 整个模块树归本包所有，包括 updates/ 里的 v4l2loopback：它按本内核的 ABI
# 编译，模块树的名字就是本内核的 release。piano-runtime 不再提供第二份。
%{_prefix}/lib/modules/*
%{_datadir}/piano/kernel-manifest-%{version}-%{release}.json
%{_prefix}/lib/piano/f2fs-disk-bootstrap
%{_prefix}/lib/piano/piano-kernel-esp-update
%{_prefix}/lib/piano/build-initramfs.py
%{_prefix}/lib/piano/piano_artifacts.py

%posttrans
# 设备侧升级：depmod 之后重建 initramfs，再为刚装上的内核写一条 systemd-boot
# 启动项。出厂是 BLS 布局（/linux/<release>/ 与 /loader/entries/），工具也认
# 可选的 UKI 布局。镜像构建（dnf --installroot）里没有 ESP 载荷，直接跳过；
# 判据是 ESP 上有没有启动项，而不是靠调用方传参，%posttrans 拿不到上下文。
ESP=/boot/efi
if [ ! -d "$ESP/loader/entries" ] && [ ! -d "$ESP/EFI/Linux" ]; then
    echo "piano: no ESP boot entries at $ESP; skipping the boot entry update"
    exit 0
fi
# manifest 按 NVRA 各存一份，盘上可能留着旧版本。这里读刚装上的那份：
# %{version}-%{release} 在构建时就已展开成这一版的字符串。
MANIFEST=%{_datadir}/piano/kernel-manifest-%{version}-%{release}.json
[ -f "$MANIFEST" ] || exit 1
RELEASE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["kernel_release"])' "$MANIFEST") || exit 1
[ -d "/usr/lib/modules/$RELEASE" ] || exit 1
depmod -a "$RELEASE" || exit 1
# 点名本包的 manifest，不让工具在盘上留下的多份之间猜。
/usr/lib/piano/piano-kernel-esp-update --release "$RELEASE" --manifest "$MANIFEST" || exit 1

%changelog
* Sat Nov 22 2026 FFT-infra <dev@piano.invalid> - 7.3.3-7.piano
- 封存序列跟到 SunUEFI edcf272：上游 261 条（新增 q6apm/audioreach 五条与
  音频 DAP 一条），自有 36 条后移到 0261–0296，末端树 b2107541 → a152a01d

* Mon Nov 17 2026 FFT-infra <dev@piano.invalid> - 7.3.3-6.piano
- 封存序列跟到 SunUEFI 18274377：上游 126 条（新增 GT9764BA 镜头驱动与
  绑定两条，排除与用户态卡尔曼冲突的 0095 及依赖它的 0122–0124），
  自有 35 条后移到 0127–0161，末端树 472ad593 → f92d2647

* Mon Nov 16 2026 FFT-infra <dev@piano.invalid> - 7.3.3-5.piano
- 内核补丁序列跟到 SunUEFI 30e23d75，重放封存序列并去掉 uefi-ebs-identity

* Sun Nov 15 2026 FFT-infra <dev@piano.invalid> - 7.3.3-4.piano
- 去掉 kernel / kernel-core / kernel-modules / kernel-modules-core 这四个
  provides。DNF 按它们判定 installonly，而模块树目录只跟编译期 release 走，
  新旧 Release 争同一批文件，`dnf upgrade` 在安装阶段报 file conflict（-3
  在真机上撞到，没有装上）。改成普通升级，`%posttrans` 照旧重建 initramfs
  并重打 UKI

* Sun Nov 15 2026 FFT-infra <dev@piano.invalid> - 7.3.3-3.piano
- 重发同一内核载荷，给设备上的 dnf upgrade 一个可见的新 NVRA。`%posttrans`
  会重建 initramfs 并重打 ESP 上的 UKI；这条自动路径要在真机上验完才记账，
  在验证前不写成已通过

* Tue Nov 10 2026 FFT-infra <dev@piano.invalid> - 7.3.3-2.piano
- 内核发布串从 -piano-sunuefi-cachy 变成 -piano-sunuefi-cachy-optimize，模块树
  目录随之改名。同一 NVRA 的下一个 Release 是设备侧 dnf 唯一能看见这次替换的
  途径，否则已装系统继续跑旧内核，只有新装机拿到新载荷
- 编译器换成固定的 LLVM 23.1.5，打开 O3、ThinLTO 与 Oryon-1 调度模型

* Sun Nov 08 2026 FFT-infra <dev@piano.invalid> - 7.3.3-1.piano
- 跟到 SunUEFI 8b0fc42d 的 stable 7.3.3。封存序列整体重放，上游 41 行加本仓库
  38 行共 79 行全部 clean apply，`target_tree` 为 `2d19373cafae`；补丁号随之
  重排到 0042–0079
- 板级 overlay 按上游 `release.json` 补到 18 个，新增 DisplayPort、蓝牙 NVM
  与 IR SPI 控制器；UFS 补丁改用上游的 `EnsureDeviceActive()`
- 打包版本号随内核升到 7.3.3，NVRA 变化让 installonly 升级替换内核载荷

* Sun Nov 08 2026 FFT-infra <dev@piano.invalid> - 7.3.0-5.piano
- 内核按 Fedora 做法补上 firewalld 需要的十个 conntrack helper，全部为模块；
  设备侧 dnf 升级需要新的 NVRA，否则同版本号不会替换内核载荷

* Mon Oct 26 2026 FFT-infra <dev@piano.invalid> - 7.3.0-4.piano
- 封存序列补齐到 55 行（`target_tree` 仍是 `81983254f51a`），整核重编
  `MAKE_EXIT=0`，Image 48650752 字节、0 error。补的是上游 2026-10 那批
  cherry-pick 的主线修复，含 `0055` 的 iris 关电线程化 IRQ 退出

* Thu Oct 22 2026 FFT-infra <dev@piano.invalid> - 7.2.9-3.piano
- manifest 按 NVRA 命名：installonly 升级时新旧内核不再争同一个
  /usr/share/piano/kernel-manifest.json，rpm 事务得以完成

* Thu Oct 22 2026 FFT-infra <dev@piano.invalid> - 7.2.9-2.piano
- 内核纳入设备侧 dnf 升级：%posttrans 重建 initramfs 并重打 ESP 的 boot.img，
  失败时旧文件保持不动；v4l2loopback 改由本包拥有，外部模块跟着内核走

* Thu Oct 22 2026 FFT-infra <dev@piano.invalid> - 7.2.9-1.piano
- Standalone kernel packaging spec with F2FS, DPU, Iris, and FS19xx hardware limit patches
