#!/usr/bin/env python3
"""相机包只有一个所有者，且源码不随包维护。

相机二进制归 `packages/piano-camera`：运行时装配不再派生第二份 camerad。
包内不再有上游源码副本，构建期按 pin 取；唯一随包的第三方源码是 Quad
worker 依赖的 demosaic（crates.io 0.3.0 加 QPPG 修正），构建因此
`--locked --offline` 不联网。
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "piano-camera"
SRC = PACKAGE / "src"
UPSTREAM = Path(os.environ.get("PIANO_TEST_SUNUEFI",
                               ROOT / "build/test-sources/sunuefi")) / \
    "linux/userspace/camera"


def check_runtime_does_not_rebuild_camerad():
    """一个路径只能有一个所有者：运行时不得再派生 camerad。"""
    text = (ROOT / "scripts/build-device-runtime.py").read_text()
    assert 'if name == "piano-camerad":' in text, (
        "build-device-runtime must keep skipping piano-camerad; "
        "packages/piano-camera owns that binary")
    assert "derive_camerad_source" not in text and "adapt_camerad_for_fedora" not in text, (
        "build-device-runtime must not derive a second camerad")


def check_source_is_not_vendored():
    """上游源码不随包；只剩补丁与 vendored demosaic。"""
    assert not (SRC / "piano-camerad.c").exists(), "上游源码副本不该入库"
    assert not (SRC / "piano-camera-ctl.c").exists(), "上游源码副本不该入库"
    assert sorted(path.name for path in SRC.iterdir()) == ["quad"], \
        "src/ 只该留 quad/"
    assert sorted(path.name for path in (SRC / "quad").iterdir()) == ["vendor"], \
        "src/quad 只该留 vendor/"
    patches = sorted(path.name for path in (PACKAGE / "patches").glob("*.patch"))
    assert patches == ["0001-still-capture-and-ancillary-lens.patch"], patches


def check_offline_rust():
    """Quad worker 只依赖随包的 demosaic，构建不联网。"""
    vendor = SRC / "quad" / "vendor" / "demosaic"
    assert (vendor / "Cargo.toml").is_file(), "vendored demosaic 缺失"
    assert (vendor / "LICENSE-MIT").is_file(), "vendored demosaic 缺少许可证"
    if not UPSTREAM.is_dir():
        print("SKIP: 上游 quad 清单需要 PIANO_TEST_SUNUEFI")
        return
    cargo = (UPSTREAM / "quad" / "Cargo.toml").read_text()
    assert 'demosaic = { path = "vendor/demosaic" }' in cargo, "demosaic 不是随包的路径依赖"
    lock = (UPSTREAM / "quad" / "Cargo.lock").read_text()
    assert 'name = "demosaic"' in lock and 'version = "0.3.0"' in lock, "锁定的 demosaic 版本变了"
    assert "--locked --offline" in (UPSTREAM / "Makefile").read_text(), \
        "上游 Makefile 的 Cargo 构建没有锁与离线约束"


def main():
    check_runtime_does_not_rebuild_camerad()
    check_source_is_not_vendored()
    check_offline_rust()
    print("OK: 相机包独占 camerad，上游源码按 pin 取，Rust vendoring 自包含")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
