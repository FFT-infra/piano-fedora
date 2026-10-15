"""编译锁定的 VA-API 和 Gunyah 用户态，并保留源码/载荷证据。"""

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def checked_copy(sources, name, output):
    lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"][name]
    source = sources / name
    def git(*args):
        return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()
    if git("rev-parse", "HEAD") != lock["commit"] or git("status", "--porcelain"):
        raise ValueError("extra runtime source is dirty or not pinned: " + name)
    target = output / (name + "-source")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".git", "target", "build"))
    return source, target, lock


def build_iris_vaapi(sources, output, package, put, sha256, run):
    """编译锁定源码里的 Iris VA-API 后端；它是 H.264/HEVC 编解码的唯一载体。"""
    origin, source, pin = checked_copy(sources, "iris-vaapi", output)
    # 交付的驱动必须自带编码入口。2026-10-15 装过一个只声明 VLD 的后端，
    # ffmpeg 的 h264_vaapi/hevc_vaapi 静默失效；这里按源码能力表硬性拒绝。
    encoder = source / "src/encoder.cpp"
    if not encoder.is_file() or "VAEntrypointEncSlice" not in encoder.read_text():
        raise ValueError("iris-vaapi source cannot provide VAEntrypointEncSlice")
    build = output / "iris-vaapi-build"
    run(["meson", "setup", build, source, "--prefix=/usr", "--libdir=lib64",
         "--buildtype=release", "-Dfastcv=disabled"])
    run(["meson", "compile", "-C", build, "-j2"])
    payload = output / "iris-vaapi-payload"
    env = dict(os.environ, DESTDIR=str(payload))
    run(["meson", "install", "-C", build], env=env)
    files = subprocess.check_output(["git", "-C", str(origin), "ls-files"], text=True).splitlines()
    import hashlib
    tracked = hashlib.sha256("".join(sha256(origin / name) + "  " + name + "\n" for name in files).encode()).hexdigest()
    # radxa-pkg/libva-v4l2 already installs the msm alias, LICENSE and README.
    package("libva-v4l2-iris", payload, output, version="1.0.0", requires=("libva", "libdrm"),
            license_expression="MIT")
    return {"commit": pin["commit"],
            "driver_sha256": sha256(payload / "usr/lib64/dri/v4l2_drv_video.so"),
            "tracked_source_sha256": tracked,
            "encoder_entrypoints": ["EncSlice"],
            "encoder_source_sha256": sha256(encoder)}


def assemble_iris_vaapi(sources, output, package, put, sha256, run):
    """只构建 VA-API 驱动就返回，供快速修复与设备端重建使用。"""
    output.mkdir(parents=True, exist_ok=True)
    (output / "rpms").mkdir(exist_ok=True)
    proof = build_iris_vaapi(sources, output, package, put, sha256, run)
    (output / "extras-manifest.json").write_text(
        json.dumps({"iris-vaapi": proof}, indent=2) + "\n")


