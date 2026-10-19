"""编译锁定的 Gunyah 用户态（crosvm、librevm），并保留源码/载荷证据。

Iris VA-API 后端由 packages/sm8750-video-accel 构建（同一 radxa-pkg/libva-v4l2
锁定 commit）。这里再编一份会产生同名不同字节的驱动，clean rootfs 的 DNF
事务会因此整单回滚。
"""

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


def build(sources, output, package, put, sha256, run):
    if platform.machine() != "aarch64":
        raise ValueError("runtime extras must be built natively on aarch64 Fedora")
    output.mkdir(parents=True, exist_ok=True)
    (output / "rpms").mkdir(exist_ok=True)
    proofs = {}

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
