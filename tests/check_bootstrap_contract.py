#!/usr/bin/env python3
"""在隔离 guest 中执行真实根身份函数，并验证实际归档的坏依赖拒绝路径。"""
import argparse
import importlib.util
import json
import os
import signal
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from check_initramfs import build_fixture, static_aarch64_busybox


def check_root_function(bootstrap, directory):
    busybox = shutil.which("busybox")
    if not busybox:
        print("SKIP: root contract guest requires a static BusyBox")
        return
    if "Requesting program interpreter" in subprocess.check_output(["readelf", "-l", busybox], text=True):
        print("SKIP: root contract guest requires a static BusyBox")
        return
    text = bootstrap.read_text()
    match = re.search(r"^verify_device\(\) \{\n.*?^\}", text, re.M | re.S)
    assert match, "bootstrap lost its root identity verifier"
    root = directory / "guest"
    for path in ("bin", "usr/bin", "shadow"):
        (root / path).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(busybox, root / "bin/busybox")
    (root / "bin/busybox").chmod(0o755)
    (root / "bin/sh").symlink_to("busybox")
    canonical = root / "usr/bin/blkid"
    canonical.write_text('''#!/bin/sh
case "$3" in
 LABEL) printf '%s\\n' "$TEST_LABEL" ;;
 PART_ENTRY_NAME) printf '%s\\n' "$TEST_PARTNAME" ;;
 TYPE) printf '%s\\n' "$TEST_TYPE" ;;
 *) exit 2 ;;
esac
''')
    canonical.chmod(0o755)
    shadow = root / "shadow/blkid"
    shadow.write_text('''#!/bin/sh
printf '/dev/test: LABEL="%s" TYPE="%s"\\n' "$TEST_LABEL" "$TEST_TYPE"
''')
    shadow.chmod(0o755)
    (root / "probe.sh").write_text("export PATH=/shadow:/usr/bin:/bin\n"
                                   + match.group() + "\nrootdev=/dev/test\nexpected=PIANOROOT\nverify_device\n")
    command = ["chroot", str(root), "/bin/sh", "/probe.sh"]
    if os.geteuid() != 0:
        if not shutil.which("sudo") or subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode:
            print("SKIP: hermetic root verifier requires root or passwordless sudo")
            return
        command = ["sudo", "-n", "--preserve-env=TEST_LABEL,TEST_PARTNAME,TEST_TYPE", *command]
    cases = [("PIANOROOT", "sunuefi_root", "f2fs", True),
             ("Android", "sunuefi_root", "f2fs", False),
             ("PIANOROOT", "userdata", "f2fs", False),
             ("PIANOROOT", "sunuefi_root", "ext4", False)]
    for label, partname, kind, accepted in cases:
        result = subprocess.run(command, capture_output=True, text=True,
                                env={**os.environ, "TEST_LABEL": label,
                                     "TEST_PARTNAME": partname, "TEST_TYPE": kind})
        assert (result.returncode == 0) == accepted, (
            f"root identity mismatch with a shadow blkid: {label}/{partname}/{kind}: "
            f"exit={result.returncode} {result.stderr}")


