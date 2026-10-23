#!/usr/bin/env python3
"""桌面配方：登录、服务、包集合、电源策略与产物名的唯一真源。

原来这些值散在两个后端里各写一份：`build-clean-rootfs.py` 写 GDM 的
custom.conf 并启用 gdm.service，`finalize-fedora-rootfs.py` 再写一遍，还带着
一份只认 `org.gnome.*` 的电源 dconf 契约。加第二个桌面意味着同一个改动要
落四处，漏一处就是一台能装不能登的机器。

本模块把差异全部收进 `scripts/profiles/fedora-desktops.json`，脚本只调用这里
的函数。`validate_against_rootfs()` 负责在装配完成后回到真实 rootfs 里核对
每一条声明——SDDM 的 `Session=`、greetd 的会话命令、会话 .desktop 路径这些
值不许靠猜，缺一样就带具体路径报错。

用法：
    python3 scripts/piano_desktop.py matrix
    python3 scripts/piano_desktop.py show --desktop kde
    python3 scripts/piano_desktop.py plan --desktop kde --releasever 44
"""

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "scripts" / "profiles"
DESKTOPS = PROFILES / "fedora-desktops.json"
PRODUCT = ROOT / "product.json"

# 桌面档必须声明的字段。少一个就在加载时报错，而不是在装配到一半时
# 因为 KeyError 才失败。
REQUIRED_FIELDS = (
    "label", "buildable", "dnf_packages", "repos", "exclude_packages",
    "required_packages", "root_size_mib", "display_manager", "session",
    "power_policy",
)
REQUIRED_DISPLAY_MANAGER = ("unit", "package", "autologin", "dependency_dropin")
REQUIRED_SESSION = ("desktop_names", "process_names", "session_files", "bins")
# autologin 的落盘机制。每种机制在 login_files() 里有且只有一个实现。
AUTOLOGIN_KINDS = ("gdm-custom-conf", "sddm-autologin", "plasmalogin-autologin", "greetd-config")
POWER_KINDS = ("dconf", "kconfig", "logind-only")


class DesktopError(Exception):
    """配方缺失、形状不对或与真实 rootfs 不符。"""


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DesktopError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DesktopError(f"invalid JSON in {path}: {exc}") from exc


def _require(profile, desktop):
    missing = [name for name in REQUIRED_FIELDS if name not in profile]
    if missing:
        raise DesktopError(f"desktop {desktop!r} lacks fields: {missing}")
    manager = profile["display_manager"]
    if manager is not None:
        missing = [name for name in REQUIRED_DISPLAY_MANAGER if name not in manager]
        if missing:
            raise DesktopError(f"desktop {desktop!r} display_manager lacks: {missing}")
        kind = manager["autologin"].get("kind")
        if kind not in AUTOLOGIN_KINDS:
            raise DesktopError(
                f"desktop {desktop!r} uses unknown autologin {kind!r}; "
                f"known: {list(AUTOLOGIN_KINDS)}")
        if kind in ("sddm-autologin", "plasmalogin-autologin") and not manager["autologin"].get("session"):
            raise DesktopError(
                f"desktop {desktop!r} uses {kind} without a session name")
        if kind == "greetd-config" and not manager["autologin"].get("command"):
            raise DesktopError(
                f"desktop {desktop!r} uses greetd-config without a session command")
        dropin = manager["dependency_dropin"]
        if not isinstance(dropin, dict) or "unit" not in dropin or "after" not in dropin:
            raise DesktopError(
                f"desktop {desktop!r} dependency_dropin needs unit and after")
    missing = [name for name in REQUIRED_SESSION if name not in profile["session"]]
    if missing:
        raise DesktopError(f"desktop {desktop!r} session lacks: {missing}")
    policy = profile["power_policy"]
    if policy.get("kind") not in POWER_KINDS:
        raise DesktopError(
            f"desktop {desktop!r} uses unknown power_policy {policy.get('kind')!r}; "
            f"known: {list(POWER_KINDS)}")
    if policy["kind"] != "logind-only" and not policy.get("source"):
        raise DesktopError(
            f"desktop {desktop!r} power_policy={policy['kind']} needs a source file")
    # 不可构建的档位（denial 坑位）显式写 null，不能是一个看起来能用的空列表。
    # server 档是合法的空列表：它只装 base_packages，没有桌面会话。
    if profile["buildable"] and profile["dnf_packages"] is None:
        raise DesktopError(
            f"desktop {desktop!r} is buildable but has dnf_packages=null; "
            "mark it unbuildable or list its packages")
    if not profile["buildable"] and profile["dnf_packages"] is not None:
        raise DesktopError(
            f"desktop {desktop!r} is not buildable; dnf_packages must be null so a "
            "plan never looks installable")
    return profile


