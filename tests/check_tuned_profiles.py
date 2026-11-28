#!/usr/bin/env python3
"""三档电源策略：profile 跟上游只差平衡档的 CPU 上限、档位有序、映射闭合，没有降频映射的设备树上不放开上限。"""

import configparser
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "piano-fedora-configs"
PROFILES = "usr/lib/tuned/profiles"
PPD_CONF = "etc/tuned/piano-ppd.conf"
PPD_DROPIN = PACKAGE / "usr/lib/systemd/system/tuned-ppd.service.d/50-piano-ppd.conf"
GUARD = "usr/libexec/piano-thermal-guard"
GUARD_DROPIN = PACKAGE / "usr/lib/systemd/system/tuned.service.d/50-piano-thermal-guard.conf"
UPSTREAM = "linux/bsp/power-profiles"
TIERS = ("power-saver", "balanced", "performance")
CEILINGS = ("/sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq",
            "/sys/devices/system/cpu/cpufreq/policy6/scaling_max_freq",
            "/sys/class/devfreq/3d00000.gpu/max_freq")
POWER_SERVICES = {"tuned", "tuned-ppd", "power-profiles-daemon"}


def read(path):
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    parser.optionxform = str
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


def profile(tier):
    return PACKAGE / PROFILES / f"piano-{tier}" / "tuned.conf"


def check_profiles_follow_upstream():
    """省电档、性能档与映射文件是上游的原文。平衡档只有 CPU 两簇的上限比上游低。"""
    sunuefi = os.environ.get("PIANO_TEST_SUNUEFI")
    if not sunuefi:
        print("SKIP: 没有 PIANO_TEST_SUNUEFI，不比对上游的 profile")
        return
    upstream = Path(sunuefi) / UPSTREAM
    names = [f"{PROFILES}/piano-{tier}/tuned.conf" for tier in TIERS if tier != "balanced"] + [PPD_CONF]
    for name in names:
        assert (PACKAGE / name).read_bytes() == (upstream / name).read_bytes(), \
            f"{name} differs from {upstream / name}"
    ours = dict(read(profile("balanced"))["sysfs"])
    theirs = dict(read(upstream / PROFILES / "piano-balanced" / "tuned.conf")["sysfs"])
    lowered = sorted(path for path in theirs if ours.get(path) != theirs[path])
    assert sorted(ours) == sorted(theirs) and lowered == sorted(CEILINGS[:2]), lowered
    assert all(int(ours[path]) < int(theirs[path]) for path in lowered), (ours, theirs)
    print(f"OK: {len(names)} 份文件与锁定的上游逐字节一致，平衡档只压低 CPU 两簇的上限")


def check_ceilings_are_ordered():
    """档位只定上限，而且一档不低于前一档。"""
    values = {}
    for tier in TIERS:
        conf = read(profile(tier))
        assert conf.sections() == ["main", "sysfs"], (tier, conf.sections())
        values[tier] = [int(conf["sysfs"][path]) for path in CEILINGS]
    for low, high in zip(TIERS, TIERS[1:]):
        assert all(a <= b for a, b in zip(values[low], values[high])), (low, high, values)
    assert values[TIERS[0]] != values[TIERS[1]], values
    print("OK: 三档都写 CPU 两簇与 GPU 的上限，省电 ≤ 平衡 ≤ 性能")


def check_ppd_mapping_is_closed():
    conf = read(PACKAGE / PPD_CONF)
    assert conf["main"]["default"] == "balanced", dict(conf["main"])
    profiles = dict(conf["profiles"])
    assert sorted(profiles) == sorted(TIERS), profiles
    for tier, name in profiles.items():
        assert name == f"piano-{tier}" and profile(tier).is_file(), (tier, name)
    assert dict(conf["battery"]) == {"balanced": "piano-balanced"}, dict(conf["battery"])
    print("OK: ppd 档位映射指向真实 profile，电池档复用 balanced")


def check_dropin_presents_mapping():
    """tuned 自己拥有 /etc/tuned/ppd.conf，我们只把它在服务里换掉。"""
    bind = [line.split("=", 1)[1] for line in PPD_DROPIN.read_text(encoding="utf-8").splitlines()
            if line.startswith("BindReadOnlyPaths=")]
    assert bind == ["/etc/tuned/piano-ppd.conf:/etc/tuned/ppd.conf"], bind
    print("OK: 服务内用 BindReadOnlyPaths 呈现映射，不覆盖 tuned 的 conffile")


