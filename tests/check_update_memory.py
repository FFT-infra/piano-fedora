#!/usr/bin/env python3
"""用真实上游 FDT 编解码器验证安装预检记录的设备内存地图。

payload DTB 的 /memory 会在启动时被固件换成设备自己的映射，所以安装侧不再
比对两份地图，只解析设备当前的 FDT 供计划与审计使用。
"""

import importlib.util
import os
from pathlib import Path
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
if "PIANO_TEST_SUNUEFI" in os.environ:
    SUNUEFI = Path(os.environ["PIANO_TEST_SUNUEFI"])
    assert (SUNUEFI / "tools/compose_piano_dtb.py").is_file(), \
        "explicit upstream test source is missing"
else:
    SUNUEFI = next((path for path in (ROOT / "build/test-sources/sunuefi",
                                      ROOT / "build/test-sunuefi")
                    if (path / "tools/compose_piano_dtb.py").is_file()), None)
    if SUNUEFI is None:
        print("SKIP: memory contract needs the SunUEFI FDT codec (PIANO_TEST_SUNUEFI)")
        sys.exit(0)

sys.path.insert(0, str(SUNUEFI / "tools"))
from compose_piano_dtb import read_fdt, write_fdt

spec = importlib.util.spec_from_file_location("piano_memory", ROOT / "scripts/piano_memory.py")
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


def dtb(banks, fixed=(), memreserve=()):
    widths = {"#address-cells": struct.pack(">I", 2), "#size-cells": struct.pack(">I", 2)}
    tree = {"/": dict(widths), "/memory@80000000": {"device_type": b"memory\0",
            "reg": b"".join(struct.pack(">QQ", start, size) for start, size in banks)},
            "/reserved-memory": dict(widths)}
    for index, (start, size) in enumerate(fixed):
        tree[f"/reserved-memory/region{index}"] = {"reg": struct.pack(">QQ", start, size), "no-map": b""}
    return write_fdt({"tree": tree, "phandles": {}, "reservations": list(memreserve), "boot_cpu": 0})


def refused(factory):
    try:
        memory.memory_contract(factory, read_fdt)
    except ValueError:
        return
    raise AssertionError("unsafe device memory description was accepted")


def main():
    factory = dtb([(0x80000000, 0x10000000), (0xA0000000, 0x4000000)],
                  [(0x82000000, 0x100000), (0x8F000000, 0x100000)],
                  memreserve=[(0x83000000, 0x100000)])
    proof = memory.memory_contract(factory, read_fdt)
    assert proof["factory_ram_bytes"] == 0x14000000
    assert proof["fixed_reservations"] == 3
    assert proof["runtime_memory_source"] == "inherited-fdt"
    assert proof["payload_map_is_fallback"] is True
    assert not proof["physical_memory_ownership_verified"] and not proof["full_device_ram_available"]
    # 相邻区间合并成一段，表示方式不同不影响记录。
    adjacent = dtb([(0x80000000, 0x8000000)], [(0x82000000, 0x80000), (0x82080000, 0x80000)])
    assert memory.memory_contract(adjacent, read_fdt)["fixed_reservations"] == 1
    refused(dtb([]))
    refused(factory[:30])
    refused(b"\0" * 64)


if __name__ == "__main__":
    main()
    print("OK: device memory map is recorded, merged and refuses malformed FDTs")