def load(desktop=None, releasever=None, product=None):
    """合并 base profile 与桌面档，做完形状校验再返回。"""
    base = load_json(PROFILES / "fedora.json")
    desktops = load_json(DESKTOPS)
    product_data = load_json(product or PRODUCT) if Path(product or PRODUCT).is_file() else {}
    target = product_data.get("target", {})
    desktop = desktop or target.get("desktop") or "gnome"
    releasever = str(releasever or target.get("releasever") or "")
    if not releasever.isdigit():
        # 留空就是留空，不猜一个版本号：产物名里有它。
        raise DesktopError(
            f"releasever is empty or not numeric ({releasever!r}); "
            "pass --releasever, it is not guessed")
    if desktop not in desktops:
        raise DesktopError(
            f"unknown desktop {desktop!r}; known: {sorted(desktops)}")
    row = _require(desktops[desktop], desktop)
    return {
        "base": base,
        "desktop": desktop,
        "releasever": releasever,
        "arch": base.get("arch", target.get("arch", "aarch64")),
        "target": target,
        "profile": row,
        "root_label": target.get("root_label", "PIANOROOT"),
    }


def matrix(only_buildable=True, desktops_only=False):
    """`build-rootfs.yml` 的矩阵：文件顺序，只含可构建档位。

    `desktops_only` 去掉没有桌面会话的档位（server）。发布链只关心带桌面的
    镜像，server 留在这里是为了能在同一套装配流程里跑无桌面测试。
    """
    desktops = load_json(DESKTOPS)
    names = [name for name, row in desktops.items()
             if (row.get("buildable") or not only_buildable)
             and (not desktops_only or row.get("display_manager") is not None)]
    for name in names:
        _require(desktops[name], name)
    return names


def artifact_names(context):
    """产物名。GNOME 的派生结果必须与旧字面量逐字节相同。"""
    desktop = context["desktop"]
    releasever = context["releasever"]
    profile = context["profile"]
    stem = f"pianoroot-fedora{releasever}-{desktop}-f2fs"
    return {
        "rootfs_dir": f"fedora-{releasever}-{desktop}",
        "rootfs_tar": f"rootfs-{releasever}-{desktop}.tar.zst",
        "rootfs_artifact": f"rootfs-{releasever}-{desktop}",
        "image_artifact": f"root-image-{releasever}-{desktop}",
        "image_stem": stem,
        "image": f"{stem}.img",
        "image_zst": f"{stem}.img.zst",
        "root_size_mib": int(profile["root_size_mib"]),
    }


