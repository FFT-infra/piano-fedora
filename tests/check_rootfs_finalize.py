#!/usr/bin/env python3
"""省略文档时仅清理缺失的手册链接，保留运行时链接与有效手册。"""

import ctypes
import ctypes.util
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("finalizer", ROOT / "scripts/finalize-fedora-rootfs.py")
finalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finalizer)


def fixture(root):
    alternatives = root / "etc/alternatives"
    man = root / "usr/share/man/man1"
    alternatives.mkdir(parents=True)
    man.mkdir(parents=True)
    (root / "usr/lib/modules/fixture").mkdir(parents=True)
    (alternatives / "apropos.1.gz").symlink_to("/usr/share/man/man1/apropos.man-db.1.gz")
    (man / "apropos.1.gz").symlink_to("/etc/alternatives/apropos.1.gz")
    (man / "present.1.gz").write_bytes(b"manual")
    (alternatives / "present-man").symlink_to("/usr/share/man/man1/present.1.gz")
    return alternatives, man


def launcher_fixture(root):
    binary = root / "usr/bin"
    target = root / "usr/share/org.gnome.Weather/org.gnome.Weather"
    binary.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"weather launcher")
    (binary / "gnome-weather").symlink_to("../../../../../../../usr/share/org.gnome.Weather/org.gnome.Weather")
    return binary, target


def check_debug_handoff(root, staged):
    """debug 交接消费者、unit 关系、NM profile 权限与 MAC 匹配必须精确。"""
    # 记录进 stage_boot_diagnostics 返回的文件列表。
    expected = {
        "etc/systemd/system/serial-getty@ttyGS0.service.d/20-piano-debug.conf",
        "etc/piano/linux-debug.conf",
        "etc/NetworkManager/system-connections/piano-debug-ncm.nmconnection",
        "etc/systemd/system/multi-user.target.wants/piano-linux-debug.service",
        "etc/systemd/system/getty.target.wants/serial-getty@ttyGS0.service",
    }
    assert expected <= set(staged), sorted(set(staged))

    # 串口 getty 实例必须在 piano-linux-debug 之后，且保留账户认证（不启用自动 root shell）。
    dropin = root / "etc/systemd/system/serial-getty@ttyGS0.service.d/20-piano-debug.conf"
    text = dropin.read_text()
    assert "Requires=piano-linux-debug.service" in text
    assert "After=piano-linux-debug.service" in text
    assert not (root / "etc/systemd/system/multi-user.target.wants/piano-linux-serial.service").exists()

    # 两个 enable 链接指向正确的上游 unit。
    debug_link = root / "etc/systemd/system/multi-user.target.wants/piano-linux-debug.service"
    assert debug_link.is_symlink()
    assert str(debug_link.readlink()) == "/usr/lib/systemd/system/piano-linux-debug.service"
    getty_link = root / "etc/systemd/system/getty.target.wants/serial-getty@ttyGS0.service"
    assert getty_link.is_symlink()
    assert str(getty_link.readlink()) == "/usr/lib/systemd/system/serial-getty@.service"

    # linux-debug.conf：上游 opt-in acm-ncm、shell=0、固定 ipv4、recovery=0、权限 600。
    config = root / "etc/piano/linux-debug.conf"
    body = config.read_text()
    assert "usb=acm-ncm" in body and "shell=0" in body
    assert "ipv4=192.168.77.1/30" in body and "recovery_seconds=0" in body
    assert config.stat().st_mode & 0o777 == 0o600, oct(config.stat().st_mode & 0o777)

    # NM profile：600，按固定 dev MAC 匹配，静态地址、never-default、ipv6 disabled。
    profile = root / "etc/NetworkManager/system-connections/piano-debug-ncm.nmconnection"
    assert profile.stat().st_mode & 0o777 == 0o600, oct(profile.stat().st_mode & 0o777)
    body = profile.read_text()
    assert "[ethernet]" in body and "[match]" not in body
    assert "mac-address=02:87:50:00:00:01" in body
    assert "interface-name=" not in body, "must not bind a renameable interface name"
    assert "method=manual" in body and "address1=192.168.77.1/30" in body
    assert "never-default=true" in body
    assert "method=disabled" in body
    assert "method=auto" not in body and "method=dhcp" not in body


