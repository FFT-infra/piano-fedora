#!/usr/bin/env python3
"""指纹用户态包的离线契约。

原生客户端是 SunUEFI 源码的副本。新的 TEE 传输只用内核 `linux/tee.h` 对象
uAPI 与 QCBOR；这里锁住：源码文件齐全、spec 不再引用 quic-teec/minkipc、
源码归档装进包里供 libfprint 编译、构建矩阵会为远程 QCBOR 跑 spectool。
设备侧的结论（TEE 能初始化、枚举）不在离线测试里。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages" / "piano-fingerprint"
SPEC = PKG / "piano-fingerprint.spec"
BUILD = ROOT / "scripts" / "build-rpms.sh"

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
    for name in SOURCES:
        assert (PKG / "src" / name).is_file(), f"缺少 {name}"
    spec = SPEC.read_text()
    match = re.search(r"%global qcbor_commit\s+([0-9a-f]{40})", spec)
    assert match, "qcbor_commit 不是 40 位提交"
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
    assert '"piano-fingerprint"' in build, "构建矩阵没有包含 piano-fingerprint"
    assert "Fetching remote dependency sources" in build, \
        "build-rpms 不会为带远程 Source 的 spec 拉取依赖源码"
    print("OK: 指纹包源码、QCBOR、TEE 头、源码归档与构建矩阵齐全")


if __name__ == "__main__":
    main()
