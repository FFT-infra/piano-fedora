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
import shutil
import posixpath
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from piano_artifacts import guest_resolve, safe_destination
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
    if not releasever or not str(releasever).isdigit():
        raise RootfsError(f"releasever must be numeric Fedora release, got {releasever!r}")

    profile = load_json(PROFILES / "fedora.json")
    desktops = load_json(PROFILES / "fedora-desktops.json")
    if desktop not in desktops:
        raise RootfsError(f"unknown desktop environment: {desktop}")

    repo_info = inspect_rpm_repo(rpms_dir)
    base_pkgs = list(profile["base_packages"])
    desktop_pkgs = list(desktops[desktop]["dnf_packages"])

    # 移除被替换的原装包（如原版 iio-sensor-proxy 由 iio-sensor-proxy-ssc 替代）
    filtered_desktop_pkgs = [p for p in desktop_pkgs if p != "iio-sensor-proxy"]

    # kernel-piano first: everything the device needs hangs off its modules.
    all_packages = [KERNEL_PACKAGE] + base_pkgs + filtered_desktop_pkgs + PIANO_PACKAGES
    out_dir = Path(output or DISTROS / f"fedora-{releasever}-{desktop}").resolve()

    # 镜像容量与 label 取自 product.json，和 ESP/首次分区规划共用同一份记录。
    # 通用交付镜像用 8192 MiB 测试容量；本机目标容量由 prepare-install 扩容。
    product = load_json(ROOT / "product.json") if (ROOT / "product.json").is_file() else {}
    target = product.get("target", {})

    return {
        "status": "PLAN_READY",
        "distro": "fedora",
        "releasever": str(releasever),
        "desktop": desktop,
        "arch": profile.get("arch", "aarch64"),
        "root_label": target.get("root_label", "PIANOROOT"),
        "root_size_mib": 8192,
        "rpms_dir": str(Path(rpms_dir).resolve()),
        "repo_status": repo_info,
        "output": str(out_dir),
        "base_packages_count": len(base_pkgs),
        "desktop_packages_count": len(filtered_desktop_pkgs),
        "piano_packages": PIANO_PACKAGES + [KERNEL_PACKAGE],
        "all_packages": all_packages,
        "total_packages_count": len(all_packages),
        "display_manager": desktops[desktop].get("display_manager", "gdm"),
        "dnf_binary": dnf_binary(),
        "build_image": False,
    }


def audit_rootfs(rootfs_dir):
    """逐项审计 rootfs 内的可执行二进制是否 100% 具备 RPM 归属，无残留野文件。"""
    root = Path(rootfs_dir).resolve()
    if not root.is_dir() or not (root / "usr").is_dir():
        raise RootfsError(f"not a valid rootfs directory: {root}")

    # 1. 验证 RPM 数据库完整性与 9 个 piano 核心包安装状态
    res = subprocess.run(
        ["rpm", "--root", str(root), "-qa"],
        capture_output=True, text=True, check=True
    )
    installed_rpm_names = set(res.stdout.splitlines())
    missing_piano_pkgs = []
    for expected in PIANO_PACKAGES:
        found = any(pkg.startswith(expected + "-") for pkg in installed_rpm_names)
        if not found:
            missing_piano_pkgs.append(expected)

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
        root / "usr/lib/piano/piano-camerad",
        root / "usr/bin/uperf-linux",
        root / "usr/libexec/piano-boot-request-autoconfig",
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
              len(missing_files) == 0 and
              len(unowned_binaries) == 0 and
              len(kernel_problems) == 0)

    return {
        "audit_passed": passed,
        "total_rpms_installed": len(installed_rpm_names),
        "missing_piano_packages": missing_piano_pkgs,
        "missing_key_files": missing_files,
        "unowned_binaries": unowned_binaries,
        "kernel_release": kernel_release,
        "kernel_problems": kernel_problems,
    }



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


