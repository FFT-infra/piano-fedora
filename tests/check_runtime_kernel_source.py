#!/usr/bin/env python3
"""runtime 模块构建只认重放出来的 7.3 源码树，不再有 Gunyah 重放证明。"""
import hashlib
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
    # 相机高像素依赖 loopback 的补丁序列：缺了补丁模块仍能编过，设备上只是
    # 把消费端尺寸钳回预览值，没有任何报错，所以这里锁两条路径。
    assert 'sun / "patches/v4l2loopback/0.15.4/series.json"' in source
    assert "apply_patch_series" in source
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        target = root / "src"
        target.mkdir()
        (target / "a.c").write_text("old\n")
        diff = root / "0001-example.patch"
        diff.write_text("--- a/a.c\n+++ b/a.c\n@@ -1 +1 @@\n-old\n+new\n")
        digest = hashlib.sha256(diff.read_bytes()).hexdigest()
        series = root / "series.json"
        series.write_text(json.dumps(
            {"schema_version": 1, "patches": [{"file": diff.name, "sha256": digest}]}))
        assert runtime.apply_patch_series(series, target) == [{"file": diff.name, "sha256": digest}]
        assert (target / "a.c").read_text() == "new\n"
        # 摘要不符必须停下：否则补丁被换掉也会静默编进模块。
        series.write_text(json.dumps(
            {"schema_version": 1, "patches": [{"file": diff.name, "sha256": "0" * 64}]}))
        try:
            runtime.apply_patch_series(series, target)
        except ValueError as error:
            assert "checksum" in str(error)
        else:
            raise AssertionError("a changed patch checksum was accepted")
    print("OK: runtime source replay reads the 7.3 manifest, applies the loopback series")


if __name__ == "__main__":
    main()
