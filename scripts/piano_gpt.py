#!/usr/bin/env python3
"""GPT 整表编解码：把分区表拆成头部与尾部两段可 dd 的 blob。

设备上没有 `sgdisk`（Fedora 只装 util-linux），Android 侧也不保证有。整表
写入因此只用 `dd`：先把完整的新表算成两段，再按"先尾后头"的顺序落盘。

两段覆盖的范围由表头自己决定，不写死：

- 头部：`MBR + 主表头 + 主分区表`，从 LBA 0 开始。
- 尾部：`备份分区表 + 备份表头`，从备份表头里记录的 `table_lba` 开始，
  连续到磁盘最后一个 LBA。

先尾后头是关键：任何一次中断都能由旧表头的 `alternate_lba` 找回头部旧表，
或者由新头部找回尾部新表；反过来会留下"头新尾旧"的半写状态。
"""

import struct
import uuid
import zlib

LINUX_TYPE = uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4")
ESP_TYPE = uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b")

BLOCK = 4096
ENTRY = 128
HEADER_SIZE = 92


class GptError(ValueError):
    """输入不是一份完整、自洽的 GPT。"""


def _header(raw, here, last):
    if len(raw) != BLOCK or raw[:8] != b"EFI PART":
        raise GptError("expected a complete 4 KiB GPT header")
    revision, size, crc, reserved = struct.unpack_from("<IIII", raw, 8)
    current, alternate, first, final = struct.unpack_from("<QQQQ", raw, 24)
    table, count, width, array_crc = struct.unpack_from("<QIII", raw, 72)
    if revision != 0x10000 or not HEADER_SIZE <= size <= BLOCK or reserved:
        raise GptError("GPT revision or header size is invalid")
    clean = bytearray(raw[:size])
    struct.pack_into("<I", clean, 16, 0)
    if zlib.crc32(clean) != crc:
        raise GptError("GPT header CRC mismatch")
    if current != here or alternate != (last if here == 1 else 1):
        raise GptError("GPT header locations differ")
    if not 2 <= first <= final < last or not 1 <= count <= 4096 or width != ENTRY:
        raise GptError("unsupported GPT usable area or entry width")
    blocks = (count * width + BLOCK - 1) // BLOCK
    if not ((here == 1 and 2 <= table and table + blocks <= first)
            or (here == last and final < table and table + blocks <= last)):
        raise GptError("GPT entry array overlaps the usable area")
    return {"here": here, "first": first, "last": final, "table_lba": table,
            "count": count, "width": width, "array_crc": array_crc,
            "blocks": blocks, "size": size, "raw": bytes(raw)}


def table_bytes(count):
    """一份 entry array 占的字节数。"""
    return (count * ENTRY + BLOCK - 1) // BLOCK * BLOCK


def head_bytes(count):
    """头部 blob 的字节数：MBR + 主表头 + 主分区表。"""
    return 2 * BLOCK + table_bytes(count)


def tail_bytes(count):
    """尾部 blob 的字节数：备份分区表 + 备份表头。"""
    return table_bytes(count) + BLOCK


