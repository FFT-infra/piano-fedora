#!/usr/bin/env python3
"""重建固定 Fedora Mesa 源 RPM，加入锁定的 piano A830v1 补丁。"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import struct
import subprocess
import urllib.request
from pathlib import Path

from piano_artifacts import sha256

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://kojipkgs.fedoraproject.org/packages/mesa/26.2.6/1.fc45/src/mesa-26.2.6-1.fc45.src.rpm"
SOURCE_SHA256 = "d7e1bfbfebac51b18a8b32b7453f8001d368ba7ab5aa347dc405bb20093665e1"
SPEC_SHA256 = "b3988d190cc451f6984b7a768caa4105c59df22213b5341f4938b715bb4af286"
TARBALL_SHA256 = "b9436b3e1a8be76fbd4823dc884f90596ab7c6ac5e1867a305e66ffccfb31a92"
PATCH = "0001-freedreno-Add-chip-id-support-for-A830v1.patch"
PATCH_SHA256 = "01bc8e6b35019f7d6b84bc2d919a60686ffb1742d4df04467a89d3aac22a7380"
# 官方 Mesa 的版本号会一直往上走，26.2.6-1.piano1 在版本序上低于 26.2.7-1。
# 抬 epoch 能压住，但 epoch 卸不掉：补丁进了上游之后，回到 Fedora 的 mesa
# 要每台设备手工 distro-sync。这里改用 RPM Fusion 对同一个包的做法：换包名，
# 声明它提供原名并与原名冲突。设备上装的从来不是 Fedora 的 mesa-libGL，
# dnf upgrade 就没有可升级的目标，装了哪一版也能从包名直接看出来。
SOURCE_PACKAGE = "mesa-piano"
# 出货的六个运行时子包，以及它们替代的 Fedora 包名。spec 里的子包名和 30 条
# 精确版本依赖都写 %{name}，改 Name 一处就全部跟着变；这里只给每个子包补上
# 它替代的原名。Fedora 增删子包时这张表要人工过一遍，整份 spec 的摘要已经
# 钉住了这种变化。
REPLACES = {
    "filesystem": "mesa-filesystem",
    "libGL": "mesa-libGL",
    "libEGL": "mesa-libEGL",
    "dri-drivers": "mesa-dri-drivers",
    "libgbm": "mesa-libgbm",
    "vulkan-drivers": "mesa-vulkan-drivers",
}
PACKAGES = {f"{SOURCE_PACKAGE}-{sub}" for sub in REPLACES}
CHIP_ID = 0xffff44050001
GALLIUM = "libgallium-26.2.6.so"
SPEC_NAME = "Name:           mesa\n"
SPEC_RELEASE = "Release:        %autorelease\n"
SPEC_SETUP = "%autosetup -n %{name}-%{ver} -p1\n"
SPEC_DESCRIPTION = "\n%description\n"


def run(*command):
    subprocess.run([str(x) for x in command], check=True)


def query(*command):
    return subprocess.check_output([str(x) for x in command], text=True).strip()


def install_build_dependencies(spec, srpm, top):
    # SRPM 头的条件依赖在其打包机上已冻结；本机重新展开 spec，
    # 同时保留 SRPM 携带的动态 Rust BuildRequires。
    run("dnf", "-y", "builddep", "--define", "_topdir " + str(top),
        "--spec", spec, "--srpm", srpm)


def adapt_spec(raw):
    if hashlib.sha256(raw).hexdigest() != SPEC_SHA256:
        raise ValueError("Fedora Mesa spec changed; review the recipe")
    text = raw.decode()
    for anchor in (SPEC_NAME, SPEC_RELEASE, SPEC_SETUP, SPEC_DESCRIPTION):
        if text.count(anchor) != 1:
            raise ValueError("unexpected Fedora Mesa spec layout: " + anchor.strip())
    # 子包名与精确版本依赖都是 %{name}-<sub>，改这一处就整份跟着变。
    text = text.replace(SPEC_NAME, f"Name:           {SOURCE_PACKAGE}\n")
    # %autosetup 的 -n 原本取 %{name}-%{ver}。改名之后那个目录不存在，tarball
    # 解出来仍然是 mesa-26.2.6。
    text = text.replace(SPEC_SETUP, "%autosetup -n mesa-%{ver} -p1\n")
    text = text.replace(SPEC_RELEASE, "Release:        %autorelease -e piano1\n")
    # 每个子包声明它替代的 Fedora 包名：Provides 让依赖原名的东西继续解析；
    # Obsoletes 让按原名提的安装请求落到这里——dnf 解析明确的包名时只认名字，
    # Provides 压不过同名的官方包，桌面 environment 里的 mesa-dri-drivers 这类
    # 成员会和这里同时进事务，报 conflicting requests；
    # Conflicts 保证两个版本不会同时装进设备，也是 Fedora 版本号超过本包时
    # 唯一的报错出口（那时 Obsoletes 的区间盖不住，构建直接红，需要跟着新
    # SRPM 重做）。
    for sub, fedora in REPLACES.items():
        anchor = f"%package {sub}\n"
        if text.count(anchor) != 1:
            raise ValueError("unexpected Fedora Mesa spec layout: " + anchor.strip())
        text = text.replace(anchor, anchor + (
            f"Provides:       {fedora} = %{{version}}-%{{release}}\n"
            f"Provides:       {fedora}%{{?_isa}} = %{{version}}-%{{release}}\n"
            f"Obsoletes:      {fedora} < %{{version}}-%{{release}}\n"
            f"Conflicts:      {fedora}%{{?_isa}}\n"), 1)
    return text.replace(SPEC_DESCRIPTION,
                        "\nPatch999:       piano-a830v1.patch\n\n%description\n", 1)


def driver_proof(root):
    proof = {}
    for name in (GALLIUM, "libvulkan_freedreno.so"):
        relative = "usr/lib64/" + name
        path = root / relative
        data = path.read_bytes()
        if (data[:5] != b"\x7fELF\x02" or struct.unpack_from("<H", data, 18)[0] != 183
                or struct.pack("<Q", CHIP_ID) not in data or b"Adreno (TM) 830v1\0" not in data):
            raise ValueError("compiled Mesa lacks the AArch64 A830v1 device table: " + name)
        proof[relative] = sha256(path)
    return proof


def build(sun, output):
    if platform.machine() not in ("aarch64", "arm64") or os.geteuid() != 0:
        raise ValueError("native root Fedora builder required")
    if output.exists() or not output.is_relative_to(ROOT / "build"):
        raise ValueError("choose a fresh build-owned Mesa output")
    lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["piano-mesa"]
    source = sun / "upstream/piano-mesa-current"
    if query("git", "-C", source, "rev-parse", "HEAD") != lock["commit"] or query(
            "git", "-C", source, "status", "--porcelain"):
        raise ValueError("Mesa patch checkout is not the clean locked source")
    patch = source / "patches" / PATCH
    if sha256(patch) != PATCH_SHA256:
        raise ValueError("piano Mesa patch changed")
    output.mkdir(parents=True)
    srpm = output / "mesa.src.rpm"
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as incoming, srpm.open("xb") as stream:
        shutil.copyfileobj(incoming, stream)
    if sha256(srpm) != SOURCE_SHA256:
        raise ValueError("Fedora Mesa source RPM changed")
    top = output / "rpmbuild"
    for directory in ("SOURCES", "SPECS", "BUILD", "BUILDROOT", "RPMS", "SRPMS"):
        (top / directory).mkdir(parents=True)
    run("rpm", "-i", "--nodeps", "--define", "_topdir " + str(top), srpm)
    spec = top / "SPECS/mesa.spec"
    effective = adapt_spec(spec.read_bytes())
    if sha256(top / "SOURCES/mesa-26.2.6.tar.xz") != TARBALL_SHA256:
        raise ValueError("Mesa source tarball differs from the official release")
    spec.write_text(effective)
    shutil.copyfile(patch, top / "SOURCES/piano-a830v1.patch")
    # 复用 Fedora 的完整依赖和打包配方，不手列 Mesa 的大量构建依赖。
    install_build_dependencies(spec, srpm, top)
    # -ba 同时产出二进制与源包。COPR 公开实例没有二进制上传入口，发布走
    # 的是 SRPM 重建，只打 -bb 的话这个包在 COPR 上是发不出去的。
    run("rpmbuild", "-ba", "--define", "_topdir " + str(top),
        "--define", "_smp_build_ncpus 4", "--define", "_smp_mflags -j4",
        "--define", "debug_package %{nil}", spec)
    srpms = output / "srpms"
    srpms.mkdir()
    built = sorted((top / "SRPMS").glob("*.src.rpm"))
    if len(built) != 1:
        raise ValueError(f"expected one Mesa SRPM, found {[p.name for p in built]}")
    if query("rpm", "-qp", "--qf", "%{NAME}", built[0]) != SOURCE_PACKAGE:
        raise ValueError("the Mesa SRPM is not the renamed piano source package")
    shutil.copyfile(built[0], srpms / built[0].name)
    rpms = output / "rpms"
    rpms.mkdir()
    selected = {}
    for path in (top / "RPMS").rglob("*.rpm"):
        name, version, release, arch = query(
            "rpm", "-qp", "--qf", "%{NAME} %{VERSION} %{RELEASE} %{ARCH}", path).split()
        if name not in PACKAGES:
            continue
        if name in selected or (version, release, arch) != ("26.2.6", "1.piano1.fc45", "aarch64"):
            raise ValueError("unexpected Mesa RPM identity: " + path.name)
        shutil.copyfile(path, rpms / path.name)
        selected[name] = {"file": path.name, "nevra": f"{name}-{version}-{release}.{arch}"}
    if selected.keys() != PACKAGES:
        raise ValueError("Mesa runtime package set is incomplete")
    # 从实际 RPM 安装载荷核对两个驱动，而非把源码有一行当作构建成功。
    payload = output / "payload"
    payload.mkdir()
    run("rpm", "--root", payload, "--dbpath", "/var/lib/rpm", "--initdb")
    # Fedora 45 的 rpm 6 默认 %_pkgverify_level=all，本地编出来的包没有签名，
    # 安装会以每个包都失败告终。这里装的是刚编出的载荷，来源已经核对过摘要。
    run("rpm", "--root", payload, "--dbpath", "/var/lib/rpm",
        "--define", "_pkgverify_level none", "--nodeps", "--noscripts",
        "-i", *sorted(rpms.glob("*.rpm")))
    drivers = driver_proof(payload)
    packages = query("rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n")
    (output / "builder-packages.tsv").write_text("\n".join(sorted(packages.splitlines())) + "\n")
    result = {"status": "FEDORA_MESA_BUILT_NOT_DEVICE_VERIFIED", "releasever": "45",
              "arch": "aarch64", "chip_id": hex(CHIP_ID), "source_package": SOURCE_PACKAGE,
              "replaces": dict(REPLACES), "source_url": SOURCE_URL,
              "source_rpm_sha256": SOURCE_SHA256, "source_tarball_sha256": TARBALL_SHA256,
              "original_spec_sha256": SPEC_SHA256, "effective_spec_sha256": sha256(spec),
              "patch_commit": lock["commit"], "patch_sha256": PATCH_SHA256,
              "driver_files": drivers, "packages": selected,
              "rpms": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                       for p in sorted(rpms.glob("*.rpm"))},
              "srpms": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                        for p in sorted(srpms.glob("*.src.rpm"))},
              "builder_packages_sha256": sha256(output / "builder-packages.tsv"),
              "compiler": query("gcc", "--version").splitlines()[0], "device_tested": False}
    (output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sunuefi", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.sunuefi.resolve(), args.output.resolve())
