#!/usr/bin/env python3
"""Build the optional extras repository.

The default image and default COPR stay on Fedora's stock desktop packages.
This script builds the opt-in set instead: GSD, KWin and GTK3 from pinned
Fedora SRPMs plus the two noarch meta packages. The result is consumed by
`publish-copr.py --mode extras` and by the release extras archive.

Run as root on the native aarch64 Fedora builder:

    python3 scripts/build-piano-extras.py \
        --sunuefi build/sources/sunuefi --output build/piano-extras
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = "PIANO_EXTRAS_BUILT_NOT_DEVICE_VERIFIED"
DESKTOP_PACKAGES = ("gnome-settings-daemon-piano", "kwin", "gtk3")
META_PACKAGES = ("piano-gnome-extras", "piano-kde-extras")
PACKAGES = ("gtk3", "kwin", "gnome-settings-daemon-piano") + META_PACKAGES


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(*command):
    subprocess.run(command, check=True)


def query(*command):
    return subprocess.check_output(command, text=True).strip()


def rpm_name(path):
    return query("rpm", "-qp", "--qf", "%{NAME}", path)


def rpm_nevra(path):
    return query("rpm", "-qp", "--qf",
                 "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}", path)


def rpm_release(path):
    return query("rpm", "-qp", "--qf", "%{RELEASE}", path)


def build_meta(spec, output, releasever):
    name = spec.stem
    top = output / "meta-build" / name
    for directory in ("SOURCES", "SPECS", "BUILD", "BUILDROOT", "RPMS", "SRPMS"):
        (top / directory).mkdir(parents=True)
    ambient = spec.parent / "etc/dconf/db/local.d/20-piano-ambient"
    if ambient.is_file():
        shutil.copy2(ambient, top / "SOURCES" / "20-piano-ambient")
    common = ("--define", "_topdir " + str(top),
              "--define", "_smp_build_ncpus 4",
              "--define", "_smp_mflags -j4",
              "--define", "debug_package %{nil}")
    rpm_dir = output / "rpms"
    srpm_dir = output / "srpms"
    # rpmbuild 给二进制包加 arch 子目录（noarch/），而 extras 清单、校验、
    # artifact 与 COPR 发布都要求 rpms/ 是平的。二进制先落在私有 topdir，
    # 再按文件名复制一份到 rpms/；SRPM 本身就落在 srpms/ 根，不用动。
    run("rpmbuild", "-ba", *common,
        "--define", "_rpmdir " + str(top / "RPMS"),
        "--define", "_srcrpmdir " + str(srpm_dir),
        str(spec))
    built = [path for path in (top / "RPMS").glob("**/*.rpm")
             if rpm_name(path) == name]
    srpms = [path for path in srpm_dir.glob("*.src.rpm")
             if rpm_name(path) == name]
    if len(built) != 1 or len(srpms) != 1:
        raise ValueError(f"{name}: expected one RPM and one SRPM, "
                         f"got {len(built)} and {len(srpms)}")
    rpm = rpm_dir / built[0].name
    shutil.copy2(built[0], rpm)
    row = {
        "source_package": name,
        "binary_package": name,
        "release": rpm_release(rpm),
        "nevra": rpm_nevra(rpm),
        "desktops": ["gnome"] if name == "piano-gnome-extras" else ["kde"],
        "rpms": {rpm.name: {"bytes": rpm.stat().st_size,
                            "sha256": sha256(rpm)}},
        "srpms": {srpms[0].name: {"bytes": srpms[0].stat().st_size,
                                  "sha256": sha256(srpms[0])}},
    }
    return row, rpm, srpms[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sunuefi", required=True, type=Path,
                        help="checked-out SunUEFI tree holding patches/")
    parser.add_argument("--output", required=True, type=Path,
                        help="fresh output directory under build/")
    parser.add_argument("--releasever", default=None)
    args = parser.parse_args()

    if platform.machine() not in ("aarch64", "arm64") or os.geteuid() != 0:
        raise SystemExit("run as root on the native aarch64 Fedora builder")
    releasever = args.releasever or json.loads(
        (ROOT / "product.json").read_text())["target"]["releasever"]
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(ROOT / "build"):
        raise SystemExit("choose a fresh build-owned output directory")
    sun = args.sunuefi.resolve()
    if not (sun / "patches").is_dir():
        raise SystemExit(f"missing patches directory: {sun / 'patches'}")

    output.mkdir(parents=True)
    rpm_dir = output / "rpms"
    srpm_dir = output / "srpms"
    rpm_dir.mkdir()
    srpm_dir.mkdir()

    desktop_dir = output / "desktop"
    run("python3", str(ROOT / "scripts" / "build-fedora-desktop-patches.py"),
        "--sunuefi", str(sun), "--output", str(desktop_dir),
        "--releasever", str(releasever))
    desktop_manifest = json.loads((desktop_dir / "manifest.json").read_text())
    if desktop_manifest.get("status") != "FEDORA_DESKTOP_PATCHES_BUILT_NOT_DEVICE_VERIFIED":
        raise ValueError("unexpected desktop manifest status")

    packages = {}
    files = {}
    srpms = {}
    for name in DESKTOP_PACKAGES:
        row = desktop_manifest["packages"].get(name)
        if not isinstance(row, dict):
            raise ValueError(f"desktop manifest lacks {name}")
        packages[name] = row
        for filename, record in row["rpms"].items():
            source = desktop_dir / "rpms" / filename
            if not source.is_file() or sha256(source) != record["sha256"]:
                raise ValueError(f"desktop RPM drifted: {filename}")
            shutil.copy2(source, rpm_dir / filename)
            files[filename] = record
        for filename, record in row["srpms"].items():
            source = desktop_dir / "srpms" / filename
            if not source.is_file() or sha256(source) != record["sha256"]:
                raise ValueError(f"desktop SRPM drifted: {filename}")
            shutil.copy2(source, srpm_dir / filename)
            srpms[filename] = record

    for spec in sorted((ROOT / "packages" / "piano-extras").glob("*.spec")):
        row, rpm, srpm = build_meta(spec, output, releasever)
        name = row["binary_package"]
        packages[name] = row
        for filename, record in row["rpms"].items():
            files[filename] = record
        for filename, record in row["srpms"].items():
            srpms[filename] = record

    if set(packages) != set(PACKAGES):
        raise ValueError(f"unexpected extras packages: {sorted(packages)}")
    for name in DESKTOP_PACKAGES:
        if "piano1" not in str(packages[name]["release"]):
            raise ValueError(f"{name} is not the piano build")

    lock = json.loads((ROOT / "sources.lock.json").read_text())
    manifest = {
        "status": STATUS,
        "releasever": str(releasever),
        "arch": "aarch64",
        "device_tested": False,
        "sunuefi_commit": lock["sources"]["sunuefi"]["commit"],
        "packages": packages,
        "files": files,
        "srpms": srpms,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copy2(desktop_dir / "manifest.json", output / "desktop-manifest.json")

    scripts_dir = output / "scripts"
    scripts_dir.mkdir()
    for script in ("piano-extras-install.sh", "piano-extras-uninstall.sh"):
        source = ROOT / "scripts" / script
        shutil.copy2(source, scripts_dir / script)
        (scripts_dir / script).chmod(0o755)

    print(json.dumps({"status": STATUS, "packages": sorted(packages),
                      "rpms": len(files), "srpms": len(srpms)}, indent=2))


if __name__ == "__main__":
    main()
