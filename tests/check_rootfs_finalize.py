#!/usr/bin/env python3
"""省略文档时仅清理缺失的手册链接，保留运行时链接与有效手册。"""

import ast
import ctypes
import ctypes.util
import importlib.util
import json
import os
import shutil
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("finalizer", ROOT / "scripts/finalize-fedora-rootfs.py")
finalizer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(finalizer)
runtime_spec = importlib.util.spec_from_file_location("fedora_runtime", ROOT / "scripts/build-device-runtime.py")
runtime = importlib.util.module_from_spec(runtime_spec)
runtime_spec.loader.exec_module(runtime)

POWER_FILES = {
    "usr/lib/piano/power-button",
    "usr/lib/systemd/user/piano-power-button.service",
    "etc/udev/rules.d/70-piano-power-button.rules",
    "etc/systemd/logind.conf.d/piano.conf",
    "etc/dconf/db/local.d/00-power",
}


def check_power_runtime_package():
    """Stage only the five locked power files and inspect an actual fixture RPM."""
    configured = os.environ.get("PIANO_TEST_SUNUEFI")
    sun = Path(configured) if configured else ROOT / "build/sources/sunuefi"
    if configured and not sun.is_dir():
        raise FileNotFoundError("PIANO_TEST_SUNUEFI checkout is missing: " + str(sun))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        if not sun.is_dir():
            sun = root / "sunuefi-fixture"
            overlay = sun / "linux/desktops/gnome/power-overlay"
            for relative in POWER_FILES | {"etc/default/locale", "etc/xdg/mimeapps.list"}:
                source = overlay / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("# fixture: " + relative + "\n")
        payload = root / "payload"
        payload.mkdir()
        staged = runtime.stage_power_overlay(sun, payload)
        assert set(staged) == POWER_FILES, sorted(staged)
        for relative in POWER_FILES:
            source = sun / "linux/desktops/gnome/power-overlay" / relative
            target = payload / relative
            assert target.is_file() and target.read_bytes() == source.read_bytes(), relative
        assert (payload / "usr/lib/piano/power-button").stat().st_mode & 0o111
        assert not (payload / "etc/default/locale").exists()
        assert not (payload / "etc/xdg/mimeapps.list").exists()

        if shutil.which("rpmbuild") and shutil.which("rpm"):
            out = root / "rpm-out"
            out.mkdir()
            (out / "rpms").mkdir()
            runtime.rpm_package("piano-power-fixture", payload, out)
            rpm = next((out / "rpms").glob("*.rpm"))
            listed = set(subprocess.run(
                ["rpm", "-qpl", str(rpm)], check=True, capture_output=True, text=True
            ).stdout.splitlines())
            assert {"/" + item for item in POWER_FILES} <= listed
            assert "/etc/default/locale" not in listed
            assert "/etc/xdg/mimeapps.list" not in listed
        else:
            print("SKIP: rpmbuild/rpm unavailable; source-to-payload fixture checked")


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


