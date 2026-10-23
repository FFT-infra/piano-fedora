"""Android boot v2 容器的打包、解包与校验。

SunUEFI 只读 ESP 上的 `/EFI/Piano/stable/boot.img`，`PianoRawLinuxBoot.c` 的
`Parse()` 只接受这一种形状：`ANDROID!` 魔数、header_version 2、pagesize 4096，
且 `second`/`recovery_dtbo`/`signature`/`trailing` 必须全为零。固件把文件自身
的 sha256 当作可信摘要（`PianoRawLinuxLoadStable` 里的 `CopyMem(AcceptedSha,
File.Sha256)`），所以设备侧重打包只需要产出结构相同的文件，不需要改固件。

本模块是那份结构的唯一实现。打包结果的字节必须与上游 `package_release.py`
调用的 `mkbootimg` 一致，否则 `verify_bundle` 与固件解析之间会出现第二套契约。
`tests/check_boot_container.py` 用设备上实测的 boot.img 做往返比对来钉住这一点。
"""

import hashlib
import struct
from pathlib import Path

MAGIC = b"ANDROID!"
PAGESIZE = 4096
HEADER_VERSION = 2
HEADER_BYTES = 1660

# mkbootimg 的默认装载地址。这些值进 header，固件不读它们（`PianoRawLinuxBoot.c`
# 只取 kernel 段里的 arm64 Image 头），但字节一致要求原样保留。
BASE = 0x10000000
KERNEL_OFFSET = 0x00008000
RAMDISK_OFFSET = 0x01000000
SECOND_OFFSET = 0x00F00000
TAGS_OFFSET = 0x00000100
DTB_OFFSET = 0x01F00000

NAME_BYTES = 16
ARGS_BYTES = 512
EXTRA_ARGS_BYTES = 1024

# arm64 Image 头在 kernel 段偏移 56 处的魔数。固件逐字比对这一个值。
ARM64_IMAGE_MAGIC = 0x644D5241


class BootContainerError(Exception):
    """输入或文件结构不满足固件的读取契约。"""


def align(value, page=PAGESIZE):
    return (value + page - 1) // page * page


def _read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise BootContainerError(f"missing or symlink input: {path}")
    return path.read_bytes()


def header_bytes(kernel_bytes, ramdisk_bytes, dtb_bytes):
    """按 mkbootimg 的顺序拼出 1660 字节的 v2 头。"""
    if not kernel_bytes:
        raise BootContainerError("kernel must not be empty")
    if not ramdisk_bytes:
        raise BootContainerError("ramdisk must not be empty")
    if not dtb_bytes:
        raise BootContainerError("dtb must not be empty")

    # mkbootimg 的 update_sha 对每段写「内容 + 该段长度的 u32」，空段只写一个零。
    # 顺序是 kernel、ramdisk、second、recovery_dtbo、dtb——dtb 在最后。
    digest = hashlib.sha1()
    for blob in (kernel_bytes, ramdisk_bytes):
        digest.update(blob)
        digest.update(struct.pack("<I", len(blob)))
    digest.update(struct.pack("<I", 0))   # second
    digest.update(struct.pack("<I", 0))   # recovery_dtbo
    digest.update(dtb_bytes)
    digest.update(struct.pack("<I", len(dtb_bytes)))

    header = bytearray()
    header += MAGIC
    header += struct.pack("<I", len(kernel_bytes))
    header += struct.pack("<I", BASE + KERNEL_OFFSET)
    header += struct.pack("<I", len(ramdisk_bytes))
    header += struct.pack("<I", BASE + RAMDISK_OFFSET)
    header += struct.pack("<I", 0)                       # second size
    header += struct.pack("<I", 0)                       # second load address
    header += struct.pack("<I", BASE + TAGS_OFFSET)
    header += struct.pack("<I", PAGESIZE)
    header += struct.pack("<I", HEADER_VERSION)
    header += struct.pack("<I", 0)                       # os_version / patch level
    header += struct.pack(f"<{NAME_BYTES}s", b"")        # board
    header += struct.pack(f"<{ARGS_BYTES}s", b"")        # cmdline
    header += struct.pack("<32s", digest.digest())       # sha1 id, zero-padded to 32
    header += struct.pack(f"<{EXTRA_ARGS_BYTES}s", b"")  # extra_cmdline
    header += struct.pack("<I", 0)                       # recovery_dtbo size
    header += struct.pack("<Q", 0)                       # recovery_dtbo offset
    header += struct.pack("<I", HEADER_BYTES)
    header += struct.pack("<I", len(dtb_bytes))
    header += struct.pack("<Q", BASE + DTB_OFFSET)

    if len(header) > PAGESIZE:
        raise BootContainerError("header does not fit one page")
    return bytes(header).ljust(PAGESIZE, b"\x00")


