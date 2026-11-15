#!/usr/bin/env python3
"""Fedora 临时重编包台账的一致性检查。

scripts/fedora-overlays.json 登记「什么时候可以删」和基准 Fedora 版本，不复制
SRPM / spec / 补丁的 sha256：那些事实仍由各构建脚本与 spec 单独拥有。本测试
把台账里的 base_version 和真相源的实际常量逐一比对，并保证没有漏登记的
重编包。改了脚本里的版本却忘了更新台账，或新增重编包没登记，这里会红。

联网巡检 Fedora 稳定库版本由 scripts/check_fedora_overlay_releases.py 负责，
make test 不联网。
"""

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
LEDGER = ROOT / "scripts" / "fedora-overlays.json"


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def mesa_versions():
    mesa = module("mesa_overlay", ROOT / "scripts" / "build-fedora-mesa.py")
    # 26.2.6 同时出现在 SOURCE_URL、GALLIUM 与 tarball 名里；取 tarball 名里
    # 的版本，避免把 release 号或补丁名里的数字误当基准版本。
    import re
    match = re.search(r"/mesa/([0-9.]+)/", mesa.SOURCE_URL)
    assert match, "SOURCE_URL 里找不到 Mesa 基准版本"
    return {"mesa": match.group(1)}


def desktop_versions():
    script = module("desktop_overlay", ROOT / "scripts" / "build-fedora-desktop-patches.py")
    return {entry["source"]: entry["version"] for entry in script.PACKAGES}


def desktop_binaries():
    script = module("desktop_binaries", ROOT / "scripts" / "build-fedora-desktop-patches.py")
    return {entry["binary"] for entry in script.PACKAGES}


def pipewire_versions():
    import re
    spec = (ROOT / "packages" / "piano-pipewire-gstreamer" /
            "piano-pipewire-gstreamer.spec").read_text()
    match = re.search(r"^Version:\s+(\S+)\s*$", spec, re.M)
    assert match, "spec 里找不到 Version"
    return {"pipewire": match.group(1)}


def main():
    data = json.loads(LEDGER.read_text())
    assert data["schema_version"] == 1, "台账 schema 不支持"
    overlays = {row["key"]: row for row in data["overlays"]}
    assert len(overlays) == len(data["overlays"]), "台账里有重复的 key"

    expected_truth = {
        "mesa-piano": "scripts/build-fedora-mesa.py",
        "piano-pipewire-gstreamer": "packages/piano-pipewire-gstreamer/piano-pipewire-gstreamer.spec",
        "fedora-desktop-patches": "scripts/build-fedora-desktop-patches.py",
    }
    assert set(overlays) == set(expected_truth), \
        f"台账条目与三类重编包不一致：{set(overlays) ^ set(expected_truth)}"
    for key, truth in expected_truth.items():
        assert overlays[key]["truth"] == truth, f"{key} 的真相源路径不对"
        assert (ROOT / truth).is_file(), f"{key} 的真相源不存在：{truth}"
        assert overlays[key]["drop_when"], f"{key} 没写删除条件"
        assert overlays[key]["upstream_fix"], f"{key} 没写对应的上游修复"

    actual = {}
    actual["mesa-piano"] = mesa_versions()
    actual["piano-pipewire-gstreamer"] = pipewire_versions()
    actual["fedora-desktop-patches"] = desktop_versions()

    for key, row in overlays.items():
        declared = {pkg["name"]: pkg["base_version"]
                    for pkg in row["fedora_packages"]}
        assert declared == actual[key], (
            f"{key} 台账基准版本与真相源不一致：\n"
            f"  台账 {declared}\n  实际 {actual[key]}\n"
            f"  改了脚本里的版本要同步 fedora-overlays.json，并重基补丁集")

    # provides 不能与真相源脱节：桌面五个包的产物名、mesa-piano、pipewire 都要在列。
    assert set(overlays["fedora-desktop-patches"]["provides"]) == \
        desktop_binaries(), "桌面 provides 与 PACKAGES 的 binary 不一致"
    assert overlays["mesa-piano"]["provides"] == ["mesa-piano"]
    assert overlays["piano-pipewire-gstreamer"]["provides"] == ["piano-pipewire-gstreamer"]

    print("OK: Fedora 重编包台账的版本、真相源、删除条件与实际脚本一致")


if __name__ == "__main__":
    main()
