#!/usr/bin/env python3
"""离线验证安装期 SSH 公钥注入：系统侧 helper 与两个安装器。

覆盖三条契约：

* 镜像不带 authorized_keys；公钥由安装器写进 ESP，首启服务再导入。
* `flash-linux.sh` / `install-f2fs.py` 只在摘要与 manifest 校验通过后注入，
  没传 `--ssh-key` 时行为不变，缺 `mcopy` 时明确失败。
* `usr/lib/piano/first-boot-ssh-key` 校验公钥格式、无文件时退出 0、重复运行
  不写重复行，并在导入后删除 ESP 上的公钥。

不接触设备，也不要求 root；`mkfs.vfat` 与 `mcopy` 可用时额外做一次真实
FAT 镜像往返，缺工具时打印 SKIP（与 check_esp_image.py 相同风格）。
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packages/piano-fedora-configs/usr/lib/piano/first-boot-ssh-key"
UNIT = ROOT / "packages/piano-fedora-configs/usr/lib/systemd/system/piano-first-boot-ssh-key.service"
SPEC = ROOT / "packages/piano-fedora-configs/piano-fedora-configs.spec"
MATRIX_PATHS = ROOT / "runtime/matrix-owned-paths.txt"
SHELL_INSTALLER = ROOT / "scripts/installer/flash-linux.sh"
PYTHON_INSTALLER = ROOT / "scripts/install-f2fs.py"
DOC = ROOT / "docs/installation.md"

# sha256 of the tablet serial this port was developed on. Stored as a digest so
# the check can catch a reintroduced serial without publishing it again.
REAL_SERIAL_SHA256 = "ea3f84c934ea8b87b1dad93328adda39b371b82d35a5b3bba21459653cbe1f47"

# Real wire-format blobs: the helper decodes base64, so the fixtures must be
# valid key material rather than plausible-looking filler.
ED25519 = ("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f"
           " user@example")
RSA = ("ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAICBgoOEhYaHiImKi4yNjo+QkZKTlJWWl5iZmpucnZ6f"
       "oKGio6SlpqeoqaqrrK2ur7CxsrO0tba3uLm6u7y9vr/AwcLDxMXGx8jJysvMzc7P0NHS09TV1tfY2drb"
       "3N3e3+Dh4uOAgYKDhIWGh4iJiouMjY6PkJGSk5SVlpeYmZqbnJ2en6ChoqOkpaanqKmqq6ytrq+wsbKz"
       "tLW2t7i5uru8vb6/wMHCw8TFxsfIycrLzM3Oz9DR0tPU1dbX2Nna29zd3t/g4eLjgIGCg4SFhoeIiYqL"
       "jI2Oj5CRkpOUlZaXmJmam5ydnp+goaKjpKWmp6ipqqusra6vsLGys7S1trc= user@example")


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def run_helper(key_file, home, extra_env=None):
    """Run the shipped helper against a fixture home, never the real one."""
    env = {**os.environ, "PIANO_SSH_KEY_FILE": str(key_file),
           "PIANO_SSH_USER": os.environ.get("USER", "nobody"),
           "PIANO_SSH_HOME": str(home)}
    env.update(extra_env or {})
    return subprocess.run(["/bin/sh", str(HELPER)], env=env, capture_output=True, text=True)


def check_helper(root, failures):
    def check(condition, message):
        if not condition:
            failures.append("helper: " + message)

    check(HELPER.is_file() and HELPER.stat().st_mode & 0o111, "helper is missing or not executable")

    # 1. No key file: exit 0 and touch nothing.
    home = root / "home-none"
    home.mkdir()
    result = run_helper(root / "absent.pub", home)
    check(result.returncode == 0, f"missing key file must exit 0, got {result.returncode}: {result.stderr}")
    check(not (home / ".ssh").exists(), "missing key file must not create ~/.ssh")

    # 2. A valid Ed25519 key is imported with the documented modes.
    home = root / "home-ed25519"
    home.mkdir()
    key = root / "id_ed25519.pub"
    key.write_text(ED25519 + "\n")
    result = run_helper(key, home)
    check(result.returncode == 0, f"valid Ed25519 key was rejected: {result.stderr}")
    directory, authorized = home / ".ssh", home / ".ssh" / "authorized_keys"
    check(directory.is_dir() and directory.stat().st_mode & 0o777 == 0o700,
          f"~/.ssh must be 0700, got {oct(directory.stat().st_mode & 0o777) if directory.exists() else 'missing'}")
    check(authorized.is_file() and authorized.stat().st_mode & 0o777 == 0o600,
          f"authorized_keys must be 0600, got {oct(authorized.stat().st_mode & 0o777) if authorized.exists() else 'missing'}")
    check(authorized.read_text().split() == ED25519.split(), "imported line differs from the key file")
    check(not key.exists(), "the ESP copy must be removed after import")
    check(str(authorized).startswith(str(home)), "helper wrote outside the fixture home")

    # 3. Idempotent: a second run with the same key adds no line.
    key.write_text(ED25519 + "\n")
    result = run_helper(key, home)
    check(result.returncode == 0, f"second run failed: {result.stderr}")
    check(len(authorized.read_text().splitlines()) == 1, "repeated run duplicated the key")
    check(not key.exists(), "second run left the ESP copy behind")

    # 4. A trailing comment change is the same key, not a second line.
    key.write_text(ED25519.rsplit(" ", 1)[0] + " other@comment\n")
    result = run_helper(key, home)
    check(result.returncode == 0, f"comment-only change failed: {result.stderr}")
    check(len(authorized.read_text().splitlines()) == 1, "a comment-only change was appended as a new key")

    # 5. ssh-rsa is accepted.
    home_rsa = root / "home-rsa"
    home_rsa.mkdir()
    key_rsa = root / "id_rsa.pub"
    key_rsa.write_text(RSA + "\n")
    result = run_helper(key_rsa, home_rsa)
    check(result.returncode == 0, f"valid ssh-rsa key was rejected: {result.stderr}")
    check((home_rsa / ".ssh/authorized_keys").read_text().split()[0] == "ssh-rsa", "ssh-rsa was not imported")

    # 6. CRLF and surrounding whitespace are tolerated.
    home_crlf = root / "home-crlf"
    home_crlf.mkdir()
    key_crlf = root / "id_crlf.pub"
    key_crlf.write_bytes(("  " + ED25519 + "  \r\n").encode())
    result = run_helper(key_crlf, home_crlf)
    check(result.returncode == 0, f"CRLF key was rejected: {result.stderr}")
    check(len((home_crlf / ".ssh/authorized_keys").read_text().splitlines()) == 1, "CRLF key imported oddly")

    # 7. Rejections leave an existing authorized_keys untouched.
    home_bad = root / "home-bad"
    home_bad.mkdir()
    (home_bad / ".ssh").mkdir()
    (home_bad / ".ssh/authorized_keys").write_text("ssh-ed25519 existing\n")
    (home_bad / ".ssh").chmod(0o700)
    before = (home_bad / ".ssh/authorized_keys").read_text()
    cases = {
        "private-key type": "-----BEGIN OPENSSH PRIVATE KEY-----\n",
        "two lines": ED25519 + "\n" + ED25519 + "\n",
        "unsupported type": "ecdsa-sha2-nistp256 AAAA user@example\n",
        "no key material": "ssh-ed25519\n",
        "invalid base64": "ssh-ed25519 !!!!not-base64!!!! user@example\n",
        "empty file": "",
    }
    for name, text in cases.items():
        key = root / "bad.pub"
        key.write_text(text)
        result = run_helper(key, home_bad)
        check(result.returncode != 0, f"{name} was accepted")
        check(key.exists(), f"{name} consumed the ESP copy before failing")
        check((home_bad / ".ssh/authorized_keys").read_text() == before,
              f"{name} modified authorized_keys")

    # 8. The unit carries the documented ordering and condition.
    text = UNIT.read_text()
    for token in ("Type=oneshot", "ConditionPathExists=/boot/efi/piano-ssh-key.pub",
                  "After=local-fs.target", "Before=sshd.service",
                  "ExecStart=/usr/lib/piano/first-boot-ssh-key", "WantedBy=multi-user.target"):
        check(token in text, f"unit is missing {token!r}")

    # 9. Packaging: spec and the matrix-owned path list claim both files.
    spec = SPEC.read_text()
    check("%{_prefix}/lib/piano/first-boot-ssh-key" in spec, "spec does not package the helper")
    check("%{_unitdir}/piano-first-boot-ssh-key.service" in spec, "spec does not package the unit")
    owned = MATRIX_PATHS.read_text().splitlines()
    for path in ("/usr/lib/piano/first-boot-ssh-key",
                 "/usr/lib/systemd/system/piano-first-boot-ssh-key.service"):
        check(path in owned, f"matrix-owned-paths.txt does not list {path}")
    # The list is alphabetical apart from a pre-existing tail entry, so check
    # that each new line sits next to its alphabetical neighbours.
    helper = owned.index("/usr/lib/piano/first-boot-ssh-key")
    check(owned[helper - 1] == "/usr/lib/piano/camera-start", "helper is in the wrong position")
    check(owned[helper + 1] == "/usr/lib/piano/piano-camerad", "helper is in the wrong position")
    unit = owned.index("/usr/lib/systemd/system/piano-first-boot-ssh-key.service")
    check(owned[unit - 1] == "/usr/lib/systemd/system/piano-fake-tablet-mode.service",
          "unit is in the wrong position")
    check(owned[unit + 1] == "/usr/lib/systemd/system/piano-touch.service",
          "unit is in the wrong position")

    # 10. The build-time key provisioning is gone. Comments may still explain
    # the change, so only executable lines count.
    capacity = "\n".join(line for line in
                          (ROOT / "scripts/prepare-root-capacity.py").read_text().splitlines()
                          if not line.lstrip().startswith("#"))
    for token in ("--ssh-public-key", "ssh_public_key_fingerprint", "authorized_keys"):
        check(token not in capacity, f"prepare-root-capacity.py still contains {token!r} in code")
    workflow = (ROOT / ".github/workflows/prepare-install.yml").read_text()
    for token in ("ssh_public_key", "device-access.pub"):
        check(token not in workflow, f"prepare-install.yml still contains {token!r}")
    finalizer = (ROOT / "scripts/finalize-fedora-rootfs.py").read_text()
    check("piano-first-boot-ssh-key.service" in finalizer,
          "finalizer does not enable the first-boot SSH key unit")


STUB_FASTBOOT = r"""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
with open(os.environ["STUB_LOG"], "a") as fh:
    fh.write(" ".join(args) + "\n")
