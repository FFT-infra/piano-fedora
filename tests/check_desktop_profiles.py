#!/usr/bin/env python3
"""桌面配方是唯一真源：形状、派生名与"脚本里不许再写桌面字面量"。"""

import importlib.util
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import piano_desktop

DESKTOPS = ROOT / "scripts" / "profiles" / "fedora-desktops.json"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_gnome_names_unchanged():
    """GNOME 的派生名必须与改动前的字面量逐字节相同。

    发布链、`verify-new-image.sh`、`package-release-bundle.sh` 与文档都按这些
    名字找文件。参数化之后名字由 profile 生成，所以要在源头锁住它们。
    """
    names = piano_desktop.artifact_names(piano_desktop.load("gnome", "44"))
    assert names["rootfs_dir"] == "fedora-44-gnome", names
    assert names["rootfs_tar"] == "rootfs-44-gnome.tar.zst", names
    assert names["rootfs_artifact"] == "rootfs-44-gnome", names
    assert names["image"] == "pianoroot-fedora44-gnome-f2fs.img", names
    assert names["image_zst"] == "pianoroot-fedora44-gnome-f2fs.img.zst", names
    assert names["root_size_mib"] == 8192, names


def check_derived_names_follow_the_desktop():
    """换一档，名字跟着换；不能仍带 gnome 字样。"""
    for desktop in piano_desktop.matrix():
        names = piano_desktop.artifact_names(piano_desktop.load(desktop, "44"))
        assert names["rootfs_dir"] == f"fedora-44-{desktop}", names
        assert names["image_artifact"] == f"root-image-44-{desktop}", names
        assert f"-{desktop}-" in names["image"], names


def check_schema_is_enforced():
    """缺字段、未知 autologin / power kind、null 与 buildable 的矛盾都要拒绝。"""
    original = DESKTOPS.read_text(encoding="utf-8")
    cases = [
        (lambda row: row.pop("session"), "lacks fields"),
        (lambda row: row["display_manager"]["autologin"].update(kind="xdm"),
         "unknown autologin"),
        (lambda row: row.update(power_policy={"kind": "gnome-shell"}), "unknown power_policy"),
        (lambda row: row.update(dnf_packages=None), "buildable but has dnf_packages=null"),
        (lambda row: row.update(power_policy={"kind": "kconfig"}), "needs a source file"),
    ]
    try:
        for mutate, message in cases:
            data = json.loads(original)
            mutate(data["kde"])
            DESKTOPS.write_text(json.dumps(data), encoding="utf-8")
            try:
                piano_desktop.load("kde", "44")
            except piano_desktop.DesktopError as error:
                assert message in str(error), f"{message!r} not in {error}"
            else:
                raise AssertionError(f"profile defect accepted: {message}")
    finally:
        DESKTOPS.write_text(original, encoding="utf-8")

    # 不可构建的档位（denial 坑位）显式写 null，plan 必须拒绝。
    data = json.loads(original)
    data["denial"] = {
        "label": "Denial", "buildable": False, "dnf_packages": None, "repos": [],
        "exclude_packages": [], "required_packages": [], "root_size_mib": 8192,
        "display_manager": None, "power_policy": {"kind": "logind-only"},
        "session": {"desktop_names": [], "process_names": [],
                    "session_files": [], "bins": []},
    }
    try:
        DESKTOPS.write_text(json.dumps(data), encoding="utf-8")
        context = piano_desktop.load("denial", "44")
        assert not context["profile"]["buildable"]
        assert "denial" not in piano_desktop.matrix(), "unbuildable rows must not enter the matrix"
        with tempfile.TemporaryDirectory() as temporary:
            tool = load_module("clean_rootfs", ROOT / "scripts/build-clean-rootfs.py")
            try:
                tool.plan("44", "denial", temporary)
            except tool.RootfsError as error:
                assert "placeholder" in str(error), error
            else:
                raise AssertionError("an unbuildable desktop produced a plan")
    finally:
        DESKTOPS.write_text(original, encoding="utf-8")


