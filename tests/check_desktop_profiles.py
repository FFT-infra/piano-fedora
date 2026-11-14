#!/usr/bin/env python3
"""桌面配方是唯一真源：形状、派生名与"脚本里不许再写桌面字面量"。"""

import contextlib
import importlib.util
import io
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

    发布链与文档都按这些名字找文件。参数化之后名字由 profile 生成，所以要在
    源头锁住它们。
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
        (lambda row: row["display_manager"]["autologin"].update(kind="greetd-config"),
         "without a session command"),
        (lambda row: row["display_manager"]["autologin"].pop("session"),
         "without a session name"),
        (lambda row: row["user_config"][0].update(target="/etc/kwinoutputconfig.json"),
         "stay under home"),
        (lambda row: row["user_config"][0].update(source="scripts/profiles/missing"),
         "user_config source is missing"),
        (lambda row: row.update(environment={"lower-case": "x"}),
         "environment entry is invalid"),
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
            planned = tool.plan("44", "denial", temporary)
            assert planned["status"] == "INPUTS_REQUIRED", planned
            assert "placeholder" in planned["reason"], planned
            assert planned["inputs_required"], planned
            # --plan 打印 INPUTS_REQUIRED 并以非零退出；--execute 在 sudo
            # 重入之前就拒绝。两条都由 main() 统一裁决。
            argv = sys.argv
            stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout), \
                     contextlib.redirect_stderr(io.StringIO()):
                    for mode in ("--plan", "--execute"):
                        sys.argv = ["build-clean-rootfs.py", mode, "--releasever", "44",
                                    "--desktop", "denial", "--rpms-dir", temporary]
                        assert tool.main() == 2, (
                            f"{mode} accepted an unbuildable desktop")
            finally:
                sys.argv = argv
            assert "INPUTS_REQUIRED" in stdout.getvalue(), stdout.getvalue()
    finally:
        DESKTOPS.write_text(original, encoding="utf-8")


def check_vulkan_desktops_install_the_driver():
    """每个档位都必须显式声明 `mesa-piano-vulkan-drivers`。

    补丁版换了包名：它提供 Fedora 的 `mesa-vulkan-drivers` 并与原名冲突。
    元包里的 `mesa-vulkan-drivers` 名字完全匹配 Fedora 的包，不显式点名
    piano 版的话，dnf 会挑官方包，finalize 阶段逐一核对 Mesa 清单里的
    6 个改名包时就会失败。niri 与 Hyprland 没有元包，本来就靠显式声明。
    """
    for desktop in ("gnome", "kde", "niri", "hyprland"):
        context = piano_desktop.load(desktop, "44")
        packages = context["profile"]["dnf_packages"]
        # Vulkan 驱动会把 libgbm、dri-drivers 与 filesystem 一起拉进来，
        # libGL 与 libEGL 没有谁依赖它们，必须自己点名，否则 Mesa 清单里
        # 那 6 个包装不齐，finalize 会逐个核对失败。
        for name in ("mesa-piano-vulkan-drivers", "mesa-piano-libGL", "mesa-piano-libEGL"):
            assert name in packages, (
                f"{desktop} needs {name}, not the Fedora-named package")


def check_repo_args():
    """第三方仓库展开成 --repofrompath + priority，变量按 arch / releasever 替换。

    带 key 的仓库必须同时打开 `gpgcheck=1` 并给出 key：全局 `--nogpgcheck`
    已收窄成只对本地未签名的 `piano-local` 生效。
    """
    context = piano_desktop.load("hyprland", "44")
    repos = context["profile"]["repos"]
    assert [repo["id"] for repo in repos] == ["copr-hyprland"], repos
    args = piano_desktop.repo_args(context)
    assert any("copr-hyprland,https://download.copr.fedorainfracloud.org/results/"
               "lionheartp/Hyprland/fedora-44-aarch64/" in arg for arg in args), args
    assert "--setopt=copr-hyprland.priority=5" in args, args
    assert "--setopt=copr-hyprland.gpgcheck=1" in args, args
    assert any(arg.startswith("--setopt=copr-hyprland.gpgkey=") for arg in args), args
    # 已校验到本地的 key 路径要顶替 URL。
    swapped = piano_desktop.repo_args(context, {"copr-hyprland": "/tmp/copr-hyprland.gpg"})
    assert "--setopt=copr-hyprland.gpgkey=/tmp/copr-hyprland.gpg" in swapped, swapped

    # GNOME / KDE / niri 不启用任何第三方仓库。
    for desktop in ("gnome", "kde", "niri"):
        plain = piano_desktop.load(desktop, "44")
        assert piano_desktop.repo_args(plain) == [], (desktop,
                                                       piano_desktop.repo_args(plain))
        assert piano_desktop.gpgkey_pins(plain) == [], desktop