def check_service_staging_order(root):
    """在空树执行 main 的真实服务准备顺序，enable 时必须已有自建单元。"""
    root.mkdir()
    tree = ast.parse((ROOT / "scripts/finalize-fedora-rootfs.py").read_text())
    main_node = next(node for node in tree.body
                     if isinstance(node, ast.FunctionDef) and node.name == "main")
    selected = []
    for node in main_node.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in
                {"services", "hardware_files", "diagnostic_files"} for target in node.targets):
            selected.append(node)
        elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Name) and node.value.func.id == "chroot"
              and len(node.value.args) > 2
              and isinstance(node.value.args[1], ast.Constant)
              and node.value.args[1].value == "systemctl"
              and any(isinstance(arg, ast.Constant) and arg.value == "enable"
                      for arg in node.value.args[2:])):
            selected.append(node)
        elif (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
              and isinstance(node.value.func, ast.Name)
              and node.value.func.id == "verify_power_overlay"):
            selected.append(node)
    assert len(selected) == 6, "service setup execution path changed; review this test"
    required = {"piano-clock-restore.service", "piano-clock-save.service",
                "piano-clock-save.timer", "piano-selinux-policy.service"}
    enabled = []

    def enable(guest, *command):
        assert guest == root and command[0:1] == ("systemctl",)
        if "--global" in command:
            assert command == ("systemctl", "--global", "enable", "piano-power-button.service"), command
            assert (root / "usr/lib/systemd/user/piano-power-button.service").is_file()
            enabled.append("global:piano-power-button.service")
        else:
            assert command[1:2] == ("enable",)
            assert required <= set(command[2:]), "enable path omitted required custom units"
            for name in required:
                unit = root / "usr/lib/systemd/system" / name
                assert unit.is_file(), "enable called before the unit was staged: " + name
            enabled.extend(command[2:])

    def verify_power(root):
        for name in finalizer.POWER_OVERLAY_FILES:
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("fixture")
        profile = root / "etc/dconf/profile/user"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text("user-db:user\nsystem-db:local\n")
        dconf = root / "etc/dconf/db/local.d/00-power"
        policy = ("[org/gnome/settings-daemon/plugins/media-keys]\npower-static=['']\n"
                  "[org/gnome/settings-daemon/plugins/power]\npower-button-action='nothing'\n"
                  "sleep-inactive-ac-type='nothing'\nsleep-inactive-battery-type='nothing'\n"
                  "[org/gnome/desktop/session]\nidle-delay=uint32 300\n"
                  "[org/gnome/desktop/screensaver]\nlock-enabled=true\nlock-delay=uint32 0\n")
        dconf.write_text(policy)
        profile.write_text("user-db:user\n")
        try:
            finalizer.verify_power_overlay(root)
        except ValueError as error:
            assert "does not include system-db:local" in str(error), error
        else:
            raise AssertionError("dconf profile without system-db:local was accepted")
        profile.write_text("user-db:user\nsystem-db:local\n")
        finalizer.verify_power_overlay(root)
        for unsafe in (
                policy.replace("sleep-inactive-ac-type='nothing'", "sleep-inactive-ac-type='suspend'"),
                policy.replace("sleep-inactive-ac-type='nothing'", "# sleep-inactive-ac-type='nothing'"),
                policy.replace("[org/gnome/desktop/screensaver]", "[wrong/group]"),
                policy + "lock-enabled=false\n"):
            dconf.write_text(unsafe)
            try:
                finalizer.verify_power_overlay(root)
            except ValueError:
                pass
            else:
                raise AssertionError("unsafe or inactive GNOME policy was accepted: " + unsafe)
        dconf.write_text(policy)

    namespace = {"root": root, "chroot": enable,
                 "stage_piano_hardware_fixes": finalizer.stage_piano_hardware_fixes,
                 "stage_boot_diagnostics": finalizer.stage_boot_diagnostics,
                 "verify_power_overlay": verify_power}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "service-setup", "exec"), namespace)
    assert required <= set(enabled), "service enable path was never exercised"
    assert "global:piano-power-button.service" in enabled


def main():
    check_power_runtime_package()
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
        touch_drop = root / "etc/systemd/system/piano-touch.service.d/20-fast-restart.conf"
        assert "RestartSec=1" in touch_drop.read_text()
        assert (root / "etc/piano/camera.conf").read_text() == "PIANO_CAMERA_OUTPUT=nv12\n"
        wp = root / "etc/wireplumber/wireplumber.conf.d/50-piano-camera.conf"
        assert "monitor.libcamera = disabled" in wp.read_text()
        dma_rules = root / "etc/udev/rules.d/70-piano-camera.rules"
        assert 'SUBSYSTEM=="dma_heap"' in dma_rules.read_text() and 'TAG+="uaccess"' in dma_rules.read_text()
        bwmon = root / "etc/modules-load.d/piano-bwmon.conf"
        assert "icc-bwmon" in bwmon.read_text()
        stats = root / "etc/modules-load.d/piano-stats.conf"
        assert "qcom_stats" in stats.read_text()
        sleep_conf = root / "etc/systemd/sleep.conf.d/piano.conf"
        assert "MemorySleepMode=deep" in sleep_conf.read_text()
        qboot_drop = root / "etc/systemd/system/qbootctl.service.d/piano.conf"
        assert "boot_a.device" in qboot_drop.read_text()
        assert (root / "usr/bin/piano-collect-boot").stat().st_mode & 0o111
        check_debug_handoff(root, staged)
        check_piano_hardware_fixes(root)
        check_modified_legacy_power_override_is_preserved(root / "legacy-power-fixture")
        check_service_staging_order(root / "service-order-fixture")
        check_login_password(root)
        daemon = root / "usr/bin/adsprpcd"
        daemon.write_bytes(b"daemon fixture")
        daemon.chmod(0o755)
        services = {}
        for name in ("usr/lib/systemd/system/adsprpcd-sensorspd.service",
                     "usr/lib/systemd/system/adsprpcd-sensorspd.service.d/20-fedora-daemon.conf"):
            unit = root / name
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_text("[Service]\nExecStart=/usr/bin/adsprpcd sensorspd\n")
            services[name] = finalizer.sha256(unit)
        proof = {"fastrpc_daemon": {
            "commit": json.loads((ROOT / "sources.lock.json").read_text())["sources"]["fastrpc"]["commit"],
            "binary_sha256": finalizer.sha256(daemon), "service_sha256": services}}
        finalizer.verify_fastrpc_daemon(root, proof)
        unit.write_text("[Service]\nExecStart=/usr/bin/false\n")
        try:
            finalizer.verify_fastrpc_daemon(root, proof)
        except ValueError:
            pass
        else:
            raise AssertionError("modified FastRPC service accepted")
        unit.write_text("[Service]\nExecStart=/usr/bin/adsprpcd sensorspd\n")
        daemon.write_bytes(b"changed daemon fixture")
        try:
            finalizer.verify_fastrpc_daemon(root, proof)
        except ValueError:
            pass
        else:
            raise AssertionError("modified FastRPC daemon accepted")
        daemon.unlink()
        try:
            finalizer.verify_fastrpc_daemon(root, proof)
        except ValueError:
            pass
        else:
            raise AssertionError("missing FastRPC daemon accepted")
        check_kernel_install(root / "kernel-install-fixture")
    print("OK: manuals pruned, launcher normalized, runtime guards, debug handoff, "
          "kernel payload, device hardware fixes and default login password preserved")


