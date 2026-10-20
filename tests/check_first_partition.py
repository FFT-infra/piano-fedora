#!/usr/bin/env python3
"""离线检查首次分区计划工具。用真实 GPT 快照做 fixture，不接触设备。"""

import importlib.util
import json
import struct
import subprocess
import sys
import tempfile
import uuid
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "plan-first-partition.py"
BLOCK = 4096
COUNT = 64
DISK_GUID = "d55fa102-1ed9-4154-b3a7-3e84cd916cb5"


def gpt_module():
    spec = importlib.util.spec_from_file_location("piano_gpt_fixture", ROOT / "scripts" / "piano_gpt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_image(path, sectors, rows, gap=0):
    """写一份稀疏的整盘镜像：表头、entry 与分区字节，其余留空。

    `gap` 是备份分区表与备份表头之间的空块数；原厂 `gpt_backup0.bin` 为 2。
    """
    gpt = gpt_module()
    table = bytearray(COUNT * 128)
    for index, (name, first, last, kind) in enumerate(rows):
        table[index * 128:(index + 1) * 128] = gpt.entry(
            index, uuid.UUID(kind), uuid.uuid5(uuid.UUID(DISK_GUID), name), first, last, 0, name)
    table = bytes(table)
    array_crc = zlib.crc32(table)
    blocks = len(table) // BLOCK

    def header(here, alternate, table_lba):
        raw = bytearray(BLOCK)
        struct.pack_into("<8sIIIIQQQQ16sQIII", raw, 0, b"EFI PART", 0x10000, 92, 0, 0,
                         here, alternate, 6, sectors - 6, uuid.UUID(DISK_GUID).bytes_le,
                         table_lba, COUNT, 128, array_crc)
        struct.pack_into("<I", raw, 16, zlib.crc32(bytes(raw[:92])))
        return bytes(raw)

    mbr = bytearray(BLOCK)
    mbr[510:512] = b"\x55\xaa"
    tail_first = sectors - 1 - gap - blocks
    with path.open("wb") as stream:
        for offset, data in ((0, bytes(mbr)), (BLOCK, header(1, sectors - 1, 2)),
                             (2 * BLOCK, table),
                             (tail_first * BLOCK, table),
                             ((sectors - 1) * BLOCK, header(sectors - 1, 1, tail_first))):
            stream.seek(offset)
            stream.write(data)
    return path

# 2026-10-09 从本机 piano 只读捕获的 sgdisk --print 尾部。
REAL_GPT = """Disk /dev/block/sda: 249518080 sectors, 951.8 GiB
Sector size (logical/physical): 4096/4096 bytes
Disk identifier (GUID): D55FA102-1ED9-4154-B3A7-3E84CD916CB5
Partition table holds up to 64 entries
First usable sector is 6, last usable sector is 249518074
Total free space is 0 sectors (0 bytes)

Number  Start (sector)    End (sector)  Size       Code  Name
   1               6               7   8.0 KiB     FFFF  switch
  32          191488          224255   128.0 MiB   A039  rescue
  33          224256         3632127   13.0 GiB    FFFF  super
  34         3632128       249518074   938.0 GiB   A03A  userdata
"""


def run(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def main():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        dump = tmp / "gpt.txt"
        dump.write_text(REAL_GPT, encoding="utf-8")

        # inspect-only：如实报告无空位。
        result = run("--dump", str(dump), "--inspect-only")
        check(result.returncode == 0, "inspect should succeed")
        if result.returncode == 0:
            data = json.loads(result.stdout)
            check(data["free_sectors"] == 0, "real dump should show zero free sectors")
            check(data["device_writes"] is False, "inspect must not write")

        # 正常计划：ESP + root 从 userdata 尾部切出。
        result = run("--dump", str(dump), "--esp-mib", "512", "--root-mib", "16384")
        check(result.returncode == 0, f"plan should succeed, got {result.stderr}")
        if result.returncode == 0:
            data = json.loads(result.stdout)
            check(data["mode"] == "tail-split", "mode should be tail-split")
            check(data["device_writes"] is False, "plan must not write")
            changes = {c["name"]: c for c in data["changes"]}
            check("userdata" in changes, "plan should shrink userdata")
            check(changes["userdata"]["old_last"] == 249518074, "userdata old_last should match dump")
            check("sunuefi_root" in changes, "plan should add sunuefi_root")
            check("sunuefi_esp" in changes, "plan should add sunuefi_esp")
            # 起点按 1 MiB 对齐会把起点往前推，实际容量只会大于等于请求值。
            check(changes["sunuefi_root"]["size_mib"] >= 16384, "root size should be at least the request")
            check(changes["sunuefi_root"]["size_mib"] <= 16384 + 1, "root rounding should stay within 1 MiB")
            check(changes["sunuefi_esp"]["size_mib"] >= 512, "esp size should be at least the request")
            check(changes["sunuefi_esp"]["size_mib"] <= 512 + 1, "esp rounding should stay within 1 MiB")
            # 尾部必须被完整占用。
            check(changes["sunuefi_esp"]["last"] == 249518074, "esp should end at the disk tail")
            # 新分区不能重叠。
            check(changes["userdata"]["new_last"] < changes["sunuefi_root"]["first"], "userdata must not overlap root")
            check(changes["sunuefi_root"]["last"] < changes["sunuefi_esp"]["first"], "root must not overlap esp")

        result = run("--dump", str(dump), "--equal-split")
        check(result.returncode == 0, f"equal split should succeed: {result.stderr}")
        if result.returncode == 0:
            data = json.loads(result.stdout)
            check(data["allocation"] == "equal-userdata-after-esp", "equal split policy must be explicit")
            check(data["capacity"]["linux_root_mib"] == 479990, "real GPT half capacity must not be capped at 256 GiB")
            check(data["capacity"]["android_userdata_mib"] == 479989, "Android must retain its aligned half")
            check(data["capacity"]["equal_split_difference_bytes"] <= 2 * 1024**2, "rounding must stay within two alignment units")
            check(data["android_data_preserved"] is False, "destructive userdata handling must be explicit")
            check(all(c["partition"] <= 64 for c in data["changes"]), "new entries must respect actual GPT capacity")
            check(len(data["baseline_sha256"]) == 64, "plan must bind its source geometry")

        large = run("--dump", str(dump), "--root-mib", "480000")
        check(large.returncode == 0, "explicit root above 256 GiB must be accepted if it fits")

        # 以 Android 容量为准：300 GiB userdata，Linux root 取剩余空间。
        result = run("--dump", str(dump), "--esp-mib", "512", "--userdata-mib", "307200")
        check(result.returncode == 0, f"explicit Android size should succeed: {result.stderr}")
        if result.returncode == 0:
            data = json.loads(result.stdout)
            check(data["allocation"] == "explicit-android-size", "explicit Android policy must be recorded")
            check(data["capacity"]["android_userdata_mib"] == 307200, "Android must keep exactly the requested 300 GiB")
            check(data["capacity"]["linux_root_mib"] == 652779, "Linux must take the remaining capacity")
            check(data["capacity"]["esp_bytes"] == 537899008, "ESP size must not change with the Android split")
            check(data["changes"][-1]["last"] == 249518074, "ESP must still end at the disk tail")
            check(data["changes"][0]["first"] == 3632128, "Android userdata start must not move")

        for args, message in (
            (("--root-mib", "16384", "--userdata-mib", "307200"), "root and Android sizes together"),
            (("--userdata-mib", "512"), "Android size below 1 GiB"),
            (("--userdata-mib", "999999"), "Android size leaving no room"),
        ):
            check(run("--dump", str(dump), *args).returncode == 2, message + " must be rejected")

        for replacement, message in (
            (REAL_GPT.replace("64 entries", "4096 entries"), "GPT tables overlap usable area"),
            (REAL_GPT.replace("64 entries", "4 entries").replace("  32 ", "   2 ")
                     .replace("  33 ", "   3 ").replace("  34 ", "   4 "), "no free GPT slots"),
            (REAL_GPT.replace("34         3632128", "33         3632128"), "duplicate partition index"),
            (REAL_GPT.replace("34         3632128", "34         3632129"), "unaligned userdata start"),
            (REAL_GPT.replace("249518074   938.0", "249518070   938.0"), "userdata must fill tail"),
            (REAL_GPT.replace("  34 ", "  65 "), "entry index exceeds table"),
        ):
            bad = tmp / "invalid.txt"
            bad.write_text(replacement, encoding="utf-8")
            check(run("--dump", str(bad), "--equal-split").returncode == 2, message + " must be rejected")

        unnamed = tmp / "unnamed.txt"
        unnamed.write_text(REAL_GPT +
                           "   2               8              15   32.0 KiB    FFFF\n", encoding="utf-8")
        result = run("--dump", str(unnamed), "--equal-split")
        check(result.returncode == 0, "unnamed occupied GPT entries must remain visible")
        if result.returncode == 0:
            data = json.loads(result.stdout)
            check(any(p["number"] == 2 and p["name"] == "" for p in data["original_partitions"]), "unnamed entry was omitted")
            check(all(c["partition"] != 2 for c in data["changes"]), "unnamed occupied entry was reused")

        # userdata 太小、放不下 ESP+root：拒绝。
        # 用一个小盘做 fixture，避免依赖真实盘的容量。
        small = tmp / "small.txt"
        small.write_text(
            REAL_GPT.replace("249518080", "200000")
                    .replace("249518074", "199999")
                    .replace("3632128       249518074", "190000       199999"),
            encoding="utf-8",
        )
        check(
            run("--dump", str(small), "--root-mib", "262144").returncode == 2,
            "root larger than available space should be rejected",
        )

        # 容量必须由实时 GPT 决定，不能写死成某台设备的尺寸。
        # 同一套规则在 128 GiB 到 1 TiB 上都应把剩余空间对半分。
        for label, last_usable in (("128GiB", 31457280), ("256GiB", 62914560),
                                   ("512GiB", 126509311), ("1TiB", 249518074)):
            capacity = tmp / ("cap-" + label + ".txt")
            capacity.write_text(
                REAL_GPT.replace("249518080", str(last_usable + 6))
                        .replace("249518074", str(last_usable))
                        .replace("3632128       249518074", "3632128       " + str(last_usable)),
                encoding="utf-8")
            result = run("--dump", str(capacity), "--equal-split")
            check(result.returncode == 0, f"{label}: equal split should succeed: {result.stderr}")
            if result.returncode != 0:
                continue
            data = json.loads(result.stdout)
            root_mib = data["capacity"]["linux_root_mib"]
            android_mib = data["capacity"]["android_userdata_mib"]
            check(abs(root_mib - android_mib) <= 1,
                  f"{label}: Android and Linux halves must match, got {android_mib}/{root_mib}")
            check(root_mib > 1024, f"{label}: root half must not collapse")
            # 1 MiB 对齐只会把起始扇区往前推，ESP 实际容量等于或略大于请求值。
            esp_mib = data["capacity"]["esp_bytes"] // 1024**2
            check(512 <= esp_mib <= 513,
                  f"{label}: ESP must stay at 512 MiB (+1 MiB alignment), got {esp_mib}")
            check(data["changes"][-1]["last"] == last_usable,
                  f"{label}: ESP must end at the disk tail")

        # ESP 越界：拒绝。
        check(
            run("--dump", str(dump), "--esp-mib", "4096").returncode == 2,
            "oversized esp should be rejected",
        )

        # 扇区大小不符：拒绝。
        bad = tmp / "bad.txt"
        bad.write_text(REAL_GPT.replace("4096/4096", "512/4096"), encoding="utf-8")
        check(run("--dump", str(bad)).returncode == 2, "512-byte sectors should be rejected")

        # 没有 userdata：拒绝。
        nodata = tmp / "nodata.txt"
        nodata.write_text("\n".join(l for l in REAL_GPT.splitlines() if "userdata" not in l), encoding="utf-8")
        check(run("--dump", str(nodata)).returncode == 2, "missing userdata should be rejected")

        # sfdisk --dump 的文本没有总扇区数与表项数，缺了就拒绝并指出怎么补。
        sfdisk_dump = tmp / "sfdisk.txt"
        sfdisk_dump.write_text(
            "label: gpt\nlabel-id: D55FA102-1ED9-4154-B3A7-3E84CD916CB5\n"
            "unit: sectors\nfirst-lba: 6\nlast-lba: 249518074\nsector-size: 4096\n\n"
            "sda32 : start=       191488, size=       32768, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, "
            "uuid=11111111-2222-3333-4444-555555555555, name=\"rescue\"\n"
            "sda34 : start=      3632128, size=  245885947, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4, "
            "uuid=66666666-7777-8888-9999-AAAAAAAAAAAA, name=\"userdata\"\n",
            encoding="utf-8")
        result = run("--dump", str(sfdisk_dump), "--equal-split")
        check(result.returncode == 2 and "--sectors" in result.stderr,
              "an sfdisk dump without the disk size must be rejected with a hint")
        result = run("--dump", str(sfdisk_dump), "--sectors", "249518080", "--entries", "64", "--equal-split")
        check(result.returncode == 0, f"sfdisk dump with hints should plan: {result.stderr}")
        if result.returncode == 0:
            data = json.loads(result.stdout)
            check(data["capacity"]["linux_root_mib"] == 479990, "sfdisk and sgdisk text must plan the same layout")
            check(data["disk"]["entry_count"] == 64, "hint entry count must reach the plan")
            check([p["number"] for p in data["original_partitions"]] == [32, 34],
                  "sfdisk partition numbers must come from the dump")

        # 整盘镜像上的规划：--emit-gpt 写出的两段 blob 必须就是整表编码。
        image = build_image(tmp / "disk.img", 249518080, (
            ("rescue", 191488, 224255, "0fc63daf-8483-4772-8e79-3d69d8477de4"),
            ("super", 224256, 3632127, "0fc63daf-8483-4772-8e79-3d69d8477de4"),
            ("userdata", 3632128, 249518074, "0fc63daf-8483-4772-8e79-3d69d8477de4")))
        out = tmp / "gpt"
        result = run("--device", str(image), "--userdata-mib", "307200", "--emit-gpt", str(out))
        check(result.returncode == 0, f"--emit-gpt should succeed: {result.stderr}")
        if result.returncode == 0:
            data = json.loads(result.stdout)
            blobs = data["gpt_blobs"]
            head = (out / "gpt-head.bin").read_bytes()
            tail = (out / "gpt-tail.bin").read_bytes()
            check(len(head) == 2 * BLOCK + COUNT * 128, "head blob must be MBR + header + entries")
            check(len(tail) == COUNT * 128 + BLOCK, "tail blob must be entries + backup header")
            check(blobs["tail_lba"] + blobs["tail_sectors"] == 249518080,
                  "tail blob must end on the last LBA")
            with image.open("rb") as stream:
                check(head[:BLOCK] == stream.read(BLOCK), "head blob must carry the protective MBR")
            sums = (out / "SHA256SUMS").read_text(encoding="utf-8").split()
            check(len(sums) == 8, "four blobs, four digests")
            for name in ("gpt-head.bin", "gpt-tail.bin", "original-gpt-head.bin", "original-gpt-tail.bin"):
                check(name in sums, name + " must be recorded in SHA256SUMS")
            reparsed = gpt_module().parse(head, tail, 249518080 * BLOCK)
            rows = {row["name"]: row for row in reparsed["partitions"]}
            check(rows["userdata"]["last"] == 82275327, "Android must be cut to 300 GiB")
            check(rows["sunuefi_root"]["first"] == 82275328, "root must start right after Android")
            check(rows["sunuefi_esp"]["last"] == 249518074, "ESP must end at the disk tail")
            check(rows["rescue"]["raw"] == gpt_module().parse(
                (out / "original-gpt-head.bin").read_bytes(),
                (out / "original-gpt-tail.bin").read_bytes(), 249518080 * BLOCK)["partitions"][0]["raw"],
                "fixed partitions must keep their bytes")
            check(gpt_module().parse((out / "original-gpt-head.bin").read_bytes(),
                                     (out / "original-gpt-tail.bin").read_bytes(),
                                     249518080 * BLOCK)["partitions"][2]["last"] == 249518074,
                  "the original blob pair must restore the untouched table")
            # 同一份现场读数再规划一次，得到的 blob 必须逐字节相同。
            again = tmp / "gpt-again"
            check(run("--device", str(image), "--userdata-mib", "307200",
                      "--emit-gpt", str(again)).returncode == 0, "re-emit should succeed")
            check((again / "gpt-head.bin").read_bytes() == head
                  and (again / "gpt-tail.bin").read_bytes() == tail, "re-emitted blobs must be identical")

        # 原厂尾部在备份表与备份表头之间留 2 个空块（gpt_backup0.bin 是 5 块）。
        # 长度只能由备份表头自己的 partition_entry_lba 算出来。
        gapped_sectors = 249518080
        gapped = build_image(tmp / "gapped.img", gapped_sectors, (
            ("rescue", 191488, 224255, "0fc63daf-8483-4772-8e79-3d69d8477de4"),
            ("super", 224256, 3632127, "0fc63daf-8483-4772-8e79-3d69d8477de4"),
            ("userdata", 3632128, 249518074, "0fc63daf-8483-4772-8e79-3d69d8477de4")), gap=2)
        gapped_out = tmp / "gapped-gpt"
        result = run("--device", str(gapped), "--userdata-mib", "307200", "--emit-gpt", str(gapped_out))
        check(result.returncode == 0, f"factory gapped tail should plan: {result.stderr}")
        if result.returncode == 0:
            data = json.loads(result.stdout)
            gapped_head = (gapped_out / "gpt-head.bin").read_bytes()
            gapped_tail = (gapped_out / "gpt-tail.bin").read_bytes()
            check(data["gpt_blobs"]["tail_lba"] == 249518075, "tail blob must start at the backup table")
            check(data["gpt_blobs"]["tail_sectors"] == 5, "tail blob must cover table, gap and header")
            check(len(gapped_tail) == 5 * BLOCK, "factory tail must stay five blocks")
            rows = {row["name"]: row for row in gpt_module().parse(
                gapped_head, gapped_tail, gapped_sectors * BLOCK)["partitions"]}
            check(rows["sunuefi_esp"]["last"] == 249518074, "gapped emit must parse back")
            check(len((gapped_out / "original-gpt-tail.bin").read_bytes()) == 5 * BLOCK,
                  "original factory table must keep the gap too")
            # 两段 dd 读数按表头算出的 5 块读取，必须产出同一对 blob。
            with gapped.open("rb") as stream:
                gapped_head_raw = stream.read(8 * BLOCK)
                stream.seek((gapped_sectors - 5) * BLOCK)
                gapped_tail_raw = stream.read(5 * BLOCK)
            (tmp / "gapped-head.raw").write_bytes(gapped_head_raw)
            (tmp / "gapped-tail.raw").write_bytes(gapped_tail_raw)
            split_out = tmp / "gapped-split"
            result = run("--head", str(tmp / "gapped-head.raw"), "--tail", str(tmp / "gapped-tail.raw"),
                         "--sectors", str(gapped_sectors), "--userdata-mib", "307200",
                         "--emit-gpt", str(split_out))
            check(result.returncode == 0, f"gapped dd reads should emit: {result.stderr}")
            if result.returncode == 0:
                check((split_out / "gpt-head.bin").read_bytes() == gapped_head
                      and (split_out / "gpt-tail.bin").read_bytes() == gapped_tail,
                      "gapped dd reads must produce the same blobs as the image read")
            # 按旧假设只读“表 + 表头”3 块时必须被拒绝，而不是静默解析错位。
            (tmp / "gapped-short.raw").write_bytes(gapped_tail[-3 * BLOCK:])
            check(run("--head", str(tmp / "gapped-head.raw"), "--tail", str(tmp / "gapped-short.raw"),
                      "--sectors", str(gapped_sectors), "--userdata-mib", "307200").returncode == 2,
                  "a tail read that skips the backup table must be rejected")

        # --emit-gpt 不能用在只有文本 dump 的输入上。
        check(run("--dump", str(dump), "--emit-gpt", str(tmp / "x")).returncode == 2,
              "--emit-gpt without a device read must be rejected")

        # 两段 dd 读数（Android 侧的手动路径）：可以比精确 blob 长，工具自己裁。
        sectors = 249518080
        with image.open("rb") as stream:
            head_raw = stream.read(8 * BLOCK)
            stream.seek((sectors - 6) * BLOCK)
            tail_raw = stream.read(6 * BLOCK)
        (tmp / "head.raw").write_bytes(head_raw)
        (tmp / "tail.raw").write_bytes(tail_raw)
        split = tmp / "split-gpt"
        result = run("--head", str(tmp / "head.raw"), "--tail", str(tmp / "tail.raw"),
                     "--sectors", str(sectors), "--userdata-mib", "307200", "--emit-gpt", str(split))
        check(result.returncode == 0, f"two dd reads should emit: {result.stderr}")
        if result.returncode == 0:
            check((split / "gpt-head.bin").read_bytes() == head
                  and (split / "gpt-tail.bin").read_bytes() == tail,
                  "dd reads with slack must produce the same blobs as the image read")
        check(run("--head", str(tmp / "head.raw"), "--tail", str(tmp / "tail.raw"),
                  "--userdata-mib", "307200").returncode == 2,
              "two raw blobs without --sectors must be rejected")

        other = build_image(tmp / "other.img", 249518080, (
            ("rescue", 191488, 224255, "0fc63daf-8483-4772-8e79-3d69d8477de4"),
            ("userdata", 3632128, 249518074, "0fc63daf-8483-4772-8e79-3d69d8477de4")))
        plan_file = tmp / "plan.json"
        result = run("--device", str(image), "--userdata-mib", "307200", "--output", str(plan_file))
        check(result.returncode == 0, "planning a reviewable JSON should succeed")
        result = run("--device", str(other), "--plan", str(plan_file), "--emit-gpt", str(tmp / "y"))
        check(result.returncode == 2, "a device read that differs from the reviewed plan must be rejected")
        result = run("--device", str(image), "--plan", str(plan_file), "--emit-gpt", str(tmp / "z"))
        check(result.returncode == 0, f"the reviewed plan should emit on the same table: {result.stderr}")
        if result.returncode == 0:
            check((tmp / "z" / "gpt-head.bin").read_bytes() == head
                  and (tmp / "z" / "gpt-tail.bin").read_bytes() == tail,
                  "a reviewed plan must emit the same blobs")

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: first-partition plan and reject paths behave as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
