#!/usr/bin/env python3
"""extras 发布资产与工作流接线的离线检查。"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "scripts" / "release-assets.py"
VERIFY = ROOT / "scripts" / "verify-copr-device.py"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
BUILD = ROOT / ".github" / "workflows" / "build-piano-extras.yml"
PUBLISH = ROOT / ".github" / "workflows" / "publish-copr-extras.yml"
INSTALL = ROOT / "scripts" / "piano-extras-install.sh"
UNINSTALL = ROOT / "scripts" / "piano-extras-uninstall.sh"


def load_assets():
    spec = importlib.util.spec_from_file_location("release_assets", ASSETS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_verify():
    spec = importlib.util.spec_from_file_location("verify_copr_device", VERIFY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PRIMARY = """<metadata>
<package type="rpm"><name>kwin</name><arch>aarch64</arch>
<version epoch="0" ver="6.8.2" rel="4.piano1.fc45"/></package>
<package type="rpm"><name>gnome-settings-daemon-piano</name><arch>aarch64</arch>
<version epoch="0" ver="51.1" rel="2.piano1.fc45"/></package>
<package type="rpm"><name>gtk3</name><arch>aarch64</arch>
<version epoch="0" ver="3.24.52" rel="7.piano1.fc45"/></package>
<package type="rpm"><name>piano-gnome-extras</name><arch>noarch</arch>
<version epoch="0" ver="0.1.0" rel="1.fc45"/></package>
<package type="rpm"><name>piano-kde-extras</name><arch>noarch</arch>
<version epoch="0" ver="0.1.0" rel="1.fc45"/></package>
</metadata>"""


def test_verify_extras_parser():
    module = load_verify()
    assert module.EXTRAS_PACKAGES == {
        "gnome-settings-daemon-piano", "kwin", "gtk3",
        "piano-gnome-extras", "piano-kde-extras"}
    records = module.package_releases(PRIMARY, module.EXTRAS_PACKAGES)
    assert "4.piano1.fc45" in [row["release"] for row in records["kwin"]]
    assert records["piano-gnome-extras"][0]["arch"] == "noarch"
    assert "gnome-settings-daemon" not in records


def test_asset_names(module):
    assert module.extras_archive_name("v45.3") == "extras-v45.3.tar.zst"
    assert module.extras_sidecar_name("v45.3") == "extras-v45.3.sha256"
    assert "extras-v45.3.tar.zst" not in module.members()
    try:
        module.extras_archive_name("../bad")
    except ValueError:
        pass
    else:
        raise AssertionError("extras archive name accepted a path escape")


def test_release_workflow():
    text = RELEASE.read_text()
    assert "extras_run:" in text
    assert "build-piano-extras.yml" in text
    assert "extras-archive-name" in text
    assert "extras-sidecar-name" in text
    assert "README-extras.txt" in text
    # extras 的 sidecar 必须在总清单上传与 attestation 之前记账。
    extras = text.index("Add the optional extras archive")
    checksums = text.index("Upload the release checksums")
    attest = text.index("Attest the published assets")
    assert extras < checksums < attest


def test_extras_workflows_and_scripts():
    assert "piano-extras" in BUILD.read_text()
    publish = PUBLISH.read_text()
    assert "--mode extras" in publish
    assert "xinyang26/piano-fedora-extras" in publish
    for script in (INSTALL, UNINSTALL):
        text = script.read_text()
        assert "copr.fedorainfracloud.org:xinyang26:piano-fedora-extras" in text
        assert script.stat().st_mode & 0o111, f"{script} is not executable"
    # 原版同名包已经满足 meta 的裸 Requires，单装 meta 不会换包；脚本必须显式
    # 升级 KWin/GTK3，让 priority=1 有机会选中 .piano1 构建。
    assert "dnf upgrade -y kwin gtk3" in INSTALL.read_text()


def main():
    module = load_assets()
    test_asset_names(module)
    test_release_workflow()
    test_extras_workflows_and_scripts()
    test_verify_extras_parser()
    print("OK: extras archive is named, uploaded and attested separately")


if __name__ == "__main__":
    sys.exit(main())