def check_kernel_install(root):
    """kernel-piano 的 DNF 模块树必须逐字节等于本次封存内核。"""
    sys.path.insert(0, str(ROOT / "tests"))
    from kernel_fixture import make_kernel
    root.mkdir()
    kernel = make_kernel(root / "kernel", "7.2.9-fixture")
    release = "7.2.9-fixture"
    installed = root / "rootfs/usr/lib/modules" / release
    shutil.copytree(kernel / "modules/lib/modules" / release, installed)
    proof = finalizer.verify_kernel_install(root / "rootfs", kernel)
    assert proof == {"kernel_release": release, "module_count": 5,
                     "kernel_manifest_sha256": finalizer.sha256(kernel / "manifest.json")}, proof
    changed = next(installed.rglob("*.ko"))
    changed.write_bytes(changed.read_bytes() + b"drift")
    try:
        finalizer.verify_kernel_install(root / "rootfs", kernel)
    except ValueError as error:
        assert "module differs" in str(error), error
    else:
        raise AssertionError("a same-release kernel module tree with different bytes was accepted")


def check_piano_hardware_fixes(root):
    """真机修复必须成套出现：脚本可执行、单元被启用、模块随镜像走。"""
    old_logind = root / "etc/systemd/logind.conf.d/10-piano-powerkey.conf"
    old_dconf = root / "etc/dconf/db/local.d/00-piano-session"
    old_logind.parent.mkdir(parents=True)
    old_dconf.parent.mkdir(parents=True)
    old_logind.write_text("[Login]\nHandlePowerKey=ignore\n")
    old_dconf.write_text("[org/gnome/desktop/session]\nidle-delay=uint32 0\n"
                         "[org/gnome/desktop/screensaver]\nlock-enabled=false\n")
    staged = finalizer.stage_piano_hardware_fixes(root)
    clock_stamp = root / "var/lib/piano-clock/last"
    assert clock_stamp.is_file(), "fresh image has no usable offline clock floor"
    seed = int(clock_stamp.read_text().strip())
    assert seed > 1735689600, "build clock floor is not a usable date"
    # 实际执行镜像中的 restore 脚本；只将绝对状态路径改到 fixture，并隔离 date/logger。
    fake_bin = root / "clock-fixture-bin"
    fake_bin.mkdir()
    date = fake_bin / "date"
    date.write_text('#!/bin/sh\ncase "$*" in\n'
                    '  "-u +%s") printf "%s\\n" "$PIANO_FIXTURE_NOW" ;;\n'
                    '  -u\\ -s\\ @*) printf "%s\\n" "$3" > "$PIANO_FIXTURE_SET" ;;\n'
                    '  *) printf "fixture time\\n" ;;\nesac\n')
    date.chmod(0o755)
    logger = fake_bin / "logger"
    logger.write_text("#!/bin/sh\nexit 0\n")
    logger.chmod(0o755)
    script = root / "clock-fixture-restore"
    script.write_text((root / "usr/libexec/piano-clock-restore").read_text().replace(
        "STAMP=/var/lib/piano-clock/last", "STAMP=" + shlex.quote(str(clock_stamp))))
    capture = root / "clock-fixture-set"
    environment = {**os.environ, "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
                   "PIANO_FIXTURE_NOW": "0", "PIANO_FIXTURE_SET": str(capture)}
    subprocess.run(["/bin/sh", script], env=environment, check=True, capture_output=True)
    assert capture.read_text().strip() == "@" + str(seed), "offline 1970 boot was not corrected"
    capture.unlink()
    # systemd 的镜像 epoch 已把 1970 提到构建日，仍须继续恢复更晚的保存时间。
    subprocess.run(["/bin/sh", script], env={**environment, "PIANO_FIXTURE_NOW": str(seed - 3600)},
                   check=True, capture_output=True)
    assert capture.is_file() and capture.read_text().strip() == "@" + str(seed), \
        "an image-epoch clock newer than 2025 bypassed the saved clock floor"
    capture.unlink()
    subprocess.run(["/bin/sh", script], env={**environment, "PIANO_FIXTURE_NOW": str(seed + 3600)},
                   check=True, capture_output=True)
    assert not capture.exists(), "a sane running clock was overwritten"
    expected = {
        "usr/libexec/piano-clock-restore",
        "usr/libexec/piano-clock-save",
        "usr/lib/systemd/system/piano-clock-restore.service",
        "usr/lib/systemd/system/piano-clock-save.service",
        "usr/lib/systemd/system/piano-clock-save.timer",
        "usr/lib/systemd/system/piano-selinux-policy.service",
        "etc/systemd/system/piano-clock-save.service.d/restore-first.conf",
        "usr/share/piano/selinux/piano_hardware.pp",
    }
    # 第二道校正：即使 sysinit 那次没生效，multi-user 也会再设一次。
    dropin = (root / "etc/systemd/system/piano-clock-save.service.d/restore-first.conf").read_text()
    assert "ExecStartPre=/usr/libexec/piano-clock-restore" in dropin
    assert expected <= set(staged), sorted(set(staged))
    assert not old_logind.exists() and not old_dconf.exists(), "legacy bring-up overrides survived"
    for name in ("piano-clock-restore", "piano-clock-save"):
        assert (root / "usr/libexec" / name).stat().st_mode & 0o111, name
    # 兜底脚本本身不能把坏时间写回去，也不该在没有戳时动手。
    save = (root / "usr/libexec/piano-clock-save").read_text()
    assert "1735689600" in save and ".last.tmp" in save
    restore = (root / "usr/libexec/piano-clock-restore").read_text()
    assert "1735689600" in restore and '"$saved" -gt "$now"' in restore
    # 策略模块必须与 .te 源码一起在镜像里，且有安装单元兜底。
    assert (root / "usr/share/piano/selinux/piano_hardware.pp").stat().st_size > 0
    unit = (root / "usr/lib/systemd/system/piano-selinux-policy.service").read_text()
    assert "semodule -i /usr/share/piano/selinux/piano_hardware.pp" in unit


def check_modified_legacy_power_override_is_preserved(root):
    root.mkdir()
    custom = root / "etc/systemd/logind.conf.d/10-piano-powerkey.conf"
    generated = root / "etc/dconf/db/local.d/00-piano-session"
    custom.parent.mkdir(parents=True)
    generated.parent.mkdir(parents=True)
    custom.write_text("[Login]\nHandlePowerKey=ignore\n# local admin note\n")
    generated.write_bytes(finalizer.LEGACY_POWER_OVERRIDES[generated.relative_to(root).as_posix()])
    try:
        finalizer.stage_piano_hardware_fixes(root)
    except ValueError as error:
        assert "was modified; preserve it for review" in str(error), error
    else:
        raise AssertionError("modified legacy power override was silently removed")
    assert custom.read_text().endswith("# local admin note\n")
    assert generated.is_file(), "exact legacy file was removed before all paths were checked"
    assert not (root / "usr/lib/systemd/system/piano-clock-save.service").exists(), \
        "finalizer staged other changes before rejecting a modified override"


if __name__ == "__main__":
    main()