def fake_device(root, compatible, zones):
    """一棵只带守卫要读写的路径的根。zones 是 {温区名: 有没有降频映射}。"""
    tree = root / "sys/firmware/devicetree/base"
    if compatible is not None:
        tree.mkdir(parents=True)
        (tree / "compatible").write_bytes(b"".join(name.encode() + b"\0" for name in compatible))
    for zone, bound in zones.items():
        (tree / "thermal-zones" / zone / ("cooling-maps" if bound else "trips")).mkdir(parents=True)
    target = root / PROFILES / "piano-power-saver"
    target.mkdir(parents=True)
    shutil.copyfile(profile("power-saver"), target / "tuned.conf")
    knobs = dict(read(profile("power-saver"))["sysfs"])
    # GPU 驱动没起来的启动上没有 devfreq 节点，守卫不该凭空建出它。
    present = [path for path in knobs if "devfreq" not in path]
    for path in present:
        node = root / path.lstrip("/")
        node.parent.mkdir(parents=True, exist_ok=True)
        node.write_text("untouched\n")
    return knobs, present


def run_guard(root):
    return subprocess.run(["sh", str(PACKAGE / GUARD)], capture_output=True, text=True,
                          env={"PATH": os.environ["PATH"], "PIANO_GUARD_ROOT": str(root)})


def check_guard_holds_ceilings_without_cooling_maps():
    cases = (
        ("别的机器", ["qcom,sm8750-mtp", "qcom,sm8750"], {"cpu-0-0-0-thermal": False}, 0),
        ("没有设备树的构建机", None, {}, 0),
        ("带降频映射的 piano", ["xiaomi,piano", "qcom,sm8750"],
         {"piano-skin": False, "cpu-0-0-0-thermal": True}, 0),
        ("没有降频映射的 piano", ["xiaomi,piano", "qcom,sm8750"], {"piano-skin": False}, 1),
    )
    for label, compatible, zones, expected in cases:
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            knobs, present = fake_device(root, compatible, zones)
            result = run_guard(root)
            assert result.returncode == expected, (label, result.returncode, result.stderr)
            for path in present:
                value = (root / path.lstrip("/")).read_text().strip()
                assert value == (knobs[path] if expected else "untouched"), (label, path, value)
            assert not (root / "sys/class/devfreq").exists(), label
            assert bool(result.stderr) == bool(expected), (label, result.stderr)
    unit = read(GUARD_DROPIN)
    assert unit["Service"]["ExecStartPre"] == "/" + GUARD, dict(unit["Service"])
    assert os.access(PACKAGE / GUARD, os.X_OK), GUARD
    print(f"OK: 守卫只在没有降频映射的 piano 上按省电档封顶并拦下 TuneD（{len(cases)} 种设备树）")


def check_every_image_gets_the_service():
    """tuned-ppd 由 piano-fedora-configs 带进每个档，镜像装配明确启用它。"""
    spec = (PACKAGE / "piano-fedora-configs.spec").read_text(encoding="utf-8")
    assert "\nRequires:       tuned-ppd\n" in spec
    for fragment in [f"lib/tuned/profiles/piano-{tier}/tuned.conf" for tier in TIERS] + [
            "sysconfdir}/tuned/piano-ppd.conf", "tuned-ppd.service.d/50-piano-ppd.conf",
            "tuned.service.d/50-piano-thermal-guard.conf", "libexecdir}/piano-thermal-guard"]:
        assert fragment in spec, fragment
    posttrans = spec.split("%posttrans")[1].split("%changelog")[0]
    assert "piano-thermal-guard" in posttrans, posttrans
    desktops = json.loads((ROOT / "scripts/profiles/fedora-desktops.json").read_text())
    for desktop, row in desktops.items():
        listed = POWER_SERVICES & set(row.get("dnf_packages", []))
        assert not listed, f"{desktop} lists {sorted(listed)}; the package dependency is the one source"
    for script in ("build-clean-rootfs.py", "finalize-fedora-rootfs.py"):
        assert '"tuned-ppd.service"' in (ROOT / "scripts" / script).read_text(), script
    print(f"OK: spec 依赖 tuned-ppd 并列全文件，{len(desktops)} 个档都不自己列电源服务")


def main():
    check_profiles_follow_upstream()
    check_ceilings_are_ordered()
    check_ppd_mapping_is_closed()
    check_dropin_presents_mapping()
    check_guard_holds_ceilings_without_cooling_maps()
    check_every_image_gets_the_service()


if __name__ == "__main__":
    main()
