#!/usr/bin/env python3
"""端到端验证文档里的手动刷机命令序列（用桩 fastboot，不接触设备）。

需求原文（2026-10-16 20:17）：“可以有一键式的，但是一定要有怎么自己手动刷入的
（包括用到 release 的哪些内容等等），并且要验证。”

`tests/check_flash_script_guard.py` 只做静态检查（一键脚本没有 erase、只写三个
分区）。本测试再进一步：把 `docs/installation.md` 手动章节里的命令**真的
提取出来执行一遍**，配一个桩 `fastboot`，验证

* 文档里的命令逐条可执行，没有笔误或引用了不存在的资产名；
* 写入顺序是 set_active b -> boot_b -> sunuefi_esp -> sunuefi_root；
* 全程没有任何 erase/-w/format；
* 两份真实摘要（deploy-bundle 与 root-install）分别校验通过；
* root-install 的来源摘要与当前 deploy-bundle 不一致时必须失败；
* 首次分区执行器在 GPT 写入后显式执行 `fastboot erase userdata`。
* 首装请求页指向 `boot_a`，Linux payload 才写入 `boot_b`。

桩 fastboot 会把每次调用记进日志并返回合理输出，不触碰任何真实设备。
"""

from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "installation.md"

# The manual chapter is delimited by these headings.
START = "## 三之二、 手动刷机"
END = "## 四、 从原始 GPT/UFS 开始的首次安装"

STUB = r"""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
log = os.environ["STUB_LOG"]
with open(log, "a") as fh:
    fh.write(" ".join(args) + "\n")

if not args:
    print("usage: fastboot ...", file=sys.stderr)
    sys.exit(1)

if "--version" in args:
    print("fastboot version 35.0.2-fixture")
    sys.exit(0)

cmd = None
i = 0
while i < len(args):
    a = args[i]
    if a == "-s":
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
    table = {
        "product": "piano",
        "current-slot": os.environ.get("STUB_CURRENT_SLOT", "a"),
        "unlocked": "yes",
        "is-userspace": "no",
        # Android fastboot prints these as bare hexadecimal; the installer must
        # not compare that text directly with decimal manifest bytes.
        "partition-size:boot_b": format(100663296, "x"),
        "partition-size:sunuefi_esp": format(537899008, "x"),
        "partition-size:sunuefi_root": format(515225600000, "x"),
    }
    print(f"{name}: {table.get(name, 'unknown')}")
    print("Finished. Total time: 0.001s")
elif cmd == "fetch":
    # The documented readback: copy the source image to the requested file.
    part, out = args[-2], args[-1]
    src = {"sunuefi_esp": os.environ.get("ESP_IMG", "")}.get(part, "")
    if src and os.path.exists(src):
        import shutil as sh
        sh.copyfile(src, out)
    else:
        print("FAILED (remote: 'unknown partition')", file=sys.stderr)
        sys.exit(1)
    print("Sending fetch command... OKAY")
    print("Finished. Total time: 0.001s")
elif cmd in ("flash", "set_active", "reboot"):
    print(f"Sending '{cmd}' command... OKAY")
    print("Finished. Total time: 0.001s")
else:
    print(f"unknown command {cmd}", file=sys.stderr)
    sys.exit(1)
"""


def extract_manual_chapter(text):
    start = text.index(START)
    end = text.index(END, start)
    return text[start:end]


def extract_checksum_blocks(text):
    """Return checksum commands from the asset-list and manual chapters.

    The file distinguishes deploy-bundle and root-install in two places. Both
    need to execute against a realistic pair of artifact directories.
    """
    start = text.index("### 1. 获取 Release 资产")
    end = text.index("### 3. 确认设备和槽位")
    return re.findall(r"```sh\n(.*?)```", text[start:end], re.DOTALL)


def extract_shell_blocks(chapter):
    """Return the contents of every ```sh fenced block in the chapter."""
    return re.findall(r"```sh\n(.*?)```", chapter, re.DOTALL)


def executed_commands(block):
    """Yield commands that can run without a live device or mtools."""
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(("fastboot", "sha256sum", "(cd ", "head ")):
            yield line


