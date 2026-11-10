#!/usr/bin/env python3
"""GNOME 三档电源策略：档位有序、映射闭合，频率所有权归 uperf-linux。"""

import configparser
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import piano_desktop

PACKAGE = ROOT / "packages" / "piano-fedora-configs"
PROFILES = PACKAGE / "usr" / "lib" / "tuned" / "profiles"
HELPER = PACKAGE / "usr" / "lib" / "piano" / "piano-power-mode"
PPD_CONF = PACKAGE / "etc" / "tuned" / "piano-ppd.conf"
DROPIN = (PACKAGE / "usr" / "lib" / "systemd" / "system" /
          "tuned-ppd.service.d" / "50-piano-ppd.conf")
TIERS = {"power-saver": "powersave", "balanced": "balance", "performance": "performance"}


def read(path):
    parser = configparser.ConfigParser(interpolation=None, delimiters=("=",))
    parser.read_string(path.read_text(encoding="utf-8"))
    return parser


def check_profiles_exist():
    for tier in TIERS:
        path = PROFILES / f"piano-{tier}" / "tuned.conf"
        assert path.is_file(), f"missing profile {path}"
    print(f"OK: {len(TIERS)} 档 tuned profile 齐")


def check_profiles_switch_uperf_modes():
    """profile 只切 uperf 的模式，不自己写频率。

    uperf-linux 在开机后接管 cpufreq 与 devfreq，几秒内就把别的写者写的
    scaling_max_freq 覆盖回去；GPU 上限被写低之后它不会恢复。tuned 自己也写
    这些文件就是第二个 owner，所以这里断言没有任何 sysfs 段。
    """
    for tier, mode in TIERS.items():
        directory = PROFILES / f"piano-{tier}"
        conf = read(directory / "tuned.conf")
        assert "sysfs" not in conf.sections(), (tier, conf.sections())
        # TuneD 只接受 profile 目录里的脚本，目录外的路径会被忽略，
        # 所以每档带一个薄壳，真正的模式切换在 /usr/lib/piano 下。
        assert conf["script"]["script"] == "${i:PROFILE_DIR}/apply.sh", tier
        apply = (directory / "apply.sh").read_text(encoding="utf-8")
        assert os.access(directory / "apply.sh", os.X_OK), tier
        assert f"/usr/lib/piano/piano-power-mode {mode}" in apply, (tier, apply)
        assert 'start)' in apply, tier
    assert len(set(TIERS.values())) == len(TIERS), TIERS
    print("OK: 三档各自映射到 uperf 的 powersave/balance/performance，不写 sysfs")


def check_helper_contract():
    assert os.access(HELPER, os.X_OK), f"{HELPER} is not executable"
    text = HELPER.read_text(encoding="utf-8")
    assert "/usr/bin/uperfctl mode" in text, text
    for mode in TIERS.values():
        assert mode in text, mode
    assert "seq 1" in text, "开机时 daemon 可能还没起来，重试逻辑丢了"
    print("OK: helper 可执行，校验模式名并重试调用 uperfctl")


def check_ppd_mapping_is_closed():
    conf = read(PPD_CONF)
    assert conf["main"]["default"] == "balanced", dict(conf["main"])
    profiles = dict(conf["profiles"])
    assert sorted(profiles) == sorted(TIERS), profiles
    for tier, profile in profiles.items():
        assert (PROFILES / profile / "tuned.conf").is_file(), (tier, profile)
    assert dict(conf["battery"]) == {"balanced": "piano-balanced"}, dict(conf["battery"])
    print("OK: ppd 档位映射指向真实 profile，电池档复用 balanced")


def check_dropin_presents_mapping():
    """tuned 自己拥有 /etc/tuned/ppd.conf，我们只把它在服务里换掉。"""
    text = DROPIN.read_text(encoding="utf-8")
    bind = [line.split("=", 1)[1] for line in text.splitlines()
            if line.startswith("BindReadOnlyPaths=")]
    assert bind == ["/etc/tuned/piano-ppd.conf:/etc/tuned/ppd.conf"], bind
    print("OK: 服务内用 BindReadOnlyPaths 呈现映射，不覆盖 tuned 的 conffile")


def check_desktop_packages():
    gnome = piano_desktop.load("gnome", "45")["profile"]["dnf_packages"]
    assert {"tuned", "tuned-ppd"} <= set(gnome), gnome
    for desktop in ("kde", "niri"):
        packages = piano_desktop.load(desktop, "45")["profile"]["dnf_packages"]
        assert not {"tuned", "tuned-ppd"} & set(packages), (desktop, packages)
    print("OK: 只有 GNOME 档装 tuned 与 tuned-ppd")


def check_spec_owns_files():
    spec = (PACKAGE / "piano-fedora-configs.spec").read_text(encoding="utf-8")
    for relative in ("tuned/piano-ppd.conf", "profiles/piano-balanced/tuned.conf",
                     "profiles/piano-power-saver/tuned.conf",
                     "profiles/piano-performance/tuned.conf",
                     "profiles/piano-balanced/apply.sh",
                     "profiles/piano-power-saver/apply.sh",
                     "profiles/piano-performance/apply.sh",
                     "tuned-ppd.service.d/50-piano-ppd.conf",
                     "lib/piano/piano-power-mode"):
        assert relative in spec, relative
    print("OK: spec 列出全部新文件")


def main():
    check_profiles_exist()
    check_profiles_switch_uperf_modes()
    check_helper_contract()
    check_ppd_mapping_is_closed()
    check_dropin_presents_mapping()
    check_desktop_packages()
    check_spec_owns_files()


if __name__ == "__main__":
    main()