def check_login_password(root):
    """真实 chpasswd 输入必须非 locked、含真实换行，并能生成匹配默认口令的 shadow。"""
    user = finalizer.DEFAULT_PIANO_LOGIN
    password = finalizer.DEFAULT_PIANO_PASSWORD
    payload = finalizer.login_password_input(user, password)
    # 输入必须以真实换行结尾，且不能把 "\n" 当成两个字面字符送进 chpasswd。
    assert payload.endswith("\n") and not payload.endswith("\\n"), repr(payload)
    assert payload == f"{user}:{password}\n", repr(payload)
    assert "\n" not in payload[:-1], "payload must carry exactly one trailing newline"

    # 真实 shadow 生成需要 root。host-check 容器里 euid=0 但不装 sudo，直接调用
    # chpasswd；非 root 才退回 sudo -n。两者都不可用时只验证输入编码。
    chpasswd = shutil.which("chpasswd") or "/usr/sbin/chpasswd"
    if not Path(chpasswd).exists():
        print("SKIP: system chpasswd unavailable; only the encoded input was checked")
        return
    if os.geteuid() == 0:
        prefix = []
    elif shutil.which("sudo") and subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0:
        prefix = ["sudo", "-n"]
    else:
        print("SKIP: real chpasswd fixture requires root or passwordless sudo")
        return

    fx = root / "login-shadow"
    (fx / "etc").mkdir(parents=True)
    (fx / "etc/passwd").write_text(f"{user}:x:1000:1000::{('/home/' + user)}:/bin/bash\n")
    (fx / "etc/shadow").write_text(f"{user}:!:20001:0:99999:7:::\n")
    # 固定 SHA512，避免空 chroot 里没有 PAM/默认加密策略时的差异。
    subprocess.run([*prefix, chpasswd, "-c", "SHA512", "-R", str(fx)],
                   input=payload, text=True, check=True)
    field = None
    for line in (fx / "etc/shadow").read_text().splitlines():
        parts = line.split(":")
        if parts and parts[0] == user:
            field = parts[1]
    assert field and not finalizer.password_locked(field), f"chpasswd left the account locked: {field!r}"
    assert field.startswith("$6$"), f"expected a SHA512 hash, got: {field!r}"
    # python3.13 已移除 crypt 模块，用标准库 ctypes 绑定系统 libcrypt 的 crypt(3)。
    crypt = _libcrypt_crypt()
    if crypt is None:
        print("SKIP: no working libcrypt found; CI provides shadow-utils/libxcrypt")
        return
    assert crypt(password, field) == field, "default password does not reproduce the shadow hash"
    assert crypt(password + "x", field) != field, "a different password must not match"


def _libcrypt_crypt():
    """返回可用的 libcrypt crypt(3) 绑定；找不到返回 None（CI 由 shadow-utils/libxcrypt 提供）。"""
    import glob
    candidates = []
    found = ctypes.util.find_library("crypt")
    if found:
        candidates.append(found)
    candidates += ["libcrypt.so.2", "libcrypt.so.1", "libcrypt.so",
                   "/lib64/libcrypt.so.2", "/usr/lib64/libcrypt.so.2",
                   "/lib64/libcrypt.so.1", "/usr/lib/libcrypt.so.1"]
    # 一些发行版/容器把 libxcrypt 放在非标准前缀，按真实文件补齐候选。
    candidates += glob.glob("/nix/store/*libxcrypt*/lib/libcrypt.so.2")
    candidates += glob.glob(str(Path(sys.prefix) / "lib" / "libcrypt.so*"))
    for name in candidates:
        try:
            lib = ctypes.CDLL(name)
            lib.crypt.restype = ctypes.c_char_p
            lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
            # 只有真能算出 crypt 的库才接受，避免选到 ABI 不兼容的 .so。
            if not lib.crypt(b"probe", b"$6$abcdefgh$"):
                continue
        except (OSError, AttributeError):
            continue

        def crypt(password, salt, _lib=lib):
            result = _lib.crypt(password.encode(), salt.encode())
            return result.decode() if result else None

        return crypt
    return None


