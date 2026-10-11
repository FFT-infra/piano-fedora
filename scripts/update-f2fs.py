#!/usr/bin/env python3
"""更新已有 piano F2FS/ESP；默认只读计划，不改 GPT、userdata 或持久 BOOT。"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import time

from piano_artifacts import sha256
from piano_sparse import require

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("piano_installer", ROOT / "scripts/install-f2fs.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def _linux_dtb(bundle):
    data = (bundle / "boot.img").read_bytes()
    require(len(data) >= 4096 and data[:8] == b"ANDROID!" and struct.unpack_from("<I", data, 40)[0] == 2,
            "expected the sealed Linux BOOT v2 container")
    kernel, ramdisk, second = (struct.unpack_from("<I", data, x)[0] for x in (8, 16, 24))
    page = struct.unpack_from("<I", data, 36)[0]
    require(page == 4096 and second == 0 and struct.unpack_from("<I", data, 1632)[0] == 0,
            "unsupported Linux BOOT layout")
    align = lambda n: (n + page - 1) // page * page
    start = page + align(kernel) + align(ramdisk)
    size = struct.unpack_from("<I", data, 1648)[0]
    require(40 <= size <= 2 * 1024**2 and start + size <= len(data), "invalid Linux DTB bounds")
    return data[start:start + size]


def _merge(spans):
    result = []
    for start, end in sorted(spans):
        require(0 <= start < end <= 2**64, "invalid memory interval")
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def _covered(start, end, spans):
    return any(left <= start and end <= right for left, right in spans)


def memory_contract(linux_dtb, factory_dtb, read_fdt):
    """比较描述的范围，不能证明实机 DDR 所有权或完整 24 GB 已可用。"""
    require(40 <= len(factory_dtb) <= 2 * 1024**2, "factory DTB is missing or too large")

    def ranges(parsed):
        tree = parsed["tree"]
        banks, reserved = [], list(parsed["reservations"])
        for path, props in tree.items():
            memory = props.get("device_type") == b"memory\0"
            fixed = path.startswith("/reserved-memory/") and "reg" in props
            if not (memory or fixed):
                continue
            parent = path.rsplit("/", 1)[0] or "/"
            address = int.from_bytes(tree[parent].get("#address-cells", b"\0\0\0\2"), "big")
            size = int.from_bytes(tree[parent].get("#size-cells", b"\0\0\0\1"), "big")
            require(address in (1, 2) and size in (1, 2), "unsupported memory tuple widths")
            raw, stride = props.get("reg", b""), (address + size) * 4
            require(raw and len(raw) % stride == 0, "invalid memory reg tuples")
            for offset in range(0, len(raw), stride):
                start = int.from_bytes(raw[offset:offset + address * 4], "big")
                length = int.from_bytes(raw[offset + address * 4:offset + stride], "big")
                if length:
                    (banks if memory else reserved).append((start, length))
        return _merge((start, start + size) for start, size in banks), _merge((start, start + size) for start, size in reserved if size)

    linux_banks, linux_reserved = ranges(read_fdt(linux_dtb))
    factory_banks, factory_reserved = ranges(read_fdt(factory_dtb))
    require(linux_banks and factory_banks, "DTB has no current RAM banks")
    for start, end in linux_banks:
        require(_covered(start, end, factory_banks), "Linux DTB advertises RAM outside this device's Android memory map")
    for first, last in factory_reserved:
        for left, right in linux_banks:
            start, end = max(first, left), min(last, right)
            if start < end:
                require(_covered(start, end, linux_reserved), "Linux DTB omits a current Android fixed reservation")
    return {"status": "DESCRIBED_MEMORY_SUBSET_AND_FIXED_RESERVATIONS_CHECKED",
            "linux_dtb_sha256": hashlib.sha256(linux_dtb).hexdigest(),
            "factory_dtb_sha256": hashlib.sha256(factory_dtb).hexdigest(),
            "linux_ram_bytes": sum(end - start for start, end in linux_banks),
            "factory_ram_bytes": sum(end - start for start, end in factory_banks),
            "full_device_ram_available": False, "physical_memory_ownership_verified": False}


def update_plan(upstream, device, bundle, root_install, boot, stock_boot):
    plan, snapshot = installer.installation_plan(upstream, device, bundle, root_install, boot, stock_boot, existing=True)
    from compose_piano_dtb import read_fdt
    factory = device.shell("cat /sys/firmware/fdt")
    plan["memory_contract"] = memory_contract(_linux_dtb(bundle), factory, read_fdt)
    plan["targets"] = ["sunuefi_esp", "sunuefi_root"]
    plan["persistent_boot_written"] = False
    plan["linux_files_replaced"] = True
    return plan, snapshot


def execute_update(upstream, device, plan, bundle, root_install, boot, stock_boot, session):
    fresh, snapshot = update_plan(upstream, device, bundle, root_install, boot, stock_boot)
    require(fresh == plan, "device, GPT, memory map, slot or inputs changed since the reviewed update plan")
    require(plan["status"] == "EXISTING_PARTITIONS_READ_ONLY_PLAN" and plan["targets"] == ["sunuefi_esp", "sunuefi_root"],
            "expected an existing-partition update plan")
    require(not session.exists() and not session.is_symlink(), "use a new update session directory")
    session.mkdir(parents=True, mode=0o700)
    identity, slot, gpt = plan["device"], plan["android_slot"], snapshot["gpt"]
    for name, data in (("gpt-primary.bin", gpt.primary), ("gpt-primary-entries.bin", gpt.primary_entries),
                       ("gpt-backup.bin", gpt.backup), ("gpt-backup-entries.bin", gpt.backup_entries),
                       ("original-boot.img", stock_boot.read_bytes())):
        with (session / name).open("xb") as stream:
            stream.write(data)
        installer.sync_file(session / name)
    installer.save(session / "plan.json", plan)
    journal = {"status": "UPDATE_PREPARED_NOT_WRITTEN", "completed": [], "device_writes": False,
               "gpt_changed": False, "userdata_written": False, "persistent_boot_written": False,
               "linux_boot_verified": False, "android_passthrough_verified": False,
               "device_readback_verified": False}

    def stage(name):
        journal["completed"].append(name)
        installer.save(session / "result.json", journal)

    try:
        stage("current_gpt_inputs_and_memory_description_validated")
        device.reboot_bootloader()
        # 工厂 bootloader 的 USB 重枚举；只等待有限时间，不扫描其他序列号。
        for attempt in range(30):
            try:
                device.call(["fastboot", "-s", identity["factory_serial"], "getvar", "product"], timeout=2)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                if attempt == 29:
                    raise
                time.sleep(1)
        installer.factory(device, identity, slot)
        partitions = {row["name"]: row for row in plan["partition_plan"]["partitions"]}
        for name in plan["targets"]:
            require(int(device.fastboot_var(identity["factory_serial"], "partition-size:" + name), 16) == partitions[name]["bytes"],
                    "factory partition capacity differs from the reviewed GPT: " + name)
        stage("factory_identity_slot_and_both_capacities_validated")
        generic = json.loads((bundle / "bundle-manifest.json").read_text())
        root = json.loads((root_install / "root-install-manifest.json").read_text())
        targets = (("sunuefi_esp", bundle / "esp.img", generic["files"]["esp.img"]),
                   ("sunuefi_root", root_install / "pianoroot.f2fs.sparse.img", root["transport"]))
        # 在第一笔写入前重新核对两份输入，避免 ESP 已写后才发现 root 被替换。
        for _, image, metadata in targets:
            installer.checked(image, metadata)
        journal["status"] = "UPDATE_WRITE_STARTED"
        journal["device_writes"] = True
        installer.save(session / "result.json", journal)
        for name, image, metadata in targets:
            installer.factory(device, identity, slot)
            require(int(device.fastboot_var(identity["factory_serial"], "partition-size:" + name), 16) == partitions[name]["bytes"],
                    "factory capacity changed before transfer: " + name)
            installer.checked(image, metadata)
            device.call(["fastboot", "-s", identity["factory_serial"], "flash", name, str(image)], timeout=3600)
            installer.checked(image, metadata)
            stage("factory_fastboot_accepted_" + name)
        journal["status"] = "UPDATE_TRANSFERRED_FIRST_BOOT_AND_READBACK_PENDING"
        journal["remaining"] = ["temporary Linux boot", "F2FS and ESP device readback", "GNOME and hardware", "both-way switching and Recovery", "persistent BOOT only after device acceptance"]
        installer.save(session / "result.json", journal)
        return journal
    except BaseException as error:
        journal.update(status="UPDATE_INTERRUPTED", error=str(error),
                       recovery_note="Inspect completed stages and live state; do not automatically retry, repartition, erase userdata or install BOOT")
        installer.save(session / "result.json", journal)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("plan", "apply"))
    for name in ("sunuefi", "bundle", "root-install", "device-boot", "stock-boot", "plan"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--session", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--accept-root-replacement", action="store_true")
    args = parser.parse_args()
    if args.operation == "apply":
        require(args.execute and args.accept_root_replacement and args.session,
                "apply requires --execute --accept-root-replacement and a new --session directory")
    else:
        require(not args.execute and not args.accept_root_replacement and args.session is None, "plan is read-only")
        require(not args.plan.exists() and not args.plan.is_symlink(), "plan output already exists")
    upstream = installer.load_source(args.sunuefi)
    device = upstream.Device(args.serial)
    inputs = (upstream, device, args.bundle, args.root_install, args.device_boot, args.stock_boot)
    if args.operation == "plan":
        result, _ = update_plan(*inputs)
        installer.save(args.plan, result)
    else:
        result = execute_update(upstream, device, json.loads(args.plan.read_text()), *inputs[2:], args.session)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