def pack(kernel, ramdisk, dtb, output):
    """把三段载荷写成 Android boot v2 容器，返回摘要记录。

    payload 各自向上补齐到页边界；文件在最后一个 payload 的页边界处结束，
    不追加尾部填充——固件的 `Parse()` 要求 `Trailing.Bytes` 为零。
    """
    kernel_bytes, ramdisk_bytes, dtb_bytes = _read(kernel), _read(ramdisk), _read(dtb)
    header = header_bytes(kernel_bytes, ramdisk_bytes, dtb_bytes)
    blob = bytearray(header)
    for data in (kernel_bytes, ramdisk_bytes, dtb_bytes):
        blob += data
        blob += b"\x00" * (align(len(blob)) - len(blob))
    output = Path(output)
    if output.exists() and not output.is_file():
        raise BootContainerError(f"output is not a regular file: {output}")
    output.write_bytes(bytes(blob))
    return {
        "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "pagesize": PAGESIZE,
        "header_version": HEADER_VERSION,
        "header_bytes": HEADER_BYTES,
        "payloads": {
            "kernel": {"bytes": len(kernel_bytes), "sha256": hashlib.sha256(kernel_bytes).hexdigest()},
            "ramdisk": {"bytes": len(ramdisk_bytes), "sha256": hashlib.sha256(ramdisk_bytes).hexdigest()},
            "dtb": {"bytes": len(dtb_bytes), "sha256": hashlib.sha256(dtb_bytes).hexdigest()},
        },
    }


def unpack(path):
    """按固件的读取契约解出三段载荷。

    校验的是 `PianoRawLinuxBoot.c` `Parse()` 实际检查的那些条件，多一条都不加：
    结构不符的文件在设备上同样起不来，早失败只是为了在构建期暴露。
    """
    data = _read(path)
    if len(data) < HEADER_BYTES or data[:8] != MAGIC:
        raise BootContainerError("not an Android boot container")
    # 字段偏移按 mkbootimg 的写入顺序：8 个 u32 大小/地址、pagesize、version、
    # os_version，然后是 board(16)、cmdline(512)、sha1 id(32)、extra_cmdline(1024)、
    # recovery_dtbo 的 size(u32)+offset(u64)、header_size(u32)、dtb 的 size(u32)+
    # addr(u64)。header_size 落在 1644，不是 1636。
    kernel_bytes, kernel_addr, ramdisk_bytes, ramdisk_addr = struct.unpack_from("<4I", data, 8)
    second_bytes, second_addr, tags_addr, pagesize, version, os_version = struct.unpack_from("<6I", data, 24)
    board = data[48:48 + NAME_BYTES]
    cmdline = data[64:64 + ARGS_BYTES]
    image_id = data[576:608]
    extra_cmdline = data[608:608 + EXTRA_ARGS_BYTES]
    dtbo_bytes = struct.unpack_from("<I", data, 1632)[0]
    dtbo_offset = struct.unpack_from("<Q", data, 1636)[0]
    header_bytes_declared = struct.unpack_from("<I", data, 1644)[0]
    dtb_bytes, dtb_addr = struct.unpack_from("<IQ", data, 1648)
    # 头只有 1660 字节，余下到第一页边界是零填充；固件要求这一段为空。
    if any(data[HEADER_BYTES:PAGESIZE]):
        raise BootContainerError("header page padding must be zero")

    if pagesize != PAGESIZE or version != HEADER_VERSION:
        raise BootContainerError(f"expected header v2 / {PAGESIZE}-byte pages, "
                                 f"got v{version} / {pagesize}")
    if header_bytes_declared != HEADER_BYTES:
        raise BootContainerError(f"unexpected header size {header_bytes_declared}")
    if second_bytes:
        raise BootContainerError("second stage must be empty")
    if dtbo_bytes or dtbo_offset:
        raise BootContainerError("recovery_dtbo must be empty")
    if not kernel_bytes or not ramdisk_bytes or not dtb_bytes:
        raise BootContainerError("kernel, ramdisk and dtb are all required")

    kernel_start = PAGESIZE
    ramdisk_start = kernel_start + align(kernel_bytes)
    dtb_start = ramdisk_start + align(ramdisk_bytes)
    end = dtb_start + align(dtb_bytes)
    if end != len(data):
        raise BootContainerError(f"container is {len(data)} bytes but its payloads end at {end}")

    kernel = data[kernel_start:kernel_start + kernel_bytes]
    if struct.unpack_from("<I", kernel, 56)[0] != ARM64_IMAGE_MAGIC:
        raise BootContainerError("kernel is not an arm64 Image")

    return {
        "kernel": kernel,
        "ramdisk": data[ramdisk_start:ramdisk_start + ramdisk_bytes],
        "dtb": data[dtb_start:dtb_start + dtb_bytes],
        "header": {
            "kernel_addr": kernel_addr,
            "ramdisk_addr": ramdisk_addr,
            "second_addr": second_addr,
            "tags_addr": tags_addr,
            "os_version": os_version,
            "board": board,
            "cmdline": cmdline,
            "image_id": image_id,
            "extra_cmdline": extra_cmdline,
            "dtb_addr": dtb_addr,
        },
    }


def verify(path, kernel, ramdisk, dtb):
    """证明容器里的三段与给定源文件逐字节相同，且容器结构合法。"""
    parsed = unpack(path)
    record = {}
    for name, expected in (("kernel", _read(kernel)), ("ramdisk", _read(ramdisk)), ("dtb", _read(dtb))):
        actual = parsed[name]
        if actual != expected:
            raise BootContainerError(f"{name} in the container differs from {name}")
        record[name] = {"bytes": len(actual), "sha256": hashlib.sha256(actual).hexdigest()}
    return {"bytes": Path(path).stat().st_size, "payloads": record}