def build(sources, output, package, put, sha256, run):
    if platform.machine() != "aarch64":
        raise ValueError("runtime extras must be built natively on aarch64 Fedora")
    output.mkdir(parents=True, exist_ok=True)
    (output / "rpms").mkdir(exist_ok=True)
    proofs = {}
    proofs["iris-vaapi"] = build_iris_vaapi(sources, output, package, put, sha256, run)

    _, source, pin = checked_copy(sources, "crosvm", output)
    build = output / "crosvm-target"
    env = dict(os.environ, CARGO_TARGET_DIR=str(build), CARGO_BUILD_JOBS=os.environ.get("PIANO_BUILD_JOBS", "2"))
    run(["cargo", "build", "--locked", "--release", "--no-default-features",
         "--features", "gunyah,net,balloon", "-p", "crosvm"], cwd=source, env=env)
    payload = output / "crosvm-payload"
    put(build / "release/crosvm", payload / "usr/bin/crosvm", True)
    put(source / "LICENSE", payload / "usr/share/licenses/piano-crosvm/LICENSE")
    put(ROOT / "runtime/crosvm/piano-crosvm.conf", payload / "usr/lib/tmpfiles.d/piano-crosvm.conf")
    run([build / "release/crosvm", "run", "--help"], timeout=15)
    package("piano-crosvm", payload, output, license_expression="BSD-3-Clause")
    proofs["crosvm"] = {"commit": pin["commit"], "features": ["gunyah", "net", "balloon"],
                         "binary_sha256": sha256(build / "release/crosvm")}

    _, source, pin = checked_copy(sources, "librevm", output)
    patch = ROOT / "runtime/librevm/fedora.patch"
    run(["git", "apply", "--check", patch], cwd=source)
    run(["git", "apply", patch], cwd=source)
    # Upstream's lock omits the daemon/GUI; the reviewed complete lock is ours.
    shutil.copyfile(ROOT / "runtime/librevm/Cargo.lock", source / "Cargo.lock")
    build = output / "librevm-target"
    env = dict(os.environ, CARGO_TARGET_DIR=str(build), CARGO_BUILD_JOBS=os.environ.get("PIANO_BUILD_JOBS", "2"))
    run(["cargo", "test", "--locked", "-p", "librevm-core", "-p", "librevm-ctl"], cwd=source, env=env)
    run(["cargo", "build", "--locked", "--release", "--workspace"], cwd=source, env=env)
    proofs["librevm"] = package_librevm(source, build, output, pin, patch, package, put, sha256)
    for name, proof in proofs.items():
        proof["device_tested"] = False
    (output / "extras-manifest.json").write_text(json.dumps(proofs, indent=2) + "\n")
    return proofs


def package_librevm(source, build, output, pin, patch, package, put, sha256):
    """打包已构建载荷；单独调用也用于失败修复后的原生复核。"""
    payload = output / "librevm-payload"
    for name in ("librevm", "librevm-daemon", "librevm-gui"):
        put(build / "release" / name, payload / "usr/bin" / name, True)
    put(source / "scripts/librevm-gui-session", payload / "usr/bin/librevm-gui-session", True)
    for name, destination in (
        ("librevm-daemon.service", "usr/lib/systemd/system/librevm-daemon.service"),
        ("org.librevm.Daemon1.conf", "usr/share/dbus-1/system.d/org.librevm.Daemon1.conf"),
        ("60-gunyah.rules", "usr/lib/udev/rules.d/60-gunyah.rules"),
        ("librevm.desktop", "usr/share/applications/librevm.desktop"),
        ("librevm.svg", "usr/share/icons/hicolor/scalable/apps/librevm.svg"),
    ):
        put(source / "packaging" / name, payload / destination)
    put(source / "LICENSE", payload / "usr/share/licenses/librevm/LICENSE")
    sysusers = payload / "usr/lib/sysusers.d/librevm.conf"
    sysusers.parent.mkdir(parents=True, exist_ok=True)
    sysusers.write_text("g librevm -\n")
    package("librevm", payload, output, version="0.2.0", requires=("piano-crosvm", "iproute", "util-linux", "e2fsprogs"),
            license_expression="GPL-3.0-or-later")
    return {"commit": pin["commit"], "adapter_sha256": sha256(patch),
                          "cargo_lock_sha256": sha256(source / "Cargo.lock"),
            "binaries": {name: sha256(build / "release" / name)
                         for name in ("librevm", "librevm-daemon", "librevm-gui")}}


def package_session_fixes(output, package, put, sha256):
    """让旋转、充电和 schedutil 配置具有相同的生产/设备 RPM 归属。"""
    own = ROOT / "runtime"
    payload = output / "session-fixes-payload"
    proof = {}
    for p in (own / "usr").rglob("*"):
        if p.is_file():
            relative = p.relative_to(own)
            put(p, payload / relative, p.stat().st_mode & 0o111)
            proof[relative.as_posix()] = sha256(p)
    package("piano-session-fixes", payload, output, version="0.2.0", requires=("python3-evdev", "util-linux", "kmod", "coreutils"),
            license_expression="GPL-2.0-only")
    return proof
