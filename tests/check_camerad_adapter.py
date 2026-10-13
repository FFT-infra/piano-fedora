#!/usr/bin/env python3
"""实测固定上游相机适配输出、GCC 修复和无设备访问的帮助入口。"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sunuefi", type=Path, default=Path(os.environ.get(
        "PIANO_TEST_SUNUEFI", ROOT / "build/test-sunuefi")))
    ap.add_argument("--public-source", type=Path)
    args = ap.parse_args()
    public = args.public_source or Path(os.environ.get(
        "PIANO_TEST_PUBLIC_SOURCE", args.sunuefi / "upstream/debian-piano-current"))
    if not (args.sunuefi / "tools/build_piano_runtime_helpers.py").is_file() or not (public / "camera/piano-camerad.c").is_file():
        print("SKIP: pinned camera sources unavailable; real adapter/GCC test not run")
        return 0
    expected = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    assert subprocess.check_output(["git", "-C", str(args.sunuefi), "rev-parse", "HEAD"], text=True).strip() == expected
    sys.path.insert(0, str(args.sunuefi / "tools"))
    import build_piano_runtime_helpers as upstream
    upstream.repository(public, upstream.PUBLIC_COMMIT)
    spec = importlib.util.spec_from_file_location("fedora_runtime", ROOT / "scripts/build-device-runtime.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        camera, proof = upstream.derive_camerad_source(public, work)
        before = camera.read_bytes()
        assert any(row["file"].endswith("piano-camerad-local-video-group.patch") for row in proof["patches"])
        # 必须保留不匹配输入的拒绝路径，不能为了让长构建通过而移除摘要检查。
        try:
            runtime.adapt_camerad_for_fedora(camera, {**proof, "effective_source_sha256": "0" * 64})
        except ValueError:
            pass
        else:
            raise AssertionError("foreign camera adapter proof accepted")
        assert camera.read_bytes() == before
        runtime.adapt_camerad_for_fedora(camera, proof)
        assert camera.read_bytes() != before
        assert proof["fedora_effective_source_sha256"] == runtime.sha256(camera)
        entry = work / "entry.c"
        entry.write_text(upstream.entry_source("piano-camerad"))
        obj, binary = work / "camera.o", work / "piano-camerad"
        subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-Dmain=PianoOriginalMain",
                        "-c", str(camera), "-o", str(obj)], check=True, capture_output=True, timeout=30)
        subprocess.run(["gcc", str(entry), str(obj), "-lm", "-o", str(binary)],
                       check=True, capture_output=True, timeout=30)
        result = subprocess.run([binary, "--help"], check=True, capture_output=True, text=True, timeout=5)
        assert "--help performs no device access" in result.stdout
    print("OK: pinned camera adapter, GCC compilation, foreign-input rejection and help entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
