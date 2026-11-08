#!/usr/bin/env python3
"""安装与更新共用的内存描述契约。

payload DTB 的 /memory 是构建时冻结的，bank 表属于当次启动的 bootloader。
带 uefi-live-memory 补丁的 UEFI 在交接前用继承 FDT 的实时表替换它，此时
payload 地图只是兜底；没有这个补丁的交付件仍然要求 payload 地图落在原厂
地图内。
"""

import hashlib
import struct

from piano_sparse import require

LIVE_MEMORY_SERIES = "patches/uefi-live-memory/series.json"


def boot_dtb(bundle):
    """取出封存 Linux BOOT 容器尾部携带的 board DTB。"""
    data = (bundle / "boot_sunuefi.img").read_bytes()
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


def live_memory_attestation(record):
    """从封存的 UEFI 源清单取实时内存补丁身份；整份清单已由 verify_uefi_source 核对。"""
    identities = (record or {}).get("project_source_sha256") or {}
    identity = identities.get(LIVE_MEMORY_SERIES)
    patches = sorted(name for name in identities
                     if name.startswith("patches/uefi-live-memory/") and name.endswith(".patch"))
    if not identity or not patches:
        return None
    return {"series": LIVE_MEMORY_SERIES, "identity": identity, "patches": patches}


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


def memory_contract(linux_dtb, factory_dtb, read_fdt, live_memory=None):
    """核对 Linux 实际会用的 RAM 描述与原厂固定保留区。

    live_memory 非空时，这次启动的 UEFI 会先换上原厂实时地图，payload 地图
    只作兜底，因此按原厂地图核对保留区；否则要求 payload 地图逐段落在原厂
    地图内。两种情况都不能证明实机 DDR 所有权或完整容量已可用。
    """
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

    if live_memory is not None:
        require(isinstance(live_memory, dict) and live_memory.get("series") == LIVE_MEMORY_SERIES
                and isinstance(live_memory.get("identity"), str)
                and len(live_memory["identity"]) == 64 and all(c in "0123456789abcdef" for c in live_memory["identity"])
                and live_memory.get("patches"),
                "live memory attestation is malformed")

    linux_banks, linux_reserved = ranges(read_fdt(linux_dtb))
    factory_banks, factory_reserved = ranges(read_fdt(factory_dtb))
    require(linux_banks and factory_banks, "DTB has no current RAM banks")
    if live_memory is None:
        for start, end in linux_banks:
            require(_covered(start, end, factory_banks), "Linux DTB advertises RAM outside this device's Android memory map")
        effective = linux_banks
    else:
        effective = factory_banks
    for first, last in factory_reserved:
        for left, right in effective:
            start, end = max(first, left), min(last, right)
            if start < end:
                require(_covered(start, end, linux_reserved), "Linux DTB omits a current Android fixed reservation")
    return {"status": "LIVE_MEMORY_ADOPTION_AND_FIXED_RESERVATIONS_CHECKED" if live_memory else
                      "DESCRIBED_MEMORY_SUBSET_AND_FIXED_RESERVATIONS_CHECKED",
            "linux_dtb_sha256": hashlib.sha256(linux_dtb).hexdigest(),
            "factory_dtb_sha256": hashlib.sha256(factory_dtb).hexdigest(),
            "linux_ram_bytes": sum(end - start for start, end in linux_banks),
            "factory_ram_bytes": sum(end - start for start, end in factory_banks),
            "effective_ram_bytes": sum(end - start for start, end in effective),
            "runtime_memory_source": "inherited-fdt" if live_memory else "payload-dtb",
            "payload_map_is_fallback": live_memory is not None,
            "live_memory": live_memory,
            "full_device_ram_available": False, "physical_memory_ownership_verified": False}
