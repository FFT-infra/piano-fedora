#!/usr/bin/env python3
"""runtime 模块构建只认重放出来的 7.3 源码树，不再有 Gunyah 重放证明。"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    spec = importlib.util.spec_from_file_location("runtime_kernel", ROOT / "scripts/prepare-runtime-kernel.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    # syncconfig 按本机 rustc 重算的探测项，在 Rust 未启用时不影响编译。
    sealed = "CONFIG_MODULES=y\nCONFIG_RUSTC_VERSION=109900\nCONFIG_RUSTC_LLVM_VERSION=230101\n"
    assert runtime.comparable_config(sealed) == runtime.comparable_config(
        sealed.replace("109900", "109901"))
    assert runtime.comparable_config(sealed) != runtime.comparable_config(
        sealed.replace("CONFIG_MODULES=y", "CONFIG_MODULES=n"))
    rust = "CONFIG_RUST=y\nCONFIG_RUSTC_VERSION=109900\n"
    assert runtime.comparable_config(rust) != runtime.comparable_config(
        rust.replace("109900", "109901"))
    # 重放证明这条路径已随 7.3 内建 Gunyah 一起删除；残留会重新引入第二份源码。
    assert not hasattr(runtime, "replay_gunyah_source")
    source = (ROOT / "scripts/prepare-runtime-kernel.py").read_text()
    assert "gunyah-driver" not in source
    assert "kernel-gunyah-source.json" not in source
    # 源码清单目录随 7.3 的 manifest_directory 改名，读旧路径会在 Actions 上
    # 直接 FileNotFoundError。
    assert 'sun / "build/release/source-manifest.json"' in source
    assert "release-7.2.9" not in source
    workflow = (ROOT / ".github/workflows/build-device-runtime.yml").read_text()
    assert "kernel-gunyah-source.json" not in workflow
    print("OK: runtime source replay reads the 7.3 manifest and has no Gunyah proof")


if __name__ == "__main__":
    main()