def login_files(context, user):
    """登录相关的落盘文件，按 desktop 的 autologin 机制分派。

    返回值把"写哪个文件、写什么"完整表达出来，调用方不再自己拼字符串——
    这正是 GDM 的 custom.conf 曾经在两个后端各写一份的来源。
    """
    profile = context["profile"]
    manager = profile["display_manager"]
    if manager is None:
        return {}
    autologin = manager["autologin"]
    kind = autologin["kind"]
    if kind == "gdm-custom-conf":
        return {
            "etc/gdm/custom.conf":
                "[daemon]\n"
                "AutomaticLoginEnable=True\n"
                f"AutomaticLogin={user}\n",
        }
    if kind == "sddm-autologin":
        session = autologin["session"]
        return {
            "etc/sddm.conf.d/10-piano-autologin.conf":
                "[Autologin]\n"
                f"User={user}\n"
                f"Session={session}\n"
                "Relogin=false\n",
        }
    if kind == "plasmalogin-autologin":
        # plasma-login-manager 是 SDDM 的衍生实现，[Autologin] 的键与
        # 会话查找规则都沿用 SDDM；配置文件换到 /etc/plasmalogin.conf。
        session = autologin["session"]
        return {
            "etc/plasmalogin.conf":
                "[Autologin]\n"
                f"User={user}\n"
                f"Session={session}\n"
                "Relogin=false\n",
        }
    if kind == "greetd-config":
        initial = autologin["command"]
        # tuigreet 是 F44 里的文本 greeter。`--time` 只是显示时钟；没有
        # 其他参数时它列出会话供选择，输错或会话崩溃后 greetd 会回到这里。
        fallback = autologin.get("fallback", "tuigreet --time")
        # greetd 的 greeter 账户由包里的 sysusers 片段创建，Fedora 上的名字是
        # `greetd`（`u greetd - "greetd daemon"`），不是通用的 `greeter`。
        # 写错名字时 greetd 每 1 秒重启一次，5 次后撞 StartLimitBurst 变成
        # failed，显示管理器整个起不来。
        greeter = autologin.get("greeter_user", "greetd")
        return {
            "etc/greetd/config.toml":
                "[terminal]\n"
                "vt = 1\n"
                "\n"
                "[initial_session]\n"
                f"command = {json.dumps(initial)}\n"
                f"user = {json.dumps(user)}\n"
                "\n"
                "[default_session]\n"
                f"command = {json.dumps(fallback)}\n"
                f"user = {json.dumps(greeter)}\n",
        }
    raise DesktopError(f"unhandled autologin kind {kind!r}")


def stage_login(root, context, user, sudo_aware=True):
    """把 login_files() 的内容写进 rootfs，返回写过的相对路径。"""
    root = Path(root)
    files = login_files(context, user)
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if sudo_aware and os.geteuid() != 0 and _is_root_owned(target.parent):
            subprocess.run(["sudo", "tee", str(target)], input=text,
                           text=True, check=True, stdout=subprocess.DEVNULL)
        else:
            target.write_text(text, encoding="utf-8")
    return sorted(files)


def _is_root_owned(path):
    """写完还要被 rpm 审计的树必须在 root 名下，否则镜像里的属主不对。"""
    try:
        return Path(path).stat().st_uid == 0
    except FileNotFoundError:
        return False


def enable_units(context):
    """桌面档负责启用的 systemd 单元。"""
    manager = context["profile"]["display_manager"]
    if manager is None:
        return []
    return [manager["unit"]]


def display_dependency_dropin(context):
    """显示服务必须在面板真正就绪之后启动。"""
    manager = context["profile"]["display_manager"]
    if manager is None:
        return None
    dropin = manager["dependency_dropin"]
    unit = dropin["unit"]
    after = dropin["after"]
    path = f"etc/systemd/system/{unit}.d/20-piano-display.conf"
    return path, f"[Unit]\nRequires={after}\nAfter={after}\n"


def required_packages(context):
    """装配后必须在 `rpm -qa` 里出现的包；缺一个就拒绝出镜像。"""
    return set(context["profile"]["required_packages"])


def excluded_packages(context):
    """被本项目自己的包替换掉的原装包，不能跟着依赖树一起装回来。

    这些名字从桌面档的安装列表里去掉，而不是传给 `dnf --exclude`：
    `iio-sensor-proxy` 现在由 `iio-sensor-proxy-ssc` 通过 Provides/Obsoletes
    提供，按名字排除会把提供者也一起挡在外面。
    """
    return list(context["profile"]["exclude_packages"])


def desktop_packages(context):
    """桌面档的安装列表，去掉被自有包替换的原装名。"""
    excluded = set(excluded_packages(context))
    return [name for name in context["profile"]["dnf_packages"] or []
            if name not in excluded]


