#!/usr/bin/env python3
"""用真实上游 FDT 编解码器验证安装预检的内存区间、实时内存采纳和容器边界。"""

import importlib.util
import os
from pathlib import Path
import struct
import sys
import tempfile

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

SERIES = "patches/uefi-live-memory/series.json"
PATCH = "patches/uefi-live-memory/0001-adopt-live-memory-map.patch"
RECORD = {"project_source_sha256": {SERIES: "a" * 64, PATCH: "b" * 64}}


def dtb(banks, fixed=(), memreserve=()):
    widths = {"#address-cells": struct.pack(">I", 2), "#size-cells": struct.pack(">I", 2)}
    tree = {"/": dict(widths), "/memory@80000000": {"device_type": b"memory\0",
            "reg": b"".join(struct.pack(">QQ", start, size) for start, size in banks)},
            "/reserved-memory": dict(widths)}
    for index, (start, size) in enumerate(fixed):
        tree[f"/reserved-memory/region{index}"] = {"reg": struct.pack(">QQ", start, size), "no-map": b""}
    return write_fdt({"tree": tree, "phandles": {}, "reservations": list(memreserve), "boot_cpu": 0})


def refused(linux, factory, live=None):
    try:
        memory.memory_contract(linux, factory, read_fdt, live)
    except ValueError:
        return
    raise AssertionError("unsafe memory description was accepted")


def main():
    linux = dtb([(0x80000000, 0x8000000), (0xA0000000, 0)], [(0x82000000, 0x100000)])
    factory = dtb([(0x80000000, 0x10000000), (0xA0000000, 0x4000000)], [(0x82000000, 0x100000)])
    proof = memory.memory_contract(linux, factory, read_fdt)
    assert proof["linux_ram_bytes"] == 0x8000000 and proof["factory_ram_bytes"] == 0x14000000
    assert proof["runtime_memory_source"] == "payload-dtb" and proof["payload_map_is_fallback"] is False
    assert not proof["physical_memory_ownership_verified"] and not proof["full_device_ram_available"]
    # 相邻受保护区可以共同覆盖，不能因为表示方式不同而误报。
    adjacent = dtb([(0x80000000, 0x8000000)], [(0x82000000, 0x80000), (0x82080000, 0x80000)])
    memory.memory_contract(adjacent, factory, read_fdt)
    # 额外 Android 保留区位于 Linux RAM 之外时，不授权或扩展该 RAM。
    memory.memory_contract(linux, dtb([(0x80000000, 0x10000000)],
                           [(0x82000000, 0x100000), (0x8F000000, 0x100000)]), read_fdt)
    refused(dtb([(0x7FFFF000, 0x8001000)]), factory)
    refused(linux, dtb([(0x80000000, 0x10000000)], [(0x83000000, 0x100000)]))
    refused(linux, dtb([(0x80000000, 0x10000000)], memreserve=[(0x83000000, 0x100000)]))
    memory.memory_contract(linux, dtb([(0x80000000, 0x10000000)], memreserve=[(0x82000000, 0x100000)]), read_fdt)
    refused(linux, factory[:30])
    # 实时内存采纳：payload 地图可以落在原厂地图之外，生效地图按原厂那份算。
    live = memory.live_memory_attestation(RECORD)
    assert live["series"] == SERIES and live["patches"] == [PATCH]
    assert memory.live_memory_attestation({}) is None
    assert memory.live_memory_attestation({"project_source_sha256": {SERIES: "a" * 64}}) is None
    adopted = memory.memory_contract(linux, factory, read_fdt, live)
    assert adopted["runtime_memory_source"] == "inherited-fdt" and adopted["payload_map_is_fallback"] is True
    assert adopted["effective_ram_bytes"] == 0x14000000 and adopted["linux_ram_bytes"] == 0x8000000
    assert adopted["live_memory"] == live
    # 采纳以后 payload 地图不再参与判断，原厂固定保留区仍必须由 DTB 声明。
    memory.memory_contract(dtb([(0x80000000, 0x2000000)], [(0x82000000, 0x100000)]), factory, read_fdt, live)
    refused(linux, dtb([(0x80000000, 0x10000000)],
                       [(0x82000000, 0x100000), (0x83000000, 0x100000)]), live)
    refused(linux, factory, {"series": "patches/other/series.json", "identity": "a" * 64, "patches": [PATCH]})
    refused(linux, factory, {"series": SERIES, "identity": "not-a-digest", "patches": [PATCH]})
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        image = bytearray(8192) + linux
        image[:8] = b"ANDROID!"
        for offset, value in ((8, 64), (16, 0), (24, 0), (36, 4096), (40, 2), (1632, 0), (1648, len(linux))):
            struct.pack_into("<I", image, offset, value)
        path = folder / "boot_sunuefi.img"
        path.write_bytes(image)
        assert memory.boot_dtb(folder) == linux
        for offset, value in ((40, 4), (36, 2048), (1632, 4096), (1648, len(image))):
            bad = bytearray(image)
            struct.pack_into("<I", bad, offset, value)
            path.write_bytes(bad)
            try:
                memory.boot_dtb(folder)
            except ValueError:
                pass
            else:
                raise AssertionError("foreign/truncated Linux BOOT layout was accepted")
    print("OK: RAM subset, live-memory adoption, fixed/FDT reservations and Linux BOOT DTB bounds")


if __name__ == "__main__":
    main()
