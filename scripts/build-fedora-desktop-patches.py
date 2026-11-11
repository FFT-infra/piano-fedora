#!/usr/bin/env python3
"""Rebuild the three patched Fedora desktop SRPMs from pinned Fedora sources.

The build is intentionally boring: download one Fedora SRPM, verify its spec
and tarball, apply the SunUEFI patches, and let rpmbuild produce both the
binary and source packages.  The RPMs feed the optional extras repository;
the default image and COPR never depend on them.

Run as root in a native aarch64 Fedora container:

    python3 scripts/build-fedora-desktop-patches.py \
        --sunuefi build/sources/sunuefi --output build/fedora-desktop
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = "FEDORA_DESKTOP_PATCHES_BUILT_NOT_DEVICE_VERIFIED"


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


def verify(path, expected, label):
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"{label} changed: {path} ({actual})")


def replace_once(text, old, new):
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one spec anchor, found {count}: {old!r}")
    return text.replace(old, new, 1)


def add_patches(text, patch_names):
    """Add Patch999, Patch998, ... in list order.

    GSD needs two independent patches; KWin and GTK3 need one each.  Refuse
    to guess if any slot is already occupied, so a Fedora spec change stops
    the build instead of silently dropping a patch.
    """
    if not patch_names:
        raise ValueError("package has no patches")
    lines = []
    for offset, patch_name in enumerate(patch_names):
        slot = 999 - offset
        marker = f"Patch{slot}:"
        if marker in text:
            raise ValueError(f"spec already carries {marker}")
        lines.append(f"{marker}        {patch_name}")
    return replace_once(text, "%prep\n", "\n".join(lines) + "\n\n%prep\n")


def add_changelog(text, version, release, note):
    entry = (f"* Wed Nov 11 2026 FFT-infra <dev@piano.invalid> - {version}-{release}\n"
             f"- {note}\n\n")
    return replace_once(text, "%changelog\n", "%changelog\n" + entry)


def adapt_gsd(text):
    """Rename GSD and make the Fedora original resolve to this package."""
    text = replace_once(text, "Name:           gnome-settings-daemon\n",
                        "Name:           gnome-settings-daemon-piano\n")
    text = replace_once(text, "Release:        %autorelease\n",
                        "Release:        1.piano1%{?dist}\n")
    text = replace_once(
        text,
        "Source0:        https://download.gnome.org/sources/%{name}/"
        "%{gnome_major_version}/%{name}-%{gnome_tarball_version}.tar.xz\n",
        "Provides:       gnome-settings-daemon = %{version}-%{release}\n"
        "Provides:       gnome-settings-daemon%{?_isa} = %{version}-%{release}\n"
        "Obsoletes:      gnome-settings-daemon < %{version}-%{release}\n"
        "Conflicts:      gnome-settings-daemon%{?_isa}\n"
        "Source0:        https://download.gnome.org/sources/gnome-settings-daemon/"
        "%{gnome_major_version}/gnome-settings-daemon-"
        "%{gnome_tarball_version}.tar.xz\n")
    text = replace_once(text, "%autosetup -p1 -n %{name}-%{gnome_tarball_version}\n",
                        "%autosetup -p1 -n gnome-settings-daemon-"
                        "%{gnome_tarball_version}\n")
    text = replace_once(text, "%find_lang %{name} --with-gnome\n",
                        "%find_lang gnome-settings-daemon --with-gnome\n")
    text = replace_once(text, "%files -f %{name}.lang\n",
                        "%files -f gnome-settings-daemon.lang\n")
    devel_requires = "Requires:       %{name}%{?_isa} = %{version}-%{release}\n"
    text = replace_once(text, devel_requires, devel_requires +
                        "Provides:       gnome-settings-daemon-devel = "
                        "%{version}-%{release}\n"
                        "Provides:       gnome-settings-daemon-devel%{?_isa} = "
                        "%{version}-%{release}\n"
                        "Obsoletes:      gnome-settings-daemon-devel < "
                        "%{version}-%{release}\n"
                        "Conflicts:      gnome-settings-daemon-devel%{?_isa}\n")
    return add_patches(text, ["piano-ambient-profile.patch",
                              "piano-keyboard-backlight.patch"])


def adapt_kwin(text):
    text = replace_once(text, "Release: 3%{?dist}\n", "Release: 3.piano1%{?dist}\n")
    return add_patches(text, ["piano-kwin-hdr.patch"])


def adapt_gtk3(text):
    text = replace_once(text, "Release: %autorelease\n",
                        "Release: 6.piano1%{?dist}\n")
    return add_patches(text, ["piano-gtk3-xi2.patch"])


PACKAGES = (
    {
        "source": "gnome-settings-daemon",
        "binary": "gnome-settings-daemon-piano",
        "version": "51.1",
        "release": "1.piano1",
        "url": "https://kojipkgs.fedoraproject.org/packages/gnome-settings-daemon/"
               "51.1/1.fc45/src/gnome-settings-daemon-51.1-1.fc45.src.rpm",
        "srpm_sha256": "e528cb1b4e1fc4822ca3b11ab7c2cb28616e3fb92ce4f35a96cc6a2219363f72",
        "spec_sha256": "d3385cda134cc36b618a6efd4ecdc87c7fe3d9d7a2158e9532f4fb788cbb92c9",
        "tarball_sha256": "e7de8d30ae2d63c7a9a21f8f0247219275cbc1d7f5ed09205e4a47642666f6ae",
        "patches": [
            ("gnome-settings-daemon/0001-power-optional-device-ambient-profile-51.patch",
             "b32b0ef0451bc5b86884a84d84b769ce7bfb73cb8e5a2b4006c3b8bb017306ff",
             "piano-ambient-profile.patch"),
            ("gnome-settings-daemon/50.1/"
             "0001-power-refresh-keyboard-backlight-after-hotplug.patch",
             "15eb6f30e09158c7dec34faf19ad6f22dbebf1c29f248862e5d77ef6014e1e8e",
             "piano-keyboard-backlight.patch"),
        ],
        "runtime": ("gnome-settings-daemon-piano",),
        "required": ("gnome-settings-daemon-piano",),
        "desktops": ("gnome",),
        "adapt": adapt_gsd,
        "changelog": "Add the piano ambient-light profile and keyboard backlight hotplug",
    },
    {
        "source": "kwin",
        "binary": "kwin",
        "version": "6.8.2",
        "release": "3.piano1",
        "url": "https://kojipkgs.fedoraproject.org/packages/kwin/6.8.2/3.fc45/src/"
               "kwin-6.8.2-3.fc45.src.rpm",
        "srpm_sha256": "45072c4f6c94ec9ff15c18945cdeef23661d3e8a71e7605cf40f307585b3ffbd",
        "spec_sha256": "59f2fa6b913bdc2ff69fa521d281a431fa65b51913986a2d453949a5d6bd561a",
        "tarball_sha256": "ea5e68d957c7d70abed97222a592d4445fda29129e20942658badd224ee138b4",
        "patches": [
            ("kwin/6.6.5/0001-drm-restore-brightness-after-hdr.patch",
             "d0f2300f63f1d2e26c430fda00433019ba4cda5be5acd04da236ce19e16513a7",
             "piano-kwin-hdr.patch"),
        ],
        "runtime": ("kwin", "kwin-libs", "kwin-common"),
        "required": ("kwin", "kwin-libs", "kwin-common"),
        "desktops": ("kde",),
        "adapt": adapt_kwin,
        "changelog": "Restore the pre-HDR backlight when HDR is turned off",
    },
    {
        "source": "gtk3",
        "binary": "gtk3",
        "version": "3.24.52",
        "release": "6.piano1",
        "url": "https://kojipkgs.fedoraproject.org/packages/gtk3/3.24.52/6.fc45/src/"
               "gtk3-3.24.52-6.fc45.src.rpm",
        "srpm_sha256": "0d8bb2424eefb86bf0735dd41f301b3434eab939cfb02f5d0e2f5f17841cc463",
        "spec_sha256": "e52084a7332956a60d3981315886221fb37286ae6bd9e040826b860a13906ef1",
        "tarball_sha256": "80931fa472a77b9a164f6740e3c0b444fac6770054632d35a7ff9d679e5e7b9f",
        "patches": [
            ("gtk/3.24.52/0001-x11-use-device-display-for-tool-atoms.patch",
             "27a599dc9e565dafb6fc8df9e4ac8b9f26467f2041bb3d8494fae3b59c308b20",
             "piano-gtk3-xi2.patch"),
        ],
        "runtime": ("gtk3", "gtk3-immodules", "gtk3-immodule-xim"),
        "required": ("gtk3", "gtk3-immodules", "gtk3-immodule-xim"),
        "desktops": ("gnome", "kde", "niri", "hyprland"),
        "adapt": adapt_gtk3,
        "changelog": "Use the device display for XI2 tool atoms on X11",
    },
)


def install_build_dependencies(spec, srpm, top):
    # 复用 Fedora 的完整 BuildRequires，不手列 KWin/GTK3 的几十个开发包。
    run("dnf", "-y", "builddep", "--define", "_topdir " + str(top),
        "--spec", str(spec), "--srpm", str(srpm))


def build_one(entry, sun, output):
    source = entry["source"]
    work = output / source
    work.mkdir()
    srpm = work / "source.src.rpm"
    with urllib.request.urlopen(entry["url"], timeout=180) as incoming, srpm.open("xb") as stream:
        shutil.copyfileobj(incoming, stream)
    verify(srpm, entry["srpm_sha256"], "source RPM")

    top = work / "rpmbuild"
    for directory in ("SOURCES", "SPECS", "BUILD", "BUILDROOT", "RPMS", "SRPMS"):
        (top / directory).mkdir(parents=True)
    run("rpm", "-i", "--nodeps", "--define", "_topdir " + str(top), srpm)

    specs = sorted((top / "SPECS").glob("*.spec"))
    if len(specs) != 1:
        raise ValueError(f"{source}: expected one spec, found {[p.name for p in specs]}")
    spec = specs[0]
    verify(spec, entry["spec_sha256"], "spec")

    tarballs = sorted((top / "SOURCES").glob("*.tar.*"))
    if len(tarballs) != 1:
        raise ValueError(f"{source}: expected one source tarball")
    verify(tarballs[0], entry["tarball_sha256"], "source tarball")

    effective = entry["adapt"](spec.read_text())
    effective = add_changelog(effective, entry["version"],
                              entry["release"], entry["changelog"])
    spec.write_text(effective)

    patch_proofs = []
    for patch_rel, patch_sha, patch_name in entry["patches"]:
        patch = sun / "patches" / patch_rel
        verify(patch, patch_sha, "patch")
        shutil.copyfile(patch, top / "SOURCES" / patch_name)
        patch_proofs.append({"file": patch_name, "upstream": patch_rel,
                             "sha256": patch_sha})

    install_build_dependencies(spec, srpm, top)
    run("rpmbuild", "-ba", "--define", "_topdir " + str(top),
        "--define", "_smp_build_ncpus 4", "--define", "_smp_mflags -j4",
        "--define", "debug_package %{nil}", spec)

    built = {}
    for rpm in sorted((top / "RPMS").glob("**/*.rpm")):
        built[query("rpm", "-qp", "--qf", "%{NAME}", rpm)] = rpm
    missing = [name for name in entry["required"] if name not in built]
    if missing:
        raise ValueError(f"{source}: missing runtime packages: {missing}")
    collected = {name: built[name] for name in entry["runtime"] if name in built}
    for name, path in collected.items():
        nevra = query("rpm", "-qp", "--qf",
                      "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}", path)
        if "piano1" not in nevra:
            raise ValueError(f"{source}: {name} is not the piano build: {nevra}")

    main = collected[entry["binary"]]
    main_nevra = query("rpm", "-qp", "--qf",
                       "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}", main)
    release = query("rpm", "-qp", "--qf", "%{RELEASE}", main)
    if entry["source"] == "gnome-settings-daemon":
        if query("rpm", "-qp", "--qf", "%{NAME}", main) != "gnome-settings-daemon-piano":
            raise ValueError("GSD payload is not the renamed package")
        provides = query("rpm", "-qp", "--provides", main)
        if "gnome-settings-daemon = " not in provides:
            raise ValueError("GSD package does not provide the Fedora name")
        if "gnome-settings-daemon" not in query("rpm", "-qp", "--obsoletes", main):
            raise ValueError("GSD package does not obsolete the Fedora name")
        payload = work / "payload"
        run("rpm", "--root", str(payload), "--initdb")
        run("rpm", "--root", str(payload), "--define", "_pkgverify_level none",
            "--nodeps", "--noscripts", "-i", str(main))
        profile = payload / "usr/share/gnome-settings-daemon/ambient-profiles/piano.ini"
        if not profile.is_file():
            raise ValueError("GSD payload lacks the piano ambient profile")

    srpms = sorted((top / "SRPMS").glob("*.src.rpm"))
    if len(srpms) != 1:
        raise ValueError(f"{source}: expected one SRPM, found {len(srpms)}")
    row = {
        "source_package": source,
        "binary_package": entry["binary"],
        "desktops": list(entry["desktops"]),
        "release": release,
        "nevra": main_nevra,
        "source_url": entry["url"],
        "source_rpm_sha256": entry["srpm_sha256"],
        "original_spec_sha256": entry["spec_sha256"],
        "effective_spec_sha256": sha256(spec),
        "source_tarball_sha256": entry["tarball_sha256"],
        "patches": patch_proofs,
        "packages": {name: query("rpm", "-qp", "--qf",
                                 "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}", path)
                     for name, path in collected.items()},
        "rpms": {},
        "srpms": {},
    }
    if entry["source"] == "gnome-settings-daemon":
        row["payload_proofs"] = {
            "usr/share/gnome-settings-daemon/ambient-profiles/piano.ini":
                sha256(profile)}
    for name, path in collected.items():
        row["rpms"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    for path in srpms:
        row["srpms"][path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    return row, collected, srpms


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

    packages = {}
    files = {}
    srpms = {}
    for entry in PACKAGES:
        row, collected, built_srpms = build_one(entry, sun, output)
        packages[entry["binary"]] = row
        for name, path in collected.items():
            shutil.copy2(path, rpm_dir / name)
            files[name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in built_srpms:
            shutil.copy2(path, srpm_dir / path.name)
            srpms[path.name] = {"bytes": path.stat().st_size, "sha256": sha256(path)}

    manifest = {
        "status": STATUS,
        "releasever": str(releasever),
        "arch": "aarch64",
        "device_tested": False,
        "packages": packages,
        "files": files,
        "srpms": srpms,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"status": STATUS, "packages": sorted(packages),
                      "rpms": len(files), "srpms": len(srpms)}, indent=2))


if __name__ == "__main__":
    main()
