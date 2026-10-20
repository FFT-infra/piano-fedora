#!/usr/bin/env python3
"""验证首次 GPT 改写仅改变 userdata 尾端及两个空槽，不连接任何设备。"""

import argparse
import importlib.util
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import uuid
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sunuefi", type=Path)
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location("installer_test", ROOT / "scripts/install-f2fs.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    count, sectors = 64, 249518080
    table = bytearray(count * 128)
    guid = uuid.UUID("d55fa102-1ed9-4154-b3a7-3e84cd916cb5")
    rows = []
    for index in range(34):
        name = "userdata" if index == 33 else ("super" if index == 32 else "fixed_" + str(index))
        first, last = ((3632128, sectors - 6) if index == 33 else
                       (256, 3632127) if index == 32 else (6 + index * 2, 7 + index * 2))
        entry = memoryview(table)[index * 128:(index + 1) * 128]
        kind = uuid.UUID("ebd0a0a2-b9e5-4433-87c0-68b6b72699c7")
        identity = uuid.uuid5(guid, name)
        entry[:16], entry[16:32] = kind.bytes_le, identity.bytes_le
        struct.pack_into("<QQQ", entry, 32, first, last, 4)
        text = name.encode("utf-16-le")
        entry[56:56 + len(text)] = text
        rows.append({"index": index, "name": name, "first": first, "last": last,
                     "bytes": (last - first + 1) * 4096, "guid": str(identity), "type": str(kind), "attributes": 4})
    table = bytes(table)

    def header(here, alternate, table_at):
        result = bytearray(4096)
        struct.pack_into("<8sIIIIQQQQ16sQIII", result, 0, b"EFI PART", 0x10000, 92, 0, 0,
                         here, alternate, 6, sectors - 6, guid.bytes_le, table_at, count, 128, zlib.crc32(table))
        struct.pack_into("<I", result, 16, zlib.crc32(result[:92]))
        return bytes(result)

    def new_header(original, crc):
        data = bytearray(original)
        struct.pack_into("<I", data, 88, crc)
        struct.pack_into("<I", data, 16, 0)
        struct.pack_into("<I", data, 16, zlib.crc32(data[:92]))
        return bytes(data)

    primary, backup = header(1, sectors - 1, 2), header(sectors - 1, 1, sectors - 3)
    gpt = SimpleNamespace(disk_bytes=sectors * 4096, primary=primary, backup=backup,
                          primary_entries=table, backup_entries=table, partitions=tuple(rows), backup_table_lba=sectors - 3,
                          geometry={"count": 64, "first": 6, "last": sectors - 6, "disk_guid": str(guid), "table_lba": 2})
    if args.sunuefi:
        upstream = installer.load_source(args.sunuefi)
        gpt = upstream.parse_gpt(primary, table, backup, table, sectors * 4096)
    else:
        upstream = SimpleNamespace(_new_header=new_header, parse_gpt=lambda *unused: gpt,
            LINUX_TYPE=uuid.UUID("0fc63daf-8483-4772-8e79-3d69d8477de4").bytes_le,
            ESP_TYPE=uuid.UUID("c12a7328-f81f-11d2-ba4b-00a0c93ec93b").bytes_le)
    installer.require_install_slot("_a", existing=False)
    installer.require_install_slot("_b", existing=True)
    try:
        installer.require_install_slot("_b", existing=False)
    except ValueError as error:
        assert "first install requires Android Slot A" in str(error), error
    else:
        raise AssertionError("first install accepted Android Slot B")
    mbr = bytes(510) + b"\x55\xaa" + bytes(3584)
    plan = installer.partition_plan(gpt)
    expected, head, tail, original_head, original_tail = installer.new_gpt(upstream, gpt, plan, mbr)
    # 头部 = MBR + 主表头 + 主分区表；尾部 = 备份分区表 + 备份表头。
    assert len(head) == 2 * 4096 + len(table) and len(tail) == len(table) + 4096
    assert head[:4096] == mbr
    new_table = head[8192:]
    assert tail[:len(table)] == new_table
    # 原表也要能原样编回来，写入失败才有东西可以写回。
    assert original_head[:4096] == mbr and original_head[8192:] == table
    assert original_tail == table + gpt.backup
    assert new_table[:33 * 128] == table[:33 * 128], "fixed Android partitions changed"
    assert new_table[33 * 128:33 * 128 + 40] == table[33 * 128:33 * 128 + 40]
    assert new_table[33 * 128 + 48:34 * 128] == table[33 * 128 + 48:34 * 128]
    assert struct.unpack_from("<Q", new_table, 33 * 128 + 40)[0] == 126509311
    for index, expected_name in ((34, "sunuefi_root"), (35, "sunuefi_esp")):
        assert new_table[index * 128 + 56:(index + 1) * 128].decode("utf-16-le").rstrip("\0") == expected_name
    assert new_table[36 * 128:] == table[36 * 128:], "unselected GPT slots changed"
    assert struct.unpack_from("<I", head, 4096 + 88)[0] == zlib.crc32(new_table)
    root_first = plan["changes"][1]["first"]
    # 两段 blob 自己就能解析回同一份表：把设备上的 dd 写作按字节还原。
    # 走真实 parser 时 `expected` 是新表；走字节 oracle 时上游被替换成原表，
    # 所以这里只核对两段 blob 解析出的边界与名字。
    reparsed = installer.gpt_entry.parse(head, tail, gpt.disk_bytes)
    rows_by_name = {row["name"]: row for row in reparsed["partitions"]}
    assert rows_by_name["userdata"]["last"] == root_first - 1
    assert rows_by_name["sunuefi_root"]["first"] == root_first
    assert rows_by_name["sunuefi_esp"]["last"] == gpt.geometry["last"]
    assert reparsed["geometry"]["count"] == 64 and reparsed["geometry"]["last"] == sectors - 6
    try:
        installer.gpt_entry.parse(head, tail[:-4096] + bytes(4096), gpt.disk_bytes)
    except ValueError:
        pass
    else:
        raise AssertionError("a corrupted backup header was accepted")
    modified = dict(plan)
    modified["capacity"] = {**plan["capacity"], "linux_root_mib": 1}
    try:
        installer.new_gpt(upstream, gpt, modified, mbr)
    except ValueError:
        pass
    else:
        raise AssertionError("modified plan was accepted")

    # 镜像比分区小是正常情形：差额留给首启扩容。
    small = installer.root_capacity(8192 * 1024**2, 684488392704)
    assert small["grow_at_first_boot"] is True and small["grow_bytes"] == 684488392704 - 8192 * 1024**2
    assert installer.root_capacity(4096 * 1024**2, 4096 * 1024**2)["grow_at_first_boot"] is False
    for image_bytes, partition_bytes, message in (
            (4096 * 1024**2, 2048 * 1024**2, "image larger than the partition"),
            (32 * 1024**2, 4096 * 1024**2, "image below the F2FS minimum")):
        try:
            installer.root_capacity(image_bytes, partition_bytes)
        except ValueError:
            pass
        else:
            raise AssertionError(message + " was accepted")

    # bundle 自带的 sparse 必须解码回封存镜像；root-install 缺席时刷的就是它。
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        raw = work / "pianoroot.raw"
        raw.write_bytes(b"piano".ljust(64 * 1024**2, b"\0"))
        bundle = work / "bundle"
        bundle.mkdir()
        installer.piano_sparse.encode(raw, bundle / "pianoroot.f2fs.sparse.img")
        image = {"bytes": raw.stat().st_size, "sha256": installer.sha256(raw)}
        generic = {"files": {"pianoroot.f2fs.sparse.img": {"bytes": (bundle / "pianoroot.f2fs.sparse.img").stat().st_size}}}
        transport, meta = installer.root_transport(bundle, bundle, generic, {}, image)
        assert transport == bundle / "pianoroot.f2fs.sparse.img", transport
        assert (meta["expanded_bytes"], meta["expanded_sha256"]) == (image["bytes"], image["sha256"]), meta
        assert meta["dont_care_chunks"] == 0 and meta["zero_policy"] == "explicit-zero-fill", meta
        # 换成另一份 sparse 就必须拒绝，不能只看文件名。
        other = work / "other.raw"
        other.write_bytes(b"other".ljust(64 * 1024**2, b"\0"))
        bundle.joinpath("pianoroot.f2fs.sparse.img").unlink()
        installer.piano_sparse.encode(other, bundle / "pianoroot.f2fs.sparse.img")
        try:
            installer.root_transport(bundle, bundle, generic, {}, image)
        except ValueError:
            pass
        else:
            raise AssertionError("a bundled sparse that decodes to other bytes was accepted")

    explicit = installer.partition_plan(gpt, 307200)
    assert explicit["allocation"] == "explicit-android-size"
    assert explicit["capacity"]["android_userdata_mib"] == 307200, explicit["capacity"]
    assert explicit != plan, "explicit Android size must change the layout"
    _, changed_explicit, _, _, _ = installer.new_gpt(upstream, gpt, explicit, mbr, 307200)
    explicit_table = changed_explicit[8192:]
    assert struct.unpack_from("<Q", explicit_table, 33 * 128 + 40)[0] == 82275327
    assert explicit["changes"][0]["new_last"] == 82275327
    try:
        installer.new_gpt(upstream, gpt, explicit, mbr)
    except ValueError:
        pass
    else:
        raise AssertionError("apply accepted a different Android size than the plan")

    wide = installer.partition_plan(gpt, 307200, 1024)
    esp_row = wide["changes"][-1]
    assert esp_row["name"] == "sunuefi_esp" and esp_row["size_mib"] == 1024, esp_row
    assert esp_row["last"] == 249518074, "a wider ESP must still end at the disk tail"
    assert wide["capacity"]["linux_root_mib"] == explicit["capacity"]["linux_root_mib"] - 512
    _, changed_wide, _, _, _ = installer.new_gpt(upstream, gpt, wide, mbr, 307200, 1024)
    assert struct.unpack_from("<Q", changed_wide, 8192 + 35 * 128 + 32)[0] == 249255680
    try:
        installer.new_gpt(upstream, gpt, wide, mbr, 307200)
    except ValueError:
        pass
    else:
        raise AssertionError("apply ignored the plan's ESP size")
    print("OK: first GPT byte preservation, CRC, half capacity, Slot A contract, deterministic new entries, "
          "explicit Android capacity, explicit ESP capacity and stale-plan rejection" +
          ("; actual pinned parser exercised" if args.sunuefi else "; byte oracle exercised"))


if __name__ == "__main__":
    main()