def check_repo_args():
    """第三方仓库展开成 --repofrompath + priority，变量按 arch / releasever 替换。"""
    original = DESKTOPS.read_text(encoding="utf-8")
    data = json.loads(original)
    data["kde"]["repos"] = [{
        "id": "copr-hyprland",
        "baseurl": "https://download.copr.fedorainfracloud.org/results/lionheartp/"
                   "Hyprland/fedora-$releasever-$basearch/",
        "gpgkey": "https://download.copr.fedorainfracloud.org/results/lionheartp/"
                  "Hyprland/pubkey.gpg",
        "priority": 5,
    }]
    try:
        DESKTOPS.write_text(json.dumps(data), encoding="utf-8")
        args = piano_desktop.repo_args(piano_desktop.load("kde", "44"))
    finally:
        DESKTOPS.write_text(original, encoding="utf-8")
    assert any("copr-hyprland,https://download.copr.fedorainfracloud.org/results/"
               "lionheartp/Hyprland/fedora-44-aarch64/" in arg for arg in args), args
    assert "--setopt=copr-hyprland.priority=5" in args, args
    assert any(arg.endswith("/pubkey.gpg") for arg in args), args


def check_excluded_packages_leave_the_install_set():
    """被自有包替换的原装名从安装列表去掉，而不是 --exclude。"""
    context = piano_desktop.load("gnome", "44")
    assert "iio-sensor-proxy" in context["profile"]["dnf_packages"]
    assert "iio-sensor-proxy" in piano_desktop.excluded_packages(context)
    assert "iio-sensor-proxy" not in piano_desktop.desktop_packages(context)
    for arg in piano_desktop.dnf_install_args(context):
        assert "--exclude" not in arg, (
            "excluding by name also blocks the package that Provides it")


def check_no_desktop_literals_outside_the_profile():
    """桌面差异只能出现在 profile、piano_desktop.py 与运行时探测里。

    两个后端各写一份 GDM 配置正是这次重构要消除的东西；再写回去
    （`gdm.service`、`/etc/gdm/custom.conf`、`sddm.service`）就该失败。
    """
    forbidden = re.compile(
        r'gdm\.service|/etc/gdm/|sddm\.service|plasma-workspace|gnome-shell"|'
        r'org\.gnome\.settings-daemon')
    offenders = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        if path.name in {"piano_desktop.py"}:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if forbidden.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not offenders, "desktop-specific literals leaked into scripts/:\n" + "\n".join(offenders)


def check_power_files_match_the_declared_kind():
    """每种电源策略只产出自己那种文件，且内容真的存在。

    GNOME 的 00-power 由 piano-fedora-configs 这个 RPM 安装，构建器不写
    第二份；`power_required()` 覆盖"谁写都算数"的最终清单。
    """
    kinds = {}
    for desktop in piano_desktop.matrix():
        context = piano_desktop.load(desktop, "44")
        kind = context["profile"]["power_policy"]["kind"]
        kinds[desktop] = kind
        files = piano_desktop.power_files(context)
        required = piano_desktop.power_required(context)
        if kind == "dconf":
            assert files == {}, (desktop, files)
            assert required == ["etc/dconf/db/local.d/00-power"], (desktop, required)
        elif kind == "kconfig":
            assert list(files) == ["etc/xdg/powerdevilrc"], (desktop, files)
            assert required == ["etc/xdg/powerdevilrc"], (desktop, required)
        else:
            assert files == {} and required == [], (desktop, files, required)
        assert piano_desktop.dconf_profile_required(context) == (kind == "dconf"), desktop
    assert kinds == {"gnome": "dconf", "kde": "kconfig", "server": "logind-only"}, kinds