def parse(head, tail, disk_bytes):
    """从两段原始字节解析完整 GPT。

    `head` 从 LBA 0 开始，`tail` 从备份分区表的第一个 LBA 开始。函数只用
    blob 自身的内容推断长度，因此同一份代码既用于设备读数，也用于文件。
    """
    if type(disk_bytes) is not int or disk_bytes % BLOCK or disk_bytes < 8 * BLOCK:
        raise GptError("disk size must be a multiple of 4 KiB and hold both tables")
    last = disk_bytes // BLOCK - 1
    if len(head) < 2 * BLOCK:
        raise GptError("truncated GPT head blob")
    front = _header(head[BLOCK:2 * BLOCK], 1, last)
    size = table_bytes(front["count"])
    if len(head) != BLOCK + BLOCK + size:
        raise GptError("head blob length differs from its own entry count")
    if len(tail) != size + BLOCK:
        raise GptError("tail blob length differs from its own entry count")
    primary, backup = head[BLOCK:2 * BLOCK], tail[size:]
    rear = _header(backup, last, last)
    for key in ("first", "last", "count", "width", "array_crc"):
        if front[key] != rear[key]:
            raise GptError("primary and backup GPT metadata differ: " + key)
    if head[2 * BLOCK:] != tail[:size]:
        raise GptError("primary and backup entry arrays differ")
    table = head[2 * BLOCK:]
    used = table[:front["count"] * ENTRY]
    if zlib.crc32(used) != front["array_crc"]:
        raise GptError("GPT entry array CRC mismatch")
    if struct.unpack_from("<16s", primary, 56)[0] != struct.unpack_from("<16s", backup, 56)[0]:
        raise GptError("disk GUIDs differ between the two headers")
    rows, numbers = [], set()
    for index in range(front["count"]):
        raw = used[index * ENTRY:(index + 1) * ENTRY]
        if not any(raw[:16]):
            continue
        first, end, attributes = struct.unpack_from("<QQQ", raw, 32)
        name = raw[56:].decode("utf-16-le").rstrip("\0")
        if "\0" in name or not front["first"] <= first <= end <= front["last"]:
            raise GptError("invalid GPT partition name or range")
        number = index + 1
        if number in numbers:
            raise GptError("duplicate GPT partition number")
        numbers.add(number)
        rows.append({
            "index": index, "number": number, "first": first, "last": end,
            "bytes": (end - first + 1) * BLOCK, "attributes": attributes, "name": name,
            "type": str(uuid.UUID(bytes_le=bytes(raw[:16]))),
            "guid": str(uuid.UUID(bytes_le=bytes(raw[16:32]))),
            "raw": bytes(raw),
        })
    return {"disk_bytes": disk_bytes, "sectors": disk_bytes // BLOCK,
            "primary": primary, "backup": backup, "table": table,
            "primary_entries": table, "backup_entries": table,
            "geometry": {"count": front["count"], "first": front["first"],
                         "last": front["last"], "table_lba": front["table_lba"],
                         "backup_table_lba": rear["table_lba"],
                         "table_blocks": front["blocks"],
                         "disk_guid": str(uuid.UUID(bytes_le=bytes(primary[56:72])))},
            "partitions": rows}


def rebuild_header(raw, table, count):
    """按新的 entry array 重算一份表头（与上游 `_new_header` 同一算法）。"""
    data = bytearray(raw)
    struct.pack_into("<I", data, 88, zlib.crc32(table[:count * ENTRY]))
    struct.pack_into("<I", data, 16, 0)
    size = struct.unpack_from("<I", data, 12)[0]
    struct.pack_into("<I", data, 16, zlib.crc32(bytes(data[:size])))
    return bytes(data)


def blobs(count, primary, backup, table, mbr):
    """把一份完整的新 entry array 编成 (head, tail) 两段可 dd 的字节。

    `table` 必须已经是 `table_bytes(count)` 字节。两个表头的 CRC 在这里
    重算，调用方不需要自己碰 CRC 字段。
    """
    size = table_bytes(count)
    if len(table) != size:
        raise GptError("entry array length differs from the GPT geometry")
    if len(primary) != BLOCK or len(backup) != BLOCK:
        raise GptError("both GPT headers must be exactly one 4 KiB block")
    if len(mbr) != BLOCK or mbr[510:512] != b"\x55\xaa":
        raise GptError("protective MBR missing")
    return mbr + rebuild_header(primary, table, count) + table, table + rebuild_header(backup, table, count)


def entry(index, kind, identity, first, last, attributes, name):
    """按 GPT 规范拼一条 128 字节的 entry。"""
    if not (isinstance(identity, uuid.UUID) and isinstance(kind, uuid.UUID)):
        raise GptError("partition and type identities must be UUID objects")
    if not 0 <= first <= last or attributes < 0:
        raise GptError("invalid partition range or attributes")
    text = name.encode("utf-16-le")
    if not text or len(text) > 72:
        raise GptError("partition name must be 1..36 UTF-16 code units")
    raw = bytearray(ENTRY)
    raw[:16], raw[16:32] = kind.bytes_le, identity.bytes_le
    struct.pack_into("<QQQ", raw, 32, first, last, attributes)
    raw[56:56 + len(text)] = text
    return bytes(raw)