if "--version" in args:
    print("fastboot version 35.0.2-fixture")
    sys.exit(0)
cmd = None
i = 0
# -s/-S 都带一个取值；把 -S 的 128M 当子命令会让桩拒绝真实安装器的调用。
VALUED = {"-s", "-S", "--slot", "-p", "-b", "-c"}
while i < len(args):
    a = args[i]
    if a in VALUED:
        i += 2
        continue
    if a.startswith("-"):
        i += 1
        continue
    cmd = a
    break
if cmd == "devices":
    print("SN-TEST-1\tfastboot")
elif cmd == "getvar":
    name = args[-1]
    table = {"product": "piano", "current-slot": "a", "unlocked": "yes",
             "is-userspace": "no",
             "partition-size:boot_a": format(100663296, "x"),
             "partition-size:sunuefi_esp": format(537899008, "x"),
             "partition-size:sunuefi_root": format(515225600000, "x")}
    print(f"{name}: {table.get(name, 'unknown')}")
    print("Finished. Total time: 0.001s")
elif cmd in ("flash", "set_active", "reboot"):
    print(f"Sending '{cmd}' command... OKAY")
    print("Finished. Total time: 0.001s")
else:
    print(f"unknown command {cmd}", file=sys.stderr)
    sys.exit(1)
