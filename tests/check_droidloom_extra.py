#!/usr/bin/env python3
"""droidloom extras：安装器固定版本、载荷来源与 extras 接线。

安装器不从仓库重建 Droidloom，只下载上游 release 的两个 aarch64 包体。
固定值同时写在这里和安装器里，改一版必须两边一起改，防止某一边悄悄漂移。
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packages" / "piano-extras" / "piano-droidloom-extra.spec"
SCRIPT = ROOT / "packages" / "piano-extras" / "piano-droidloom-extra"
BUILD = ROOT / "scripts" / "build-piano-extras.py"
PUBLISH = ROOT / "scripts" / "publish-copr.py"
VERIFY = ROOT / "scripts" / "verify-copr-device.py"
UNINSTALL = ROOT / "scripts" / "piano-extras-uninstall.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "build-piano-extras.yml"

PINS = {
    "droidloom-runtime": (
        "droidloom-runtime-0.1.0-46-aarch64.pkg.tar.zst",
        "6bf308248af311190143783507378c290812b3c73d53fb4ef74ac9dc24c7542d",
        10855659),
    "droidloom-image": (
        "droidloom-image-0.1.0-46-aarch64.pkg.tar.zst",
        "ef6d50223f12eda7ab8ff05e12ae960367a30af2e6d95293eea0a1c384059c26",
        969606760),
}


def pins():
    text = SCRIPT.read_text()
    rows = re.findall(
        r'^    "(droidloom-[a-z]+)\|([^|]+)\|([0-9a-f]{64})\|(\d+)"$',
        text, re.M)
    return {name: (filename, sha, int(size))
            for name, filename, sha, size in rows}


def test_pins():
    assert re.search(r"^DROIDLOOM_RELEASE=0\.1\.0-46$", SCRIPT.read_text(), re.M)
    assert pins() == PINS, pins()
    text = SCRIPT.read_text()
    assert "releases/download/packages" in text, "must fetch the upstream release"
    assert "denialwm/droidloom" in text, text


def test_installer_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_installer_guards():
    text = SCRIPT.read_text()
    # 载荷只能落在 /usr；出现 etc/ 或 var/ 成员时安装器必须拒绝。
    assert "^(etc|var)/" in text, "installer must refuse payloads outside /usr"
    # 卸载必须按记录清单逐条删除，且不删除 Android 用户数据。
    assert "files.list" in text
    assert "/var/lib/droidloom/users" in text
    assert "rm -rf -- /var/lib/droidloom" not in text


def test_spec():
    text = SPEC.read_text()
    for token in ("Source0:        piano-droidloom-extra",
                  "ExclusiveArch:  aarch64",
                  "%{_bindir}/piano-droidloom-extra",
                  '"$1" -eq 0'):
        assert token in text, f"spec lacks {token}"
    assert "piano-droidloom-extra remove" in text


def test_extras_wiring():
    name = "piano-droidloom-extra"
    assert f'"{name}"' in BUILD.read_text(), BUILD
    assert f'"{name}")' in PUBLISH.read_text(), PUBLISH
    assert name in VERIFY.read_text(), VERIFY
    assert name in UNINSTALL.read_text(), UNINSTALL
    assert name in WORKFLOW.read_text(), WORKFLOW


def main():
    test_pins()
    test_installer_syntax()
    test_installer_guards()
    test_spec()
    test_extras_wiring()
    print("OK: droidloom extras pins and wiring")


if __name__ == "__main__":
    main()
