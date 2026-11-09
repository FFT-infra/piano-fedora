#!/usr/bin/env python3
"""更新已有 piano F2FS/ESP；默认只读计划，不改 GPT、userdata 或持久 BOOT。"""

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import time

from piano_artifacts import sha256
from piano_sparse import require

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("piano_installer", ROOT / "scripts/install-f2fs.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def update_plan(upstream, device, bundle, root_install, boot, stock_boot):
    # 内存契约由 installation_plan 对首装与更新一并给出，这里只补更新路径的靶点。
    plan, snapshot = installer.installation_plan(upstream, device, bundle, root_install, boot, stock_boot, existing=True)
    plan["targets"] = ["sunuefi_esp", "sunuefi_root"]
    plan["persistent_boot_written"] = False
    plan["linux_files_replaced"] = True
    return plan, snapshot


def wait_fastboot_enumerated(serial, runner=subprocess.run, attempts=120, delay=1):
    """等原厂 bootloader 重新出现在 fastboot 里，不打开数据端点。

    `fastboot -s <serial> getvar` 在设备出现前会一直等。旧代码给它 2 秒并循环
    重试，原厂 bootloader 的 USB 重枚举实测要 8 秒，于是第一次调用就被杀掉；
    被杀之后这条 USB 端点不再响应，后面 30 次重试全部超时，只能重置端口或
    重新插拔（2026-11-09 真机复现）。`fastboot devices` 只读 USB 描述符，不会
    碰到这个端点，用它轮询到设备出现，再发一次真正的 getvar。
    """
    for attempt in range(attempts):
        try:
            listing = runner(["fastboot", "devices"], capture_output=True, text=True, timeout=20).stdout
        except subprocess.TimeoutExpired:
            listing = ""
        if any(line.split()[:1] == [serial] for line in listing.splitlines() if line.strip()):
            return
        if attempt == attempts - 1:
            break
        time.sleep(delay)
    raise RuntimeError("factory fastboot did not enumerate: " + serial)


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
        # 只等这一台序列号出现，不扫描其他设备。
        wait_fastboot_enumerated(identity["factory_serial"])
        device.call(["fastboot", "-s", identity["factory_serial"], "getvar", "product"])
        installer.factory(device, identity, slot)
        partitions = {row["name"]: row for row in plan["partition_plan"]["partitions"]}
        for name in plan["targets"]:
            require(int(device.fastboot_var(identity["factory_serial"], "partition-size:" + name), 16) == partitions[name]["bytes"],
                    "factory partition capacity differs from the reviewed GPT: " + name)
        stage("factory_identity_slot_and_both_capacities_validated")
        generic = json.loads((bundle / "bundle-manifest.json").read_text())
        root = json.loads((root_install / "root-install-manifest.json").read_text())
        targets = (("sunuefi_esp", bundle / "sunuefi_esp.img", generic["files"]["sunuefi_esp.img"]),
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
