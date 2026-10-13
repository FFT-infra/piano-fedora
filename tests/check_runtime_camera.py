#!/usr/bin/env python3
"""Reproduce the pinned camera adapter input and GCC bounds fix on real source."""

import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fail(message):
    raise SystemExit(message)


def compile_command(compiler, uapi, source, output):
    return [
        compiler, "-O2", "-Wall", "-Wextra", "-Werror",
        "-Dmain=PianoOriginalMain", "-isystem", str(uapi / "include"),
        "-c", str(source), "-o", str(output),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sunuefi", type=Path, required=True)
    parser.add_argument("--public-source", type=Path, required=True)
    parser.add_argument("--uapi", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    sun = args.sunuefi.resolve()
    public = args.public_source.resolve()
    uapi = args.uapi.resolve()
    output = args.output.resolve()
    sources = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
    if output.exists():
        fail("choose a fresh runtime camera regression output")
    if not (uapi / "include").is_dir():
        fail("UAPI input must contain include/; pass the build-device-runtime --uapi root")
    output.mkdir(parents=True)

    sys.path.insert(0, str(sun / "tools"))
    import build_piano_runtime_helpers as runtime

    helper = Path(runtime.__file__).resolve()
    if not helper.is_relative_to(sun):
        fail("runtime helper was imported outside the selected SunUEFI source")
    sun_head = subprocess.check_output(
        ["git", "-C", str(sun), "rev-parse", "HEAD"], text=True).strip()
    if sun_head != sources["sunuefi"]["commit"]:
        fail("runtime camera regression expects the selected project SunUEFI pin")
    if runtime.PUBLIC_COMMIT != sources["debian-piano"]["commit"]:
        fail("selected SunUEFI helper pins a different Debian-Piano source")
    runtime.repository(public, runtime.PUBLIC_COMMIT)
    camera, proof = runtime.derive_camerad_source(public, output / "derived")
    if proof["public_source_sha256"] != runtime.PUBLIC_SOURCES["piano-camerad"][1]:
        fail("public camera source pin changed; review its adapter patches")

    builder_path = ROOT / "scripts/build-device-runtime.py"
    spec = importlib.util.spec_from_file_location("build_device_runtime", builder_path)
    if spec is None or spec.loader is None:
        fail("cannot load runtime builder contract")
    builder = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec.loader.exec_module(builder)
    expected = builder.EXPECTED_CAMERA_EFFECTIVE_SOURCE_SHA256
    if proof["effective_source_sha256"] != expected:
        fail("effective camera source hash differs from the builder's reviewed pin")

    original = camera.read_text(encoding="utf-8")
    required_old_sites = (
        'snprintf(dev, sizeof(dev), "/dev/%s",',
        'if (write(ufd, "change", 6) < 0)',
    )
    if not all(site in original for site in required_old_sites):
        fail("GCC patch input changed upstream; review whether the patch still applies")

    compiler = shutil.which("gcc")
    if not compiler:
        fail("GCC is required for the camera source regression check")

    def compile_source(source, object_path):
        return subprocess.run(
            compile_command(compiler, uapi, source, object_path),
            capture_output=True, text=True, check=False,
        )

    baseline_object = output / "unpatched-camera.o"
    baseline = compile_source(camera, baseline_object)
    if baseline.returncode == 0:
        fail("unpatched effective camera unexpectedly passes strict GCC flags")
    if not any(marker in baseline.stderr for marker in ("empty-body", "format-truncation")):
        fail("unpatched compile failed for an unrelated reason:\n" + baseline.stderr)
    (output / "unpatched-gcc.log").write_text(
        baseline.stdout + baseline.stderr, encoding="utf-8")

    patched_root = output / "patched"
    patched_camera = patched_root / "camera/piano-camerad.c"
    patched_camera.parent.mkdir(parents=True)
    shutil.copyfile(camera, patched_camera)
    gcc_patch = ROOT / "patches/runtime/camerad-gcc-bounds.patch"
    subprocess.run(
        ["patch", "--batch", "--forward", "--fuzz=0", "--no-backup-if-mismatch",
         "-p1", "-i", str(gcc_patch)],
        cwd=patched_root, check=True,
    )
    patched = patched_camera.read_text(encoding="utf-8")
    if ("name_bytes >= sizeof(dev) - 5" not in patched or
            "memcpy(dev + 5, line + 8, name_bytes + 1)" not in patched or
            'snprintf(dev, sizeof(dev), "/dev/%s",' in patched):
        fail("GCC patch no longer enforces the reviewed DEVNAME bound")

    patched_object = output / "patched-camera.o"
    fixed = compile_source(patched_camera, patched_object)
    if fixed.returncode:
        fail("GCC bounds patch did not compile with the selected UAPI:\n" + fixed.stderr)
    if not patched_object.is_file() or patched_object.stat().st_size == 0:
        fail("GCC produced no patched camera object")

    print(json.dumps({
        "status": "CAMERA_GCC_BOUNDS_REGRESSION_PASS",
        "sunuefi_head": sun_head,
        "runtime_helper": str(helper),
        "public_head": subprocess.check_output(
            ["git", "-C", str(public), "rev-parse", "HEAD"], text=True).strip(),
        "public_source_sha256": proof["public_source_sha256"],
        "adapter_effective_source_sha256": proof["effective_source_sha256"],
        "gcc": subprocess.check_output([compiler, "--version"], text=True).splitlines()[0],
        "uapi_root": str(uapi),
        "unpatched_failure": next(
            (line.strip() for line in baseline.stderr.splitlines()
             if "empty-body" in line or "format-truncation" in line), ""),
        "bounds_patch_sha256": hashlib.sha256(gcc_patch.read_bytes()).hexdigest(),
        "patched_object_bytes": patched_object.stat().st_size,
    }, indent=2))


if __name__ == "__main__":
    main()
