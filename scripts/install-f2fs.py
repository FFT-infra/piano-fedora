#!/usr/bin/env python3
"""首次安装 Fedora/F2FS：默认只读计划，显式执行才允许清空 userdata。"""

import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import struct
import subprocess
import sys
import time
import uuid
import zlib

from piano_artifacts import sha256, verify_uefi_source
import piano_gpt as gpt_entry
import piano_sparse
from piano_sparse import require

ROOT = Path(__file__).resolve().parents[1]
BLOCK = 4096
# 与 product.json 的 esp_size_mib 默认值一致；首次分区可用 --esp-mib 覆盖，
# 但镜像字节数必须等于计划算出的分区容量。
DEFAULT_ESP_MIB = 512
# 封存 root 镜像的最小容量，与 build-f2fs-image.py 的下限一致。
MIN_ROOT_MIB = 64
# 请求页由选择器在"正在引导的那份镜像"末尾读取，ABL 只加载当前槽；首装把
# Linux 写进 boot_b 并 set_active b，所以 Linux 的 boot-request 只能指向它。
LINUX_BOOT_DEVICE = "/dev/disk/by-partlabel/boot_b"
BUNDLE_ASSETS = {
    "sunuefi_esp.img": "sunuefi_esp.img",
    "boot_sunuefi.img": "boot_sunuefi.img",
    "PianoUEFI-product.img": "PianoUEFI-product.img",
}


def load_source(source):
    pin = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    names = ("install_piano.py", "provision_piano_bluetooth.py", "provision_piano_ssh.py", "compose_piano_dtb.py")
    if (source / ".git").exists():
        actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        require(actual == pin, "installer source does not match sources.lock.json")
        for name in names:
            path = "tools/" + name
            expected = subprocess.check_output(["git", "-C", str(source), "show", "HEAD:" + path])
            require((source / path).read_bytes() == expected, "locked installer source changed: " + name)
    else:
        proof = json.loads((source / "source-manifest.json").read_text())
        require(proof["commit"] == pin and set(proof["files"]) == {"tools/" + name for name in names},
                "exported source manifest differs from the pinned installer")
        for name, record in proof["files"].items():
            checked(source / name, record)
    sys.path.insert(0, str(source / "tools"))
    import install_piano
    return install_piano