def make_asset_set(root, cross_bundle=False):
    """Build two artifacts with the same layout prepare-install publishes."""
    staging = root
    root_install = root / "root-install"
    root_install.mkdir()
    boot, esp = staging / "boot_sunuefi.img", staging / "sunuefi_esp.img"
    sparse = root_install / "pianoroot.f2fs.sparse.img"
    boot.write_bytes(b"BOOT" * 64)
    esp.write_bytes(b"ESP" * 128)
    sparse.write_bytes(b"ROOTFS" * 512)

    # Use the same digest shape as real manifests; values only need to be
    # stable for the provenance binding test.
    bundle_manifest = staging / "bundle-manifest.json"
    root_manifest = staging / "root-image-manifest.json"
    bundle_manifest.write_text('{"status":"fixture"}\n')
    root_manifest.write_text('{"format":"raw-f2fs"}\n')
    root_install_manifest = {
        "android_slot": "_a",
        "expanded_bytes": 515225600000,
        "source_bundle_manifest_sha256": hashlib.sha256(
            (bundle_manifest.read_bytes() if not cross_bundle else b"other-bundle")).hexdigest(),
        "source_root_manifest_sha256": hashlib.sha256(root_manifest.read_bytes()).hexdigest(),
        "transport": {
            "file": sparse.name,
            "sha256": hashlib.sha256(sparse.read_bytes()).hexdigest(),
            "bytes": sparse.stat().st_size,
        },
    }
    (root_install / "root-install-manifest.json").write_text(
        json.dumps(root_install_manifest, indent=2) + "\n")

    for folder, names in ((staging, (boot.name, esp.name)),
                          (root_install, (sparse.name, "root-install-manifest.json"))):
        sums = []
        for name in names:
            digest = hashlib.sha256((folder / name).read_bytes()).hexdigest()
            sums.append(f"{digest}  {name}")
        (folder / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    return staging, root_install


def make_boot_linux_image(root):
    """A boot_b image that satisfies the SM8750 ABL contract.

    The bundle's boot_sunuefi.img is the ESP payload and the installer refuses
    it for boot_b, so these fixtures keep a separate device image with its own
    SHA256SUMS, exactly like prepare-device-boot.py produces.
    """
    import struct

    folder = root / "device-boot"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "boot_linux.img"
    data = bytearray(100663296)
    data[0:8] = b"ANDROID!"
    struct.pack_into("<I", data, 16, 0x0026C059)
    struct.pack_into("<I", data, 40, 4)
    data[4096:4100] = b"AVB0"
    data[8192:8207] = b"SUNUEFI-NEXTv1\0"
    path.write_bytes(bytes(data))
    (folder / "SHA256SUMS").write_text(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")
    return path


def copy_asset_set(source, destination):
    """Copy a fixture deploy-bundle and root-install into one staging dir."""
    destination.mkdir()
    for asset in ("boot_sunuefi.img", "sunuefi_esp.img", "SHA256SUMS",
                  "bundle-manifest.json", "root-image-manifest.json"):
        shutil.copy2(source / asset, destination / asset)
    shutil.copytree(source / "root-install", destination / "root-install")
    return destination


def invoke_installer(staging, env, *extra, boot=None):
    return subprocess.run(
        [
            str(ROOT / "scripts/installer/flash-linux.sh"), "-y", "--no-reboot",
            "--boot", str(boot or staging / "boot_sunuefi.img"),
            "--esp", str(staging / "sunuefi_esp.img"),
            "--root", str(staging / "root-install/pianoroot.f2fs.sparse.img"),
            "--root-sha256sums", str(staging / "root-install/SHA256SUMS"),
            *extra,
        ],
        cwd=staging, env=env, capture_output=True, text=True)


def assert_no_device_write(log):
    if not log.exists():
        return True
    return not any(" set_active " in " " + line + " " or " flash " in " " + line + " "
                   for line in log.read_text().splitlines())


def main():
    failures = []
    if not DOC.is_file():
        print(f"FAIL: missing {DOC.relative_to(ROOT)}", file=sys.stderr)
        return 1
    document = DOC.read_text(encoding="utf-8")
    chapter = extract_manual_chapter(document)
    blocks = extract_checksum_blocks(document) + extract_shell_blocks(chapter)
    if not blocks:
        print("FAIL: manual chapter has no shell blocks", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # A stub fastboot plus fake release assets, so every documented command
        # can actually run. Sizes are small; the point is the command sequence.
        bindir = work / "bin"
        bindir.mkdir()
        stub = bindir / "fastboot"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)

        staging, root_install = make_asset_set(work)
        device_boot = make_boot_linux_image(work)
        bundle = staging
        boot = bundle / "boot_sunuefi.img"
        esp = bundle / "sunuefi_esp.img"
        root_sparse = root_install / "pianoroot.f2fs.sparse.img"

        log = work / "fastboot.log"
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "STUB_LOG": str(log),
            "ESP_IMG": str(esp),
        }

        ran = 0
        executed = []
        for block in blocks:
            for line in executed_commands(block):
                executed.append(line)
                # The manual chapter runs from the staging directory, where
                # deploy-bundle files are at the top level and root-install is
                # a sibling subdirectory.
                res = subprocess.run(line, shell=True, cwd=staging, env=env,
                                     capture_output=True, text=True)
                ran += 1
                if res.returncode != 0:
                    failures.append(
                        f"documented command failed: {line}\n"
                        f"    stdout: {res.stdout.strip()[:200]}\n"
                        f"    stderr: {res.stderr.strip()[:200]}")

        if ran == 0:
            failures.append("no documented commands were executed")
        if "sha256sum -c SHA256SUMS" not in executed:
            failures.append("manual flow does not verify deploy-bundle/SHA256SUMS")
        if "(cd root-install && sha256sum -c SHA256SUMS)" not in executed:
            failures.append("manual flow does not verify root-install/SHA256SUMS")

        # Verify the write sequence the stub recorded.
        calls = [l.split() for l in log.read_text().splitlines() if l.strip()]
        verbs = []
        for args in calls:
            if "flash" in args:
                i = args.index("flash")
                verbs.append(("flash", args[i + 1] if i + 1 < len(args) else ""))
            elif "set_active" in args:
                i = args.index("set_active")
                verbs.append(("set_active", args[i + 1] if i + 1 < len(args) else ""))

        # The flash sequence, optionally followed by the documented rollback
        # (`set_active a`) from the "回滚到 Android" step.
        expected = [("set_active", "b"), ("flash", "boot_b"),
                    ("flash", "sunuefi_esp"), ("flash", "sunuefi_root")]
        if verbs[:len(expected)] != expected:
            failures.append(f"documented write order is {verbs}, expected {expected}")
        tail = verbs[len(expected):]
        if tail and tail != [("set_active", "a")]:
            failures.append(f"unexpected writes after the flash sequence: {tail}")

        # Nothing destructive may appear, even in the manual path.
        text_calls = log.read_text()
        for forbidden in ("erase", " -w", "format", "flashing"):
            if re.search(rf"\b{forbidden.strip()}\b", text_calls):
                failures.append(f"manual flow issued forbidden command: {forbidden}")

        # The readback step must be present and must be shown to be conditional.
        if "fastboot fetch" not in chapter:
            failures.append("manual chapter has no readback step")
        # The readback file the documented commands produce must hash to the
        # flashed image; a formula that truncates to the wrong length would
        # print two different digests while every command still exits 0.
        readback = staging / "esp-readback-head.img"
        if not readback.is_file():
            failures.append("manual chapter does not truncate the readback to the image length")
        elif hashlib.sha256(readback.read_bytes()).digest() != hashlib.sha256(esp.read_bytes()).digest():
            failures.append("documented readback truncation does not reproduce the image digest")
        if "partition-size:sunuefi_esp" not in chapter:
            failures.append("manual chapter does not gate the readback on partition size")
        if "partition-size:sunuefi_root" not in chapter:
            failures.append("manual chapter does not verify the prepared root capacity")
        if "expanded_bytes" not in chapter:
            failures.append("manual chapter does not bind root capacity to the manifest")
        # First install keeps Android on Slot A. The request page is inside the
        # combined BOOT on boot_a; boot_b only receives the Linux payload.
        if "/dev/disk/by-partlabel/boot_a" not in document:
            failures.append("first-install boot request is not documented as boot_a")
        if "/dev/disk/by-partlabel/boot_b`" in document:
            failures.append("first-install boot request is incorrectly documented as boot_b")

        # The install-time SSH key is documented in both paths: the one-click
        # installer takes --ssh-key, and the manual path writes the key with
        # mcopy before the ESP is flashed.
        for token in ("--ssh-key", "mcopy", "/piano-ssh-key.pub", "mtools"):
            if token not in document:
                failures.append(f"installation guide does not document {token!r}")
        if "mcopy" not in chapter:
            failures.append("manual chapter does not show the ESP key injection")
        key_step = chapter.find("mcopy")
        esp_flash = chapter.find("fastboot flash sunuefi_esp")
        if key_step < 0 or esp_flash < 0 or key_step > esp_flash:
            failures.append("manual chapter injects the SSH key after the ESP flash")

        # The asset list must name the files the commands actually use.
        for asset in ("boot_sunuefi.img", "sunuefi_esp.img",
                      "root-install/pianoroot.f2fs.sparse.img",
                      "root-install/SHA256SUMS", "SHA256SUMS"):
            if asset not in chapter:
                failures.append(f"manual chapter does not list asset {asset}")

        # A root-install produced by another bundle must be rejected even when
        # both artifacts' own SHA256SUMS are internally valid.
        cross = work / "cross"
        cross.mkdir()
        cross_bin = cross / "bin"
        cross_bin.mkdir()
        cross_stub = cross_bin / "fastboot"
        cross_stub.write_text(STUB, encoding="utf-8")
        cross_stub.chmod(0o755)
        cross_assets, _ = make_asset_set(cross, cross_bundle=True)
        cross_staging = copy_asset_set(cross_assets, cross / "piano-install")
        cross_log = cross / "fastboot.log"
        cross_env = {
            **env,
            "PATH": f"{cross_bin}:{os.environ['PATH']}",
            "STUB_LOG": str(cross_log),
            "ESP_IMG": str(cross_staging / "sunuefi_esp.img"),
        }
        result = invoke_installer(cross_staging, cross_env, boot=device_boot)
        if result.returncode == 0 or "different deploy-bundle" not in (result.stdout + result.stderr):
            failures.append("cross-bundle root-install was not rejected")
        if not assert_no_device_write(cross_log):
            failures.append("cross-bundle root-install caused a device write")

        # A root-install from the right bundle but the wrong capacity must also
        # fail before set_active or any flash command is issued.
        capacity = work / "capacity"
        capacity.mkdir()
        capacity_assets, capacity_root_install = make_asset_set(capacity)
        capacity_manifest = capacity_root_install / "root-install-manifest.json"
        capacity_record = json.loads(capacity_manifest.read_text())
        capacity_record["expanded_bytes"] += 4096
        capacity_manifest.write_text(json.dumps(capacity_record, indent=2) + "\n")
        sparse = capacity_root_install / "pianoroot.f2fs.sparse.img"
        capacity_sums = "\n".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
            for path in (sparse, capacity_manifest)) + "\n"
        (capacity_root_install / "SHA256SUMS").write_text(capacity_sums)
        capacity_staging = copy_asset_set(capacity_assets, capacity / "piano-install")
        capacity_log = capacity / "fastboot.log"
        capacity_env = {
            **env,
            "STUB_LOG": str(capacity_log),
            "ESP_IMG": str(capacity_staging / "sunuefi_esp.img"),
        }
        result = invoke_installer(capacity_staging, capacity_env, boot=device_boot)
        if result.returncode == 0 or "must exactly match sunuefi_root" not in (result.stdout + result.stderr):
            failures.append("wrong-capacity root-install was not rejected")
        if not assert_no_device_write(capacity_log):
            failures.append("wrong-capacity root-install caused a device write")

        # The root-install must target the Android slot that is active now. A
        # root prepared for _a must not overwrite boot_b on a _b device.
        slot = work / "slot"
        slot.mkdir()
        slot_assets, slot_root_install = make_asset_set(slot)
        slot_manifest = slot_root_install / "root-install-manifest.json"
        slot_record = json.loads(slot_manifest.read_text())
        slot_record["android_slot"] = "_b"
        slot_manifest.write_text(json.dumps(slot_record, indent=2) + "\n")
        slot_sparse = slot_root_install / "pianoroot.f2fs.sparse.img"
        (slot_root_install / "SHA256SUMS").write_text("\n".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}"
            for path in (slot_sparse, slot_manifest)) + "\n")
        slot_staging = copy_asset_set(slot_assets, slot / "piano-install")
        slot_log = slot / "fastboot.log"
        slot_env = {
            **env,
            "STUB_LOG": str(slot_log),
            "ESP_IMG": str(slot_staging / "sunuefi_esp.img"),
        }
        result = invoke_installer(slot_staging, slot_env, boot=device_boot)
        if result.returncode == 0 or "prepared for Android slot" not in (result.stdout + result.stderr):
            failures.append("root-install for the wrong Android slot was not rejected")
        if not assert_no_device_write(slot_log):
            failures.append("wrong-slot root-install caused a device write")

        # The one-click installer always writes Linux to Slot B. If Android is
        # currently on B, it must stop before overwriting the Android boot path.
        current = work / "current-slot-b"
        current.mkdir()
        current_assets, _ = make_asset_set(current)
        current_staging = copy_asset_set(current_assets, current / "piano-install")
        current_log = current / "fastboot.log"
        current_env = {
            **env,
            "STUB_LOG": str(current_log),
            "STUB_CURRENT_SLOT": "b",
            "ESP_IMG": str(current_staging / "sunuefi_esp.img"),
        }
        result = invoke_installer(current_staging, current_env, boot=device_boot)
        if result.returncode == 0 or "Android must be active on Slot A" not in (
                result.stdout + result.stderr):
            failures.append("installer accepted Android currently on Slot B")
        if not assert_no_device_write(current_log):
            failures.append("Android-on-Slot-B case caused a device write")

        # First install is not a recovery path: the legacy checksum bypass must
        # not skip provenance, slot, capacity, or sparse-transport validation.
        bypass = work / "bypass"
        bypass.mkdir()
        bypass_assets, _ = make_asset_set(bypass)
        bypass_staging = copy_asset_set(bypass_assets, bypass / "piano-install")
        bypass_log = bypass / "fastboot.log"
        bypass_env = {
            **env,
            "STUB_LOG": str(bypass_log),
            "ESP_IMG": str(bypass_staging / "sunuefi_esp.img"),
        }
        result = invoke_installer(bypass_staging, bypass_env, "--skip-checksum", boot=device_boot)
        if result.returncode == 0 or "--skip-checksum is not accepted" not in (
                result.stdout + result.stderr):
            failures.append("first-install path accepted --skip-checksum")
        if not assert_no_device_write(bypass_log):
            failures.append("--skip-checksum caused a device write")

    installer = (ROOT / "scripts/install-f2fs.py").read_text(encoding="utf-8")
    erase = 'device.call(["fastboot", "-s", identity["factory_serial"], "erase", "userdata"]'
    if erase not in installer:
        failures.append("first-install executor does not explicitly erase userdata")
    if installer.index(erase) < installer.index('device.shell("sgdisk --load-backup='):
        failures.append("first-install executor erases userdata before the GPT write")
    if 'journal["device_writes"] = True' not in installer[:installer.index(erase)]:
        failures.append("first-install executor does not journal the destructive write boundary")

    windows = (ROOT / "scripts/installer/flash-linux.bat").read_text(encoding="utf-8")
    if "call :verify_transport_digest" not in windows:
        failures.append("Windows installer does not verify the manifest transport digest")
    if ".transport.sha256" not in windows:
        failures.append("Windows installer does not read transport.sha256 from the manifest")

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: 手动刷机章节的命令序列可执行、顺序正确、无破坏性命令，"
          "校验与读回步骤齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
