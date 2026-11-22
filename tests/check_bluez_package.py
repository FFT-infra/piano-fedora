#!/usr/bin/env python3
"""piano-bluez 重编包的离线契约。

补丁来自 SunUEFI `patches/bluez/5.88/`，摘要必须和那边的 series.json 一致。
这里锁住：两条补丁的内容、整套二进制包对 Fedora 同名包的 Provides/Obsoletes、
构建矩阵与发布链的接入。设备侧（HoG 键盘拿到正确 VID/PID、mpris-proxy 退出）
不在离线测试里。
"""

import hashlib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package_sets


def load_publish_copr():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "publish_copr", ROOT / "scripts" / "publish-copr.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
PKG = ROOT / "packages" / "piano-bluez"
SPEC = PKG / "piano-bluez.spec"
BUILD = ROOT / "scripts" / "build-rpms.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "build-piano-rpms.yml"
SUNUEFI = Path(os.environ.get("PIANO_TEST_SUNUEFI",
                              ROOT / "build/test-sources/sunuefi"))

HOG_SHA256 = "e406f8f7125f0a58d77c3fdbac5eec0e840b9cfdc0159369ed21fd19a0466ed8"
MPRIS_SHA256 = "5b15f000a0a97cc0228bc84279624c628defcddc3b7f03bca194dbe2ffd9c254"

# Fedora 45 的 bluez 5.88 拆成这些二进制包；全部要被顶掉，否则升级时同一
# 路径会落两个所有者。
REPLACED = ("bluez", "bluez-libs", "bluez-libs-devel", "bluez-cups",
            "bluez-hid2hci", "bluez-mesh", "bluez-obexd", "bluez-deprecated")


def main():
    hog = PKG / "0001-hog-read-uncached-pnp-id.patch"
    mpris = PKG / "0002-mpris-proxy-handle-signals-in-main-loop.patch"
    assert hashlib.sha256(hog.read_bytes()).hexdigest() == HOG_SHA256, \
        "HoG 补丁与 SunUEFI series.json 的摘要不一致"
    assert hashlib.sha256(mpris.read_bytes()).hexdigest() == MPRIS_SHA256, \
        "mpris-proxy 补丁与 SunUEFI series.json 的摘要不一致"
    hog_text = hog.read_text()
    assert "find_pnp_id" in hog_text and "PNP_ID_SIZE" in hog_text, \
        "HoG 补丁没有先读 PnP ID"
    assert "DEVICE_INFORMATION_UUID" in hog_text, "PnP ID 走错了 GATT 服务"
    mpris_text = mpris.read_text()
    assert "g_unix_signal_add" in mpris_text, \
        "mpris-proxy 仍从信号处理里直接退主循环"
    assert "glib-unix.h" in mpris_text, "mpris-proxy 没有用 GLib 的 Unix 信号源"

    spec = SPEC.read_text()
    assert re.search(r"^Name:\s+piano-bluez$", spec, re.M), "包名应是 piano-bluez"
    assert re.search(r"^Version:\s+5\.88$", spec, re.M), "版本没有对上 Fedora 的 5.88"
    assert re.search(r"^Release:\s+[2-9]", spec, re.M), \
        "Release 必须高于 Fedora 的 5.88-1，否则 Obsoletes 不生效"
    assert "Patch0:  0001-hog-read-uncached-pnp-id.patch" in spec, "spec 没有套 HoG 补丁"
    assert "Patch1:  0002-mpris-proxy-handle-signals-in-main-loop.patch" in spec, \
        "spec 没有套 mpris-proxy 补丁"
    for name in REPLACED:
        assert f"Provides:       {name} = %{{version}}-%{{release}}" in spec or \
               f"Provides: {name} = %{{version}}-%{{release}}" in spec, \
            f"没有声明替换 {name}"
        assert f"Obsoletes:      {name} < %{{version}}-%{{release}}" in spec or \
               f"Obsoletes: {name} < %{{version}}-%{{release}}" in spec, \
            f"没有淘汰 Fedora 的 {name}"
    assert "Conflicts:      bluez > %{version}-%{release}" in spec, \
        "没有拦住 Fedora 更高版本的静默替换"

    assert "piano-bluez" in package_sets.matrix(), "构建矩阵没有包含 piano-bluez"
    workflow = WORKFLOW.read_text()
    assert "package_sets.matrix()" in workflow, "workflow 的产物清单没有从 package_sets 派生"
    assert "libell-devel" in workflow, "workflow 没有安装 libell-devel"

    # 发布链的包名不再逐处手写，都从 package-sets.json 派生。这里核对派生
    # 结果真的含 piano-bluez，而不是核对源码里有没有那串字面量。
    copr = load_publish_copr()
    assert "piano-bluez" in copr.REQUIRED, "publish-copr 的必需清单没有 piano-bluez"
    assert "piano-bluez" in copr.BUILD_ORDER, "publish-copr 的构建顺序没有 piano-bluez"
    for script in ("build-clean-rootfs.py", "finalize-fedora-rootfs.py",
                   "verify-copr-device.py"):
        text = (ROOT / "scripts" / script).read_text()
        assert "package_sets" in text, f"{script} 没有从 package_sets 取包清单"

    if (SUNUEFI / "patches/bluez/5.88/series.json").is_file():
        series = json.loads((SUNUEFI / "patches/bluez/5.88/series.json").read_text())
        upstream = {row["sha256"] for row in series["patches"]}
        assert upstream == {HOG_SHA256, MPRIS_SHA256}, \
            "SunUEFI 的 bluez 序列变了，本地补丁要跟着换"
    else:
        print("SKIP: SunUEFI bluez 序列需要 PIANO_TEST_SUNUEFI")

    print("OK: bluez 两条补丁、整套替换声明、构建与发布链接入齐全")


if __name__ == "__main__":
    main()
