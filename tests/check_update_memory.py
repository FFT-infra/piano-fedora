#!/usr/bin/env python3
"""用真实上游 FDT 编解码器验证更新预检的内存区间和容器边界。"""

import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
source = Path(os.environ.get("PIANO_TEST_SUNUEFI", ROOT.parent / "mipad8p-piano/sources/Project-SunUEFI"))
sys.path.insert(0, str(source / "tools"))
from compose_piano_dtb import read_fdt, write_fdt

spec = importlib.util.spec_from_file_location("update_memory", ROOT / "scripts/update-f2fs.py")
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


def dtb(banks, fixed=(), memreserve=()):
    widths = {"#address-cells": struct.pack(">I", 2), "#size-cells": struct.pack(">I", 2)}
    tree = {"/": dict(widths), "/memory@80000000": {"device_type": b"memory\0",
            "reg": b"".join(struct.pack(">QQ", start, size) for start, size in banks)},
            "/reserved-memory": dict(widths)}
    for index, (start, size) in enumerate(fixed):
        tree[f"/reserved-memory/region{index}"] = {"reg": struct.pack(">QQ", start, size), "no-map": b""}
    return write_fdt({"tree": tree, "phandles": {}, "reservations": list(memreserve), "boot_cpu": 0})


def refused(linux, factory):
    try:
        update.memory_contract(linux, factory, read_fdt)
    except ValueError:
        return
    raise AssertionError("unsafe memory description was accepted")


def main():
    linux = dtb([(0x80000000, 0x8000000), (0xA0000000, 0)], [(0x82000000, 0x100000)])
    factory = dtb([(0x80000000, 0x10000000), (0xA0000000, 0x4000000)], [(0x82000000, 0x100000)])
    proof = update.memory_contract(linux, factory, read_fdt)
    assert proof["linux_ram_bytes"] == 0x8000000 and proof["factory_ram_bytes"] == 0x14000000
    assert not proof["physical_memory_ownership_verified"] and not proof["full_device_ram_available"]
    # 相邻受保护区可以共同覆盖，不能因为表示方式不同而误报。
    adjacent = dtb([(0x80000000, 0x8000000)], [(0x82000000, 0x80000), (0x82080000, 0x80000)])
    update.memory_contract(adjacent, factory, read_fdt)
    # 额外 Android 保留区位于 Linux RAM 之外时，不授权或扩展该 RAM。
    update.memory_contract(linux, dtb([(0x80000000, 0x10000000)],
                           [(0x82000000, 0x100000), (0x8F000000, 0x100000)]), read_fdt)
    refused(dtb([(0x7FFFF000, 0x8001000)]), factory)
    refused(linux, dtb([(0x80000000, 0x10000000)], [(0x83000000, 0x100000)]))
    refused(linux, dtb([(0x80000000, 0x10000000)], memreserve=[(0x83000000, 0x100000)]))
    update.memory_contract(linux, dtb([(0x80000000, 0x10000000)], memreserve=[(0x82000000, 0x100000)]), read_fdt)
    refused(linux, factory[:30])
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        image = bytearray(8192) + linux
        image[:8] = b"ANDROID!"
        for offset, value in ((8, 64), (16, 0), (24, 0), (36, 4096), (40, 2), (1632, 0), (1648, len(linux))):
            struct.pack_into("<I", image, offset, value)
        path = folder / "boot.img"
        path.write_bytes(image)
        assert update._linux_dtb(folder) == linux
        for offset, value in ((40, 4), (36, 2048), (1632, 4096), (1648, len(image))):
            bad = bytearray(image)
            struct.pack_into("<I", bad, offset, value)
            path.write_bytes(bad)
            try:
                update._linux_dtb(folder)
            except ValueError:
                pass
            else:
                raise AssertionError("foreign/truncated Linux BOOT layout was accepted")
    print("OK: RAM subset, fixed/FDT reservations, adjacent coverage and Linux BOOT DTB bounds")


if __name__ == "__main__":
    main()
