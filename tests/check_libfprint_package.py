#!/usr/bin/env python3
"""libfprint 重编包的离线契约。

补丁来自 SunUEFI `patches/libfprint/1.94.100/`，摘要必须和那边的 series.json
一致。这里锁住：替换发行版包所需的 Provides/Obsoletes、piano-goodix 构建依赖、
fprintd 的 UIO/TEE 放行与状态目录、构建矩阵里的先后顺序，以及镜像清单真的
会装上这个包和 fprintd。设备侧的验证（fprintd-list 认到设备）不在离线测试里。
"""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages" / "piano-libfprint"
SPEC = PKG / "piano-libfprint.spec"
PATCH = PKG / "0001-piano-goodix-uio.patch"
DROPIN = PKG / "20-piano.conf"
BUILD = ROOT / "scripts" / "build-rpms.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "build-piano-rpms.yml"

PATCH_SHA256 = "6577da4849d748dfe472c9a2026419f91be175a98a926e3b98f0de5203ca1696"


def main():
    assert hashlib.sha256(PATCH.read_bytes()).hexdigest() == PATCH_SHA256, \
        "补丁内容与 SunUEFI pin 的摘要不一致"
    assert "drivers/piano-goodix.c" in PATCH.read_text(), "补丁没有带 piano_goodix 驱动"
    meson_patch = PKG / "0002-meson-dict-foreach.patch"
    assert meson_patch.is_file(), "缺少 meson 1.12 的 foreach 兼容补丁"
    assert "foreach driver_test, driver_test_args: drivers_tests" in meson_patch.read_text(), \
        "兼容补丁没有改掉单变量遍历 dict"

    spec = SPEC.read_text()
    assert re.search(r"^Version:\s+1\.94\.100$", spec, re.M), "版本没有对上 Fedora 的 1.94.100"
    assert re.search(r"^Release:\s+[2-9]", spec, re.M), \
        "Release 必须高于 Fedora 的 1.94.100-1，否则两份包会同时装"
    assert "Provides:       libfprint = %{version}-%{release}" in spec, "没有声明替换 libfprint"
    assert "Obsoletes:      libfprint < %{version}-%{release}" in spec, "没有淘汰发行版 libfprint"
    assert "BuildRequires:  pkgconfig(piano-goodix)" in spec, "没有依赖 piano-fingerprint 的 pkgconfig"
    for option in ("-Ddrivers=all", "-Dintrospection=false", "-Dudev_hwdb=disabled"):
        assert option in spec, f"meson 参数缺少 {option}"
    assert "BuildRequires:  gcc-c++" in spec, "libfprint 的 nbis 需要用 C++ 编译"

    dropin = DROPIN.read_text()
    for directive in ("DeviceAllow=char-uio rw", "DeviceAllow=char-tee rw",
                      "DeviceAllow=char-bsg rw", "StateDirectory=piano-goodix",
                      "StateDirectoryMode=0700"):
        assert directive in dropin, f"fprintd drop-in 缺少 {directive}"

    build = BUILD.read_text()
    order = re.findall(r'"([a-z0-9-]+)"', build.split("PACKAGES_TO_BUILD+=(")[1].split(")")[0])
    assert "piano-libfprint" in order, "构建矩阵没有包含 piano-libfprint"
    assert order.index("piano-fingerprint") < order.index("piano-libfprint"), \
        "piano-fingerprint 必须排在 piano-libfprint 前面"
    assert "libssc|piano-fingerprint)" in build, \
        "build-rpms 没有把刚编的 piano-fingerprint 装进构建环境"

    workflow = WORKFLOW.read_text()
    for name in ("piano-fingerprint", "piano-libfprint"):
        assert f'"{name}"' in workflow, f"workflow 的产物清单没有 {name}"
    for dep in ("libgusb-devel", "pixman-devel", "openssl-devel"):
        assert dep in workflow, f"workflow 没有安装 {dep}"

    rootfs = (ROOT / "scripts" / "build-clean-rootfs.py").read_text()
    for name in ("piano-fingerprint", "piano-libfprint"):
        assert f'"{name}"' in rootfs, f"clean rootfs 清单没有 {name}"
    finalize = (ROOT / "scripts" / "finalize-fedora-rootfs.py").read_text()
    for name in ("piano-fingerprint", "piano-libfprint"):
        assert f'"{name}"' in finalize, f"finalize 的必需包集合没有 {name}"

    profiles = (ROOT / "scripts" / "profiles" / "fedora-desktops.json").read_text()
    assert '"fprintd"' in profiles and '"fprintd-pam"' in profiles, "GNOME 档没有装 fprintd"
    print("OK: libfprint 补丁、替换声明、fprintd drop-in 与镜像清单齐全")


if __name__ == "__main__":
    main()