def main():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        alternatives, man = fixture(root)
        (alternatives / "runtime").symlink_to("/usr/bin/missing-runtime")
        removed = finalizer.prune_missing_manpage_links(root)
        assert sorted(removed) == ["etc/alternatives/apropos.1.gz", "usr/share/man/man1/apropos.1.gz"], removed
        assert not (alternatives / "apropos.1.gz").is_symlink()
        assert not (man / "apropos.1.gz").is_symlink()
        assert (alternatives / "present-man").is_symlink()
        assert (man / "present.1.gz").read_bytes() == b"manual"
        assert (alternatives / "runtime").is_symlink(), "runtime failures must remain visible to the packager"
        assert finalizer.prune_missing_manpage_links(root) == [], "cleanup must be idempotent"
        binary, target = launcher_fixture(root)
        changes = finalizer.normalize_launcher_links(root)
        assert set(changes) == {"usr/bin/gnome-weather"}, changes
        assert (binary / "gnome-weather").resolve() == target
        assert (binary / "gnome-weather").read_bytes() == b"weather launcher"
        assert finalizer.normalize_launcher_links(root) == {}
        (binary / "bad").symlink_to("../../../etc/passwd")
        try:
            finalizer.normalize_launcher_links(root)
        except ValueError:
            pass
        else:
            raise AssertionError("an unexpected runtime escape was normalized")
        assert (binary / "bad").is_symlink()
        staged = finalizer.stage_boot_diagnostics(root)
        assert "usr/bin/piano-collect-boot" in staged
        gdm = root / "etc/systemd/system/gdm.service.d/20-piano-display.conf"
        assert "Requires=piano-display.service" in gdm.read_text()
        assert "After=piano-display.service" in gdm.read_text()
        camera = root / "etc/systemd/system/piano-camerad.service.d/20-camera-dependency.conf"
        assert "Wants=piano-camera.service" in camera.read_text()
        assert "Requires=" not in camera.read_text()
        assert "StartLimitBurst=3" in camera.read_text()
        assert not (root / "etc/systemd/system/piano-camerad.service").is_symlink()
        assert (root / "usr/bin/piano-collect-boot").stat().st_mode & 0o111
        check_debug_handoff(root, staged)
        check_piano_hardware_fixes(root)
        check_login_password(root)
    print("OK: manuals pruned, launcher normalized, runtime guards, debug handoff, "
          "device hardware fixes and default login password preserved")


def check_piano_hardware_fixes(root):
    """真机修复必须成套出现：脚本可执行、单元被启用、模块随镜像走。"""
    staged = finalizer.stage_piano_hardware_fixes(root)
    expected = {
        "usr/libexec/piano-clock-restore",
        "usr/libexec/piano-clock-save",
        "usr/lib/systemd/system/piano-clock-restore.service",
        "usr/lib/systemd/system/piano-clock-save.service",
        "usr/lib/systemd/system/piano-clock-save.timer",
        "usr/lib/systemd/system/piano-selinux-policy.service",
        "etc/systemd/logind.conf.d/10-piano-powerkey.conf",
        "etc/systemd/system/piano-clock-save.service.d/restore-first.conf",
        "etc/dconf/db/local.d/00-piano-session",
        "usr/share/piano/selinux/piano_hardware.pp",
    }
    # 第二道校正：即使 sysinit 那次没生效，multi-user 也会再设一次。
    dropin = (root / "etc/systemd/system/piano-clock-save.service.d/restore-first.conf").read_text()
    assert "ExecStartPre=/usr/libexec/piano-clock-restore" in dropin
    assert expected <= set(staged), sorted(set(staged))
    for name in ("piano-clock-restore", "piano-clock-save"):
        assert (root / "usr/libexec" / name).stat().st_mode & 0o111, name
    # 兜底脚本本身不能把坏时间写回去，也不该在没有戳时动手。
    save = (root / "usr/libexec/piano-clock-save").read_text()
    assert "1735689600" in save and ".last.tmp" in save
    restore = (root / "usr/libexec/piano-clock-restore").read_text()
    assert "1735689600" in restore and '"$saved" -gt "$now"' in restore
    # 短按电源键不能再关机（默认动作是 poweroff，这台机器又不支持挂起）。
    logind = (root / "etc/systemd/logind.conf.d/10-piano-powerkey.conf").read_text()
    assert "HandlePowerKey=ignore" in logind
    # 策略模块必须与 .te 源码一起在镜像里，且有安装单元兜底。
    assert (root / "usr/share/piano/selinux/piano_hardware.pp").stat().st_size > 0
    unit = (root / "usr/lib/systemd/system/piano-selinux-policy.service").read_text()
    assert "semodule -i /usr/share/piano/selinux/piano_hardware.pp" in unit
    dconf = (root / "etc/dconf/db/local.d/00-piano-session").read_text()
    assert "idle-delay=uint32 0" in dconf and "lock-enabled=false" in dconf


if __name__ == "__main__":
    main()