def check_kde_power_policy_is_safe():
    """KDE 的 powerdevilrc 三个 profile 段都不得自动挂起或接管电源键。

    真机上睡眠/唤醒仍不可靠，GNOME 侧早已把两种自动睡眠改成 nothing；KDE
    侧必须是同一份契约，否则换桌面就换掉了电源行为。
    """
    import configparser
    context = piano_desktop.load("kde", "44")
    text = piano_desktop.power_files(context)["etc/xdg/powerdevilrc"]
    policy = configparser.ConfigParser(interpolation=None, strict=False)
    policy.read_string(text)
    for profile_id in ("AC", "Battery", "LowBattery"):
        section = f"{profile_id}][SuspendAndShutdown"
        assert policy.has_section(section), f"missing [{section}]"
        for key in ("AutoSuspendAction", "PowerButtonAction", "LidAction"):
            assert policy.get(section, key) == "0", f"{profile_id} {key} is not NoAction"
    display = "AC][Display"
    assert policy.get(display, "TurnOffDisplayIdleTimeoutSec") == "300"


def check_validate_against_rootfs_reports_paths():
    """装配后核对：缺一条就带具体路径报错，不猜。"""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        context = piano_desktop.load("kde", "44")
        try:
            piano_desktop.validate_against_rootfs(root, context)
        except piano_desktop.DesktopError as error:
            message = str(error)
        else:
            raise AssertionError("an empty rootfs passed desktop validation")
        assert "sddm.service" in message, message
        assert "plasma.desktop" in message, message

        # 补上 KDE 声明过的全部文件后必须通过。
        for unit in ("usr/lib/systemd/system/sddm.service",):
            (root / unit).parent.mkdir(parents=True, exist_ok=True)
            (root / unit).write_text("[Unit]\n")
        (root / "usr/bin").mkdir(parents=True, exist_ok=True)
        for name in ("sddm", "plasmashell", "startplasma-wayland"):
            (root / "usr/bin" / name).write_text("#!/bin/sh\n")
            (root / "usr/bin" / name).chmod(0o755)
        for relative in ("usr/share/wayland-sessions/plasma.desktop",
                         "etc/sddm.conf.d/10-piano-autologin.conf",
                         "etc/systemd/system/sddm.service.d/20-piano-display.conf",
                         "etc/xdg/powerdevilrc"):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text("fixture")
        # 装配脚本在 enable 之前核对，所以放宽启用检查；finalizer 在 enable
        # 之后核对，那一步必须看到 .wants 链接。
        checks = piano_desktop.validate_against_rootfs(root, context, require_enabled=False)
        assert checks and all(row["ok"] for row in checks), checks
        try:
            piano_desktop.validate_against_rootfs(root, context)
        except piano_desktop.DesktopError as error:
            assert "unit enabled: sddm.service" in str(error), error
        else:
            raise AssertionError("a display manager that was never enabled passed")
        wants = root / "etc/systemd/system/graphical.target.wants"
        wants.mkdir(parents=True, exist_ok=True)
        (wants / "sddm.service").symlink_to("/usr/lib/systemd/system/sddm.service")
        piano_desktop.validate_against_rootfs(root, context)

        # --desktop 与 rootfs 不符时（DM 单元缺失）仍然拒绝。
        (root / "usr/lib/systemd/system/sddm.service").unlink()
        try:
            piano_desktop.validate_against_rootfs(root, context)
        except piano_desktop.DesktopError as error:
            assert "sddm.service" in str(error), error
        else:
            raise AssertionError("a rootfs without the display manager unit was accepted")


def main():
    check_gnome_names_unchanged()
    check_derived_names_follow_the_desktop()
    check_schema_is_enforced()
    check_repo_args()
    check_excluded_packages_leave_the_install_set()
    check_no_desktop_literals_outside_the_profile()
    check_power_files_match_the_declared_kind()
    check_kde_power_policy_is_safe()
    check_validate_against_rootfs_reports_paths()
    print("OK: desktop profiles are the single source, derived names unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
