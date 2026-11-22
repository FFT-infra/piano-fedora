#!/usr/bin/env python3
"""指纹用户态包的离线契约。

源码不随包：spec 在构建期从 SunUEFI pin 取 `linux/userspace/fingerprint`。
新的 TEE 传输只用内核 `linux/tee.h` 对象 uAPI 与 QCBOR；这里锁住：pin 与
sources.lock.json 一致、spec 不再引用 quic-teec/minkipc、源码归档装进包里
供 libfprint 编译、构建矩阵会为远程 Source 跑 spectool。设备侧的结论
（TEE 能初始化、枚举）不在离线测试里。
"""

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages" / "piano-fingerprint"
SPEC = PKG / "piano-fingerprint.spec"
BUILD = ROOT / "scripts" / "build-rpms.sh"
sys.path.insert(0, str(ROOT / "scripts"))
import package_sets
UPSTREAM = Path(os.environ.get("PIANO_TEST_SUNUEFI",
                               ROOT / "build/test-sources/sunuefi")) / \
    "linux/userspace/fingerprint"

SOURCES = (
    "piano-goodix.c", "piano-goodix.h", "piano-goodix-cli.c",
    "piano-goodix-storage.c", "piano-goodix-storage.h",
    "piano-goodix-storage-protocol.h",
    "piano-goodix-events.c", "piano-goodix-events.h",
    "piano-goodix-gatekeeper.c", "piano-goodix-gatekeeper.h",
    "piano-goodix-operations.c", "piano-goodix-operations.h",
    "piano-goodix-rpmb.c", "piano-goodix-rpmb.h",
    "piano-goodix-tee.c", "piano-goodix-tee.h",
    "BSD-3-Clause.txt", "BSD-3-Clause-Clear.txt",
)


def main():
    assert not (PKG / "src").exists(), "上游源码副本不该入库"

    spec = SPEC.read_text()
    match = re.search(r"%global qcbor_commit\s+([0-9a-f]{40})", spec)
    assert match, "qcbor_commit 不是 40 位提交"
    pin = re.search(r"%global sunuefi_commit\s+([0-9a-f]{40})", spec)
    assert pin, "spec 没有 sunuefi_commit"
    lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    assert pin.group(1) == lock, "指纹包 pin 与 sources.lock.json 不一致"
    assert "quic_teec_commit" not in spec and "minkipc_commit" not in spec, \
        "spec 仍在引用已经退出构建路径的 Qualcomm 依赖"
    assert "BuildRequires:  kernel-headers" in spec, "需要内核的 linux/tee.h"
    assert "BuildRequires:  glibc-static" in spec, "静态客户端需要 glibc-static"
    for out in ("%{_sbindir}/piano-fingerprint-ctl",
                "%{_datadir}/piano-fingerprint/piano-goodix-sources.tar"):
        assert out in spec, f"spec 没有安装 {out}"
    assert "%{_libdir}/piano-goodix" not in spec, "旧静态库不应再安装"
    assert "pkgconfig/piano-goodix.pc" not in spec, "旧 pkg-config 不应再安装"
    # 归档必须带上 libfprint 编译所需的 QCBOR、TEE 头与两份许可。
    assert "backend/uapi/linux/tee.h" in spec, "归档没有 TEE 头文件"
    assert "backend/qcbor" in spec, "归档没有 QCBOR 源码"

    build = BUILD.read_text()
    assert "piano-fingerprint" in package_sets.matrix(), "构建矩阵没有包含 piano-fingerprint"
    assert "Fetching remote dependency sources" in build, \
        "build-rpms 不会为带远程 Source 的 spec 拉取依赖源码"

    if UPSTREAM.is_dir():
        missing = [name for name in SOURCES if not (UPSTREAM / name).is_file()]
        assert not missing, f"pin 里缺 {missing}"
    else:
        print("SKIP: 上游指纹源码需要 PIANO_TEST_SUNUEFI")
    print("OK: 指纹包按 pin 取源，QCBOR、TEE 头、源码归档与构建矩阵齐全")


if __name__ == "__main__":
    main()
