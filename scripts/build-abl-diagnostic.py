#!/usr/bin/env python3
"""封装独立的 ABL 只读存储诊断包，不替代 SunUEFI 部署包。"""
import argparse
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys

from piano_artifacts import artifact_output, inspect_kernel, sha256

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("initramfs_builder", ROOT / "scripts/build-initramfs.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def container_payload(path):
    data = Path(path).read_bytes()
    if (len(data) < 4096 or data[:8] != b"ANDROID!" or struct.unpack_from("<I", data, 40)[0] != 4
            or struct.unpack_from("<I", data, 20)[0] != 1584):
        raise ValueError("expected Android header v4")
    kernel_size, ramdisk_size = struct.unpack_from("<II", data, 8)
    ramdisk_start = 4096 + (kernel_size + 4095) // 4096 * 4096
    if ramdisk_start + ramdisk_size > len(data):
        raise ValueError("truncated Android boot payload")
    return data[4096:4096 + kernel_size], data[ramdisk_start:ramdisk_start + ramdisk_size]


def build(kernel, initramfs, source, output, dtbo):
    kernel, initramfs, source, output, dtbo = [Path(p).resolve() for p in (kernel, initramfs, source, output, dtbo)]
    if output.exists():
        raise ValueError("use a new diagnostic output directory")
    m, kernel_hash = inspect_kernel(kernel)
    metadata = json.loads((initramfs / "manifest.json").read_text())
    if (metadata.get("boot_chain"), metadata.get("handoff"), metadata.get("kernel_manifest_sha256")) != (
            "abl-storage", "read-only-storage-probe", kernel_hash):
        raise ValueError("diagnostic initramfs and kernel do not share the ABL read-only contract")
    ramdisk = initramfs / "initramfs.cpio.gz"
    if sha256(ramdisk) != metadata["initramfs_sha256"]:
        raise ValueError("diagnostic initramfs changed after its manifest was written")
    raw = gzip.decompress(ramdisk.read_bytes())
    proof = builder.verify_archive(raw, m, "abl-storage")
    pin = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["debian-piano"]["commit"]
    if subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip() != pin:
        raise ValueError("mkbootimg source differs from the pinned debian-piano")
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--", "mkbootimg"], text=True):
        raise ValueError("mkbootimg source has local modifications")
    maker = source / "mkbootimg/mkbootimg.py"
    table = dtbo.read_bytes()
    if len(table) < 64 or struct.unpack_from(">I", table)[0] != 0xd7b7ab1e:
        raise ValueError("diagnostic DTBO has no Android DT table")
    magic, total, header, entry, count, table_offset, page, version = struct.unpack_from(">8I", table)
    if total != len(table) or header != 32 or entry != 32 or count != 1 or table_offset != 32 or page != 4096 or version != 0:
        raise ValueError("unsupported diagnostic DTBO layout")
    size, offset = struct.unpack_from(">II", table, 32)
    if (size < 40 or offset != 64 or offset + size != len(table)
            or table[offset:offset + 4] != bytes.fromhex("d00dfeed")):
        raise ValueError("truncated or invalid DTBO entry")
    compressed = subprocess.check_output(["lz4", "-l", "-9", "-c"], input=raw)
    restored = subprocess.check_output(["lz4", "-d", "-c"], input=compressed)
    if restored != raw or compressed[:4] != bytes.fromhex("02214c18"):
        raise ValueError("legacy LZ4 round trip failed")
    with artifact_output(output):
        payload = output / "initramfs.cpio.lz4"
        payload.write_bytes(compressed)
        boot, init_boot = output / "boot-abl-storage.img", output / "init_boot-abl-storage.img"
        subprocess.run([sys.executable, str(maker), "--header_version", "4", "--kernel", str(kernel / "Image"), "-o", str(boot)], check=True)
        subprocess.run([sys.executable, str(maker), "--header_version", "4", "--ramdisk", str(payload), "-o", str(init_boot)], check=True)
        actual_kernel, boot_ramdisk = container_payload(boot)
        empty_kernel, actual_ramdisk = container_payload(init_boot)
        if (hashlib.sha256(actual_kernel).hexdigest() != m["image"]["sha256"]
                or boot_ramdisk or empty_kernel or actual_ramdisk != compressed):
            raise ValueError("packed Android boot payload differs from its sealed inputs")
        shutil.copyfile(dtbo, output / "dtbo-abl-storage.img")
        shutil.copyfile(initramfs / "manifest.json", output / "initramfs-manifest.json")
        payload.unlink()
        record = {"status": "HOST_VERIFIED_ABL_STORAGE_DIAGNOSTIC_NOT_DEVICE_VERIFIED",
                  "boot_chain": "abl-storage", "handoff": "read-only-storage-probe",
                  "kernel_release": m["kernel_release"], "kernel_manifest_sha256": kernel_hash,
                  "initramfs_manifest_sha256": sha256(initramfs / "manifest.json"),
                  "mkbootimg_commit": pin, "mkbootimg_sha256": sha256(maker),
                  "dtbo_source": "pinned debian-piano boot/dtbo-piano-power.dts",
                  "verification": {**proof, "boot_payload_roundtrip": True, "lz4_roundtrip": True},
                  "full_desktop_tested": False, "device_tested": False,
                  "requirements": ["confirmed current slot and recoverable original Slot B boot material",
                                   "stock vendor_boot/base DT paired with the supplied ABL overlay",
                                   "dedicated sunuefi_root GPT partition containing PIANOROOT F2FS"],
                  "files": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                            for p in (boot, init_boot, output / "dtbo-abl-storage.img", output / "initramfs-manifest.json")}}
        (output / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        (output / "SHA256SUMS").write_text("".join(sha256(p) + "  " + p.name + "\n"
                                                    for p in sorted(output.iterdir()) if p.is_file()))
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("kernel", "initramfs", "source", "output", "dtbo"):
        ap.add_argument("--" + name, type=Path, required=True)
    args = ap.parse_args()
    print(json.dumps(build(args.kernel, args.initramfs, args.source, args.output, args.dtbo), indent=2))


if __name__ == "__main__":
    main()