def check_archive(directory):
    busybox = static_aarch64_busybox()
    if busybox is None:
        print("SKIP: native ELF/archive fixture requires static AArch64 BusyBox")
        return
    spec = importlib.util.spec_from_file_location("builder_contract", ROOT / "scripts/build-initramfs.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    rootfs, kernel = build_fixture(directory, busybox)
    output = directory / "built"
    metadata = builder.build(rootfs, kernel, output, "PIANOROOT", "sunuefi_root")
    import gzip
    raw = gzip.decompress((output / "initramfs.cpio.gz").read_bytes())
    assert builder.verify_archive(raw)["elf_closure_verified"]
    records = builder.inspect_newc(raw)
    assert metadata["boot_chain"] == "sunuefi"
    assert not any(row["name"] == "usr/bin/piano-qup-smmu" for row in records)
    bad_cases = []
    bad_cases.append([row for row in records if row["name"] != "lib64/libblkid.so.1"])
    bad_cases.append([row for row in records if row["name"] != "etc/piano/boot-chain"])
    bad_cases.append([{**row, "data": busybox.read_bytes()} if row["name"] == "usr/bin/blkid" else row
                      for row in records])
    bad_cases.append([{**row, "data": row["data"].replace(b"/usr/bin/blkid -p", b"blkid -p")}
                      if row["name"] in ("init", "pianoinit") else row for row in records])
    for entries in bad_cases:
        try:
            builder.verify_archive(builder.make_newc(entries))
        except builder.InitramfsError:
            pass
        else:
            raise AssertionError("archive accepted a missing library, BusyBox substitution or bare blkid")
    try:
        builder.verify_archive(raw[:200])
    except builder.InitramfsError:
        pass
    else:
        raise AssertionError("truncated cpio was accepted")
    try:
        builder.verify_archive(raw + raw)
    except builder.InitramfsError:
        pass
    else:
        raise AssertionError("a second overriding cpio after the trailer was accepted")
    try:
        builder.verify_archive(builder.make_newc([
            {**row, "mode": stat.S_IFREG | 0o644} if row["name"] == "pianoinit" else row
            for row in records]))
    except builder.InitramfsError:
        pass
    else:
        raise AssertionError("a non-executable init process was accepted")

    # 每个实际需要的 BusyBox applet 必须是链接到已验证 BusyBox 的 bin/<name>；
    # debug helper 与 collector 必须存在且可执行。缺失、被换成普通文件或断链
    # 都必须在构建时暴露，不能等到设备上第一次执行才失败。
    def abl_records(source):
        release = next(row["data"] for row in source
                       if row["name"] == "etc/piano/kernel-release").decode().strip()
        out = []
        for row in source:
            if row["name"] == "etc/piano/boot-chain":
                row = {**row, "data": b"abl-storage\n"}
            elif row["name"] == "etc/piano/modules-load-order":
                row = {**row, "data": (
                    f"phy_qcom_qmp_ufs /lib/modules/{release}/kernel/drivers/phy/qualcomm/phy-qcom-qmp-ufs.ko\n"
                    f"ufs_qcom /lib/modules/{release}/kernel/drivers/ufs/ufs-qcom.ko\n").encode()}
            out.append(row)
        out.append({"name": "usr/bin/piano-qup-smmu", "mode": stat.S_IFREG | 0o755,
                    "data": b"#!/bin/sh\nexit 0\n"})
        out.append({"name": "bin/devmem", "mode": stat.S_IFLNK | 0o777, "data": b"busybox"})
        return out

    abl = abl_records(records)
    # 该 ABL 归档本身必须通过，才能证明下面的负例确实由被移除的成员触发。
    builder.verify_archive(builder.make_newc(abl), expected_chain="abl-storage")

    def without(entries, name):
        return [row for row in entries if row["name"] != name]

    def with_mode(entries, name, mode):
        return [{**row, "mode": mode} if row["name"] == name else row for row in entries]

    missing_cases = [
        ("bin/sh", "sunuefi", without(records, "bin/sh")),
        ("debug helper", "sunuefi", without(records, "usr/local/sbin/piano-debug-bootstrap")),
        ("collector", "sunuefi", without(records, "usr/bin/piano-collect-boot")),
        ("ABL devmem", "abl-storage", without(abl, "bin/devmem")),
        ("ABL collector", "abl-storage", without(abl, "usr/bin/piano-collect-boot")),
    ]
    for label, chain, entries in missing_cases:
        try:
            builder.verify_archive(builder.make_newc(entries), expected_chain=chain)
        except builder.InitramfsError:
            pass
        else:
            raise AssertionError(f"archive accepted a missing {label}")
    substitution_cases = [
        ("non-executable collector", "sunuefi",
         with_mode(records, "usr/bin/piano-collect-boot", stat.S_IFREG | 0o644)),
        ("bin/sh not linked to the verified BusyBox", "sunuefi",
         with_mode(records, "bin/sh", stat.S_IFREG | 0o755)),
    ]
    for label, chain, entries in substitution_cases:
        try:
            builder.verify_archive(builder.make_newc(entries), expected_chain=chain)
        except builder.InitramfsError:
            pass
        else:
            raise AssertionError(f"archive accepted a {label}")

    try:
        builder.build(rootfs, kernel, directory / "mixed", "PIANOROOT", "sunuefi_root",
                      boot_chain="sunuefi", abl_source=directory)
    except builder.InitramfsError:
        pass
    else:
        raise AssertionError("ABL initialization entered the SunUEFI profile")
    broken_modules = [{**row, "data": row["data"] + b"tampered"} if row["name"].endswith(".ko") else row
                      for row in records]
    try:
        builder.verify_archive(builder.make_newc(broken_modules), json.loads((kernel / 'manifest.json').read_text()))
    except builder.InitramfsError:
        pass
    else:
        raise AssertionError("archive module bytes were not bound to the sealed kernel")
    failed_output = directory / "failed-output"
    write_text = Path.write_text
    def fail_manifest(path, *args, **kwargs):
        if path == failed_output / "manifest.json":
            raise OSError("injected manifest write failure")
        return write_text(path, *args, **kwargs)
    with patch.object(Path, "write_text", fail_manifest):
        try:
            builder.build(rootfs, kernel, failed_output, "PIANOROOT", "sunuefi_root")
        except OSError as error:
            assert str(error) == "injected manifest write failure"
        else:
            raise AssertionError("injected output failure was hidden")
    assert not failed_output.exists(), "failed build leaves an output directory that blocks retry"


def check_serial_stop(bootstrap, directory):
    busybox = shutil.which("busybox")
    if not busybox:
        print("SKIP: serial lifecycle regression requires BusyBox")
        return
    match = re.search(r"^stop_debug_serial\(\) \{\n.*?^\}", bootstrap.read_text(), re.M | re.S)
    assert match, "bootstrap lost its serial handoff function"
    for index, startup_delay in enumerate((0, 0.2)):
        fifo = directory / f"serial-input-{index}"
        os.mkfifo(fifo)
        # 与生产路径相同：一个 exec cat 日志转发器，不再模拟已移除的 getty 会话。
        command = (match.group() + '\n'
                   + '(exec "$PIANO_BUSYBOX" cat "$PIANO_SERIAL_FIFO") &\n'
                   + 'debug_serial_pid=$!\n'
                   + f'sleep {startup_delay}\nstop_debug_serial\nprintf "HANDOFF_REACHED\\n"\n')
        process = subprocess.Popen([busybox, "sh", "-c", command], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, start_new_session=True,
                                   env={**os.environ, "PIANO_BUSYBOX": busybox, "PIANO_SERIAL_FIFO": str(fifo)})
        try:
            output, error = process.communicate(timeout=2)
            assert process.returncode == 0 and "HANDOFF_REACHED" in output, error
        except subprocess.TimeoutExpired:
            raise AssertionError("serial log forwarder remained alive or prevented handoff") from None
        finally:
            # 只回收测试创建的独立会话，生产日志转发器没有脱离会话的子进程。
            groups = [process.pid]
            for group in groups:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", type=Path, default=ROOT / "initramfs/f2fs-disk-bootstrap")
    parser.add_argument("--root-only", action="store_true")
    parser.add_argument("--serial-only", action="store_true")
    parser.add_argument("--git-ref", help="在相同测试中检查旧版本 bootstrap")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as path:
        directory = Path(path)
        if args.git_ref:
            baseline = directory / "baseline-bootstrap"
            baseline.write_bytes(subprocess.check_output(["git", "show", args.git_ref + ":initramfs/f2fs-disk-bootstrap"], cwd=ROOT))
            args.bootstrap = baseline
        if not args.serial_only:
            check_root_function(args.bootstrap, directory)
        if not args.root_only:
            check_serial_stop(args.bootstrap, directory)
        if not args.root_only and not args.serial_only:
            check_archive(directory / "archive")
    print("OK: executable root identity/shadow regression and archive dependency rejection")


if __name__ == "__main__":
    main()