def check_gpgkeys_are_pinned():
    """网络取来的 key 必须按 sha256 钉住；不钉或钉错都失败。

    只对真实 URL 做一次下载会把测试绑在网络上，所以这里用 file:// 指向
    本测试写的临时文件，验证的仍是 `fetch_gpgkeys()` 的哈希分支。
    """
    pins = piano_desktop.gpgkey_pins(piano_desktop.load("hyprland", "44"))
    assert pins and pins[0][0] == "copr-hyprland", pins
    assert len(pins[0][2]) == 64, pins

    original = DESKTOPS.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory() as temporary:
        key = Path(temporary) / "key.gpg"
        key.write_bytes(b"piano fixture key")
        digest = __import__("hashlib").sha256(key.read_bytes()).hexdigest()
        data = json.loads(original)
        repo = data["hyprland"]["repos"][0]
        repo["gpgkey"] = key.as_uri()
        repo["gpgkey_sha256"] = digest
        try:
            DESKTOPS.write_text(json.dumps(data), encoding="utf-8")
            context = piano_desktop.load("hyprland", "44")
            paths = piano_desktop.fetch_gpgkeys(context, Path(temporary) / "out")
            assert paths["copr-hyprland"].endswith("copr-hyprland.gpg"), paths
            assert Path(paths["copr-hyprland"]).read_bytes() == b"piano fixture key"

            # 钉住的值不匹配时必须失败，而不是接受换掉的 key。
            repo["gpgkey_sha256"] = "0" * 64
            DESKTOPS.write_text(json.dumps(data), encoding="utf-8")
            try:
                piano_desktop.fetch_gpgkeys(
                    piano_desktop.load("hyprland", "44"), Path(temporary) / "out")
            except piano_desktop.DesktopError as error:
                assert "sha256 changed" in str(error), error
            else:
                raise AssertionError("a repinned gpg key was accepted silently")

            # 没写 gpgkey_sha256 的仓库不许进入 profile。
            repo.pop("gpgkey_sha256")
            DESKTOPS.write_text(json.dumps(data), encoding="utf-8")
            try:
                piano_desktop.gpgkey_pins(piano_desktop.load("hyprland", "44"))
            except piano_desktop.DesktopError as error:
                assert "needs gpgkey_sha256" in str(error), error
            else:
                raise AssertionError("an unpinned gpg key was accepted")
        finally:
            DESKTOPS.write_text(original, encoding="utf-8")


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
    （`gdm.service`、`/etc/gdm/custom.conf`、`plasmalogin.service`）就该失败。
    """
    forbidden = re.compile(
        r'gdm\.service|/etc/gdm/|sddm\.service|plasmalogin\.service|plasma-workspace|gnome-shell"|'
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
    assert kinds == {"gnome": "dconf", "kde": "kconfig", "niri": "logind-only",
                     "hyprland": "logind-only", "server": "logind-only"}, kinds


def check_kde_power_policy_is_safe():
    """KDE 的 powerdevilrc 三个 profile 段都不自动挂起，合盖只关屏。

    电源键交给 piano-plasma-power-button 助手，所以 PowerButtonAction 必须是
    NoAction：留 TurnOffScreen(64) 的话 PowerDevil 在按下那一刻就关屏锁屏，
    长按到 800 毫秒时对话框已经落在锁屏后面，看不见。合盖仍由 PowerDevil
    处理，动作跟 GNOME 档对齐：关屏并锁屏，机器继续跑。三个 profile 的
    AutoSuspendAction 也必须是 NoAction，任何一段改成 Sleep 都会让平板在没人
    碰的时候自己睡下去。
    """
    import configparser
    context = piano_desktop.load("kde", "44")
    text = piano_desktop.power_files(context)["etc/xdg/powerdevilrc"]
    policy = configparser.ConfigParser(interpolation=None, strict=False)
    policy.read_string(text)
    for profile_id in ("AC", "Battery", "LowBattery"):
        section = f"{profile_id}][SuspendAndShutdown"
        assert policy.has_section(section), f"missing [{section}]"
        assert policy.get(section, "AutoSuspendAction") == "0", (
            f"{profile_id} AutoSuspendAction is not NoAction")
        assert policy.get(section, "PowerButtonAction") == "0", (
            f"{profile_id} PowerButtonAction must be NoAction; the helper owns the key")
        assert policy.get(section, "LidAction") == "64", (
            f"{profile_id} LidAction is not TurnOffScreen")
    display = "AC][Display"
    assert policy.get(display, "TurnOffDisplayIdleTimeoutSec") == "300"


def check_kde_desktop_defaults():
    """KDE 的显示、输入与锁屏默认值与上游一致，且只有 KDE 声明它们。"""
    import configparser

    context = piano_desktop.load("kde", "44")
    files = piano_desktop.user_config_files(context, "piano")
    assert sorted(files) == [
        "home/piano/.config/kcminputrc",
        "home/piano/.config/kscreenlockerrc",
        "home/piano/.config/kwinoutputconfig.json",
    ], sorted(files)

    output = json.loads(files["home/piano/.config/kwinoutputconfig.json"])
    outputs = next(row["data"] for row in output if row["name"] == "outputs")
    setups = next(row["data"] for row in output if row["name"] == "setups")
    assert len(outputs) == len(setups) == 2, output
    for index, entry in enumerate(outputs):
        assert entry["connectorName"] == "DSI-1", entry
        assert entry["mode"] == {"width": 3200, "height": 2136,
                                 "refreshRate": 120000}, entry
        assert entry["scale"] == 2, entry
        assert entry["autoRotation"] == "InTabletMode", entry
        assert entry["brightness"] == 0.8, entry
        assert setups[index]["outputs"][0]["outputIndex"] == index, setups[index]
    # 同一块面板两种身份：内核没读到 EDID 时用空标识，读到真实 EDID 时
    # 用 XMI 129 0 0 2025 0，两者必须指向同一份输出配置。
    assert {entry["edidIdentifier"] for entry in outputs} == {
        "", "XMI 129 0 0 2025 0"}, outputs

    input_config = configparser.ConfigParser(interpolation=None)
    input_config.read_string(files["home/piano/.config/kcminputrc"])
    touchpad = "Libinput][5593][161][Xiaomi Touchpad"
    assert input_config.get(touchpad, "NaturalScroll") == "true", input_config.items(touchpad)

    locker = configparser.ConfigParser(interpolation=None)
    locker.read_string(files["home/piano/.config/kscreenlockerrc"])
    assert locker.get("Daemon", "Autolock") == "false", locker.items("Daemon")
    assert locker.get("Daemon", "LockOnResume") == "true", locker.items("Daemon")

    # 面板源模式不是生成出来的 DRM 模式，KWin 需要 FULL 才肯按它缩放。
    assert piano_desktop.environment_files(context) == {
        "etc/environment.d/60-piano-display.conf":
            "KWIN_DRM_FORCE_SCALING_MODE=FULL\n"}, piano_desktop.environment_files(context)

    # autoRotation 只有 KWin 能读到传感器时才出现；kwin 自己没声明这个包。
    assert "qt6-qtsensors" in context["profile"]["dnf_packages"], \
        "KDE 档没有装 qt6-qtsensors，Plasma 不会提供自动旋转"

    # KWin 的键对其它桌面没有意义：别的档位既不写每用户配置，也不带
    # 桌面专用环境变量。
    for desktop in ("gnome", "niri", "hyprland", "server"):
        other = piano_desktop.load(desktop, "44")
        assert piano_desktop.user_config_files(other, "piano") == {}, desktop
        assert piano_desktop.environment_files(other) == {}, desktop
        assert piano_desktop.environment_required(other) == [], desktop


def check_user_config_follows_the_home_owner():
    """每用户配置写成 home 目录的属主：KWin 运行时要改写这些文件。"""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        context = piano_desktop.load("kde", "44")
        try:
            piano_desktop.stage_user_config(root, context, "piano", sudo_aware=False)
        except piano_desktop.DesktopError as error:
            assert "useradd" in str(error), error
        else:
            raise AssertionError("user config was staged without a home directory")
        home = root / "home" / "piano"
        home.mkdir(parents=True)
        staged = piano_desktop.stage_user_config(root, context, "piano", sudo_aware=False)
        assert staged == sorted(piano_desktop.user_config_files(context, "piano")), staged
        for name in staged:
            assert (root / name).stat().st_uid == home.stat().st_uid, name
            assert (root / name).stat().st_gid == home.stat().st_gid, name


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
        assert "plasmalogin.service" in message, message
        assert "plasma.desktop" in message, message

        # 补上 KDE 声明过的全部文件后必须通过。
        for unit in ("usr/lib/systemd/system/plasmalogin.service",):
            (root / unit).parent.mkdir(parents=True, exist_ok=True)
            (root / unit).write_text("[Unit]\n")
        (root / "usr/bin").mkdir(parents=True, exist_ok=True)
        for name in ("plasmalogin", "plasmashell", "startplasma-wayland"):
            (root / "usr/bin" / name).write_text("#!/bin/sh\n")
            (root / "usr/bin" / name).chmod(0o755)
        for relative in ("usr/share/wayland-sessions/plasma.desktop",
                         "etc/plasmalogin.conf",
                         "etc/xdg/powerdevilrc"):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text("fixture")
        for relative in piano_desktop.environment_files(context):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text("fixture")
        (root / "home/piano").mkdir(parents=True)
        piano_desktop.stage_user_config(root, context, "piano", sudo_aware=False)
        # 装配脚本在 enable 之前核对，所以放宽启用检查；finalizer 在 enable
        # 之后核对，那一步必须看到 .wants 链接。
        checks = piano_desktop.validate_against_rootfs(root, context, require_enabled=False)
        assert checks and all(row["ok"] for row in checks), checks
        try:
            piano_desktop.validate_against_rootfs(root, context)
        except piano_desktop.DesktopError as error:
            assert "unit enabled: plasmalogin.service" in str(error), error
        else:
            raise AssertionError("a display manager that was never enabled passed")
        wants = root / "etc/systemd/system/graphical.target.wants"
        wants.mkdir(parents=True, exist_ok=True)
        (wants / "plasmalogin.service").symlink_to("/usr/lib/systemd/system/plasmalogin.service")
        piano_desktop.validate_against_rootfs(root, context)

        # --desktop 与 rootfs 不符时（DM 单元缺失）仍然拒绝。
        (root / "usr/lib/systemd/system/plasmalogin.service").unlink()
        try:
            piano_desktop.validate_against_rootfs(root, context)
        except piano_desktop.DesktopError as error:
            assert "plasmalogin.service" in str(error), error
        else:
            raise AssertionError("a rootfs without the display manager unit was accepted")


def check_gdm_enable_is_the_display_manager_alias():
    """GDM 只有 `Alias=display-manager.service`，启用后没有 `*.wants/gdm.service`。

    真实 Fedora 的 `gdm.service` 的 `[Install]` 段只有这一条别名，`systemctl
    enable gdm.service` 落的是 `etc/systemd/system/display-manager.service`
    指向它的链接。只查 `*.wants/` 会把装好的 GDM 判成未启用，四档镜像全部
    在 finalize 阶段被拒。
    """
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        context = piano_desktop.load("gnome", "44")
        unit = "usr/lib/systemd/system/gdm.service"
        (root / unit).parent.mkdir(parents=True, exist_ok=True)
        (root / unit).write_text("[Unit]\n")
        (root / "usr/bin").mkdir(parents=True, exist_ok=True)
        for name in ("gdm", "gnome-shell"):
            (root / "usr/bin" / name).write_text("#!/bin/sh\n")
            (root / "usr/bin" / name).chmod(0o755)
        for relative in ("etc/gdm/custom.conf",
                         "usr/share/wayland-sessions/gnome.desktop",
                         "etc/dconf/db/local.d/00-power"):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text("fixture")
        # 未启用时仍然拒绝，报的必须是启用这一项。
        try:
            piano_desktop.validate_against_rootfs(root, context)
        except piano_desktop.DesktopError as error:
            assert "unit enabled: gdm.service" in str(error), error
        else:
            raise AssertionError("an unenabled gdm.service passed")

        # systemctl enable 对 gdm.service 的实际产物：display-manager 别名。
        enabled = root / "etc/systemd/system/display-manager.service"
        enabled.parent.mkdir(parents=True, exist_ok=True)
        enabled.symlink_to("/usr/lib/systemd/system/gdm.service")
        assert piano_desktop._unit_enabled(root, "gdm.service")
        # 别名指向别家时不能把 gdm 判成已启用。
        enabled.unlink()
        enabled.symlink_to("/usr/lib/systemd/system/plasmalogin.service")
        assert not piano_desktop._unit_enabled(root, "gdm.service")


def check_greetd_desktops_stage_a_config_toml():
    """niri / Hyprland 共用 greetd：config.toml 的 initial_session 指向各自会话。

    greetd 的 `[initial_session]` 是自动登录的唯一入口；命令名写错时
    greetd 只在日志里报失败，屏幕停在一个没有内容的 VT 上。这里同时断言
    profile 值与落盘内容，并由 `validate_against_rootfs` 在真实树里核对
    命令存在。
    """
    expected = {"niri": "niri-session", "hyprland": "start-hyprland"}
    for desktop, command in expected.items():
        context = piano_desktop.load(desktop, "44")
        manager = context["profile"]["display_manager"]
        assert manager["unit"] == "greetd.service", (desktop, manager)
        assert manager["autologin"]["kind"] == "greetd-config", (desktop, manager)
        files = piano_desktop.login_files(context, "piano")
        assert list(files) == ["etc/greetd/config.toml"], (desktop, files)
        text = files["etc/greetd/config.toml"]
        assert json.loads(
            text.split("[initial_session]", 1)[1]
                .split("command = ", 1)[1].splitlines()[0]
        ) == command, (desktop, text)
        assert '[default_session]' in text and 'user = "greetd"' in text, text
        # 上面那句只证明字符串出现过。分段落再断言一次：`[default_session]`
        # 的 user 才是 tuigreet 的运行账户，写成 greeter 时 greetd 每 1 秒
        # 重启一次，5 次后撞 StartLimitBurst 变成 failed，显示管理器整个起不来。
        # 这个 bug 曾经源码和断言一起写错，CI 全绿照样放行，所以这里不能
        # 满足于子串命中。
        default_section = text.split("[default_session]", 1)[1]
        assert 'user = "greetd"' in default_section, (desktop, text)
        assert 'user = "greeter"' not in text, (desktop, text)
        # 单元启用指向 greetd。显示管理器不再挂 piano-display 依赖：那个单元
        # 已随上游的标准 modalias 架构删除，挂上去只会让桌面起不来。
        assert piano_desktop.enable_units(context) == ["greetd.service"], desktop


def check_greetd_validation_checks_the_session_command():
    """greetd 档的核对必须落到命令是否存在，而不是只查单元。"""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        context = piano_desktop.load("niri", "44")
        for unit in ("usr/lib/systemd/system/greetd.service",):
            (root / unit).parent.mkdir(parents=True, exist_ok=True)
            (root / unit).write_text("[Unit]\n")
        for relative in ("usr/share/wayland-sessions/niri.desktop",
                         "etc/greetd/config.toml"):
            (root / relative).parent.mkdir(parents=True, exist_ok=True)
            (root / relative).write_text("fixture")
        (root / "usr/bin").mkdir(parents=True, exist_ok=True)
        # greetd 自己的二进制也必须在树里（display_manager.package）。
        (root / "usr" / "bin" / "greetd").write_text("#!/bin/sh\n")
        (root / "usr" / "bin" / "greetd").chmod(0o755)
        # inspect 阶段（enable 之前）
        try:
            piano_desktop.validate_against_rootfs(root, context, require_enabled=False)
        except piano_desktop.DesktopError as error:
            assert "greetd session command exists: niri-session" in str(error), error
        else:
            raise AssertionError("a missing greetd session command passed")
        for name in ("niri", "niri-session"):
            (root / "usr/bin" / name).write_text("#!/bin/sh\n")
            (root / "usr/bin" / name).chmod(0o755)
        piano_desktop.validate_against_rootfs(root, context, require_enabled=False)


def check_build_rootfs_expands_the_profile():
    """build-rootfs.yml 的矩阵由 profile 展开，不在 YAML 里手写桌面名。

    新增一档只改 `fedora-desktops.json`；如果 workflow 里出现第二份桌面列表，
    两边就会分叉，`desktop=all` 出的产物数量和 profile 对不上。
    """
    import subprocess
    workflow = (ROOT / ".github/workflows/build-rootfs.yml").read_text(encoding="utf-8")
    assert "scripts/piano_desktop.py expand" in workflow, (
        "build-rootfs.yml must expand the matrix from the profile")
    assert "needs.prepare.outputs.matrix" in workflow, (
        "the rootfs job must consume the prepare job's matrix")
    assert "fromJSON(needs.prepare.outputs.matrix)" in workflow
    assert "matrix.desktop" in workflow
    # 矩阵 job 里的步骤不能再读 inputs.desktop（只有 prepare 可以）。
    rootfs_section = workflow.split("  rootfs:", 1)[1]
    assert "inputs.desktop" not in rootfs_section, (
        "a matrix job must use matrix.desktop, not the dispatch input")

    # 子命令行为：all 展开全部桌面档，server 与占位档都不进矩阵。
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/piano_desktop.py"), "expand",
         "--desktop", "all", "--releasever", "44"],
        capture_output=True, text=True, cwd=ROOT)
    assert completed.returncode == 0, completed.stderr
    rows = json.loads(completed.stdout)
    desktops = [row["desktop"] for row in rows]
    assert desktops == piano_desktop.matrix(desktops_only=True), desktops
    assert "server" not in desktops and "denial" not in desktops, desktops
    for row in rows:
        assert row["rootfs_artifact"] == f"rootfs-44-{row['desktop']}", row
        assert row["rootfs_tar"] == f"rootfs-44-{row['desktop']}.tar.zst", row


def main():
    check_gnome_names_unchanged()
    check_derived_names_follow_the_desktop()
    check_schema_is_enforced()
    check_vulkan_desktops_install_the_driver()
    check_repo_args()
    check_gpgkeys_are_pinned()
    check_greetd_desktops_stage_a_config_toml()
    check_greetd_validation_checks_the_session_command()
    check_excluded_packages_leave_the_install_set()
    check_no_desktop_literals_outside_the_profile()
    check_power_files_match_the_declared_kind()
    check_kde_power_policy_is_safe()
    check_kde_desktop_defaults()
    check_user_config_follows_the_home_owner()
    check_validate_against_rootfs_reports_paths()
    check_gdm_enable_is_the_display_manager_alias()
    check_build_rootfs_expands_the_profile()
    print("OK: desktop profiles are the single source, derived names unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