def probe(head, sectors):
    """从 LBA 0 起的一段读数里取出主表头，算出完整两段 blob 各要多少字节。

    设备上读表时表长要由表头自己决定，所以先读一小段探一次，再按结果读准。
    """
    if len(head) < 2 * BLOCK:
        raise GptError("GPT dump must start at LBA 0 and hold both MBR and primary header")
    return head_bytes(_header(head[BLOCK:2 * BLOCK], 1, sectors - 1)["count"])


def load(head, tail, disk_bytes):
    """解析一次设备读数：`head` 从 LBA 0 开始，`tail` 以磁盘最后一个 LBA 结束。

    两段都可以比精确 blob 长，多出来的部分被切掉。设备上的 `dd` 只能按固定
    扇区数读，先探长度再读两遍没有必要。
    """
    if type(disk_bytes) is not int or disk_bytes < 8 * BLOCK or disk_bytes % BLOCK:
        raise GptError("disk size must be a multiple of 4 KiB and hold both tables")
    head_size = probe(head, disk_bytes // BLOCK)
    if len(head) < head_size:
        raise GptError("GPT head dump is shorter than the entry table it declares")
    tail_size = head_size - BLOCK
    if len(tail) < tail_size:
        raise GptError("GPT tail dump is shorter than the entry table it declares")
    return parse(head[:head_size], tail[len(tail) - tail_size:], disk_bytes)


def partition_guid(disk_guid, name, first, last):
    """新分区的唯一 GUID 由磁盘 GUID 与分区边界决定，重算得到同一个值。"""
    return uuid.uuid5(uuid.UUID(disk_guid), name + ":" + str(first) + ":" + str(last))


def adapt(parsed):
    """接受 `parse()` 的结果或锁定上游的 `Gpt`，统一成 `rewrite()` 要的字段。"""
    if isinstance(parsed, dict):
        return parsed
    entries = parsed.primary_entries
    return {"geometry": parsed.geometry, "primary": parsed.primary, "backup": parsed.backup,
            "primary_entries": entries, "backup_entries": parsed.backup_entries,
            "partitions": [dict(row, raw=entries[row["index"] * ENTRY:(row["index"] + 1) * ENTRY])
                           for row in parsed.partitions]}


def rewrite(parsed, plan, mbr):
    """按计划改写 entry array，返回 (head, tail, original_head, original_tail)。

    只动 `userdata` 的尾端与两个全新的空 entry。所有既有分区（含固定 Android
    分区）的 128 字节 entry 必须原样保留，因此这份函数既能写新表，也能把
    分区前读到的原表编回同样两段，写入失败时按相反顺序写回。
    """
    parsed = adapt(parsed)
    count = parsed["geometry"]["count"]
    table = bytearray(parsed["primary_entries"])
    changes = {row["name"]: row for row in plan["changes"]}
    userdata = next((row for row in parsed["partitions"] if row["name"] == "userdata"), None)
    if userdata is None:
        raise GptError("parsed GPT has no userdata partition")
    struct.pack_into("<Q", table, userdata["index"] * ENTRY + 40, changes["userdata"]["new_last"])
    identities = {row["guid"] for row in parsed["partitions"]}
    for name, kind in (("sunuefi_root", LINUX_TYPE), ("sunuefi_esp", ESP_TYPE)):
        row = changes[name]
        offset = (row["partition"] - 1) * ENTRY
        if any(table[offset:offset + ENTRY]):
            raise GptError("new GPT entry is not completely empty: " + name)
        identity = partition_guid(parsed["geometry"]["disk_guid"], name, row["first"], row["last"])
        if str(identity) in identities:
            raise GptError("new partition GUID collides: " + name)
        identities.add(str(identity))
        table[offset:offset + ENTRY] = entry(row["partition"] - 1, kind, identity,
                                            row["first"], row["last"], 0, name)
    table = bytes(table)
    for row in parsed["partitions"]:
        if row["name"] != "userdata":
            offset = row["index"] * ENTRY
            if table[offset:offset + ENTRY] != row["raw"]:
                raise GptError("fixed partition entry changed: " + row["name"])
    head, tail = blobs(count, parsed["primary"], parsed["backup"], table, mbr)
    original_head, original_tail = blobs(
        count, parsed["primary"], parsed["backup"], parsed["primary_entries"], mbr)
    return head, tail, original_head, original_tail