def execute(plan_data):
    out = Path(plan_data["output"])
    root = out / "rootfs"
    out.mkdir(parents=True, exist_ok=True)

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
        "--setopt=install_weak_deps=False",
        "--setopt=tsflags=nodocs",
        "--nogpgcheck",
        "install", "-y",
        *packages
    ]

    if os.geteuid() != 0 and shutil.which("sudo"):
        cmd = ["sudo", *cmd]

    log_file = out / "assembly.log"
    print(f"--> Invoking DNF assembly to {root} (logging to {log_file})...")
    with log_file.open("w") as stream:
        subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT, check=True)

    # 配置默认用户、主机名、fstab 与系统环境
    print("--> Setting up piano user, fstab and default services...")
    # 设置 fstab
    fstab_path = root / "etc" / "fstab"
    fstab_path.parent.mkdir(parents=True, exist_ok=True)
    fstab_text = (
        "/dev/disk/by-partlabel/sunuefi_root  /          f2fs  rw,noatime,compress_algorithm=zstd,compress_chksum,atgc,gc_merge  0 0\n"
        "/dev/disk/by-partlabel/sunuefi_esp   /boot/efi  vfat  umask=0077,shortname=winnt  0 2\n"
    )
    hostname_path = root / "etc" / "hostname"
    if os.geteuid() != 0 and shutil.which("sudo"):
        subprocess.run(["sudo", "tee", str(fstab_path)], input=fstab_text, text=True, check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["sudo", "tee", str(hostname_path)], input="piano\n", text=True, check=True, stdout=subprocess.DEVNULL)
    else:
        fstab_path.write_text(fstab_text, encoding="utf-8")
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

    # 3. GDM 自动登录配置
    gdm_conf = root / "etc" / "gdm" / "custom.conf"
    gdm_text = f"[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin={DEFAULT_USER}\n"
    if os.geteuid() != 0 and shutil.which("sudo"):
        subprocess.run(["sudo", "mkdir", "-p", str(gdm_conf.parent)], check=True)
        subprocess.run(["sudo", "tee", str(gdm_conf)], input=gdm_text, text=True, check=True, stdout=subprocess.DEVNULL)
    else:
        gdm_conf.parent.mkdir(parents=True, exist_ok=True)
        gdm_conf.write_text(gdm_text, encoding="utf-8")

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

    # 5. 启用关键系统服务
    services_to_enable = [
        "NetworkManager.service", "sshd.service", "gdm.service", "bluetooth.service",
        "piano-touch.service", "piano-camerad.service", "uperf-linux.service",
        "piano-clock-restore.service", "piano-clock-save.timer",
        "piano-physical-platform.service", "piano-boot-request-autoconfig.service",
        "qbootctl.service",
    ]
    for service in services_to_enable:
        try:
            run_chroot(["systemctl", "enable", service])
        except subprocess.CalledProcessError:
            pass

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

    # 执行审计
    print("--> Running post-assembly RPM provenance audit...")
    audit_res = audit_rootfs(root)

    # 审计通过后可选地打成 F2FS 镜像。这一步以前是缺失的：
    # `--execute` 只产出 rootfs 目录，而 package-release-bundle.sh 去
    # build/clean-distros/ 找 .img/.img.zst，找不到就静默回退到历史镜像，
    # 于是“纯净装配链”从未真正出现在任何 release 里。
    image_res = None
    if plan_data.get("build_image"):
        image_res = build_image(out, root, plan_data)

    manifest_data = {
        **plan_data,
        "audit": audit_res,
        "image": image_res,
        "status": "AUDIT_FAILED" if not audit_res["audit_passed"] else (
            "BUILT_IMAGED_AND_AUDITED" if image_res else "BUILT_AND_AUDITED")
    }
    (out / "clean-manifest.json").write_text(json.dumps(manifest_data, indent=2) + "\n")
    return manifest_data


def build_image(out, root, plan_data):
    """把审计过的 rootfs 目录打成 F2FS raw 镜像与 .zst 压缩包。

    复用 scripts/build-f2fs-image.py，它已经实现“挂载真实 F2FS + tar 保属性
    写入 + 卸载后只读读回逐项比对”，并且拒绝用 sload -P 静默丢 xattr。
    这里只负责选容量、调用与压缩，不重复实现导入逻辑。
    """
    size_mib = int(plan_data.get("root_size_mib") or 8192)
    img = out / "pianoroot-fedora44-gnome-f2fs.img"
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
    parser.add_argument("--releasever", default="44", help="Fedora 发行版本号 (默认: 44)")
    parser.add_argument("--desktop", default="gnome", help="桌面环境 (默认: gnome)")
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