def power_files(context):
    """构建器要写的电源策略文件。

    GNOME 的 `00-power` 由 `piano-fedora-configs` 这个 RPM 拥有并安装，
    构建器再写一份就是同一路径的第二个写者（内容分叉时 RPM 校验也发现
    不了）；那一档只核对，不写。KDE 的 `powerdevilrc` 没有包拥有它，
    所以由构建器落盘。
    """
    policy = context["profile"]["power_policy"]
    if policy["kind"] == "kconfig":
        source = ROOT / policy["source"]
        return {policy["target"]: source.read_text(encoding="utf-8")}
    # dconf 档：文件由自有 RPM 安装。
    # logind-only：没有桌面侧文件，电源键交给 logind 的默认动作。
    return {}


def power_required(context):
    """装配后必须在树里的电源策略路径，不论由谁写入。"""
    policy = context["profile"]["power_policy"]
    if policy["kind"] == "dconf":
        return ["etc/dconf/db/local.d/00-power"]
    if policy["kind"] == "kconfig":
        return [policy["target"]]
    return []


def dconf_profile_required(context):
    """只有 iio/dconf 那一档才需要 /etc/dconf/profile/user 里的 system-db:local。"""
    return context["profile"]["power_policy"]["kind"] == "dconf"


def repo_args(context, key_paths=None):
    """额外仓库的 dnf 参数（只用 --repofrompath，不写设备侧持久配置）。

    具体仓库由 .repo 文件在设备上提供，构建期只是让它能被解析到。
    `key_paths` 把 gpgkey 换成本地已校验的路径，供 `fetch_gpgkeys()` 使用。
    """
    key_paths = key_paths or {}
    args = []
    for repo in context["profile"]["repos"]:
        name = repo["id"]
        baseurl = repo["baseurl"].replace("$basearch", _basearch(context["arch"]))
        baseurl = baseurl.replace("$releasever", context["releasever"])
        args += [f"--repofrompath={name},{baseurl}",
                 f"--setopt={name}.priority={repo.get('priority', 5)}"]
        gpgkey = key_paths.get(name) or repo.get("gpgkey")
        if gpgkey:
            # 第三方仓库必须验签；全局 --nogpgcheck 只留给本地未签名的
            # piano-local，见 dnf_install_args()。
            args += [f"--setopt={name}.gpgcheck=1",
                     f"--setopt={name}.gpgkey={gpgkey}"]
        if repo.get("skip_if_unavailable"):
            args.append(f"--setopt={name}.skip_if_unavailable=True")
    return args


def gpgkey_pins(context):
    """第三方仓库的 key 固定值。

    `--nogpgcheck` 现在只对本地 `piano-local` 生效，第三方仓库必须校验签名。
    校验的是从 URL 下载到的 key 本身，所以 key 的内容要按 sha256 钉住：
    `pubkey.gpg` 被换掉时，构建会失败而不是静默接受一把新钥匙。
    """
    pins = []
    for repo in context["profile"]["repos"]:
        digest = repo.get("gpgkey_sha256")
        if not digest:
            raise DesktopError(
                f"repository {repo['id']!r} needs gpgkey_sha256; a key fetched "
                "from the network must be pinned")
        pins.append((repo["id"], repo["gpgkey"], digest))
    return pins


def fetch_gpgkeys(context, directory):
    """下载并核对第三方仓库的 key，返回 {repo_id: 本地路径}。

    构建机（以及 dnf 的 --releasever/--forcearch 组合）不保证能直接按 URL
    取 key，所以先落到本地再交给 dnf。sha256 不符直接失败。
    """
    import urllib.request

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name, url, expected in gpgkey_pins(context):
        target = directory / f"{name}.gpg"
        with urllib.request.urlopen(url, timeout=60) as response:
            body = response.read()
        actual = hashlib.sha256(body).hexdigest()
        if actual != expected:
            raise DesktopError(
                f"{name} gpg key sha256 changed: got {actual}, pinned {expected}; "
                "review the new key before updating the profile")
        target.write_bytes(body)
        paths[name] = str(target)
    return paths


def _basearch(arch):
    return {"aarch64": "aarch64", "x86_64": "x86_64"}.get(arch, arch)


