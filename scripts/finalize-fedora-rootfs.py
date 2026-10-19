#!/usr/bin/env python3
"""完成新构建的 Fedora rootfs：匹配模块、服务、身份清理和最终 manifest。"""

import argparse
import configparser
import json
import os
import posixpath
import shutil
import subprocess
import time
from pathlib import Path

from piano_artifacts import guest_resolve, inspect_kernel, safe_destination, safe_relative, sha256

ROOT = Path(__file__).resolve().parents[1]

# 上游 1b3c538 的默认登录策略：piano 用文档化默认口令，root 保持锁定、不加
# NOPASSWD。口令只在 finalizer 定义一次，测试与 manifest proof 都引用它。
DEFAULT_PIANO_LOGIN = "piano"
DEFAULT_PIANO_PASSWORD = "piano123456"

# 上游 piano-debug-bootstrap 在 NCM gadget 上使用的固定 dev MAC。NM profile 只按
# 这个 MAC 匹配，避免绑定可能被内核改名的 usb0。
PIANO_DEBUG_NCM_MAC = "02:87:50:00:00:01"
PIANO_DEBUG_NCM_ADDRESS = "192.168.77.1/30"
PIANO_DEBUG_NM_UUID = "5b1c9a2e-6f3d-4a71-8c2b-0d4e5f607182"

# 重建 FFmpeg 只装运行时库和 CLI；-devel 与 freeworld 不进设备镜像。集合必须
# 恰好等于这 8 个，空集合或漏包都要拒绝，否则 NEVRA 校验会被空清单绕过。
FFMPEG_PACKAGES = {"ffmpeg-free", "libavcodec-free", "libavdevice-free", "libavfilter-free",
                   "libavformat-free", "libavutil-free", "libswresample-free", "libswscale-free"}


def chroot(root, *command):
    return subprocess.check_output(["chroot", str(root), *command], text=True)


def login_password_input(user=DEFAULT_PIANO_LOGIN, password=DEFAULT_PIANO_PASSWORD):
    """chpasswd 的 stdin：user:password 后跟一个真实换行（不是字面 \\n）。"""
    return f"{user}:{password}\n"


def set_login_password(root, user=DEFAULT_PIANO_LOGIN, password=DEFAULT_PIANO_PASSWORD):
    """在目标树里用 chpasswd 设置默认口令；不碰 root，也不加 NOPASSWD。"""
    subprocess.run(["chroot", str(root), "/usr/sbin/chpasswd"],
                   input=login_password_input(user, password), text=True, check=True)


def shadow_password(root, user):
    """读目标树 /etc/shadow 里某个账号的口令字段。"""
    for line in safe_destination(root, "etc/shadow").read_text().splitlines():
        parts = line.split(":")
        if parts and parts[0] == user and len(parts) >= 2:
            return parts[1]
    raise ValueError("missing shadow entry for " + user)


def password_locked(field):
    """shadow 口令字段为空或以 !/* 开头即视为锁定。"""
    return field == "" or field[0] in "!*"


