#!/usr/bin/env python3
"""纯净 DNF RootFS 装配工具：基于标准 Fedora 仓库与 piano-local RPM 矩阵构建 F2FS 根文件系统。

契约说明：
1. 彻底淘汰旧版的混杂 finalize 注入逻辑；
2. 系统内所有驱动、配置与服务均由标准 RPM (piano-fedora-configs, alsa-xiaomi-piano,
   xiaomi-piano-thp, piano-camera, sm8750-video-accel, uperf-linux, libssc,
   iio-sensor-proxy-ssc, xiaomi-piano-firmware) 规范化装配；
3. 具备完整的构建计划 (plan)、构建执行 (execute) 与 100% RPM 文件归属审计 (audit)。

用法：
    scripts/build-clean-rootfs.py --plan --releasever 44
    scripts/build-clean-rootfs.py --execute --releasever 44 --output build/clean-rootfs
    scripts/build-clean-rootfs.py --execute --releasever 44 --image \
        --output build/clean-distros/fedora-44-gnome
    scripts/build-clean-rootfs.py --audit-only --rootfs build/clean-rootfs
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import posixpath
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from piano_artifacts import guest_resolve, safe_destination
import piano_desktop
RPMS_DEFAULT = ROOT / "out" / "rpms"
PROFILES = ROOT / "scripts" / "profiles"
DISTROS = ROOT / "build" / "clean-distros"

PIANO_PACKAGES = [
    "piano-fedora-configs",
    "alsa-xiaomi-piano",
    "piano-camera",
    "xiaomi-piano-thp",
    "sm8750-video-accel",
    "uperf-linux",
    "libssc",
    "iio-sensor-proxy-ssc",
    "xiaomi-piano-firmware",
]

# The device runtime payload (piano-runtime) and the VM bits it depends on are
# built together by build-device-runtime and merged into the same local
# repository. They were listed here as "optional", so a clean rootfs silently
# shipped without the VM manager or crosvm and build-image then failed
# demanding them. A release rootfs needs them, so they are part of the install
# set. The VA-API backend is no longer built here: it comes from
# packages/sm8750-video-accel, and a second build under the old
# libva-v4l2-iris name owned the same driver path with different bytes.
RUNTIME_PACKAGES = [
    "piano-runtime",
    "librevm",
    "piano-crosvm",
]

# Superseded by piano-fedora-configs; kept out so an old package lingering in a
# repository cannot pull its files back in.
LEGACY_OPTIONAL_PACKAGES = [
    "piano-session-fixes",
    "libva-v4l2-iris",
]

# The device kernel is not in the Fedora repositories: it is packaged by
# packages/kernel-piano and installed from the same local repo. It used to be
# missing from this list, so a "clean" assembly produced a root filesystem with
# an empty /usr/lib/modules while the ESP booted a 7.2.9 kernel - the system
# came up with no drivers at all. It is required, not optional.
KERNEL_PACKAGE = "kernel-piano"

DEFAULT_USER = "piano"
DEFAULT_PASS = "piano123456"


class RootfsError(Exception):
    """装配或审计过程中的可预期错误。"""


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise RootfsError(f"failed to load JSON from {path}: {exc}") from exc


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def dnf_binary():
    for name in ("dnf5", "dnf"):
        found = shutil.which(name)
        if found:
            return found
    return None


def inspect_rpm_repo(rpms_dir):
    rpms_path = Path(rpms_dir).resolve()
    if not rpms_path.is_dir():
        return {"valid": False, "error": f"directory does not exist: {rpms_path}", "packages": {}}
    repodata = rpms_path / "repodata" / "repomd.xml"
    if not repodata.is_file():
        return {"valid": False, "error": f"missing repodata/repomd.xml in {rpms_path}", "packages": {}}

    required = PIANO_PACKAGES + [KERNEL_PACKAGE]
    found_pkgs = {}
    for p in sorted(rpms_path.glob("*.rpm")):
        name = p.name
        for expected in required:
            if name.startswith(expected + "-") or name == expected:
                found_pkgs[expected] = str(p)
                break

    missing = set(required) - set(found_pkgs.keys())
    return {
        "valid": len(missing) == 0,
        "missing": sorted(missing),
        "packages": found_pkgs,
        "repodata": str(repodata),
    }


def plan(releasever, desktop, rpms_dir, output=None):
    # 桌面差异（包集合、登录、服务、电源）全在 piano_desktop 里，这里只读
    # 一份合并后的上下文。desktop 未知、字段缺失或不可构建的档位在这里就
    # 报错，不会走到 dnf。releasever 与 desktop 缺省时由 product.json 给出；
    # 显式传空值仍然拒绝，不猜一个版本号。
    try:
        context = piano_desktop.load(desktop, releasever)
    except piano_desktop.DesktopError as exc:
        raise RootfsError(str(exc)) from exc
    if not context["profile"]["buildable"]:
        raise RootfsError(
            f"desktop {context['desktop']!r} is a placeholder with dnf_packages: null; "
            "it has no install set and must not produce an image")
    desktop = context["desktop"]
    releasever = context["releasever"]

    profile = context["base"]
    repo_info = inspect_rpm_repo(rpms_dir)
    base_pkgs = list(profile["base_packages"])
    desktop_pkgs = piano_desktop.desktop_packages(context)

    # kernel-piano first: everything the device needs hangs off its modules.
    all_packages = ([KERNEL_PACKAGE] + base_pkgs + desktop_pkgs
                    + PIANO_PACKAGES + RUNTIME_PACKAGES)
    out_dir = Path(output or DISTROS / f"fedora-{releasever}-{desktop}").resolve()
    artifacts = piano_desktop.artifact_names(context)

    return {
        "status": "PLAN_READY",
        "distro": "fedora",
        "releasever": str(releasever),
        "desktop": desktop,
        "arch": context["arch"],
        "root_label": context["root_label"],
        "root_size_mib": artifacts["root_size_mib"],
        "rpms_dir": str(Path(rpms_dir).resolve()),
        "repo_status": repo_info,
        "output": str(out_dir),
        "base_packages_count": len(base_pkgs),
        "desktop_packages_count": len(desktop_pkgs),
        "piano_packages": PIANO_PACKAGES + RUNTIME_PACKAGES + [KERNEL_PACKAGE],
        "all_packages": all_packages,
        "total_packages_count": len(all_packages),
        "artifact_names": artifacts,
        "display_manager": _display_manager_summary(context),
        "dnf_install_args": piano_desktop.dnf_install_args(context),
        "dnf_binary": dnf_binary(),
        "build_image": False,
    }


def _display_manager_summary(context):
    """plan JSON 里的显示管理器摘要（不再是一个裸字符串）。"""
    manager = context["profile"]["display_manager"]
    if manager is None:
        return None
    return {
        "unit": manager["unit"],
        "package": manager["package"],
        "autologin": manager["autologin"]["kind"],
        "label": context["profile"]["label"],
    }


def audit_rootfs(rootfs_dir):
    """逐项审计 rootfs 内的可执行二进制是否 100% 具备 RPM 归属，无残留野文件。"""
    root = Path(rootfs_dir).resolve()
    if not root.is_dir() or not (root / "usr").is_dir():
        raise RootfsError(f"not a valid rootfs directory: {root}")

    # 1. 验证 RPM 数据库完整性与 9 个 piano 核心包安装状态
    #    这里必须用 rpm 而不是直接读 sqlite：rpm 会打开 WAL 并看到最新事务。
    #    曾经直接按文件大小判断，结果镜像里的库是 0 张表（WAL 未合并就被删），
    #    而源目录一切正常。total_rpms_installed 为 0 时下面会显式失败。
    res = subprocess.run(
        ["rpm", "--root", str(root), "-qa"],
        capture_output=True, text=True, check=True
    )
    installed_rpm_names = set(res.stdout.splitlines())
    if not installed_rpm_names:
        raise RootfsError(
            f"rpm database at {root} reports no packages; the rootfs is unusable")
    installed_package_names = {
        re.match(r"(.+?)-\d", name).group(1)  # e.g. dbus-daemon-1.16...
        for name in installed_rpm_names
        if re.match(r"(.+?)-\d", name)
    }
    missing_piano_pkgs = []
    for expected in PIANO_PACKAGES:
        found = any(pkg.startswith(expected + "-") for pkg in installed_rpm_names)
        if not found:
            missing_piano_pkgs.append(expected)
    # These two subpackages are not pulled by any dependency closure when
    # install_weak_deps=0. They are the difference between a rootfs that
    # boots to a dark desktop and one that reaches all of mult-user/graphical.*
    session_packages = ("dbus-daemon", "systemd-pam")
    missing_session_pkgs = [name for name in session_packages
                             if name not in installed_package_names]

    # 2. 抽查关键系统文件与守护服务
    key_checks = [
        "usr/lib/piano/power-button",
        "usr/lib/piano/piano-ram-hardware-prepare",
        "usr/lib/piano/piano-disk-hardware-prepare",
        "usr/libexec/piano-boot-request-autoconfig",
        "usr/bin/piano-next-boot",
        "usr/bin/piano-thp-live",
        "usr/lib/piano/piano-camerad",
        "usr/bin/uperf-linux",
        "usr/lib64/libssc.so",
        "usr/lib64/dri/v4l2_drv_video.so",
        "usr/lib/systemd/system/piano-boot-request-autoconfig.service",
        "etc/systemd/system/qbootctl.service.d/piano.conf",
        # initramfs 在 chroot 之后立刻运行 piano-disk-hardware-prepare，
        # 它的 require_ram() 直接读 root-policy.json。缺这个文件，
        # 系统就停在 initramfs 救援 shell。
        "etc/piano/root-policy.json",
        "etc/udev/rules.d/01-piano-protect-android.rules",
        "etc/fstab",
        # dbus-daemon supplies dbus-daemon(1)/dbus-run-session(1); GDM's
        # gdm-wayland-session uses the latter to create a private session bus.
        "usr/bin/dbus-daemon",
        "usr/bin/dbus-run-session",
        # systemd-pam supplies pam_systemd.so; without it logind never
        # creates /run/user/<uid>, so the session bus cannot start.
        "usr/lib64/security/pam_systemd.so",
    ]
    missing_files = [f for f in key_checks if not (root / f).exists()]

    # The kernel must be present and its modules directory must match the
    # release the ESP will boot. An empty /usr/lib/modules produces a system
    # that boots and then has no drivers at all; that shipped once because
    # kernel-piano was missing from the package list.
    kernel_release = installed_kernel_release(root)
    kernel_problems = []
    if kernel_release is None:
        modules = root / "usr" / "lib" / "modules"
        found = sorted(p.name for p in modules.iterdir()) if modules.is_dir() else []
        kernel_problems.append(
            "expected exactly one kernel release under /usr/lib/modules, found "
            + (", ".join(found) if found else "none"))
    else:
        if not any((root / "usr" / "lib" / "modules" / kernel_release / name).is_file()
                   for name in ("modules.dep", "modules.builtin")):
            kernel_problems.append(
                f"/usr/lib/modules/{kernel_release} has no modules.dep or modules.builtin")
        if not any((root / "boot").glob("vmlinuz-*")):
            kernel_problems.append("/boot has no vmlinuz-*")

    # 3. 审计 /usr/bin 与 /usr/libexec 下的专有二进制所有权
    unowned_binaries = []
    audit_targets = [
        root / "usr/bin/piano-next-boot",
        root / "usr/bin/piano-thp-live",
        root / "usr/bin/piano-touch-view",
        root / "usr/lib/piano/piano-camerad",
        root / "usr/lib/piano/camera-start",
        root / "usr/lib/piano/touch-start",
        root / "usr/lib/piano/touch-input",
        root / "usr/lib/piano/pen-bluetooth",
        root / "usr/lib/piano/power-button",
        root / "usr/bin/uperf-linux",
        root / "usr/libexec/piano-boot-request-autoconfig",
        root / "usr/libexec/xiaomi-charger-mode",
    ]
    for target in audit_targets:
        if target.exists():
            rel_path = "/" + str(target.relative_to(root))
            q_res = subprocess.run(
                ["rpm", "--root", str(root), "-qf", rel_path],
                capture_output=True, text=True
            )
            if q_res.returncode != 0:
                unowned_binaries.append(rel_path)

    passed = (len(missing_piano_pkgs) == 0 and
              len(missing_session_pkgs) == 0 and
              len(missing_files) == 0 and
              len(unowned_binaries) == 0 and
              len(kernel_problems) == 0)

    # SELinux 标签数：DNF 不写 security.selinux，必须由 relabel 步骤补。
    # 这个数曾经是 0 而审计照样通过，因为没有任何一项检查覆盖它，
    # 结果 systemd 在 enforcing 下拒绝打标签并冻结。
    selinux_problems = []
    contexts = root / "etc" / "selinux" / "targeted" / "contexts" / "files" / "file_contexts"
    labeled = count_selinux_labels(root)
    if contexts.is_file() and labeled == 0:
        selinux_problems.append(
            "rootfs has file_contexts but no security.selinux xattrs; "
            "systemd would abort with 'Failed to mount API filesystems'")
    if selinux_problems:
        passed = False

    return {
        "audit_passed": passed,
        "total_rpms_installed": len(installed_rpm_names),
        "missing_piano_packages": missing_piano_pkgs,
        "missing_session_packages": missing_session_pkgs,
        "missing_key_files": missing_files,
        "unowned_binaries": unowned_binaries,
        "kernel_release": kernel_release,
        "kernel_problems": kernel_problems,
        "selinux_labels": labeled,
        "selinux_problems": selinux_problems,
    }



def count_selinux_labels(root):
    """统计 rootfs 里带 security.selinux xattr 的文件数。

    只做计数，不做匹配：这里要回答的是"标签有没有打"，不是"打得对不对"。
    打错由 relabel-selinux.py 的规则集和 tests 覆盖。
    """
    total = 0
    skip = {"dev", "proc", "sys", "run"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            try:
                if os.listxattr(os.path.join(dirpath, name),
                                follow_symlinks=False).count("security.selinux"):
                    total += 1
            except OSError:
                continue
    return total


def normalize_launcher_links(root):
    """收敛 Fedora 启动器中依赖 guest 根处 .. 截断的相对链接。"""
    binary = safe_destination(root, "usr/bin")
    changes = {}
    if not binary.is_dir():
        return changes
    for path in sorted(binary.iterdir()):
        if not path.is_symlink() or path.readlink().is_absolute():
            continue
        target, depth, escaped = str(path.readlink()), 2, False
        for part in Path(target).parts:
            if part == "..":
                escaped |= depth == 0
                depth = max(0, depth - 1)
            else:
                depth += 1
        if not escaped:
            continue
        parts = Path(target).parts
        leading = 0
        while leading < len(parts) and parts[leading] == "..":
            leading += 1
        if leading < 3 or parts[leading:leading + 2] != ("usr", "share") or ".." in parts[leading:]:
            raise ValueError("unexpected escaping launcher link: " + path.name)
        normalized = posixpath.normpath("/usr/bin/" + target)
        resolved = guest_resolve(root, normalized)
        if not normalized.startswith("/usr/share/") or not resolved.is_relative_to(root / "usr/share") or not resolved.is_file():
            raise ValueError("unexpected escaping launcher link: " + path.name)
        relative = posixpath.relpath(normalized, "/usr/bin")
        path.unlink()
        path.symlink_to(relative)
        changes[path.relative_to(root).as_posix()] = {"original": target, "target": relative}
    return changes


def prune_missing_manpage_links(root):
    """nodocs 会留下 alternatives 与 man 目录两端的手册链接。"""
    missing = []
    for relative in ("etc/alternatives", "usr/share/man"):
        folder = safe_destination(root, relative)
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.is_symlink():
                target = guest_resolve(root, "/" + path.relative_to(root).as_posix())
                if target.is_relative_to(root / "usr/share/man") and not target.exists():
                    missing.append(path)
    for path in missing:
        path.unlink()
    return [p.relative_to(root).as_posix() for p in missing]


def prune_kernel_module_links(root):
    """清理内核安装时留下的宿主构建树软链接。"""
    modules_dir = root / "usr" / "lib" / "modules"
    removed = []
    if modules_dir.is_dir():
        for rel_dir in modules_dir.iterdir():
            if rel_dir.is_dir():
                for link_name in ("build", "source"):
                    link_p = rel_dir / link_name
                    if link_p.is_symlink():
                        target = os.readlink(link_p)
                        if target.startswith(("/home/", "/tmp/")) or not link_p.exists():
                            link_p.unlink()
                            removed.append(link_p.relative_to(root).as_posix())
    return removed


def prune_rpm_transient(root):
    """把 rpm 数据库的 WAL 合并进主库，再删掉临时文件。

    rpmdb 是 SQLite，查询时会生成 -shm/-wal 和 .rpm.lock。审计步骤用
    `rpm --root` 打开数据库，这些文件随之出现又消失；如果它们被打进镜像，
    读回比对就会因为 mtime 变化报
    "rootfs metadata/content mismatch: ...rpmdb.sqlite-shm: mtime_ns"。

    但不能直接删：如果 -wal 里还有未合并的事务，删掉就等于丢数据。
    实测过这个坑——镜像里的 rpmdb.sqlite 变成 0 张表，源目录却是 50 张。
    所以先跑一次 wal_checkpoint(TRUNCATE)，确认合并成功再删。
    """
    rpm_dir = root / "usr" / "lib" / "sysimage" / "rpm"
    database = rpm_dir / "rpmdb.sqlite"
    if not database.is_file():
        return
    import sqlite3
    connection = sqlite3.connect(database)
    try:
        # TRUNCATE 会把 WAL 内容写回主库并把 WAL 截断为零长度。
        busy, remaining, checkpointed = connection.execute(
            "PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy or remaining:
            raise RootfsError(
                f"rpm database WAL could not be checkpointed "
                f"(busy={busy}, remaining={remaining}); refusing to drop it "
                "because that would lose package records")
        tables = connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if tables == 0:
            raise RootfsError(
                "rpm database has no tables after checkpoint; the image would "
                "contain no package records")
    finally:
        connection.close()
    for name in ("rpmdb.sqlite-shm", "rpmdb.sqlite-wal", ".rpm.lock"):
        target = rpm_dir / name
        if target.exists() or target.is_symlink():
            target.unlink()


def sanitize_rootfs_identity(root):
    """重置机器唯一 ID，确保镜像不带特定主机身份。"""
    machine = root / "etc" / "machine-id"
    if machine.exists():
        machine.chmod(0o644)
        machine.write_text("", encoding="utf-8")
        machine.chmod(0o444)
    dbus = root / "var" / "lib" / "dbus" / "machine-id"
    dbus.parent.mkdir(parents=True, exist_ok=True)
    if dbus.exists() or dbus.is_symlink():
        dbus.unlink()
    dbus.symlink_to("/etc/machine-id")


def require_local_installroot(out):
    """确认输出目录所在文件系统允许 root 在里面建目录。

    dnf --installroot 以 root 身份解包，filesystem 包会创建 /afs、/var/tmp
    这类顶层目录。如果输出目录落在 virtiofs（macOS 共享）或 9p 上，
    这些 mkdir 会返回 EACCES/EBUSY，filesystem 包整体安装失败，
    它拥有的 /var/tmp 也就不存在，随后几十个 scriptlet 连锁报
    "error creating temporary file /var/tmp/rpm-tmp.XXXX"。
    失败信息离原因很远，所以在这里提前挡掉。
    """
    probe = out / ".installroot-probe"
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe.mkdir()
        probe.rmdir()
    except OSError as exc:
        raise RootfsError(
            f"output directory {out} cannot host an installroot: {exc}. "
            "dnf --installroot must run on a local filesystem; virtiofs and "
            "9p shares reject the directory creation that the filesystem "
            "package performs. Pass --output with a path on local disk."
        ) from exc


def execute(plan_data):
    out = Path(plan_data["output"])
    root = out / "rootfs"
    # 桌面上下文在 execute 里重新取一份：登录、服务、电源都由它派生。
    try:
        context = piano_desktop.load(plan_data["desktop"], plan_data["releasever"])
    except piano_desktop.DesktopError as exc:
        raise RootfsError(str(exc)) from exc
    out.mkdir(parents=True, exist_ok=True)
    require_local_installroot(out)

    # DNF 事务失败时（历史上真发生过：Obsoletes 自匹配）会留下一个半装状态
    # 的 installroot。旧代码直接复用这个目录，于是第二次运行会在残骸上叠装，
    # 报出难以理解的冲突。除非显式 --resume，否则先清空再装。
    if root.exists() and any(root.iterdir()):
        if not plan_data.get("resume"):
            print(f"--> Removing stale installroot at {root}")
            shutil.rmtree(root)
        else:
            print(f"--> Reusing existing installroot at {root} (--resume)")
    root.mkdir(parents=True, exist_ok=True)

    # RPM scriptlet 在首次安装时就要往 installroot 里写临时文件。空目录没有
    # /var/tmp，dnf 会在 unpack 阶段就报一堆
    # "error creating temporary file /var/tmp/rpm-tmp.XXXX: No such file or
    # directory"，最后以 transaction failed 收场。先建好这几个目录再装。
    bootstrap_dirs = ("var/tmp", "var/log", "run", "tmp")
    for name in bootstrap_dirs:
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "tmp").chmod(0o1777)
    (root / "var/tmp").chmod(0o1777)

    dnf = dnf_binary()
    if not dnf:
        raise RootfsError("dnf or dnf5 is required for rootfs assembly")

    rpms_dir = plan_data["rpms_dir"]
    releasever = plan_data["releasever"]
    packages = plan_data.get("all_packages", plan_data["piano_packages"])

    cmd = [
        dnf,
        f"--installroot={root}",
        "--use-host-config",
        f"--releasever={releasever}",
        f"--repofrompath=piano-local,{rpms_dir}",
        # The piano repository must win every name it provides. Fedora 44 updates
        # carries mesa-26.2.4-1.fc44, which sorts above the locally built
        # 26.2.3-1.piano1.fc44; without a priority dnf installed the distro Mesa
        # and finalize then rejected the image because the package was not the
        # verified A830v1 build. A lower value means a higher priority.
        "--setopt=piano-local.priority=1",
        "--setopt=install_weak_deps=False",
        "--setopt=tsflags=nodocs",
        "--nogpgcheck",
        # 桌面档需要额外仓库（目前只有 Hyprland 档用到第三方 COPR）。
        # plan JSON 里报的也是同一份列表，两处不会分叉。
        *piano_desktop.dnf_install_args(context),
        "install", "-y",
        *packages
    ]

    if os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo", *cmd]

    log_file = out / "assembly.log"
    print(f"--> Invoking DNF assembly to {root} (logging to {log_file})...")
    with log_file.open("w") as stream:
        subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT, check=True)

    # 配置默认用户、主机名与系统环境。fstab 由后面的 apply-rootfs-policy 写，
    # 这里不再重复一份，避免两处内容漂移（这里曾经写的是 compress_algorithm
    # 版本，策略脚本写的是 data_flush 版本）。
    print("--> Setting up piano user, SELinux mode and default services...")
    hostname_path = root / "etc" / "hostname"
    if os.geteuid() != 0 and shutil.which("sudo"):
        subprocess.run(["sudo", "tee", str(hostname_path)], input="piano\n", text=True, check=True, stdout=subprocess.DEVNULL)
    else:
        hostname_path.parent.mkdir(parents=True, exist_ok=True)
        hostname_path.write_text("piano\n", encoding="utf-8")

    # 辅助 chroot 执行工具
    def run_chroot(cmd_args, stdin_text=None):
        base_cmd = ["chroot", str(root), *cmd_args]
        if os.geteuid() != 0 and shutil.which("sudo"):
            base_cmd = ["sudo", *base_cmd]
        return subprocess.run(base_cmd, input=stdin_text, text=True, check=True, capture_output=True)

    # 1. 确保 piano 用户创建并分配辅助组
    print(f"--> Configuring user {DEFAULT_USER}...")
    check_user_cmd = ["chroot", str(root), "id", DEFAULT_USER]
    if os.geteuid() != 0 and shutil.which("sudo"):
        check_user_cmd = ["sudo", *check_user_cmd]
    if subprocess.run(check_user_cmd, capture_output=True).returncode != 0:
        run_chroot([
            "useradd", "--create-home",
            "--groups", "wheel,audio,video,input,render",
            "--shell", "/bin/bash",
            DEFAULT_USER
        ])
    run_chroot(["groupadd", "--force", "--system", "librevm"])
    run_chroot(["usermod", "--append", "--groups", "librevm,audio,video,input,render", DEFAULT_USER])

    # 2. 设置用户密码
    run_chroot(["/usr/sbin/chpasswd"], stdin_text=f"{DEFAULT_USER}:{DEFAULT_PASS}\n")

    # 3. 自动登录：文件位置与内容由桌面档决定（GDM custom.conf / SDDM
    #    autologin / greetd config.toml）。原来这里硬编码 GDM，加第二个桌面
    #    就得在两个后端各改一处。
    print(f"--> Staging {context['desktop']} autologin configuration...")
    staged_login = piano_desktop.stage_login(root, context, DEFAULT_USER, sudo_aware=False)

    # 4. 屏蔽未适配的磁盘休眠目标，锁定为 deep 挂起
    for unit in ("hibernate.target", "hybrid-sleep.target", "suspend-then-hibernate.target"):
        target_unit = root / "etc" / "systemd" / "system" / unit
        if os.geteuid() != 0 and shutil.which("sudo"):
            subprocess.run(["sudo", "mkdir", "-p", str(target_unit.parent)], check=True)
            subprocess.run(["sudo", "ln", "-sf", "/dev/null", str(target_unit)], check=True)
        else:
            target_unit.parent.mkdir(parents=True, exist_ok=True)
            if target_unit.is_symlink() or target_unit.exists():
                target_unit.unlink()
            target_unit.symlink_to("/dev/null")

    # 5. 启用关键系统服务。显示管理器由桌面档提供，不在这个固定列表里。
    services_to_enable = [
        "NetworkManager.service", "sshd.service", "bluetooth.service",
        *piano_desktop.enable_units(context),
        "piano-touch.service", "piano-camerad.service", "uperf-linux.service",
        "piano-clock-restore.service", "piano-clock-save.timer",
        "piano-physical-platform.service", "piano-boot-request-autoconfig.service",
        "qbootctl.service",
    ]
    for service in services_to_enable:
        try:
            run_chroot(["systemctl", "enable", service])
        except subprocess.CalledProcessError:
            # 显示管理器没有单元就是一台起不来的机器，不能和其它单元一样
            # 静默吞掉；其余单元缺了由 finalize 的必装包与本次审计兜底。
            if service in piano_desktop.enable_units(context):
                raise RootfsError(
                    f"cannot enable the {context['desktop']} display manager: {service}")
            pass

    # 显示依赖：显示管理器必须等面板真正就绪之后再起。
    dropin = piano_desktop.display_dependency_dropin(context)
    if dropin is not None:
        dropin_path, dropin_text = dropin
        target = root / dropin_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dropin_text, encoding="utf-8")

    # 电源策略：KDE 的 powerdevilrc 由这里落盘；GNOME 的 dconf 文件由
    # piano-fedora-configs 这个 RPM 安装，构建器不写第二个写者；
    # /etc/dconf/profile/user 同样由 dconf 包自带（已含 system-db:local）。
    power = piano_desktop.power_files(context)
    for relative, text in power.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    if piano_desktop.dconf_profile_required(context):
        profile_path = root / "etc" / "dconf" / "profile" / "user"
        if not profile_path.is_file() or "system-db:local" not in {
                line.strip() for line in profile_path.read_text().splitlines()}:
            raise RootfsError(
                "the installed dconf profile does not include system-db:local; "
                "the GNOME power overlay would be ignored")

    # 根分区策略：/etc/fstab、/etc/piano/root-policy.json 与 udev 只读保护规则。
    # 旧流水线（build-image.yml）在 stage-rootfs-payload 之后调
    # apply-rootfs-policy.py；纯净装配链一直没有这一步，于是镜像里既没有
    # root-policy.json 也没有保护规则。piano-ram-hardware-prepare 的
    # require_ram() 第一件事就是读 root-policy.json，读不到就 REFUSED，
    # initramfs 的 chroot 因此失败并停在救援 shell。
    print("--> Applying root partition policy (fstab, root-policy.json, udev rules)...")
    policy_cmd = [sys.executable, str(ROOT / "scripts" / "apply-rootfs-policy.py"),
                  "--rootfs", str(root)]
    if os.geteuid() != 0 and shutil.which("sudo"):
        policy_cmd = ["sudo", *policy_cmd]
    subprocess.run(policy_cmd, check=True)

    # 清空 DNF/chroot 留下的运行时树。它们不是镜像内容：/dev 下会出现
    # 普通文件 null，/run 下会出现 dnf、sshd、selinux-policy 等宿主状态。
    # package_release.py 会（正确地）拒绝这种树，F2FS 镜像也不应带它们。
    print("--> Clearing transient /dev, /proc, /sys and /run entries...")
    for runtime_dir in ("dev", "proc", "sys", "run"):
        runtime_path = root / runtime_dir
        runtime_path.mkdir(parents=True, exist_ok=True)
        for child in list(runtime_path.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()

    print("--> Normalizing launcher symlinks, pruning dangling links and sanitizing identity...")
    normalize_launcher_links(root)
    prune_missing_manpage_links(root)
    prune_kernel_module_links(root)
    sanitize_rootfs_identity(root)
    prune_rpm_transient(root)

    # SELinux 标签：DNF 只写文件内容，不写 security.selinux xattr。旧流水线
    # （build-image.yml）在 staging 之后跑一次 setfiles；这条装配链漏了那一步，
    # 产出的镜像 selinux_labels=0，systemd 在 enforcing 下给 /dev/shm、
    # /dev/pts、/sys/fs/* 打标签时被拒，直接 "Failed to mount API
    # filesystems" 并冻结。setfiles 是 aarch64 二进制，交叉装配时不可用，
    # 所以用等价的 Python 实现按 file_contexts 逐条写。
    contexts = root / "etc" / "selinux" / "targeted" / "contexts" / "files" / "file_contexts"
    if contexts.is_file():
        print("--> Applying SELinux labels from file_contexts...")
        relabel = ROOT / "scripts" / "relabel-selinux.py"
        cmd = [sys.executable, str(relabel), str(root), str(contexts)]
        if os.geteuid() != 0 and shutil.which("sudo"):
            cmd = ["sudo", *cmd]
        subprocess.run(cmd, check=True)
        # Relabeling replaces the filesystem metadata the image ships. Any
        # pre-existing .autorelabel marker would otherwise force a second,
        # unbounded SELinux relabel on first boot and hide a broken build.
        (root / ".autorelabel").unlink(missing_ok=True)
    else:
        print("--> NOTE: no file_contexts found; skipping SELinux labeling")

    # 桌面配方核对：登录单元、会话 .desktop、会话二进制、电源文件、显示依赖
    # 都要真的在树里。这一步是"待核实项"的落地处——SDDM 的 Session= 值不靠
    # 猜，树里没有 plasma.desktop 就在这里带路径报错。
    print(f"--> Validating the {context['desktop']} profile against the assembled rootfs...")
    try:
        desktop_checks = piano_desktop.validate_against_rootfs(
            root, context, require_enabled=False)
    except piano_desktop.DesktopError as exc:
        raise RootfsError(str(exc)) from exc

    # 执行审计
    print("--> Running post-assembly RPM provenance audit...")
    audit_res = audit_rootfs(root)

    # audit_rootfs 用 `rpm --root` 打开数据库，会重新产生 -wal/-shm。这些文件
    # 没合并就打包出去，别的机器上 `rpm --root` 读出来是 0 个包（实测
    # build-image 因此判定所有 release 包缺失）。审计之后再 checkpoint 一次。
    print("--> Checkpointing the rpm database after the audit...")
    prune_rpm_transient(root)

    # 审计通过后可选地打成 F2FS 镜像。这一步以前是缺失的：
    # `--execute` 只产出 rootfs 目录，而 package-release-bundle.sh 去
    # build/clean-distros/ 找 .img/.img.zst，找不到就静默回退到历史镜像，
    # 于是“纯净装配链”从未真正出现在任何 release 里。
    image_res = None
    if plan_data.get("build_image"):
        image_res = build_image(out, root, plan_data, context)

    manifest_data = {
        **plan_data,
        "audit": audit_res,
        "desktop_validation": desktop_checks,
        "image": image_res,
        "status": "AUDIT_FAILED" if not audit_res["audit_passed"] else (
            "BUILT_IMAGED_AND_AUDITED" if image_res else "BUILT_AND_AUDITED")
    }
    (out / "clean-manifest.json").write_text(json.dumps(manifest_data, indent=2) + "\n")
    return manifest_data


def build_image(out, root, plan_data, context):
    """把审计过的 rootfs 目录打成 F2FS raw 镜像与 .zst 压缩包。

    复用 scripts/build-f2fs-image.py，它已经实现“挂载真实 F2FS + tar 保属性
    写入 + 卸载后只读读回逐项比对”，并且拒绝用 sload -P 静默丢 xattr。
    这里只负责选容量、调用与压缩，不重复实现导入逻辑。
    """
    size_mib = int(plan_data.get("root_size_mib") or 8192)
    artifacts = piano_desktop.artifact_names(context)
    img = out / artifacts["image"]
    # The name matters: package-release-bundle.sh reads root-image-manifest.json
    # from the image's directory to cross-check that the ESP kernel and the
    # rootfs modules are the same build. Writing only image-manifest.json left
    # that check silently skipped.
    manifest = out / "root-image-manifest.json"
    tool = ROOT / "scripts" / "build-f2fs-image.py"
    if not tool.is_file():
        raise RootfsError(f"missing image builder: {tool}")
    if img.exists():
        # 重新装配时不要复用上一次的镜像：容量或内容可能已经变了。
        img.unlink()

    print(f"--> Building F2FS image ({size_mib} MiB) from {root}...")
    cmd = [
        sys.executable, str(tool),
        "--rootfs", str(root),
        "--size-mib", str(size_mib),
        "--label", plan_data.get("root_label", "PIANOROOT"),
        "--output", str(img),
        "--verify",
        "--manifest", str(manifest),
    ]
    # 挂载式导入需要 root；容器或 CI 里通常已经是 root。
    if os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo", *cmd]
    subprocess.run(cmd, check=True)

    zst = Path(str(img) + ".zst")
    if not shutil.which("zstd"):
        raise RootfsError("zstd is required to publish the rootfs image")
    print(f"--> Compressing {img.name} -> {zst.name}...")
    subprocess.run(["zstd", "-T0", "-19", "-f", str(img), "-o", str(zst)], check=True)

    record = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else {}
    kernel_release = installed_kernel_release(root)
    if kernel_release:
        record["kernel_release"] = kernel_release
    # 桌面维度从这一层就要进记录：build-image 与 release 都靠它核对
    # root 镜像和它声明的桌面档是不是同一份。
    record["desktop"] = context["desktop"]
    record["releasever"] = context["releasever"]
    record["desktop_profile"] = {
        "label": context["profile"]["label"],
        "display_manager": _display_manager_summary(context),
        "session_files": context["profile"]["session"]["session_files"],
        "power_policy": context["profile"]["power_policy"]["kind"],
    }
    manifest.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    # Keep the historical filename as a copy so existing readers still work.
    (out / "image-manifest.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return {
        "image": str(img),
        "image_bytes": img.stat().st_size,
        "image_sha256": sha256(img),
        "zst": str(zst),
        "zst_bytes": zst.stat().st_size,
        "zst_sha256": sha256(zst),
        "size_mib": size_mib,
        "kernel_release": kernel_release,
        "desktop": context["desktop"],
        "verify": record.get("verify"),
        "status": record.get("status"),
    }


def installed_kernel_release(root):
    """Return the single kernel release installed in the rootfs, or None.

    The ESP boots one specific kernel release and the modules must carry the
    same string or nothing loads. An empty /usr/lib/modules or more than one
    release means the image is not a matched set; both are reported loudly by
    the caller instead of being papered over.
    """
    modules = Path(root) / "usr" / "lib" / "modules"
    if not modules.is_dir():
        return None
    releases = sorted(p.name for p in modules.iterdir() if p.is_dir())
    if len(releases) == 1:
        return releases[0]
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--releasever", help="Fedora 发行版本号（默认读 product.json）")
    parser.add_argument("--desktop", help="桌面档（默认读 product.json 的 target.desktop）")
    parser.add_argument("--rpms-dir", default=str(RPMS_DEFAULT), help="本地 RPM 仓库目录")
    parser.add_argument("--output", help="产物输出路径")
    parser.add_argument("--rootfs", help="用于 audit-only 的已安装 rootfs 路径")
    parser.add_argument("--image", action="store_true",
                        help="装配后额外产出 F2FS raw 镜像与 .zst（需要 root 挂载权限）")
    parser.add_argument("--size-mib", type=int,
                        help="镜像容量 MiB（默认 8192，即通用交付测试容量）")
    parser.add_argument("--resume", action="store_true",
                        help="复用已存在的 installroot，不先清空（默认清空，避免在失败残骸上叠装）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="打印组装计划与本地 RPM 源验证")
    mode.add_argument("--execute", action="store_true", help="执行装配并生成 clean rootfs")
    mode.add_argument("--audit-only", action="store_true", help="只审计目标 rootfs 的 RPM 文件归属")
    args = parser.parse_args()

    try:
        if args.plan:
            p = plan(args.releasever, args.desktop, args.rpms_dir, args.output)
            print(json.dumps(p, indent=2, ensure_ascii=False))
            return 0
        elif args.execute:
            p = plan(args.releasever, args.desktop, args.rpms_dir, args.output)
            if not p["repo_status"]["valid"]:
                raise RootfsError(f"RPM repository is invalid: {p['repo_status']}")
            p["build_image"] = bool(args.image)
            if args.size_mib:
                p["root_size_mib"] = args.size_mib
            p["resume"] = bool(args.resume)
            # 整个 execute() 必须以 root 跑。dnf 用 sudo 建出来的文件属 root，
            # 后续清理 /dev、写 policy、成像再读回都要 root 权限；否则会在
            # "Clearing transient /dev..." 处 Permission denied。
            # 用 re-exec 而不是局部提权，避免同一份逻辑在两种权限下分叉。
            if os.geteuid() != 0 and shutil.which("sudo"):
                os.execvp("sudo", ["sudo", sys.executable, *sys.argv])
            res = execute(p)
            print(json.dumps({
                "status": res["status"],
                "output": res["output"],
                "audit_passed": res["audit"]["audit_passed"],
                "total_rpms": res["audit"]["total_rpms_installed"],
                "image": (res.get("image") or {}).get("image"),
                "image_sha256": (res.get("image") or {}).get("image_sha256"),
            }, indent=2, ensure_ascii=False))
            return 0
        elif args.audit_only:
            if not args.rootfs:
                raise RootfsError("--rootfs must be provided for --audit-only")
            res = audit_rootfs(args.rootfs)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0 if res["audit_passed"] else 1
    except RootfsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"ERROR: command execution failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
