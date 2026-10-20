#!/usr/bin/env python3
"""生成首次分区计划：从满盘的 userdata 尾部切出 ESP 与 Linux root。

上游 install_piano.py 对首次分区返回 NEW_INSTALL_NOT_READY，理由是缺少
经验证的 Android 缩容 helper 与 GPT 写传输。本工具的默认行为只做**计划**：
读入真实 GPT，算出新的分区布局，并把结果写成 JSON 供复核。

约束来自本机实测：
- 逻辑/物理扇区都是 4096 字节
- userdata 占满盘尾，全盘无空位
- userdata 是加密 dm 映射，无损缩容的前提不成立

计划只有一种产物，输入有两种：
- tail-split：保持 userdata 起点不变，只缩短它，在尾部放 ESP + root。
  这会破坏 userdata 里的现有数据，是初装路径。
- inspect-only：只报告现状与可行性，不改任何东西。

分区表的输入有三种：文本 dump（规划用）、整块设备或整盘镜像、两段 dd 读数。
后两种才能配合 `--emit-gpt` 写出可直接 dd 的新表。

用法：
    scripts/plan-first-partition.py --dump GPT.txt --esp-mib 512 --root-mib 16384
    scripts/plan-first-partition.py --dump GPT.txt --esp-mib 512 --userdata-mib 307200
    scripts/plan-first-partition.py --dump GPT.txt --inspect-only
    scripts/plan-first-partition.py --device /dev/sda --userdata-mib 307200 --emit-gpt out/
    scripts/plan-first-partition.py --head head.raw --tail tail.raw --sectors 249518080 \\
        --userdata-mib 307200 --emit-gpt out/
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import struct
import sys
from pathlib import Path

SECTOR = 4096
MIB = 1024 * 1024
SECTORS_PER_MIB = MIB // SECTOR
# 新分区起点按 1 MiB 对齐，这是分区工具的常规做法。
ALIGN_SECTORS = MIB // SECTOR
# sfdisk 的文本 dump 不带总扇区数与 GPT 项数，这两个值由调用方补。
# `--sectors` 要的是 4096 字节 LBA 总数；`/sys/block/sda/size` 的单位是
# 512 字节扇区，两者差 8 倍。主表头的 alternate_lba 才是磁盘最后一个 LBA。
SFDISK_HINT = ("sfdisk --dump does not carry the disk size or GPT entry count; pass "
               "--sectors N (last LBA + 1, from the primary header's alternate_lba at "
               "offset 4128) and --entries N "
               "(dd if=/dev/sda bs=4096 skip=1 count=1 | od -An -tu4 -j 80 -N 4)")


def load_gpt_module():
    spec = importlib.util.spec_from_file_location("piano_gpt", Path(__file__).resolve().parent / "piano_gpt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def align_down(value, alignment):
    return value - (value % alignment)


class PlanError(Exception):
    """输入不满足要求，属于可预期失败。"""


def parse_sgdisk(text):
    """解析 sgdisk --print 的输出。返回磁盘信息与分区列表。"""
    disk = {}
    m = re.search(r"Disk .*?: ([\d,]+) sectors", text)
    if m:
        disk["sectors"] = int(m.group(1).replace(",", ""))
    m = re.search(r"Sector size \(logical/physical\): (\d+)/(\d+) bytes", text)
    if m:
        disk["logical_sector"] = int(m.group(1))
        disk["physical_sector"] = int(m.group(2))
    m = re.search(r"Partition table holds up to (\d+) entries", text)
    if m:
        disk["entry_count"] = int(m.group(1))
    m = re.search(r"Disk identifier \(GUID\): ([0-9A-Fa-f-]{36})", text)
    if m:
        disk["guid"] = m.group(1).lower()
    m = re.search(r"First usable sector is (\d+), last usable sector is (\d+)", text)
    if m:
        disk["first_usable"] = int(m.group(1))
        disk["last_usable"] = int(m.group(2))

    parts = []
    for line in text.splitlines():
        row = re.match(
            r"\s*(\d+)\s+(\d+)\s+(\d+)\s+([\d.]+ \w+)\s+([0-9A-F]{4})(?:\s+(\S.*?))?\s*$", line
        )
        if row:
            parts.append({
                "number": int(row.group(1)),
                "first": int(row.group(2)),
                "last": int(row.group(3)),
                "size_text": row.group(4),
                "code": row.group(5),
                "name": row.group(6) or "",
            })
    if not parts:
        raise PlanError("no partitions parsed; is this sgdisk --print output?")
    return disk, parts


def find_userdata(parts):
    rows = [part for part in parts if part["name"] == "userdata"]
    if len(rows) != 1:
        raise PlanError("exactly one userdata partition is required")
    return rows[0]


def build_plan(disk, parts, esp_mib, root_mib=None, userdata_mib=None):
    if disk.get("logical_sector") != SECTOR:
        raise PlanError(
            f"this plan assumes {SECTOR}-byte logical sectors, got {disk.get('logical_sector')}"
        )

    for key in ("sectors", "first_usable", "last_usable", "entry_count"):
        if type(disk.get(key)) is not int or disk[key] <= 0:
            raise PlanError(f"missing or invalid GPT geometry: {key}")
    if not disk["first_usable"] < disk["last_usable"] < disk["sectors"] - 1:
        raise PlanError("invalid GPT usable range")
    if not 1 <= disk["entry_count"] <= 4096:
        raise PlanError("unsupported GPT entry count")
    table_sectors = (disk["entry_count"] * 128 + SECTOR - 1) // SECTOR
    if (2 + table_sectors > disk["first_usable"]
            or disk["last_usable"] + table_sectors >= disk["sectors"] - 1):
        raise PlanError("GPT entry arrays overlap the declared usable area")
    if type(esp_mib) is not int or not 64 <= esp_mib <= 2048:
        raise PlanError("esp size must be 64..2048 MiB")
    numbers, names = set(), set()
    ordered = sorted(parts, key=lambda p: p["first"])
    for part in ordered:
        if (not 1 <= part["number"] <= disk["entry_count"] or part["number"] in numbers
                or (part["name"] and part["name"] in names)
                or not disk["first_usable"] <= part["first"] <= part["last"] <= disk["last_usable"]):
            raise PlanError("duplicate or out-of-range GPT partition")
        numbers.add(part["number"])
        names.add(part["name"])
    if any(a["last"] >= b["first"] for a, b in zip(ordered, ordered[1:])):
        raise PlanError("GPT partitions overlap")
    if names & {"sunuefi_root", "sunuefi_esp"}:
        raise PlanError("dedicated Linux partitions already exist; this is a first-install planner")
    userdata = find_userdata(parts)
    if userdata != ordered[-1] or userdata["last"] != disk["last_usable"]:
        raise PlanError("userdata must occupy the full disk tail")
    if userdata["first"] % ALIGN_SECTORS:
        raise PlanError("userdata start must be 1 MiB aligned")
    esp_want = esp_mib * SECTORS_PER_MIB

    # 从盘尾往前排：ESP 占尾，root 紧随其后，两者起点各自按 1 MiB 对齐。
    # 对齐把起点往前推，所以实际容量只会大于等于请求值。
    # userdata 起点不动，只把末尾缩短到 root 起点之前。
    esp_last = userdata["last"]
    esp_first = align_down(esp_last - esp_want + 1, ALIGN_SECTORS)
    root_last = esp_first - 1
    if root_mib is not None and userdata_mib is not None:
        raise PlanError("specify either root size or Android userdata size, not both")
    if userdata_mib is not None:
        # 以 Android 侧容量为准：userdata 起点不动，root 从它后面开始。
        # 起点已按 1 MiB 对齐，所以 userdata 就是请求的整数值。
        if type(userdata_mib) is not int or userdata_mib < 1024:
            raise PlanError("Android userdata size must be at least 1024 MiB")
        root_first = align_down(userdata["first"] + userdata_mib * SECTORS_PER_MIB, ALIGN_SECTORS)
        if root_first > root_last:
            raise PlanError("Android userdata size leaves no room for Linux root")
    elif root_mib is None:
        root_first = align_down(userdata["first"] + (esp_first - userdata["first"]) // 2, ALIGN_SECTORS)
    else:
        if type(root_mib) is not int or root_mib < 1024:
            raise PlanError("root size must be at least 1024 MiB")
        root_first = align_down(root_last - root_mib * SECTORS_PER_MIB + 1, ALIGN_SECTORS)
    new_userdata_last = root_first - 1

    if min(root_last - root_first + 1, new_userdata_last - userdata["first"] + 1) < 1024 * SECTORS_PER_MIB:
        raise PlanError("Android userdata and Linux root each need at least 1024 MiB")
    for label, value in (("root_first", root_first), ("esp_first", esp_first)):
        if value % ALIGN_SECTORS:
            raise PlanError(f"{label}={value} is not {ALIGN_SECTORS}-sector aligned")
    if esp_last != userdata["last"]:
        raise PlanError("tail split did not consume the full tail")

    root_sectors = root_last - root_first + 1
    esp_sectors = esp_last - esp_first + 1

    used_numbers = {p["number"] for p in parts}
    free_numbers = [n for n in range(1, disk["entry_count"] + 1) if n not in used_numbers]
    if len(free_numbers) < 2:
        raise PlanError("fewer than two free GPT entries for ESP and root")

    return {
        "status": "PLAN",
        "schema_version": 1,
        "mode": "tail-split",
        "allocation": ("explicit-android-size" if userdata_mib is not None
                       else "explicit-root-size" if root_mib is not None
                       else "equal-userdata-after-esp"),
        "baseline_sha256": hashlib.sha256(
            json.dumps(canonical(disk, parts), sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "disk": disk,
        "original_partitions": parts,
        "android_data_preserved": False,
        "capacity": {
            "android_userdata_mib": (new_userdata_last - userdata["first"] + 1) // SECTORS_PER_MIB,
            "linux_root_mib": root_sectors // SECTORS_PER_MIB,
            "esp_bytes": esp_sectors * SECTOR,
            "equal_split_difference_bytes": abs(root_sectors - (new_userdata_last - userdata["first"] + 1)) * SECTOR,
        },
        "changes": [
            {
                "partition": userdata["number"],
                "name": "userdata",
                "first": userdata["first"],
                "old_last": userdata["last"],
                "new_last": new_userdata_last,
                "note": "起点不变，只缩短尾部；分区内现有数据不再可用",
            },
            {
                "partition": free_numbers[0],
                "name": "sunuefi_root",
                "first": root_first,
                "last": root_last,
                "size_sectors": root_sectors,
                "size_mib": root_sectors // SECTORS_PER_MIB,
                "type": "linux",
                "fs": "f2fs",
                "label": "PIANOROOT",
            },
            {
                "partition": free_numbers[1],
                "name": "sunuefi_esp",
                "first": esp_first,
                "last": esp_last,
                "size_mib": esp_mib,
                "type": "efi",
                "fs": "vfat",
                "label": "SUNUEFI_ESP",
            },
        ],
        "warnings": [
            "这会破坏 userdata 内的现有数据，是初装路径，不是无损缩容。",
            "保留 Android 固定系统分区；userdata 清空重建，不对加密文件系统做无损缩容。",
            "GPT 和原 BOOT 恢复材料不包含 Android 用户数据。",
        ],
        "device_writes": False,
    }


def parse_sfdisk(text, sectors=None, entries=None):
    """解析 sfdisk --dump 的文本。返回磁盘信息与分区列表。

    dump 只有 `first-lba` / `last-lba` 与每条分区的 `start=` / `size=`，没有
    总扇区数与表项数。这两个值决定新表能不能写、新 entry 用哪一号，所以缺了
    就拒绝，不从 `last-lba` 反推。
    """
    if not re.search(r"^\s*label:\s*gpt\s*$", text, re.M | re.I):
        raise PlanError("not a GPT sfdisk dump")
    size = re.search(r"^\s*sector-size:\s*(\d+)\s*$", text, re.M)
    logical = int(size.group(1)) if size else 512
    if logical != SECTOR:
        raise PlanError(f"this plan assumes {SECTOR}-byte logical sectors, got {logical}")
    first_lba = re.search(r"^\s*first-lba:\s*(\d+)\s*$", text, re.M)
    last_lba = re.search(r"^\s*last-lba:\s*(\d+)\s*$", text, re.M)
    label = re.search(r"^\s*label-id:\s*([0-9A-Fa-f-]{36})\s*$", text, re.M)
    if not (first_lba and last_lba and label):
        raise PlanError("sfdisk dump is missing first-lba, last-lba or label-id")
    if sectors is None or entries is None:
        raise PlanError(SFDISK_HINT)
    disk = {"sectors": sectors, "logical_sector": logical, "physical_sector": logical,
            "entry_count": entries, "first_usable": int(first_lba.group(1)),
            "last_usable": int(last_lba.group(1)), "guid": label.group(1).lower()}

    parts = []
    for line in text.splitlines():
        row = re.match(r'\s*(\S+?)(\d+)\s*:\s*(.*)$', line)
        if not row or row.group(1) in ("label-id", "first-lba", "last-lba", "sector-size"):
            continue
        fields = {m[0]: (m[1] or m[2]).strip() for m in
                  re.findall(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,]+))', row.group(3))}
        for required in ("start", "size"):
            if required not in fields:
                raise PlanError(f"sfdisk partition line lacks {required}: {line.strip()}")
        first = int(fields["start"])
        kind = fields.get("type", "").upper()
        parts.append({"number": int(row.group(2)), "first": first,
                      "last": first + int(fields["size"]) - 1,
                      "size_text": str(int(fields["size"]) * logical),
                      "code": kind, "name": fields.get("name", "")})
    if not parts:
        raise PlanError("no partitions parsed; is this sfdisk --dump output?")
    return disk, parts


def parse_text(text, sectors=None, entries=None):
    """按内容判断是 sgdisk --print 还是 sfdisk --dump 的文本。"""
    if re.search(r"^\s*Number\s+Start \(sector\)", text, re.M):
        return parse_sgdisk(text)
    return parse_sfdisk(text, sectors, entries)


def read_blobs(paths, disk_bytes=None):
    """读一份 GPT：整块设备或整盘镜像，或一份 dd 出来的两段原始读数。"""
    gpt = load_gpt_module()
    if len(paths) == 1:
        path = Path(paths[0])
        if not path.exists():
            raise PlanError("no such device or image: " + str(path))
        size = path.stat().st_size if path.is_file() else None
        if size is None:
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
        if size is None or size < 8 * SECTOR or size % SECTOR:
            raise PlanError("device or image size must be a nonzero multiple of 4 KiB")
        with path.open("rb") as stream:
            head = stream.read(2 * SECTOR)
            length = gpt.probe(head, size // SECTOR)
            stream.seek(0)
            head = stream.read(length)
            stream.seek(size - (length - SECTOR))
            tail = stream.read(length - SECTOR)
        return gpt.load(head, tail, size)
    first, second = (Path(path) for path in paths)
    head, tail = first.read_bytes(), second.read_bytes()
    if disk_bytes is None:
        raise PlanError("--sectors is required when reading two raw blobs")
    return gpt.load(head, tail, disk_bytes)


def plan_rows(gpt):
    """把 `piano_gpt.parse()` 的结果换成 `build_plan()` 的输入形状。"""
    disk = {"sectors": gpt["sectors"], "logical_sector": SECTOR, "physical_sector": SECTOR,
            "entry_count": gpt["geometry"]["count"], "first_usable": gpt["geometry"]["first"],
            "last_usable": gpt["geometry"]["last"], "guid": gpt["geometry"]["disk_guid"]}
    rows = [{"number": row["index"] + 1, "first": row["first"], "last": row["last"],
             "name": row["name"], "code": row["type"], "size_text": str(row["bytes"])}
            for row in gpt["partitions"]]
    return disk, rows


def canonical(disk, parts):
    """计划与现场读数之间的可比部分：边界、名字、编号与磁盘几何。"""
    geometry = {key: disk[key] for key in
                ("sectors", "logical_sector", "entry_count", "first_usable", "last_usable", "guid")}
    rows = sorted(({"number": p["number"], "first": p["first"], "last": p["last"], "name": p["name"]}
                   for p in parts), key=lambda row: row["first"])
    return {"disk": geometry, "partitions": rows}


def compare_base(plan, gpt):
    """现场读到的表与 `--dump` 算出的计划必须是同一份。

    `--emit-gpt` 的边界来自现场读数，计划里的边界来自文本 dump。两者不一致
    时写出的表就与复核过的计划无关，所以在这里停住。
    """
    disk, rows = plan_rows(gpt)
    if canonical(disk, rows) != canonical(plan["disk"], plan["original_partitions"]):
        raise PlanError("partition table on the device differs from the --dump plan")


def emit(plan, gpt, mbr, directory):
    """写出两段可直接 dd 的新表 blob、分区前的原表与摘要。"""
    gpt_mod = load_gpt_module()
    head, tail, original_head, original_tail = gpt_mod.rewrite(gpt, plan, mbr)
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    files = {"gpt-head.bin": head, "gpt-tail.bin": tail,
             "original-gpt-head.bin": original_head, "original-gpt-tail.bin": original_tail}
    for name, data in files.items():
        (out / name).write_bytes(data)
    lines = [f"{hashlib.sha256(data).hexdigest()}  {name}" for name, data in files.items()]
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    sectors = gpt["sectors"]
    return {"head_bytes": len(head), "tail_bytes": len(tail),
            "head_lba": 0, "head_sectors": len(head) // SECTOR,
            "tail_lba": sectors - len(tail) // SECTOR, "tail_sectors": len(tail) // SECTOR,
            "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}


def inspect(disk, parts):
    userdata = find_userdata(parts)
    total = disk.get("sectors", 0)
    return {
        "status": "INSPECT",
        "disk": disk,
        "userdata": userdata,
        "free_sectors": 0 if userdata["last"] >= disk.get("last_usable", 0) else disk["last_usable"] - userdata["last"],
        "note": "userdata 占满尾部，全盘无空位" if userdata["last"] >= disk.get("last_usable", 0) else "尾部有空位",
        "device_writes": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dump", help="sgdisk --print 或 sfdisk --dump 的输出文件")
    source.add_argument("--device", help="整块设备或整盘镜像，例如 /dev/sda")
    source.add_argument("--head", help="从 LBA 0 起的 dd 读数；与 --tail 同时使用")
    parser.add_argument("--tail", help="包含备份表头、以磁盘最后一个扇区结束的 dd 读数")
    parser.add_argument("--sectors", type=int, help="磁盘总扇区数；读两段 blob 时必填")
    parser.add_argument("--entries", type=int, help="GPT 表项数；纯 sfdisk dump 时必填")
    parser.add_argument("--esp-mib", type=int, default=512)
    sizes = parser.add_mutually_exclusive_group()
    sizes.add_argument("--root-mib", type=int, help="Linux root 容量；默认将扣除 ESP 后的 userdata 对半分")
    sizes.add_argument("--userdata-mib", type=int,
                       help="Android userdata 容量；Linux root 取扣除 ESP 后的剩余空间")
    sizes.add_argument("--equal-split", action="store_true", help="扣除 ESP 后的 userdata 对半分（默认）")
    parser.add_argument("--inspect-only", action="store_true")
    parser.add_argument("--output", help="把计划写入这个 JSON")
    parser.add_argument("--plan", help="复核过的计划 JSON；先核对现场表再按它写 blob")
    parser.add_argument("--emit-gpt", metavar="DIR",
                        help="写出未落盘的 gpt-head.bin、gpt-tail.bin、分区前的原表与 SHA256SUMS")
    args = parser.parse_args(argv)
    if bool(args.tail) != bool(args.head):
        parser.error("--head and --tail go together")

    try:
        if args.dump:
            disk, parts = parse_text(Path(args.dump).read_text(encoding="utf-8"), args.sectors, args.entries)
            gpt = None
        elif args.device:
            gpt = read_blobs([args.device], args.sectors * SECTOR if args.sectors else None)
            disk, parts = plan_rows(gpt)
        else:
            gpt = read_blobs([args.head, args.tail], args.sectors * SECTOR if args.sectors else None)
            disk, parts = plan_rows(gpt)
        if args.emit_gpt is not None and gpt is None:
            raise PlanError("--emit-gpt needs --device or --head/--tail")

        if args.plan:
            if gpt is None:
                raise PlanError("--plan needs --device or --head/--tail to check the live table")
            result = json.loads(Path(args.plan).read_text(encoding="utf-8"))
            compare_base(result, gpt)
        elif args.inspect_only:
            result = inspect(disk, parts)
        else:
            if not 64 <= args.esp_mib <= 2048:
                raise PlanError(f"esp size must be 64..2048 MiB, got {args.esp_mib}")
            result = build_plan(disk, parts, args.esp_mib, args.root_mib, args.userdata_mib)

        if args.emit_gpt is not None:
            if gpt is None:
                raise PlanError("--emit-gpt needs --device or --head/--tail")
            # 保护性 MBR 不在两段读数里：尾部读数只覆盖盘尾，头部读数虽从
            # LBA 0 开始，但规划只需要表本身。缺 MBR 时用规范里的那一种。
            with Path(args.device or args.head).open("rb") as stream:
                mbr = stream.read(SECTOR)
            if mbr[510:512] != b"\x55\xaa":
                mbr = bytearray(SECTOR)
                mbr[510:512] = b"\x55\xaa"
                mbr[446:462] = struct.pack("<BBBBBBBBII", 0, 0, 2, 0, 0xEE, 0xFF, 0xFF, 0xFF,
                                           1, 0xFFFFFFFF)
                mbr = bytes(mbr)
            result["gpt_blobs"] = emit(result, gpt, mbr, args.emit_gpt)

        if args.output:
            Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except PlanError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
