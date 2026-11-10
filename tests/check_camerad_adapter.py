#!/usr/bin/env python3
"""相机包只有一个所有者，且源码自包含。

相机二进制归 `packages/piano-camera`：运行时装配不再派生第二份 camerad。
包内源码是上游 `linux/userspace/camera` 的副本，Quad worker 依赖的 demosaic
按 crates.io 0.3.0 随包 vendor，构建因此 `--locked --offline` 不联网。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "piano-camera"
SRC = PACKAGE / "src"


def check_runtime_does_not_rebuild_camerad():
    """一个路径只能有一个所有者：运行时不得再派生 camerad。"""
    text = (ROOT / "scripts/build-device-runtime.py").read_text()
    assert 'if name == "piano-camerad":' in text, (
        "build-device-runtime must keep skipping piano-camerad; "
        "packages/piano-camera owns that binary")
    assert "derive_camerad_source" not in text and "adapt_camerad_for_fedora" not in text, (
        "build-device-runtime must not derive a second camerad")


def check_source_copy():
    """源码是上游副本加本包补上的静态拍摄，不再靠 Fedora 侧补丁。"""
    assert (SRC / "piano-camerad.c").is_file(), "缺少守护进程源码"
    assert (SRC / "piano-camera-ctl.c").is_file(), "缺少客户端源码"
    assert not list(PACKAGE.glob("*.patch")), "相机包不再带 Fedora 侧补丁"
    origin = (SRC / "ORIGIN.md").read_text()
    assert "debian-piano" in origin and "sources.lock.json" in origin, "来源记录缺失"
    assert (SRC / "LICENSE-MIT").is_file(), "MIT 许可证没有随源码"


def check_offline_rust():
    """Quad worker 只依赖随包的 demosaic，构建不联网。"""
    cargo = (SRC / "quad" / "Cargo.toml").read_text()
    assert 'demosaic = { path = "vendor/demosaic" }' in cargo, "demosaic 不是随包的路径依赖"
    vendor = SRC / "quad" / "vendor" / "demosaic"
    assert (vendor / "Cargo.toml").is_file(), "vendored demosaic 缺失"
    assert (vendor / "LICENSE-MIT").is_file(), "vendored demosaic 缺少许可证"
    lock = (SRC / "quad" / "Cargo.lock").read_text()
    assert 'name = "demosaic"' in lock and 'version = "0.3.0"' in lock, "锁定的 demosaic 版本变了"
    makefile = (SRC / "Makefile").read_text()
    assert "--locked --offline" in makefile, "Cargo 构建没有锁与离线约束"


def main():
    check_runtime_does_not_rebuild_camerad()
    check_source_copy()
    check_offline_rust()
    print("OK: 相机包独占 camerad，源码与 Rust vendoring 自包含")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