def validate_against_rootfs(root, context, *, require_enabled=True):
    """装配完成后在真实 rootfs 里核对配方声明过的每一样东西。

    返回核对记录，写进 manifest。任何一项缺失都抛 DesktopError，并带上
    具体路径——不靠猜 `Session=plasma` 还是 `plasmax11`，树里没有什么就报什么。
    包集合不在这里查：finalizer 那边的 `rpm -qa` 已经逐包核对过，这里只管
    登录、会话与电源文件是否真的落到了镜像里。
    """
    root = Path(root)
    profile = context["profile"]
    checks = []
    problems = []

    def want(condition, message):
        checks.append({"check": message, "ok": bool(condition)})
        if not condition:
            problems.append(message)

    if not profile["buildable"]:
        raise DesktopError(
            f"desktop {context['desktop']!r} is not buildable (purposeful placeholder); "
            "it must never produce a rootfs")

    manager = profile["display_manager"]
    if manager is not None:
        unit = manager["unit"]
        candidates = [
            root / "usr/lib/systemd/system" / unit,
            root / "etc/systemd/system" / unit,
        ]
        want(any(path.exists() or path.is_symlink() for path in candidates),
             f"display manager unit exists: {unit}")
        binary = next((root / prefix / manager["package"]
                       for prefix in ("usr/bin", "usr/sbin")
                       if (root / prefix / manager["package"]).exists()), None)
        want(binary is not None,
             f"display manager binary exists: {manager['package']}")
        autologin = manager["autologin"]
        if autologin["kind"] in ("sddm-autologin", "plasmalogin-autologin"):
            # SDDM 与 plasma-login-manager 都按 <session>.desktop 在
            # SessionDir 里找条目，名字对不上就只在日志里留一句
            # "Unable to find autologin session entry" 然后退回登录界面。
            # 这个文件必须真的在树里。
            session = autologin["session"]
            found = any((root / d / f"{session}.desktop").is_file()
                        for d in ("usr/share/wayland-sessions", "usr/share/xsessions"))
            want(found, f"autologin session exists: {session}.desktop")
        elif autologin["kind"] == "greetd-config":
            command = shlex.split(autologin["command"])[0]
            want((root / "usr/bin" / command).exists()
                 or (root / "usr/sbin" / command).exists(),
                 f"greetd session command exists: {command}")

    for name in login_files(context, "piano"):
        want((root / name).is_file(), f"login file staged: {name}")

    for relative in profile["session"]["session_files"]:
        want((root / relative.lstrip("/")).is_file(),
             f"session desktop file exists: {relative}")

    for relative in profile["session"]["bins"]:
        path = root / relative.lstrip("/")
        want(path.is_file(), f"session binary exists: {relative}")
        if path.is_file():
            want(_mode_executable(path), f"session binary is executable: {relative}")

    dropin = display_dependency_dropin(context)
    if dropin is not None:
        path, _ = dropin
        want((root / path).is_file(), f"display dependency drop-in staged: {path}")

    for name in power_required(context):
        want((root / name).is_file(), f"power policy file staged: {name}")

    if require_enabled:
        for unit in enable_units(context):
            want(_unit_enabled(root, unit),
                 f"display manager unit enabled: {unit}")

    if problems:
        raise DesktopError(
            "rootfs does not match the desktop profile: " + "; ".join(problems))
    return checks


def _unit_enabled(root, unit):
    """`systemctl enable` 对这个单元留下的痕迹存在吗。

    大多数单元带 `WantedBy=`，启用后落一个 `*.wants/<unit>` 链接。GDM 只有
    `Alias=display-manager.service`，启用后落的是
    `etc/systemd/system/display-manager.service` 这个指向它的别名链接，
    `*.wants/` 下什么都不会有。只看 wants 目录会把正常装好的 GDM 判成未启用。
    """
    root = Path(root)
    if list(root.glob(f"etc/systemd/system/*.wants/{unit}")):
        return True
    alias = root / "etc/systemd/system/display-manager.service"
    if not alias.is_symlink():
        return False
    target = os.readlink(alias)
    name = Path(target).name
    return name == unit


def _mode_executable(path):
    try:
        return bool(Path(path).stat().st_mode & 0o111)
    except OSError:
        return False