"""

STUB_MCOPY = r"""#!/bin/sh
# Record the call and emulate the FAT write. Arguments: -o -i <image> <source> ::<guest>
printf '%s\n' "$*" >> "$MCOPY_LOG"
image=""
source=""
previous=""
for arg in "$@"; do
    if [ "$previous" = "-i" ]; then
        image="$arg"
    fi
    case "$arg" in
        -o|-i|::*) ;;
        -*) ;;
        *) source="$arg" ;;
    esac
    previous="$arg"
done
: "${image:?stub mcopy needs -i <image>}"
: "${source:?stub mcopy needs a source}"
mkdir -p "$STUB_DIR"
cat "$source" > "$STUB_DIR/injected.pub"
# Simulate the guest write so the image digest changes like a real mcopy.
printf '\n# %s\n' "$(cat "$source")" >> "$image"
"""


def make_boot_linux_image(root):
    """A boot_a image with the shape SM8750 ABL accepts.

    The bundle's boot_sunuefi.img is the ESP payload and must never be flashed
    here, so the fixtures keep both: the payload in the staging directory and a
    device image in its own directory with its own SHA256SUMS.
    """
    import struct

    folder = root / "boot"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "boot_linux.img"
    data = bytearray(100663296)
    data[0:8] = b"ANDROID!"
    struct.pack_into("<I", data, 16, 0)
    struct.pack_into("<I", data, 40, 4)
    data[8192:8208] = b"SUNUEFI-SPLITv1\0"
    data[16384:16399] = b"SUNUEFI-NEXTv1\0"
    # 与 check_manual_flash_flow.py 相同的合体 BOOT 形态：分区内的 AVB vbmeta
    # 块加上末尾 footer，算法 NONE（0），没有 auth 字节。
    vbmeta_offset, vbmeta_size = 4096, 256
    vbmeta = bytearray(vbmeta_size)
    vbmeta[0:4] = b"AVB0"
    struct.pack_into(">I", vbmeta, 4, 1)
    struct.pack_into(">Q", vbmeta, 20, 256)
    struct.pack_into(">I", vbmeta, 28, 0)
    data[vbmeta_offset:vbmeta_offset + vbmeta_size] = vbmeta
    footer = bytearray(64)
    footer[0:4] = b"AVBf"
    struct.pack_into(">I", footer, 4, 1)
    struct.pack_into(">Q", footer, 12, 8192 + 512)
    struct.pack_into(">Q", footer, 20, vbmeta_offset)
    struct.pack_into(">Q", footer, 28, vbmeta_size)
    data[-64:] = footer
    path.write_bytes(bytes(data))
    (folder / "SHA256SUMS").write_text(f"{sha256(path)}  {path.name}\n")
    return path


def make_asset_set(root):
    root.mkdir(parents=True)
    root_install = root / "root-install"
    root_install.mkdir()
    (root / "boot_sunuefi.img").write_bytes(b"BOOT" * 64)
    (root / "sunuefi_esp.img").write_bytes(b"ESP" * 128)
    sparse = root_install / "pianoroot.f2fs.sparse.img"
    sparse.write_bytes(b"ROOTFS" * 512)
    bundle_manifest = root / "bundle-manifest.json"
    root_manifest = root / "root-image-manifest.json"
    bundle_manifest.write_text('{"status":"fixture"}\n')
    root_manifest.write_text('{"format":"raw-f2fs"}\n')
    (root_install / "root-install-manifest.json").write_text(json.dumps({
        "android_slot": "_a",
        "expanded_bytes": 515225600000,
        "source_bundle_manifest_sha256": sha256(bundle_manifest),
        "source_root_manifest_sha256": sha256(root_manifest),
        "transport": {"file": sparse.name, "sha256": sha256(sparse),
                      "bytes": sparse.stat().st_size},
    }, indent=2) + "\n")
    for folder, names in ((root, ("boot_sunuefi.img", "sunuefi_esp.img")),
                          (root_install, (sparse.name, "root-install-manifest.json"))):
        (folder / "SHA256SUMS").write_text("".join(
            f"{sha256(folder / name)}  {name}\n" for name in names))
    return root, root_install


def check_shell_installer(root, failures):
    def check(condition, message):
        if not condition:
            failures.append("flash-linux.sh: " + message)

    bindir = root / "bin"
    bindir.mkdir()
    fastboot = bindir / "fastboot"
    fastboot.write_text(STUB_FASTBOOT)
    fastboot.chmod(0o755)
    mcopy = bindir / "mcopy"
    mcopy.write_text(STUB_MCOPY)
    mcopy.chmod(0o755)

    staging, _ = make_asset_set(root / "shell")
    boot_linux = make_boot_linux_image(root)
    key = root / "id_ed25519.pub"
    key.write_text(ED25519 + "\n")
    log = root / "fastboot.log"
    mcopy_log = root / "mcopy.log"
    injected = root / "injected"
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}",
           "STUB_LOG": str(log), "MCOPY_LOG": str(mcopy_log), "STUB_DIR": str(injected)}

    def invoke(*extra, staging=staging):
        return subprocess.run(
            ["bash", str(SHELL_INSTALLER), "-y", "--no-reboot",
             "--boot", str(boot_linux),
             "--esp", str(staging / "sunuefi_esp.img"),
             "--root", str(staging / "root-install/pianoroot.f2fs.sparse.img"),
             "--root-sha256sums", str(staging / "root-install/SHA256SUMS"),
             *extra],
            cwd=staging, env=env, capture_output=True, text=True)

    # 1. With a key: mcopy runs once, before the ESP flash, and the new digest
    #    is printed for the readback comparison.
    before = sha256(staging / "sunuefi_esp.img")
    result = invoke("--ssh-key", str(key))
    check(result.returncode == 0, f"--ssh-key run failed: {result.stdout[-400:]}{result.stderr[-400:]}")
    calls = [line for line in mcopy_log.read_text().splitlines() if line.strip()] if mcopy_log.exists() else []
    check(len(calls) == 1, f"expected exactly one mcopy call, got {calls}")
    check(calls and "::/piano-ssh-key.pub" in calls[0], f"mcopy target path is wrong: {calls}")
    check((injected / "injected.pub").read_text().strip() == ED25519.strip(),
          "mcopy received the wrong key file")
    flash_log = log.read_text().splitlines()
    esp_flash = next((i for i, line in enumerate(flash_log) if " flash sunuefi_esp " in " " + line + " "), None)
    check(esp_flash is not None, "sunuefi_esp was never flashed")
    check("SSH public key written to" in result.stdout, "installer does not report the injection")
    # The key goes into the ESP image that is flashed, after every check passed.
    check(sha256(staging / "sunuefi_esp.img") != before,
          "the ESP image was not modified; the key must be written into it")
    # The printed digest must describe the injected image, not the original.
    check(sha256(staging / "sunuefi_esp.img") in result.stdout,
          "installer does not print the post-injection digest")

    # 2. Without a key: mcopy is never called and the flash still happens.
    staging2, _ = make_asset_set(root / "shell-nokey")
    mcopy_log.unlink(missing_ok=True)
    log.unlink(missing_ok=True)
    result = invoke(staging=staging2)
    check(result.returncode == 0, f"no-key run failed: {result.stderr[-400:]}")
    check(not mcopy_log.exists() or not mcopy_log.read_text().strip(),
          "mcopy ran without --ssh-key")
    check(log.exists() and " flash sunuefi_esp " in " " + log.read_text() + " ",
          "no-key run did not flash the ESP")

    # 3. A checksum failure stops before injection.
    staging3, _ = make_asset_set(root / "shell-badsum")
    (staging3 / "sunuefi_esp.img").write_bytes(b"tampered" * 16)
    mcopy_log.unlink(missing_ok=True)
    result = invoke("--ssh-key", str(key), staging=staging3)
    check(result.returncode != 0, "tampered ESP image was accepted")
    check(not mcopy_log.exists() or not mcopy_log.read_text().strip(),
          "mcopy ran before the checksum check passed")

    # 4. A malformed key is rejected before any device write.
    staging4, _ = make_asset_set(root / "shell-badkey")
    bad = root / "private.key"
    bad.write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    mcopy_log.unlink(missing_ok=True)
    log4 = root / "fastboot-badkey.log"
    result = subprocess.run(
        ["bash", str(SHELL_INSTALLER), "-y", "--no-reboot",
         "--boot", str(boot_linux),
         "--esp", str(staging4 / "sunuefi_esp.img"),
         "--root", str(staging4 / "root-install/pianoroot.f2fs.sparse.img"),
         "--root-sha256sums", str(staging4 / "root-install/SHA256SUMS"),
         "--ssh-key", str(bad)],
        cwd=staging4, env={**env, "STUB_LOG": str(log4)}, capture_output=True, text=True)
    check(result.returncode != 0, "a private key was accepted as --ssh-key")
    check(not mcopy_log.exists() or not mcopy_log.read_text().strip(),
          "mcopy ran with a rejected key")
    check(not log4.exists() or not any(" flash " in " " + line + " "
                                       for line in log4.read_text().splitlines()),
          "a rejected key still flashed partitions")

    # 6. The bundle's ESP payload is refused as a boot_a image.
    staging6, _ = make_asset_set(root / "shell-payload-boot")
    log6 = root / "fastboot-payload.log"
    result = subprocess.run(
        ["bash", str(SHELL_INSTALLER), "-y", "--no-reboot",
         "--boot", str(staging6 / "boot_sunuefi.img"),
         "--esp", str(staging6 / "sunuefi_esp.img"),
         "--root", str(staging6 / "root-install/pianoroot.f2fs.sparse.img"),
         "--root-sha256sums", str(staging6 / "root-install/SHA256SUMS")],
        cwd=staging6, env={**env, "STUB_LOG": str(log6)}, capture_output=True, text=True)
    check(result.returncode != 0, "boot_sunuefi.img was accepted as a boot_a image")
    check("ESP payload" in result.stdout + result.stderr,
          "the refusal does not explain that boot_sunuefi.img is the ESP payload")
    check(not log6.exists() or not any(" flash " in " " + line + " "
                                       for line in log6.read_text().splitlines()),
          "the installer flashed before refusing the payload")

    # 5. Missing mcopy: clear error naming mtools, and no ESP flash. Build a PATH
    #    that has the tools the installer needs but no mcopy at all, so the
    #    check cannot pass by finding the host's real mtools.
    bindir_nomcopy = root / "bin-nomcopy"
    bindir_nomcopy.mkdir()
    needed = ("bash", "sh", "awk", "sed", "grep", "head", "tr", "wc", "base64",
              "stat", "dirname", "basename", "cat", "id", "sha256sum", "python3")
    for name in needed:
        found = shutil.which(name)
        if found:
            (bindir_nomcopy / name).symlink_to(found)
    (bindir_nomcopy / "fastboot").write_text(STUB_FASTBOOT)
    (bindir_nomcopy / "fastboot").chmod(0o755)
    staging5, _ = make_asset_set(root / "shell-nomcopy")
    log5 = root / "fastboot-nomcopy.log"
    result = subprocess.run(
        [str(bindir_nomcopy / "bash"), str(SHELL_INSTALLER), "-y", "--no-reboot",
         "--boot", str(boot_linux),
         "--esp", str(staging5 / "sunuefi_esp.img"),
         "--root", str(staging5 / "root-install/pianoroot.f2fs.sparse.img"),
         "--root-sha256sums", str(staging5 / "root-install/SHA256SUMS"),
         "--ssh-key", str(key)],
        cwd=staging5,
        env={**os.environ, "PATH": str(bindir_nomcopy), "STUB_LOG": str(log5)},
        capture_output=True, text=True)
    check(result.returncode != 0, "missing mcopy did not stop the installer")
    check("mtools" in result.stdout + result.stderr, "missing-mcopy error does not mention mtools")
    check(not log5.exists() or not any(" flash sunuefi_esp " in " " + line + " "
                                       for line in log5.read_text().splitlines()),
          "the ESP was flashed without the requested key")


def check_python_installer(root, failures):
    def check(condition, message):
        if not condition:
            failures.append("install-f2fs.py: " + message)

    source = PYTHON_INSTALLER.read_text()
    # Ordering is a source-level contract: the injection helper is called after
    # the manifest checks and before the flash loop.
    inject_at = source.index("inject_ssh_key(bundle / \"sunuefi_esp.img\"")
    erase_at = source.index('device.call(["fastboot", "-s", identity["factory_serial"], "erase", "userdata"]')
    flash_at = source.index('"fastboot", "-s", identity["factory_serial"], "-S", "128M",\n'
                            '                         "flash", name, str(image)')
    require_at = source.index('require(before == expected_hashes[name]')
    check(erase_at < inject_at < require_at < flash_at,
          "inject_ssh_key must run after the manifest checks and before the flash loop")
    # A bad key must fail before the destructive GPT write and userdata erase.
    preflight = source.index("if ssh_key is not None:")
    gpt_write = source.index('journal["device_writes"] = True')
    check(preflight < gpt_write,
          "the SSH key is validated only after the destructive writes start")

    sys.path.insert(0, str(ROOT / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("installer_key", PYTHON_INSTALLER)
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)

    key = root / "id_ed25519.pub"
    key.write_text(ED25519 + "\n")
    check(installer.validate_ssh_key(key) == (ED25519.split()[0], ED25519.split()[1]),
          "validate_ssh_key rejected a valid Ed25519 key")
    key_rsa = root / "id_rsa.pub"
    key_rsa.write_text(RSA + "\n")
    installer.validate_ssh_key(key_rsa)
    for name, text in (("private", "-----BEGIN OPENSSH PRIVATE KEY-----\n"),
                       ("two lines", ED25519 + "\n" + ED25519 + "\n"),
                       ("unsupported", "ecdsa-sha2-nistp256 AAAA x\n"),
                       ("bad base64", "ssh-ed25519 !!!! x\n")):
        bad = root / f"{name}.pub"
        bad.write_text(text)
        try:
            installer.validate_ssh_key(bad)
        except ValueError:
            pass
        else:
            failures.append(f"install-f2fs.py: validate_ssh_key accepted a {name} file")

    # Injection writes to a copy and returns the new digest. mcopy needs a real
    # FAT filesystem, so this part is skipped without dosfstools/mtools.
    if not (shutil.which("mkfs.vfat") and shutil.which("mcopy")):
        print("SKIP: mkfs.vfat/mtools unavailable; inject_ssh_key was not executed")
    else:
        esp = root / "esp.img"
        with esp.open("xb") as stream:
            stream.truncate(16 * 1024 * 1024)
        subprocess.run(["mkfs.vfat", "--invariant", "-F", "32", "-S", "4096",
                        "-n", "SUNUEFI_ESP", str(esp)], check=True, capture_output=True)
        original = sha256(esp)
        target = root / "session-esp.img"
        digest = installer.inject_ssh_key(esp, key, target)
        check(digest != original, "inject_ssh_key returned the unchanged digest")
        check(sha256(target) == digest, "inject_ssh_key digest does not describe the copy")
        check(sha256(esp) == original, "inject_ssh_key modified the input image")
        readback = root / "injected-readback.pub"
        subprocess.run(["mcopy", "-i", str(target), "::/piano-ssh-key.pub", str(readback)],
                       check=True, capture_output=True)
        check(readback.read_text().strip() == ED25519.strip(),
              "injected ESP does not contain the expected key at /piano-ssh-key.pub")


def check_docs(failures):
    doc = DOC.read_text()
    for token in ("--ssh-key", "mcopy", "mtools", "/piano-ssh-key.pub"):
        if token not in doc:
            failures.append(f"docs/installation.md: missing {token!r}")
    # The serial this repository was brought up on must not come back into the
    # docs. Only its digest lives here, so the guard does not republish it.
    for lineno, line in enumerate(doc.splitlines(), 1):
        for digits in re.findall(r"\d{6,}", line):
            if hashlib.sha256(digits.encode()).hexdigest() == REAL_SERIAL_SHA256:
                failures.append(f"docs/installation.md:{lineno}: still contains a real device serial")
    readme = (ROOT / "scripts/installer/README-INSTALL.txt").read_text()
    for token in ("--ssh-key", "mtools", "piano-ssh-key.pub"):
        if token not in readme:
            failures.append(f"README-INSTALL.txt: missing {token!r}")
    bat = (ROOT / "scripts/installer/flash-linux.bat").read_text()
    if "mcopy" not in bat:
        failures.append("flash-linux.bat: no manual mcopy hint for the SSH key")


def check_real_vfat(root, failures):
    """Optional: a real FAT image roundtrip proves mcopy writes the guest path."""
    root.mkdir(parents=True, exist_ok=True)
    if not (shutil.which("mkfs.vfat") and shutil.which("mcopy") and shutil.which("mdir")):
        print("SKIP: mkfs.vfat/mtools unavailable; only the stubbed mcopy path was checked")
        return
    image = root / "real-esp.img"
    with image.open("xb") as stream:
        stream.truncate(16 * 1024 * 1024)
    subprocess.run(["mkfs.vfat", "--invariant", "-F", "32", "-S", "4096", "-n", "SUNUEFI_ESP",
                    str(image)], check=True, capture_output=True)
    key = root / "id_ed25519.pub"
    key.write_text(ED25519 + "\n")
    subprocess.run(["mcopy", "-o", "-i", str(image), str(key), "::/piano-ssh-key.pub"],
                   check=True, capture_output=True)
    readback = root / "readback.pub"
    subprocess.run(["mcopy", "-i", str(image), "::/piano-ssh-key.pub", str(readback)],
                   check=True, capture_output=True)
    if readback.read_text().strip() != ED25519.strip():
        failures.append("real mcopy roundtrip did not preserve the public key")


def main():
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        check_helper(root, failures)
        check_shell_installer(root, failures)
        check_python_installer(root, failures)
        check_real_vfat(root, failures)
    check_docs(failures)

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: 首启公钥 helper 幂等且校验格式，两个安装器都在校验后注入，"
          "镜像与文档不再带预置 authorized_keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
