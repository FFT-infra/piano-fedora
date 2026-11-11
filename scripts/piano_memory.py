#!/usr/bin/env python3
"""安装与更新共用的内存描述契约。

payload DTB 的 /memory 是构建时冻结的，bank 表属于当次启动的 bootloader。
锁定的 SunUEFI 通过 EFI_DT_FIXUP_PROTOCOL 在交接前用本次启动的映射替换
/memory、ddr_size 与 /ddr-regions，再用同一份冷启动布局核对容量、区间与
固定保留区，不符就拒绝启动。raw 与 UKI 两条路径都经过这个协议。

安装侧因此只记录设备自己的实时 FDT，供计划与审计对照；payload 地图在启动
时会被换掉，不再要求它逐段落在设备地图内。
"""

import hashlib

from piano_sparse import require


def _merge(spans):
    result = []
    for start, end in sorted(spans):
        require(0 <= start < end <= 2**64, "invalid memory interval")
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(end, result[-1][1]))
        else:
            result.append((start, end))
    return result


def memory_contract(factory_dtb, read_fdt):
    """记录设备本次启动的 RAM 区间与原厂固定保留区。

    这不能证明实机 DDR 所有权或完整容量已可用；容量、区间与保留区的实际
    核对由固件在每次交接时完成。
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

    banks, reserved = ranges(read_fdt(factory_dtb))
    require(banks, "device DTB has no current RAM banks")
    return {"status": "DEVICE_MEMORY_MAP_RECORDED_AND_VALIDATED_AT_BOOT",
            "factory_dtb_sha256": hashlib.sha256(factory_dtb).hexdigest(),
            "factory_ram_bytes": sum(end - start for start, end in banks),
            "fixed_reservations": len(reserved),
            "runtime_memory_source": "inherited-fdt",
            "payload_map_is_fallback": True,
            "full_device_ram_available": False,
            "physical_memory_ownership_verified": False}