def dnf_install_args(context, gpgkeys=None):
    """桌面档额外传给 dnf 的命令行参数（额外仓库、优先级与已校验的 key）。

    `build-clean-rootfs.py` 的 execute() 直接展开这个列表；base 的
    `dnf_options` 不在这里——它属于已删除的第二套后端
    （`build-fedora-rootfs.py`），而其中的 `--exclude=kernel*` 会把
    `kernel-piano` 一起挡掉。
    """
    return repo_args(context, gpgkeys)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("matrix", help="打印可构建的桌面档，一行一个")

    # build-rootfs.yml 的 prepare 步骤消费这个子命令：输出一行 JSON，
    # 直接喂给 strategy.matrix。--desktop all（或不填）展开全部档位。
    expand = sub.add_parser(
        "expand", help="把 --desktop 展开成 JSON 矩阵（all 表示全部桌面档）")
    expand.add_argument("--desktop", default="all")
    expand.add_argument("--releasever")

    show = sub.add_parser("show", help="打印合并后的配方")
    show.add_argument("--desktop")
    show.add_argument("--releasever")

    plan = sub.add_parser("plan", help="打印产物名与关键落盘路径")
    plan.add_argument("--desktop")
    plan.add_argument("--releasever")

    images = sub.add_parser(
        "images", help="打印每个可构建档位的镜像路径，制表符分隔 desktop<TAB>path")
    images.add_argument("--desktop")
    images.add_argument("--releasever")
    args = parser.parse_args(argv)

    try:
        if args.command == "matrix":
            print("\n".join(matrix()))
            return 0
        if args.command == "expand":
            known = load_json(DESKTOPS)
            if args.desktop in (None, "", "all"):
                names = matrix(desktops_only=True)
            else:
                if args.desktop not in known:
                    raise DesktopError(
                        f"unknown desktop {args.desktop!r}; known: {sorted(known)}")
                context = load(args.desktop, args.releasever)
                if not context["profile"]["buildable"]:
                    raise DesktopError(
                        f"desktop {args.desktop!r} is a placeholder and cannot "
                        "enter the matrix")
                names = [args.desktop]
            rows = []
            for name in names:
                context = load(name, args.releasever)
                names_for_desktop = artifact_names(context)
                rows.append({
                    "desktop": name,
                    "rootfs_artifact": names_for_desktop["rootfs_artifact"],
                    "rootfs_dir": names_for_desktop["rootfs_dir"],
                    "rootfs_tar": names_for_desktop["rootfs_tar"],
                    "image_artifact": names_for_desktop["image_artifact"],
                    "root_size_mib": names_for_desktop["root_size_mib"],
                })
            print(json.dumps(rows, ensure_ascii=False))
            return 0
        if args.command == "images":
            # shell 脚本的候选列表从这里生成，不手写桌面名。输出相对仓库
            # 根的路径，调用方自己拼 REPO_ROOT。
            rows = []
            for name in ([args.desktop] if args.desktop else matrix()):
                names = artifact_names(load(name, args.releasever))
                rows.append(f"{name}\tbuild/clean-distros/{names['rootfs_dir']}/{names['image']}")
            print("\n".join(rows))
            return 0
        context = load(args.desktop, args.releasever)
        names = artifact_names(context)
        if args.command == "show":
            print(json.dumps({
                "desktop": context["desktop"],
                "releasever": context["releasever"],
                "arch": context["arch"],
                "profile": context["profile"],
                "artifacts": names,
                "login_files": sorted(login_files(context, "piano")),
                "enable_units": enable_units(context),
                "display_dependency_dropin": (display_dependency_dropin(context) or [None])[0],
                "power_files": sorted(power_required(context)),
                "repo_args": repo_args(context),
                "gpgkey_pins": [{"repo": name, "sha256": digest}
                                for name, _url, digest in gpgkey_pins(context)],
                "dnf_install_args": dnf_install_args(context),
            }, indent=2, ensure_ascii=False))
            return 0
        print(json.dumps({
            "desktop": context["desktop"],
            "releasever": context["releasever"],
            "artifacts": names,
            "rootfs_packages": (context["profile"]["dnf_packages"] or []),
            "excluded": excluded_packages(context),
            "repo_args": repo_args(context),
        }, indent=2, ensure_ascii=False))
        return 0
    except DesktopError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
