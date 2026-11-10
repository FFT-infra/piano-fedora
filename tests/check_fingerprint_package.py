#!/usr/bin/env python3
"""指纹用户态包的离线契约。

原生客户端是 SunUEFI 源码的副本，依赖三个固定提交。这里锁住：spec 里三个
提交齐全、libfprint 链接用的 pkgconfig 与静态库都装、构建脚本把包列进矩阵
并且会为带远程 Source 的 spec 跑 spectool。设备侧的结论（TEE 能初始化
Goodix）记录在 docs/upstream-parity.md，不在离线测试里。
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
    "piano-goodix-events.c", "piano-goodix-events.h",
    "piano-goodix-gatekeeper.c", "piano-goodix-gatekeeper.h",
    "piano-goodix-operations.c", "piano-goodix-operations.h",
    "piano-goodix-rpmb.c", "piano-goodix-rpmb.h",
)


def main():
    for name in SOURCES:
        assert (PKG / "src" / name).is_file(), f"缺少 {name}"
    spec = SPEC.read_text()
    for key in ("quic_teec_commit", "minkipc_commit", "qcbor_commit"):
        match = re.search(r"%%global %s\s+([0-9a-f]{40})" % key, spec)
        assert match, f"{key} 不是 40 位提交"
    assert "BuildRequires:  glibc-static" in spec, "静态客户端需要 glibc-static"
    for out in ("%{_sbindir}/piano-fingerprint-ctl",
                "%{_libdir}/piano-goodix/libpiano-goodix.a",
                "%{_libdir}/pkgconfig/piano-goodix.pc"):
        assert out in spec, f"spec 没有安装 {out}"

    pc = (PKG / "piano-goodix.pc").read_text()
    assert "Libs:" in pc and "-lpiano-goodix" in pc, "pkgconfig 没有导出静态库"
    # 静态库装在 %{_libdir}=/usr/lib64，pc 里的 -L 必须跟着走，否则
    # libfprint 链接时拿到的是 /usr/lib/piano-goodix 这个不存在的目录。
    assert "libdir=${exec_prefix}/lib64" in pc, "pkgconfig 的 libdir 不是 lib64"

    build = BUILD.read_text()
    assert '"piano-fingerprint"' in build, "构建矩阵没有包含 piano-fingerprint"
    assert "Fetching remote dependency sources" in build, \
        "build-rpms 不会为带远程 Source 的 spec 拉取依赖源码"
    print("OK: 指纹包源码、三个依赖提交、pkgconfig 与构建矩阵齐全")


if __name__ == "__main__":
    main()