def stage_linux_debug_handoff(root):
    """成功交接后的 debug 消费者：piano-linux-debug + Fedora 原生 ttyGS0 getty。

    正常 root 不提供免密 root shell：linux-debug.conf 用 shell=0，串口 getty 走
    系统默认的账户/口令认证，不启用上游自动 root shell 的 piano-linux-serial。
    NCM 静态地址由 NetworkManager 按 gadget 固定 dev MAC 匹配，不改接口名、不
    DHCP、不设默认路由。
    """
    files = {
        # 串口 getty 实例只在 piano-linux-debug 建立 gadget 之后启动，并保留登录认证。
        "etc/systemd/system/serial-getty@ttyGS0.service.d/20-piano-debug.conf":
            "[Unit]\nRequires=piano-linux-debug.service\nAfter=piano-linux-debug.service\n",
        # 上游 opt-in 配置：正常 root 不提供免密 root shell。
        "etc/piano/linux-debug.conf":
            "usb=acm-ncm\nshell=0\n"
            f"ipv4={PIANO_DEBUG_NCM_ADDRESS}\nrecovery_seconds=0\n",
        # 只按 gadget 固定 dev MAC 匹配，避免绑定可能被改名的 usb0。
        "etc/NetworkManager/system-connections/piano-debug-ncm.nmconnection":
            "[connection]\n"
            "id=piano-debug-ncm\n"
            f"uuid={PIANO_DEBUG_NM_UUID}\n"
            "type=ethernet\n"
            "autoconnect=true\n"
            "\n"
            "[ethernet]\n"
            f"mac-address={PIANO_DEBUG_NCM_MAC}\n"
            "\n"
            "[ipv4]\n"
            "method=manual\n"
            f"address1={PIANO_DEBUG_NCM_ADDRESS}\n"
            "never-default=true\n"
            "\n"
            "[ipv6]\n"
            "method=disabled\n",
    }
    modes = {"etc/piano/linux-debug.conf": 0o600,
             "etc/NetworkManager/system-connections/piano-debug-ncm.nmconnection": 0o600}
    for name, text in files.items():
        target = safe_destination(root, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(modes.get(name, 0o644))
    # 启用 debug stage 与原生 ttyGS0 getty（不启用上游自动 root shell service）。
    wants = {
        "etc/systemd/system/multi-user.target.wants/piano-linux-debug.service":
            "/usr/lib/systemd/system/piano-linux-debug.service",
        "etc/systemd/system/getty.target.wants/serial-getty@ttyGS0.service":
            "/usr/lib/systemd/system/serial-getty@.service",
    }
    for name, destination in wants.items():
        link = safe_destination(root, name)
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() and str(link.readlink()) == destination:
            continue
        if link.exists() or link.is_symlink():
            raise ValueError("existing debug enable link differs: " + name)
        link.symlink_to(destination)
    return sorted([*files, *wants])


def normalize_launcher_links(root):
    """收敛 Fedora 启动器中依赖 guest 根处 .. 截断的相对链接。"""
    binary = safe_destination(root, "usr/bin")
    changes = {}
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
    # 先解析完整链再删除，避免删除 alternatives 后隐藏 man 端的最终目标。
    removed = sorted(path.relative_to(root).as_posix() for path in missing)
    for path in missing:
        path.unlink()
    return removed


def stage_boot_diagnostics(root):
    """Fedora 的 GDM 单元名不同于 Debian；复用上游相同的显示依赖。"""
    files = {
        "etc/systemd/system/gdm.service.d/20-piano-display.conf":
            "[Unit]\nRequires=piano-display.service\nAfter=piano-display.service\n",
        "etc/systemd/system/piano-camerad.service.d/20-camera-dependency.conf":
            "[Unit]\nWants=piano-camera.service\nAfter=piano-camera.service\n"
            "StartLimitIntervalSec=60\nStartLimitBurst=3\n",
        "etc/systemd/system/piano-touch.service.d/20-fast-restart.conf":
            "[Service]\nRestartSec=1\n",
        "etc/piano/camera.conf":
            "PIANO_CAMERA_OUTPUT=nv12\n",
        "etc/wireplumber/wireplumber.conf.d/50-piano-camera.conf":
            "# Applications get the cameras from the v4l2loopback devices that\n"
            "# piano-camerad feeds with the hardware ISP output. The CAMSS capture nodes\n"
            "# belong to the daemon, and libcamera must not claim the sensors.\n"
            "wireplumber.profiles = {\n"
            "  main = {\n"
            "    monitor.libcamera = disabled\n"
            "  }\n"
            "}\n"
            "monitor.v4l2.rules = [\n"
            "  {\n"
            "    matches = [ { device.product.name = \"Qualcomm Camera Subsystem\" } ]\n"
            "    actions = { update-props = { device.disabled = true } }\n"
            "  }\n"
            "]\n",
        "etc/udev/rules.d/70-piano-camera.rules":
            "# libcamera allocates frame buffers from the system DMA heap (see\n"
            "# /usr/lib/piano/camera-start); let video group members and the active\n"
            "# session user open it.\n"
            'SUBSYSTEM=="dma_heap", KERNEL=="system", GROUP="video", MODE="0660", TAG+="uaccess"\n',
        "etc/udev/rules.d/70-piano-power-button.rules":
            "# Let the active session user open the power key and the keyboard-cover\n"
            "# lid switch: the piano-power-button user service owns both (see\n"
            "# /usr/lib/piano/power-button).  The power key is grabbed; the lid switch\n"
            "# is only read, so its state stays visible to logind and GNOME.\n"
            'SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="pmic_pwrkey", TAG+="uaccess"\n'
            'SUBSYSTEM=="input", KERNEL=="event*", ATTRS{name}=="hall_lid", TAG+="uaccess"\n',
        "etc/modules-load.d/piano-bwmon.conf":
            "# CPU bandwidth monitors: icc-bwmon samples each CPU cluster's memory\n"
            "# traffic and votes on the APPS->EBI1 interconnect path.\n"
            "icc-bwmon\n",
        "etc/modules-load.d/piano-stats.conf":
            "# RPMh sleep statistics: /sys/kernel/debug/qcom_stats counters for the\n"
            "# AOSS/CX/DDR low-power modes and the ADSP/CDSP subsystems.\n"
            "qcom_stats\n",
        "etc/systemd/sleep.conf.d/piano.conf":
            "# SPDX-License-Identifier: BSD-2-Clause-Patent\n"
            "[Sleep]\n"
            "AllowSuspend=yes\n"
            "AllowHibernation=no\n"
            "AllowHybridSleep=no\n"
            "AllowSuspendThenHibernate=no\n"
            "SuspendState=mem\n"
            "MemorySleepMode=deep\n",
        "etc/systemd/system/qbootctl.service.d/piano.conf":
            "# qbootctl finds the slots through the partition labels. Right after the\n"
            "# switch to the root filesystem udev may not have recreated them yet, and\n"
            "# qbootctl then fails with \"No slots found\": wait for both boot partitions.\n"
            "[Unit]\n"
            "Wants=dev-disk-by\\x2dpartlabel-boot_a.device dev-disk-by\\x2dpartlabel-boot_b.device\n"
            "After=dev-disk-by\\x2dpartlabel-boot_a.device dev-disk-by\\x2dpartlabel-boot_b.device\n",
    }
    for name, text in files.items():
        target = safe_destination(root, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    target = safe_destination(root, "usr/bin/piano-collect-boot")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "scripts/collect-boot-evidence.sh", target)
    target.chmod(0o755)
    return sorted([*files, "usr/bin/piano-collect-boot", *stage_linux_debug_handoff(root)])


# 真机上逐条验过的平台修复。每一条都对应一个已经发生的故障，不是预防性配置。
PIANO_CLOCK_RESTORE = """#!/bin/sh
# Boot-time clock floor. This device's PMIC RTC reads 1970 and refuses set_time
# (rtc-pm8xxx gates writes behind the allow-set-time DT property), so the clock
# starts in the past and rpm/dnf signature checks fail before NTP can help.
set -eu
STAMP=/var/lib/piano-clock/last
[ -r "$STAMP" ] || exit 0
saved=$(cat "$STAMP" 2>/dev/null || echo 0)
case "$saved" in ''|*[!0-9]*) exit 0 ;; esac
now=$(date -u +%s)
if [ "$saved" -gt 1735689600 ] && [ "$saved" -gt "$now" ]; then
  date -u -s "@$saved"
  logger -t piano-clock "restored clock floor to $(date -u +%Y-%m-%dT%H:%M:%SZ)"
fi
"""

PIANO_CLOCK_SAVE = """#!/bin/sh
# Persist the current time for piano-clock-restore. Never persist a bogus past
# time: a device that boots in 1970 must not overwrite a good stamp.
set -eu
now=$(date -u +%s)
[ "$now" -gt 1735689600 ] || exit 0
mkdir -p /var/lib/piano-clock
printf '%s\\n' "$now" > /var/lib/piano-clock/.last.tmp
mv /var/lib/piano-clock/.last.tmp /var/lib/piano-clock/last
"""

# The offline clock floor can revisit a date already used by bootlog's dateext.
# Include the time so recovery does not overwrite an earlier boot's archive.
PIANO_BOOT_LOG_ROTATION = """/var/log/boot.log
{
    missingok
    daily
    copytruncate
    rotate 7
    notifempty
    dateext
    dateformat -%Y%m%d-%H%M%S
}
"""

PIANO_CLOCK_RESTORE_UNIT = """[Unit]
Description=Piano clock floor from the last saved time
DefaultDependencies=no
After=systemd-remount-fs.service
Before=sysinit.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/libexec/piano-clock-restore
[Install]
WantedBy=sysinit.target
"""

PIANO_CLOCK_SAVE_UNIT = """[Unit]
Description=Save the current time for the next boot
DefaultDependencies=no
Before=shutdown.target
After=local-fs.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/libexec/piano-clock-save
ExecStop=/usr/libexec/piano-clock-save
[Install]
WantedBy=multi-user.target
"""

PIANO_CLOCK_SAVE_TIMER = """[Unit]
Description=Periodic clock save
[Timer]
OnCalendar=hourly
Persistent=false
[Install]
WantedBy=timers.target
"""

# 自建服务跑在 init_t，没有域宏可用（镜像里没有 selinux-policy-devel），
# 所以策略模块是手写的定向 allow；每条规则对应真机上一条 AVC。
PIANO_SELINUX_UNIT = """[Unit]
Description=Install the piano SELinux policy module
DefaultDependencies=no
After=systemd-remount-fs.service
Before=sysinit.target
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'semodule -l | grep -q "^piano_hardware" || semodule -i /usr/share/piano/selinux/piano_hardware.pp'
[Install]
WantedBy=sysinit.target
"""

POWER_OVERLAY_FILES = (
    "usr/lib/piano/power-button",
    "usr/lib/systemd/user/piano-power-button.service",
    "etc/udev/rules.d/70-piano-power-button.rules",
    "etc/systemd/logind.conf.d/piano.conf",
    "etc/dconf/db/local.d/00-power",
)
LEGACY_POWER_OVERRIDES = {
    "etc/systemd/logind.conf.d/10-piano-powerkey.conf":
        b"[Login]\nHandlePowerKey=ignore\n",
    "etc/dconf/db/local.d/00-piano-session":
        b"[org/gnome/desktop/session]\nidle-delay=uint32 0\n"
        b"[org/gnome/desktop/screensaver]\nlock-enabled=false\n",
}


def remove_legacy_power_overrides(root):
    """Remove only byte-for-byte matches to files generated by the old finalizer."""
    existing = []
    for name, expected in LEGACY_POWER_OVERRIDES.items():
        target = safe_destination(root, name)
        if target.is_symlink():
            raise ValueError("legacy power override is a symlink; preserve and inspect it: " + name)
        if target.exists():
            if not target.is_file() or target.read_bytes() != expected:
                raise ValueError("legacy power override was modified; preserve it for review: " + name)
            existing.append(target)
    for target in existing:
        target.unlink()
    return [str(target.relative_to(root)) for target in existing]


def seed_clock_floor(root):
    """新镜像离线启动也有时间下限；保留已存在的有效保存时间。"""
    name = "var/lib/piano-clock/last"
    stamp = safe_destination(root, name)
    if stamp.exists():
        saved = stamp.read_text().strip()
        if not saved.isascii() or not saved.isdigit() or int(saved) <= 1735689600:
            raise ValueError("existing Piano clock floor is invalid")
        return name
    epoch = int(time.time())
    if epoch <= 1735689600:
        raise ValueError("builder clock is too old to seed the offline clock floor")
    stamp.parent.mkdir(parents=True, exist_ok=True)
    with stamp.open("x") as output:
        output.write(str(epoch) + "\n")
    stamp.chmod(0o644)
    return name


def stage_piano_hardware_fixes(root):
    """真机验证过的平台修复：时钟兜底与 SELinux 策略。"""
    remove_legacy_power_overrides(root)
    scripts = {
        "usr/libexec/piano-clock-restore": PIANO_CLOCK_RESTORE,
        "usr/libexec/piano-clock-save": PIANO_CLOCK_SAVE,
    }
    files = {
        **scripts,
        "etc/logrotate.d/bootlog": PIANO_BOOT_LOG_ROTATION,
        "usr/lib/systemd/system/piano-clock-restore.service": PIANO_CLOCK_RESTORE_UNIT,
        "usr/lib/systemd/system/piano-clock-save.service": PIANO_CLOCK_SAVE_UNIT,
        "usr/lib/systemd/system/piano-clock-save.timer": PIANO_CLOCK_SAVE_TIMER,
        "usr/lib/systemd/system/piano-selinux-policy.service": PIANO_SELINUX_UNIT,
        # sysinit 阶段那次在真机上没有留下痕迹（单元显示成功但时钟仍是 1970），
        # 所以在 multi-user 阶段再校正一次：两条路都走，任一条生效即可。
        "etc/systemd/system/piano-clock-save.service.d/restore-first.conf":
            "[Service]\nExecStartPre=/usr/libexec/piano-clock-restore\n",
        "etc/dconf/db/local.d/00-power":
            "[org/gnome/settings-daemon/plugins/media-keys]\npower-static=['']\n"
            "[org/gnome/settings-daemon/plugins/power]\npower-button-action='nothing'\n"
            "sleep-inactive-ac-type='nothing'\nsleep-inactive-battery-type='nothing'\n"
            "[org/gnome/desktop/session]\nidle-delay=uint32 300\n"
            "[org/gnome/desktop/screensaver]\nlock-enabled=true\nlock-delay=uint32 0\n",
    }
    for name, text in files.items():
        target = safe_destination(root, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        if name in scripts:
            target.chmod(0o755)
    module = safe_destination(root, "usr/share/piano/selinux/piano_hardware.pp")
    module.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "rootfs/selinux/piano_hardware.pp", module)
    return sorted([*files, "usr/share/piano/selinux/piano_hardware.pp", seed_clock_floor(root)])


def verify_power_overlay(root):
    """Require the runtime package's power/session payload and safe suspend defaults."""
    for name in POWER_OVERLAY_FILES:
        if not guest_resolve(root, "/" + name).is_file():
            raise ValueError("installed power overlay is missing: " + name)
    profile = guest_resolve(root, "/etc/dconf/profile/user")
    if not profile.is_file() or "system-db:local" not in {
            line.strip() for line in profile.read_text().splitlines()}:
        raise ValueError("dconf user profile does not include system-db:local")
    dconf = configparser.ConfigParser(interpolation=None)
    required = {
        "org/gnome/settings-daemon/plugins/media-keys": {"power-static": "['']"},
        "org/gnome/settings-daemon/plugins/power": {
            "power-button-action": "'nothing'", "sleep-inactive-ac-type": "'nothing'",
            "sleep-inactive-battery-type": "'nothing'"},
        "org/gnome/desktop/session": {"idle-delay": "uint32 300"},
        "org/gnome/desktop/screensaver": {"lock-enabled": "true", "lock-delay": "uint32 0"},
    }
    try:
        dconf.read_string(guest_resolve(root, "/etc/dconf/db/local.d/00-power").read_text())
        if dconf.defaults() or any(
                dconf.get(group, key, fallback=None) != value
                for group, keys in required.items() for key, value in keys.items()):
            raise ValueError("installed power overlay has unsafe or incomplete GNOME defaults")
    except configparser.Error as error:
        raise ValueError("installed power overlay has invalid GNOME defaults") from error


def verify_fastrpc_daemon(root, runtime_meta):
    """拒绝只装 FastRPC 库、漏掉服务实际执行文件的集成镜像。"""
    expected = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["fastrpc"]["commit"]
    proof = runtime_meta.get("fastrpc_daemon", {})
    executable = guest_resolve(root, "/usr/bin/adsprpcd")
    if (proof.get("commit") != expected or not executable.is_file()
            or not os.access(executable, os.X_OK)
            or sha256(executable) != proof.get("binary_sha256")):
        raise ValueError("installed FastRPC daemon differs from the runtime manifest")
    services = proof.get("service_sha256", {})
    expected_services = {"usr/lib/systemd/system/adsprpcd-sensorspd.service",
                         "usr/lib/systemd/system/adsprpcd-sensorspd.service.d/20-fedora-daemon.conf"}
    if set(services) != expected_services:
        raise ValueError("FastRPC service assembly is missing from the runtime manifest")
    for name, digest in services.items():
        unit = guest_resolve(root, "/" + name)
        if not unit.is_file() or sha256(unit) != digest:
            raise ValueError("installed FastRPC service differs: " + name)


def verify_runtime_extras(root, runtime_meta):
    """核对实际安装的虚拟机程序和平台配置，拒绝仅包名匹配。

    Iris VA-API 驱动改由 sm8750-video-accel 构建，它的编码入口断言在 spec 的
    %build 里；运行时不再产出第二份同路径驱动。
    """
    sources = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
    extras = runtime_meta.get("extras", {})
    if not extras:
        return
    if set(extras) != {"crosvm", "librevm"}:
        raise ValueError("runtime extras source proof is incomplete")
    for name, proof in extras.items():
        if proof.get("commit") != sources[name]["commit"]:
            raise ValueError("runtime extras source differs: " + name)
    librevm = extras["librevm"]
    if (librevm.get("adapter_sha256") != sha256(ROOT / "runtime/librevm/fedora.patch")
            or librevm.get("cargo_lock_sha256") != sha256(ROOT / "runtime/librevm/Cargo.lock")
            or set(librevm.get("binaries", {})) != {"librevm", "librevm-daemon", "librevm-gui"}):
        raise ValueError("LibreVM adapter or binary proof differs")
    if extras["crosvm"].get("features") != ["gunyah", "net", "balloon"]:
        raise ValueError("crosvm lacks the production Gunyah feature proof")
    files = {"usr/bin/crosvm": extras["crosvm"].get("binary_sha256")}
    files.update({"usr/bin/" + name: digest for name, digest in librevm["binaries"].items()})
    config = "usr/lib/tmpfiles.d/piano-crosvm.conf"
    files[config] = sha256(ROOT / "runtime/crosvm/piano-crosvm.conf")
    for name, digest in files.items():
        target = guest_resolve(root, "/" + name)
        if not target.is_file() or sha256(target) != digest:
            raise ValueError("installed runtime extra differs: " + name)
    alias = guest_resolve(root, "/usr/lib64/dri") / "msm_drv_video.so"
    if not alias.is_symlink() or str(alias.readlink()) != "v4l2_drv_video.so":
        raise ValueError("VA-API msm driver alias differs")


def verify_device_integration(root, runtime_meta):
    """固定来源、适配代码和实际安装载荷必须一致。

    piano-camerad、uperf 与 VA-API 驱动都由 RPM 矩阵构建，这里只核对仍然
    由运行时产出的触控载荷。手指电极为矩阵载荷，运行时复算工厂 INI 后把
    它的摘要写进 proof，所以仍按安装后的文件逐字节核对。
    """
    sources = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
    integration = runtime_meta.get("integration", {})
    if not integration:
        return
    required = {"usr/bin/piano-touch-view", "usr/lib/piano/touch-input",
                "usr/share/piano/touch/piano-csot.json", "usr/share/piano/touch/piano-boe.json"}
    if not required <= set(integration.get("files", {})):
        raise ValueError("incomplete device integration payload proof")
    if integration.get("commit") != sources["piano-integration"]["commit"] or not integration.get("files"):
        raise ValueError("missing pinned integration proof: piano-integration")
    for relative, digest in integration["files"].items():
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError("unsafe integration payload path")
        target = guest_resolve(root, "/" + relative)
        if not target.is_file() or sha256(target) != digest:
            raise ValueError("installed integration differs: " + relative)
    touch = integration.get("touch", {})
    adapters = {p.name: sha256(p) for p in (ROOT / "runtime/touch").iterdir() if p.is_file()}
    if touch.get("finger_adapter_sha256") != adapters or touch.get("single_thp_reader") is not True:
        raise ValueError("finger pipeline adapter proof differs")


def verify_ffmpeg_install(root, ffmpeg):
    """拒绝装成官方 ffmpeg-free 或漏装解码器的镜像。

    与 Mesa 一样，逐个核对已安装包的 NEVRA 是否就是本次重建的产物；再在目标树里
    真跑一次 `ffmpeg -decoders`，确认两个 v4l2m2m 解码器真的注册进来，而不是只把
    RPM 拷进去。官方 ffmpeg-free 的 release 更低，dnf 会把它升级成 piano1 产物。
    """
    if (ffmpeg.get("status"), ffmpeg.get("releasever"), ffmpeg.get("arch")) != (
            "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED", "44", "aarch64"):
        raise ValueError("FFmpeg is not the verified native piano build")
    expected_decoders = {"h264_v4l2m2m", "hevc_v4l2m2m"}
    if set(ffmpeg.get("decoders", {})) != expected_decoders:
        raise ValueError("FFmpeg manifest does not prove the v4l2m2m decoders")
    if set(ffmpeg.get("packages", {})) != FFMPEG_PACKAGES:
        raise ValueError("FFmpeg manifest does not name the exact runtime package set")
    for name, row in ffmpeg["packages"].items():
        if chroot(root, "rpm", "-q", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}", name).strip() != row["nevra"]:
            raise ValueError("installed FFmpeg package differs: " + name)
    listing = chroot(root, "/usr/bin/ffmpeg", "-hide_banner", "-decoders")
    present = set()
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[1] in expected_decoders:
            present.add(fields[1])
    if present != expected_decoders:
        raise ValueError("installed ffmpeg lacks the v4l2m2m decoders: " + repr(sorted(present)))


def verify_kernel_install(root, kernel):
    """Prove the installed kernel-piano tree is the exact sealed kernel input.

    kernel-piano is installed in the clean DNF transaction. The sealed kernel
    artifact is downloaded separately, so matching release strings alone are
    insufficient: two runs can share a release while carrying different
    modules. Compare every kernel-owned module and index byte. The ABI-matched
    v4l2loopback module from piano-runtime lives under updates/ and is checked
    separately below against the runtime manifest.
    """
    record, _ = inspect_kernel(kernel)
    release = record["kernel_release"]
    installed = root / "usr/lib/modules" / release
    if not installed.is_dir():
        raise ValueError("kernel-piano did not install the sealed kernel release: " + release)
    expected = {row["path"].split(f"lib/modules/{release}/", 1)[1]: row
                for row in record["modules"]}
    actual = {}
    for path in installed.rglob("*"):
        relative = path.relative_to(installed).as_posix()
        if path.is_symlink():
            if relative in ("build", "source") or relative.startswith(("build/", "source/")):
                continue
            raise ValueError("installed kernel tree contains an unexpected symlink: " + relative)
        if relative.startswith("updates/"):
            continue
        if not path.is_file():
            continue
        actual[relative] = path
    expected_paths = set(expected) | {
        relative.split(f"lib/modules/{release}/", 1)[1]
        for relative in record["module_summary"]["index_sha256"]
    }
    if set(actual) != expected_paths:
        missing = sorted(expected_paths - set(actual))
        extra = sorted(set(actual) - expected_paths)
        raise ValueError("installed kernel payload file set differs from the sealed artifact: "
                         f"missing={missing[:5]} extra={extra[:5]}")
    for name, row in expected.items():
        if sha256(actual[name]) != row["sha256"]:
            raise ValueError("installed kernel module differs from sealed artifact: " + name)
    indexes = record["module_summary"]["index_sha256"]
    for relative, digest in indexes.items():
        name = relative.split(f"lib/modules/{release}/", 1)[1]
        path = installed / name
        if not path.is_file() or sha256(path) != digest:
            raise ValueError("installed kernel index differs from sealed artifact: " + name)
    return {"kernel_release": release, "module_count": len(expected),
            "kernel_manifest_sha256": sha256(kernel / "manifest.json")}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("rootfs", "kernel", "runtime", "mesa", "ffmpeg", "output"):
        ap.add_argument("--" + name, required=True, type=Path)
    args = ap.parse_args()
    root, kernel, runtime, output = [getattr(args, name).resolve() for name in ("rootfs", "kernel", "runtime", "output")]
    if not root.is_relative_to(ROOT / "build") or not root.is_dir() or os.geteuid() != 0:
        raise ValueError("finalize only a fresh build rootfs as root")
    m, kernel_hash = inspect_kernel(kernel)
    runtime_meta = json.loads((runtime / "manifest.json").read_text())
    if runtime_meta["kernel_manifest_sha256"] != kernel_hash:
        raise ValueError("runtime and rootfs select different kernel builds")
    verify_fastrpc_daemon(root, runtime_meta)
    verify_runtime_extras(root, runtime_meta)
    verify_device_integration(root, runtime_meta)
    mesa_dir = args.mesa.resolve()
    mesa = json.loads((mesa_dir / "manifest.json").read_text())
    if (mesa.get("status"), mesa.get("releasever"), mesa.get("arch"), mesa.get("chip_id")) != (
            "FEDORA_MESA_BUILT_NOT_DEVICE_VERIFIED", "44", "aarch64", "0xffff44050001"):
        raise ValueError("Mesa is not the verified native piano build")
    for name, row in mesa["packages"].items():
        if chroot(root, "rpm", "-q", "--qf", "%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}", name).strip() != row["nevra"]:
            raise ValueError("installed Mesa package differs: " + name)
    for relative, digest in mesa["driver_files"].items():
        safe_relative(relative)
        if sha256(guest_resolve(root, "/" + relative)) != digest:
            raise ValueError("installed A830v1 driver differs: " + relative)
    ffmpeg_dir = args.ffmpeg.resolve()
    verify_ffmpeg_install(root, json.loads((ffmpeg_dir / "manifest.json").read_text()))
    kernel_install = verify_kernel_install(root, kernel)
    for row in m["modules"]:
        path = root / "usr" / row["path"]
        if sha256(path) != row["sha256"]:
            raise ValueError("installed core kernel module changed: " + row["path"])
    proof = json.loads((runtime / "kernel-abi.json").read_text())
    loop = root / f"usr/lib/modules/{m['kernel_release']}/updates/v4l2loopback.ko"
    if proof["kernel_manifest_sha256"] != kernel_hash or sha256(loop) != proof["module_sha256"]:
        raise ValueError("installed camera module ABI or content differs")
    chroot(root, "/usr/sbin/depmod", "-a", m["kernel_release"])
    # 正式 root 的服务及账号在目标树中配置，绝不启动构建机上的设备服务。
    if subprocess.run(["chroot", str(root), "id", "piano"], capture_output=True).returncode:
        chroot(root, "useradd", "--create-home", "--groups", "wheel,audio,video,input,render", "--shell", "/bin/bash", "piano")
    chroot(root, "groupadd", "--force", "--system", "librevm")
    chroot(root, "usermod", "--append", "--groups", "librevm,audio,video,input,render", "piano")
    # 默认登录口令（上游 1b3c538 同款）；root 仍锁定，wheel 也不加 NOPASSWD。
    set_login_password(root)
    piano_field = shadow_password(root, DEFAULT_PIANO_LOGIN)
    if password_locked(piano_field):
        raise ValueError("default piano login password was not applied")
    if not password_locked(shadow_password(root, "root")):
        raise ValueError("root account must stay locked")
    gdm = root / "etc/gdm/custom.conf"
    gdm.parent.mkdir(parents=True, exist_ok=True)
    gdm.write_text("[daemon]\nAutomaticLoginEnable=True\nAutomaticLogin=piano\n")
    # 解除早期对 qbootctl 的屏蔽，开机自动执行向 ABL 标记成功槽位，彻底根治 fastboot 掉入
    qboot_mask = root / "etc/systemd/system/qbootctl.service"
    if qboot_mask.is_symlink() and qboot_mask.readlink() == Path("/dev/null"):
        qboot_mask.unlink()
    # 屏蔽未适配且破坏内存状态的磁盘休眠目标，锁定为 deep 挂起
    for unit in ("hibernate.target", "hybrid-sleep.target", "suspend-then-hibernate.target"):
        p = root / "etc/systemd/system" / unit
        if not p.exists() and not p.is_symlink():
            p.symlink_to("/dev/null")
    # 先写入自建时钟/策略单元及依赖配置，再让 systemctl 查找并启用它们。
    hardware_files = stage_piano_hardware_fixes(root)
    diagnostic_files = stage_boot_diagnostics(root)
    verify_power_overlay(root)
    services = ["NetworkManager.service", "sshd.service", "gdm.service", "bluetooth.service",
                "piano-display.service", "piano-touch.service", "piano-radio.service",
                "piano-adsp.service", "piano-audio.service", "piano-keyboard.service",
                "piano-cpufreq.service", "piano-video.service", "piano-camera.service",
                "piano-camerad.service", "adsprpcd-sensorspd.service",
                "piano-fake-tablet-mode.service", "xiaomi-charger-mode.service",
                "librevm-daemon.service", "uperf-linux.service",
                "piano-clock-restore.service", "piano-clock-save.service",
                "piano-clock-save.timer", "piano-selinux-policy.service",
                "piano-pstore-save.service", "qbootctl.service"]
    chroot(root, "systemctl", "enable", *services)
    chroot(root, "systemctl", "--global", "enable", "piano-power-button.service")
    if shutil.which("dconf") or (root / "usr/bin/dconf").exists():
        chroot(root, "dconf", "update")
    # FastRPC 的默认 root/audio PD 自动启动与 piano 的顺序服务冲突。
    for unit in ("adsprpcd.service", "adsprpcd_audiopd.service", "adsprpcd-audiopd.service"):
        p = root / "etc/systemd/system" / unit
        if not p.exists() and not p.is_symlink():
            p.symlink_to("/dev/null")
    for name in ("dev", "proc", "sys", "run"):
        d = root / name
        if d.is_symlink():
            raise ValueError("runtime directory is an unexpected symlink: " + name)
        d.mkdir(exist_ok=True)
        for p in d.iterdir():
            if os.path.ismount(p):
                raise ValueError("builder mount remains in the rootfs: " + str(p))
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
    for key in (root / "etc/ssh").glob("ssh_host_*"):
        key.unlink()
    machine = root / "etc/machine-id"
    if machine.is_symlink():
        machine.unlink()
    machine.write_text("")
    dbus = root / "var/lib/dbus/machine-id"
    if dbus.exists() or dbus.is_symlink():
        dbus.unlink()
    dbus.parent.mkdir(parents=True, exist_ok=True)
    dbus.symlink_to("/etc/machine-id")
    normalized_launchers = normalize_launcher_links(root)
    removed_manpages = prune_missing_manpage_links(root)
    packages = chroot(root, "rpm", "-qa", "--qf", "%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n")
    output.mkdir(parents=True, exist_ok=True)
    (output / "packages.tsv").write_text("".join(sorted(packages.splitlines(keepends=True))))
    # The clean matrix names the hardware packages explicitly. The aggregate
    # runtime still provides librevm/crosvm/VA-API for older bundles, but it is
    # no longer the only accepted owner of those payloads.
    required = {"piano-fedora-configs", "alsa-xiaomi-piano", "piano-camera",
                "xiaomi-piano-thp", "sm8750-video-accel", "uperf-linux",
                "libssc", "iio-sensor-proxy-ssc", "xiaomi-piano-firmware",
                "kernel-piano", "systemd", "gdm", "gnome-shell"}
    installed = {line.split("\t")[0] for line in packages.splitlines()}
    missing = required - installed
    if missing:
        raise ValueError("missing required installed packages: " + str(missing))
    if any(name.startswith("kernel-core") for name in installed):
        raise ValueError("stock Fedora kernel entered the device rootfs")
    result = {"status": "FEDORA_DEVICE_ROOT_STAGED_NOT_BOOT_VERIFIED",
              "kernel_release": m["kernel_release"], "kernel_commit": m["source_commit"],
              "kernel_manifest_sha256": kernel_hash, "root_policy": "LABEL=PIANOROOT",
              "root_fstype": "f2fs", "runtime_manifest_sha256": sha256(runtime / "manifest.json"),
              "mesa_manifest_sha256": sha256(mesa_dir / "manifest.json"),
              "ffmpeg_manifest_sha256": sha256(ffmpeg_dir / "manifest.json"),
              "kernel_install_verified": kernel_install,
              "packages_sha256": sha256(output / "packages.tsv"), "enabled_services": services,
              "login": (f"{DEFAULT_PIANO_LOGIN} autologin; documented default password set; "
                        "root locked; no NOPASSWD"),
              "login_proof": {"user": DEFAULT_PIANO_LOGIN,
                              "password_set": not password_locked(piano_field),
                              "password_source": "chpasswd user:documented-default (real newline)",
                              "password_matches_default_verified_by":
                                  "tests/check_rootfs_finalize.py (isolated chpasswd + crypt check)",
                              "root_locked": password_locked(shadow_password(root, "root")),
                              "nopasswd": False, "autologin": True,
                              "device_login_tested": False},
              "device_configuration_required": ["/etc/piano/boot-request.json", "active Android slot for sensors"],
              "normalized_launcher_links": normalized_launchers,
              "removed_manpage_links": removed_manpages, "device_tested": False}
    result["boot_diagnostic_files"] = diagnostic_files
    result["device_hardware_fix_files"] = hardware_files
    (output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