def load_planner():
    spec = importlib.util.spec_from_file_location("first_partition_planner", ROOT / "scripts/plan-first-partition.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def partition_plan(gpt, android_data_mib=None, esp_mib=None):
    disk = {"sectors": gpt.disk_bytes // BLOCK, "logical_sector": BLOCK, "physical_sector": BLOCK,
            "entry_count": gpt.geometry["count"], "first_usable": gpt.geometry["first"],
            "last_usable": gpt.geometry["last"], "guid": gpt.geometry["disk_guid"]}
    rows = [{"number": row["index"] + 1, "first": row["first"], "last": row["last"],
             "name": row["name"], "code": row["type"], "size_text": str(row["bytes"])} for row in gpt.partitions]
    return load_planner().build_plan(disk, rows, DEFAULT_ESP_MIB if esp_mib is None else esp_mib,
                                     userdata_mib=android_data_mib)


def new_gpt(upstream, complete, plan, mbr, android_data_mib=None, esp_mib=None):
    """按计划生成新表，并给出四段可直接 dd 的字节。

    头部落 LBA 0，尾部从备份分区表开始，覆盖到磁盘最后一个扇区。返回的
    第 1、2 段是新表，第 3、4 段是分区前从设备读到的原表；写入失败时按
    相反顺序写回的就是后两段。
    """
    require(partition_plan(complete, android_data_mib, esp_mib) == plan,
            "partition plan differs from the actual complete GPT")
    require(len(mbr) == BLOCK and mbr[510:512] == b"\x55\xaa", "protective MBR missing")
    head, tail, original_head, original_tail = gpt_entry.rewrite(complete, plan, mbr)
    table = head[2 * BLOCK:]
    count = complete.geometry["count"]
    block_count = gpt_entry.table_bytes(count)
    # 备份表头在尾部 blob 的最后一块；原厂布局在表与表头之间留有空隙，
    # 不能用 `tail[block_count:block_count + BLOCK]` 取表头。
    after = upstream.parse_gpt(head[BLOCK:2 * BLOCK], head[2 * BLOCK:2 * BLOCK + block_count],
                               tail[-BLOCK:], tail[:block_count],
                               complete.disk_bytes)
    return after, head, tail, original_head, original_tail


def checked(path, meta):
    require(path.is_file() and not path.is_symlink(), "missing or symlink input: " + str(path))
    require(path.stat().st_size == meta["bytes"] and sha256(path) == meta["sha256"], "input hash/size differs: " + str(path))


def require_install_slot(slot, existing):
    """Keep the first-install path on Android Slot A.

    The one-click installer always writes Linux to Slot B and rewrites boot_b.
    A first install that started from Android Slot B would therefore destroy
    the only Android boot path. Existing-partition updates may still use B.
    """
    require(slot in ("_a", "_b"), "unknown active Android slot")
    if not existing:
        require(slot == "_a", "first install requires Android Slot A; Slot B is reserved for Linux")


def root_capacity(image_bytes, partition_bytes):
    """封存镜像装进一个不比自己小的分区，差额由首启扩容补齐。

    镜像比分区大就装不进去；小于分区时 initramfs 会在第一次读写挂载之前
    把文件系统长满分区。容量因此不再需要与设备匹配。
    """
    require(type(image_bytes) is int and image_bytes >= MIN_ROOT_MIB * 1024**2,
            "root image is smaller than the F2FS minimum")
    require(image_bytes <= partition_bytes, "root image is larger than its partition")
    return {"image_bytes": image_bytes, "partition_bytes": partition_bytes,
            "grow_at_first_boot": image_bytes < partition_bytes,
            "grow_bytes": partition_bytes - image_bytes}


def root_transport(bundle, root_install, generic, root, image):
    """选出要刷的 root sparse 与它的摘要记录。

    root-install 里那份是按需预先扩到目标容量的产物；没有时退回 bundle 自带
    的 sparse，它与封存镜像逐字节相同，差额由首启扩容补齐。两条路都不要求
    镜像与分区等大。
    """
    prepared = root.get("transport")
    if prepared is not None:
        return root_install / "pianoroot.f2fs.sparse.img", prepared
    bundled = generic["files"].get("pianoroot.f2fs.sparse.img")
    require(bundled is not None, "neither root-install nor the bundle carries a root transport")
    # bundle-manifest 只记文件摘要；解码后的字节要与封存镜像逐字节相同，
    # 由 verify-bundle 在发布前证明。这里现算一遍，改动过的 sparse 过不去。
    transport = bundle / "pianoroot.f2fs.sparse.img"
    decoded = piano_sparse.inspect(transport)
    require((decoded["expanded_bytes"], decoded["expanded_sha256"]) == (image["bytes"], image["sha256"]),
            "root transport is not the sealed root image")
    return transport, {**bundled, **decoded}


def installation_plan(upstream, device, bundle, root_install, boot, stock_boot, existing=False,
                      android_data_mib=None, esp_mib=None):
    # 没有预扩产物时把 bundle 自己当作 root 来源：它自带与封存镜像相同的 sparse。
    root_install = root_install or bundle
    require(not any((p / ".incomplete").exists() for p in (bundle, root_install, boot)), "incomplete input package")
    snapshot = device.inspect()
    disk_name = Path(snapshot["identity"]["disk"]).name
    require(device.text("cat /sys/block/" + disk_name + "/queue/physical_block_size") == str(BLOCK), "unexpected physical sector size")
    if existing:
        require(android_data_mib is None, "--android-data-mib only applies to a first install")
        require(esp_mib is None, "--esp-mib only applies to a first install")
        layout = upstream.plan_gpt(snapshot["gpt"])
        require(layout["mode"] == "update", "existing root and ESP partitions are required")
    else:
        layout = partition_plan(snapshot["gpt"], android_data_mib, esp_mib)
    slot = device.text("getprop ro.boot.slot_suffix")
    require_install_slot(slot, existing)
    prepared_manifest = root_install / "root-install-manifest.json"
    root = json.loads(prepared_manifest.read_text()) if prepared_manifest.is_file() else {}
    combined = json.loads((boot / "device-boot-manifest.json").read_text())
    generic = json.loads((bundle / "bundle-manifest.json").read_text())
    image = json.loads((bundle / "root-image-manifest.json").read_text())
    require(generic.get("status") == "HOST_VERIFIED_FEDORA_F2FS_BUNDLE_NOT_DEVICE_VERIFIED", "generic bundle not verified")
    source_runs = json.loads((bundle / "source-runs.json").read_text())
    verify_uefi_source(ROOT, bundle / "PianoUEFI-product.img",
                       json.loads((bundle / "uefi-source.json").read_text()), source_runs.get("uefi_run"))
    for asset, packaged in BUNDLE_ASSETS.items():
        checked(bundle / asset, generic["files"][packaged])
    # root-install 只是把同一份封存镜像预先扩到目标容量的可选产物。带了它就要
    # 与这份 bundle 逐项对上；没带就直接用 bundle 自带的 sparse，此时镜像里
    # 没有 /etc/piano/boot-request.json，由首启的
    # piano-boot-request-autoconfig.service 按 qbootctl 的当前槽重建（首装后
    # 是 boot_b），再与 device-boot 记录的同一条配置对上。
    if root:
        require(root.get("status") == "ROOT_CAPACITY_VERIFIED_NOT_DEVICE_VERIFIED"
                and root["source_bundle_manifest_sha256"] == sha256(bundle / "bundle-manifest.json")
                and root["source_root_manifest_sha256"] == sha256(bundle / "root-image-manifest.json")
                and root["source_root_sha256"] == image["sha256"], "capacity image belongs to another bundle")
        require(root["expanded_bytes"] == root["target_size_mib"] * 1024**2, "root image capacity fields differ")
        require(root["fsck_exit"] == 0 and root["fsck_read_only"] is True,
                "root capacity/transport verification incomplete")
        for field in ("file_content_checked", "numeric_owner_checked", "mode_checked", "symlink_targets_checked",
                      "hardlink_groups_checked", "mtime_checked", "xattr_checked"):
            require(root["metadata_verification"].get(field) is True, "root metadata proof missing: " + field)
        require(root["device_configuration"]["boot_request"] == combined["boot_request"]
                and root["device_configuration"]["android_slot"] == slot
                and root["device_configuration"].get("ssh_public_key_in_image") is False,
                "root device configuration differs from BOOT")
    transport, transport_meta = root_transport(bundle, root_install, generic, root, image)
    if existing:
        targets = {row["name"]: row for row in layout["partitions"]}
        capacity = root_capacity(transport_meta["expanded_bytes"], targets["sunuefi_root"]["bytes"])
        require(generic["files"]["sunuefi_esp.img"]["bytes"] == targets["sunuefi_esp"]["bytes"], "ESP image does not match the existing partition capacity")
    else:
        capacity = root_capacity(transport_meta["expanded_bytes"], layout["capacity"]["linux_root_mib"] * 1024**2)
        # ESP 镜像按 product.json 的 esp_size_mib 造出，分区必须正好装下它。
        # 少了装不进，多了刷完会留下未格式化的尾部。
        esp_row = next(row for row in layout["changes"] if row["name"] == "sunuefi_esp")
        require(generic["files"]["sunuefi_esp.img"]["bytes"] == esp_row["size_mib"] * 1024**2,
                "ESP image does not match the planned ESP partition capacity")
    require(transport_meta["dont_care_chunks"] == 0
            and transport_meta["zero_policy"] == "explicit-zero-fill", "root capacity/transport verification incomplete")
    require(root.get("transport", {}).get("file", "pianoroot.f2fs.sparse.img") == "pianoroot.f2fs.sparse.img",
            "unexpected root transport path")
    checked(transport, transport_meta)
    with transport.open("rb") as stream:
        header = stream.read(28)
    require(len(header) == 28, "truncated sparse header")
    magic, major, minor, fh, ch, block, total, chunks, crc = struct.unpack("<IHHHHIIII", header)
    require((magic, major, minor, fh, ch, block, crc) == (0xED26FF3A, 1, 0, 28, 12, BLOCK, 0)
            and 0 < chunks <= total and total * block == transport_meta["expanded_bytes"],
            "sparse geometry differs from the verified root capacity")
    require(combined.get("status") == "DEVICE_BOOT_FILE_VERIFIED_NOT_DEVICE_BOOTED"
            and combined["byte_equal_restoration"] is True and combined["active_slot"] == slot,
            "combined BOOT does not match the current slot or lacks lossless restore proof")
    # 请求页只在当前引导的那份 BOOT 里被读到。指向 boot_a 的配置写得进去，
    # 但首装后的 Linux 从 boot_b 启动，下一次启动读不到，切换会静默失效。
    require(combined["boot_request"].get("boot_device") == LINUX_BOOT_DEVICE,
            "device-boot records the boot request for "
            f"{combined['boot_request'].get('boot_device')!r}; it must be {LINUX_BOOT_DEVICE}")
    require(combined["uefi_product_sha256"] == sha256(bundle / "PianoUEFI-product.img"), "BOOT and ESP use different UEFI cores")
    for name in ("boot_android.img", "boot_linux.img", "boot-request.json"):
        checked(boot / name, combined["files"][name])
    current_boot = device.text("sha256sum /dev/block/by-name/boot" + slot).split()[0]
    require(current_boot == combined["source_boot_sha256"], "current Android BOOT changed since packaging")
    require(stock_boot.is_file() and not stock_boot.is_symlink() and stock_boot.stat().st_size == 96 * 1024**2
            and sha256(stock_boot) == current_boot, "current original BOOT recovery file is missing or differs")
    # 擦除后 Android 必须能通过自己的 fs_mgr 重建加密 userdata；不在宿主格式化密文分区。
    if not existing:
        fstab = device.text("cat /vendor/etc/fstab.*")
        rows = [line for line in fstab.splitlines() if re.search(r"\s/data\s", line) and not line.lstrip().startswith("#")]
        require(rows and all("formattable" in line.split()[-1].split(",") for line in rows),
                "Android userdata auto-format contract is unverified; do not repartition")
    require(device.text("getprop sys.boot_completed") == "1", "normal Android boot must be complete")
    return {"schema_version": 1, "status": "EXISTING_PARTITIONS_READ_ONLY_PLAN" if existing else "FIRST_INSTALL_READ_ONLY_PLAN", "device": snapshot["identity"],
            "android_slot": slot, "android_build": device.text("getprop ro.build.version.incremental"),
            "gpt_baseline": snapshot["gpt"].baseline(), "partition_plan": layout,
            "android_data_mib": android_data_mib, "esp_mib": esp_mib, "source_boot_sha256": current_boot,
            "inputs": {"bundle_manifest_sha256": sha256(bundle / "bundle-manifest.json"),
                       "root_manifest_sha256": sha256(prepared_manifest) if prepared_manifest.is_file() else None,
                       "boot_manifest_sha256": sha256(boot / "device-boot-manifest.json")},
            "android_data_preserved": existing, "root_capacity": capacity,
            "boot_request": {"device": combined["boot_request"]["boot_device"],
                             "source": "root-install" if root else "first-boot-autoconfig"},
            "device_writes": False}, snapshot


def sync_file(path):
    with path.open("rb") as stream:
        os.fsync(stream.fileno())


def sync_dir(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def wait_factory(device, identity, attempts=90, delay=2):
    """Wait for the stock bootloader to enumerate after the GPT reboot."""
    serial = identity["factory_serial"]
    for attempt in range(attempts):
        try:
            if device.fastboot_var(serial, "serialno") == serial:
                return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass
        if attempt + 1 < attempts:
            time.sleep(delay)
    raise RuntimeError(f"factory fastboot did not enumerate serial {serial} after partitioning")


def save(path, value):
    pending = path.with_suffix(".pending")
    with pending.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)
    sync_dir(path.parent)


def dd_available(device):
    """Prove the on-device dd accepts the flags the GPT write depends on.

    Android toybox and Fedora coreutils both ship `dd`, but `conv=fsync,notrunc`
    is the part the write relies on. Checking it on a scratch file keeps a
    missing flag from surfacing after the destructive boundary.
    """
    probe = "/data/local/tmp/piano-dd-probe-" + uuid.uuid4().hex
    device.shell("printf 'piano' > " + shlex.quote(probe) +
                 " && dd if=/dev/zero of=" + shlex.quote(probe) +
                 " bs=4096 seek=1 count=1 conv=fsync,notrunc 2>/dev/null" +
                 " && test \"$(stat -c %s " + shlex.quote(probe) + ")\" = 4096" +
                 " && rm -f " + shlex.quote(probe))
    return True


def write_gpt_blobs(device, identity, session, head, tail):
    """Write both GPT copies with dd, tail first, then prove both read back.

    Tail first is the recoverable order: if power is lost after the tail lands,
    the still-old primary header's alternate_lba finds the new tail, and a
    reboot re-parses cleanly. The reverse order would leave a new primary
    pointing at an old tail.
    """
    disk = identity["disk"]
    sectors = identity["disk_bytes"] // BLOCK
    tail_lba = sectors - (len(tail) // BLOCK)
    remote = "/data/local/tmp/piano-first-install-" + uuid.uuid4().hex
    device.call(["adb", "-s", device.serial, "push", str(session / "gpt-tail.bin"), remote + "-tail"])
    device.call(["adb", "-s", device.serial, "push", str(session / "gpt-head.bin"), remote + "-head"])
    for suffix, data in (("-tail", tail), ("-head", head)):
        require(device.text("sha256sum " + remote + suffix).split()[0] == hashlib.sha256(data).hexdigest(),
                "staged GPT blob differs: " + suffix)
    device.shell(f"dd if={remote}-tail of={disk} bs={BLOCK} seek={tail_lba} conv=fsync,notrunc")
    device.shell(f"dd if={remote}-head of={disk} bs={BLOCK} seek=0 conv=fsync,notrunc && sync")
    device.shell("rm -f " + remote + "-tail " + remote + "-head")
    return tail_lba


def restore_gpt_blobs(device, identity, session):
    """Put the pre-install table back, head last, so a reboot lands on the old one."""
    disk, sectors = identity["disk"], identity["disk_bytes"] // BLOCK
    tail, head = session / "original-gpt-tail.bin", session / "original-gpt-head.bin"
    remote = "/data/local/tmp/piano-gpt-restore-" + uuid.uuid4().hex
    device.call(["adb", "-s", device.serial, "push", str(tail), remote + "-tail"])
    device.call(["adb", "-s", device.serial, "push", str(head), remote + "-head"])
    device.shell(f"dd if={remote}-tail of={disk} bs={BLOCK} seek={sectors - len(tail.read_bytes()) // BLOCK} "
                 f"conv=fsync,notrunc")
    device.shell(f"dd if={remote}-head of={disk} bs={BLOCK} seek=0 conv=fsync,notrunc && sync")
    device.shell("rm -f " + remote + "-tail " + remote + "-head")



    pending = path.with_suffix(".pending")
    with pending.open("w") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    pending.replace(path)
    sync_dir(path.parent)


SSH_KEY_GUEST_PATH = "/piano-ssh-key.pub"


def validate_ssh_key(key_file):
    """Accept one ssh-ed25519 or ssh-rsa public key; never a private key."""
    require(key_file.is_file() and not key_file.is_symlink(), "--ssh-key must name a regular public-key file")
    text = key_file.read_text()
    require(len(text.splitlines()) == 1 and len(text) <= 1024, "--ssh-key must contain exactly one public key")
    fields = text.strip().split()
    require(len(fields) in (2, 3) and fields[0] in ("ssh-ed25519", "ssh-rsa"),
            "provide one ssh-ed25519 or ssh-rsa public key, never a private key")
    try:
        base64.b64decode(fields[1], validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("--ssh-key has invalid base64 key material") from error
    return fields[0], fields[1]


def inject_ssh_key(source, key_file, target):
    """Copy the validated ESP image and add the key the device imports on boot.

    The bundle is a read-only artifact, so the key goes into a private session
    copy. Callers flash the copy and compare readbacks against its digest.
    """
    validate_ssh_key(key_file)
    require(shutil.which("mcopy"), "mcopy is required for --ssh-key; install mtools")
    shutil.copyfile(source, target)
    result = subprocess.run(["mcopy", "-o", "-i", str(target), str(key_file), "::" + SSH_KEY_GUEST_PATH],
                            capture_output=True, text=True)
    require(result.returncode == 0, "mcopy failed to add the SSH key to the ESP: " +
            (result.stderr.strip() or result.stdout.strip() or "no output"))
    require(sha256(target) != sha256(source), "SSH key injection did not change the ESP image")
    return sha256(target)


def factory(device, identity, slot):
    serial = identity["factory_serial"]
    for key, expected in (("serialno", serial), ("product", "piano"), ("unlocked", "yes"),
                          ("is-userspace", "no"), ("current-slot", slot[1:])):
        require(device.fastboot_var(serial, key) == expected, "factory bootloader identity differs: " + key)


def execute(upstream, device, plan, bundle, root_install, boot, stock_boot, session, ssh_key=None,
            android_data_mib=None, esp_mib=None):
    root_install = root_install or bundle
    require(plan.get("android_data_mib") == android_data_mib,
            "apply repeats the plan's Android userdata size (or both are the equal split)")
    require(plan.get("esp_mib") == esp_mib,
            "apply repeats the plan's ESP size (or both use the built-in default)")
    fresh, snapshot = installation_plan(upstream, device, bundle, root_install, boot, stock_boot,
                                        android_data_mib=android_data_mib, esp_mib=esp_mib)
    require(fresh == plan, "device, GPT, slot or input package changed since the reviewed plan")
    # A bad key or a missing mcopy must fail before the GPT write and the
    # userdata erase, not halfway through the transfer.
    if ssh_key is not None:
        validate_ssh_key(ssh_key)
        require(shutil.which("mcopy"), "mcopy is required for --ssh-key; install mtools")
    require(not session.exists() and not session.is_symlink(), "use a new installation session directory")
    session.mkdir(mode=0o700, parents=True)
    identity, slot = plan["device"], plan["android_slot"]
    original_mbr = device.read(identity["disk"], 0, 1)
    expected_gpt, new_head, new_tail, old_head, old_tail = new_gpt(
        upstream, snapshot["gpt"], plan["partition_plan"], original_mbr, android_data_mib, esp_mib)
    for name, data in (("original-gpt-head.bin", old_head), ("original-gpt-tail.bin", old_tail),
                       ("gpt-head.bin", new_head), ("gpt-tail.bin", new_tail)):
        (session / name).write_bytes(data)
        sync_file(session / name)
    shutil.copyfile(stock_boot, session / "original-boot.img")
    sync_file(session / "original-boot.img")
    sync_dir(session)
    save(session / "plan.json", plan)
    journal = {"status": "PREPARED_NOT_WRITTEN", "completed": [], "device_writes": False,
               "original_boot_sha256": plan["source_boot_sha256"], "android_data_preserved": False,
               "ssh_key_injected": False}

    def stage(name):
        journal["completed"].append(name)
        save(session / "result.json", journal)

    try:
        stage("inputs_and_current_gpt_validated")
        # 不继承历史设备试验；本执行器没有执行临时引导，不能写成已验证。
        journal["temporary_uefi_verified"] = False
        journal["temporary_android_passthrough_verified"] = False
        # dd 的参数覆盖要在破坏性边界之前证明一次，不能等到写表时才发现。
        dd_available(device)
        latest = device.inspect()
        require(latest["identity"] == identity and latest["gpt"].baseline() == plan["gpt_baseline"], "GPT changed immediately before writing")
        require(device.text("sha256sum /dev/block/by-name/boot" + slot).split()[0] == plan["source_boot_sha256"],
                "original BOOT changed immediately before partitioning")
        # GPT 缩短 userdata 后，现有加密 /data 不再可信。必须在同一会话里
        # 显式擦除 userdata，让 Android 首次启动走 factory-reset 初始化。
        journal["device_writes"] = True
        journal["status"] = "GPT_WRITE_STARTED"
        save(session / "result.json", journal)
        gpt = expected_gpt
        try:
            # 尾部先写：中断后仍能由旧主表头的 alternate_lba 找到新尾部。
            write_gpt_blobs(device, identity, session, new_head, new_tail)
            measured = upstream.parse_gpt(device.read(identity["disk"], 1, 1),
                device.read(identity["disk"], gpt.geometry["table_lba"], gpt.geometry["table_blocks"]),
                device.read(identity["disk"], gpt.disk_bytes // BLOCK - 1, 1),
                device.read(identity["disk"], gpt.backup_table_lba, gpt.geometry["table_blocks"]), gpt.disk_bytes)
            require(measured.partitions == gpt.partitions and measured.geometry == gpt.geometry, "written GPT readback differs")
        except BaseException:
            # 与上游 apply() 的 restore 分支同义：写失败先把原表放回去，再报错。
            restore_gpt_blobs(device, identity, session)
            raise
        stage("gpt_written_and_both_copies_readback_validated")
        device.reboot_bootloader()
        wait_factory(device, identity)
        factory(device, identity, slot)
        for row in gpt.partitions:
            if row["name"] in ("userdata", "sunuefi_root", "sunuefi_esp"):
                require(int(device.fastboot_var(identity["factory_serial"], "partition-size:" + row["name"]), 16) == row["bytes"],
                        "bootloader did not load the new partition capacity")
        device.call(["fastboot", "-s", identity["factory_serial"], "erase", "userdata"], timeout=1800)
        journal.update(userdata_erased=True, status="USERDATA_ERASED_IMAGES_PENDING")
        stage("factory_fastboot_erased_userdata")
        generic = json.loads((bundle / "bundle-manifest.json").read_text())
        image = json.loads((bundle / "root-image-manifest.json").read_text())
        root = json.loads((root_install / "root-install-manifest.json").read_text()) \
            if (root_install / "root-install-manifest.json").is_file() else {}
        combined = json.loads((boot / "device-boot-manifest.json").read_text())
        root_sparse, _ = root_transport(bundle, root_install, generic, root, image)
        expected_hashes = {
            "sunuefi_esp": generic["files"]["sunuefi_esp.img"]["sha256"],
            "sunuefi_root": sha256(root_sparse),
            "boot" + slot: combined["files"]["boot_android.img"]["sha256"],
        }
        images = {"sunuefi_esp": bundle / "sunuefi_esp.img",
                  "sunuefi_root": root_sparse,
                  "boot" + slot: boot / "boot_android.img"}
        # The manifest was verified above, so the install-time key can now be
        # added to a session copy of the ESP. Never modify the input bundle.
        if ssh_key is not None:
            injected = session / "sunuefi_esp.img"
            expected_hashes["sunuefi_esp"] = inject_ssh_key(bundle / "sunuefi_esp.img", ssh_key, injected)
            images["sunuefi_esp"] = injected
            journal.update(ssh_key_injected=True, sunuefi_esp_sha256=expected_hashes["sunuefi_esp"])
            stage("installer_injected_ssh_public_key")
        for name, image in images.items():
            before = sha256(image)
            require(before == expected_hashes[name], "input image hash differs from validated manifest: " + name)
            device.call(["fastboot", "-s", identity["factory_serial"], "flash", name, str(image)], timeout=3600)
            require(sha256(image) == before, "input image changed during transfer")
            stage("factory_fastboot_accepted_" + name)
        journal.update(status="IMAGES_TRANSFERRED_FIRST_BOOT_PENDING",
                       android_userdata_initialized=False, linux_boot_verified=False,
                       android_boot_verified=False, device_readback_verified=False,
                       remaining=["first Android boot to initialize erased userdata",
                                  "first Linux boot and filesystem readback",
                                  "both-way switching"])
        save(session / "result.json", journal)
        return journal
    except BaseException as error:
        journal.update(status="INSTALL_INTERRUPTED", error=str(error),
                       recovery_gpt_head=str(session / "original-gpt-head.bin"),
                       recovery_gpt_tail=str(session / "original-gpt-tail.bin"),
                       recovery_boot=str(session / "original-boot.img"),
                       recovery_note="GPT restore cannot recover erased Android data; inspect completed stages before recovery or reboot")
        save(session / "result.json", journal)
        raise


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("operation", choices=("plan", "apply"))
    ap.add_argument("--sunuefi", type=Path, required=True)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--root-install", type=Path,
                    help="可选的预扩 root 产物；留空则直接刷 bundle 自带的 sparse")
    ap.add_argument("--device-boot", type=Path, required=True)
    ap.add_argument("--stock-boot", type=Path, required=True)
    ap.add_argument("--android-data-mib", type=int,
                    help="首次分区时留给 Android userdata 的 MiB；留空则扣除 ESP 后对半分")
    ap.add_argument("--esp-mib", type=int,
                    help="首次分区时 ESP 的 MiB；留空用 512。必须与 deploy-bundle 的 ESP 镜像等大")
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--ssh-key", type=Path,
                    help="One ssh-ed25519 or ssh-rsa public key for user piano; "
                         "injected into the ESP after every manifest check passes")
    ap.add_argument("--session", type=Path)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--accept-data-loss", action="store_true")
    args = ap.parse_args()
    # Validate the key before any device access, so a bad file never reaches
    # the destructive apply path.
    if args.ssh_key is not None:
        validate_ssh_key(args.ssh_key)
    upstream = load_source(args.sunuefi)
    device = upstream.Device(args.serial)
    if args.operation == "plan":
        result, _ = installation_plan(upstream, device, args.bundle, args.root_install or args.bundle,
                                      args.device_boot, args.stock_boot,
                                      android_data_mib=args.android_data_mib, esp_mib=args.esp_mib)
        require(not args.plan.exists(), "plan output already exists")
        save(args.plan, result)
    else:
        require(args.execute and args.accept_data_loss and args.session,
                "apply requires --execute --accept-data-loss and a new --session directory")
        result = execute(upstream, device, json.loads(args.plan.read_text()), args.bundle,
                         args.root_install, args.device_boot, args.stock_boot, args.session, args.ssh_key,
                         args.android_data_mib, args.esp_mib)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
