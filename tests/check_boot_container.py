#!/usr/bin/env python3
"""离线检查 Android boot v2 容器的打包、解包与校验。

核心证据是最后一条：把设备 ESP 上实测的 `boot.img` 解出三段再重新打包，
输出必须与原文件逐字节相同。它同时证明我们的打包器与上游 `mkbootimg`
的产物一致，因此设备侧升级不会产出固件读不懂的文件。

那份镜像带设备身份（内核与 DTB 的构建指纹），不入库。宿主没有时明确记
SKIP，不算通过；其余用例用合成载荷，任何时候都跑。
"""

import hashlib
import os
import struct
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import piano_boot_container as container  # noqa: E402

PAGESIZE = container.PAGESIZE
ARM64_MAGIC = container.ARM64_IMAGE_MAGIC

# 真实镜像默认从 build/test-sources 取，可用 PIANO_TEST_ESP_BOOT 覆盖。
ESP_IMAGE = Path(os.environ.get("PIANO_TEST_ESP_BOOT",
                                ROOT / "build/test-sources/esp-boot.img"))


def arm64_kernel(size, seed=b"K"):
    """造一段带 arm64 Image 头的载荷；固件在偏移 56 处读魔数。"""
    blob = bytearray((seed * size)[:size])
    struct.pack_into("<I", blob, 56, ARM64_MAGIC)
    return bytes(blob)


def write(tmp, name, data):
    path = Path(tmp) / name
    path.write_bytes(data)
    return path


def expect_error(fn, label, failures, fragment=None):
    try:
        fn()
    except container.BootContainerError as error:
        if fragment is not None and fragment not in str(error):
            failures.append(f"{label}: wrong rejection reason: {error}")
        return
    failures.append(f"{label}: expected BootContainerError, none raised")


def main():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        kernel_bytes = arm64_kernel(9000)
        ramdisk_bytes = b"R" * 5000
        dtb_bytes = b"D" * 3000
        kernel = write(tmp, "kernel", kernel_bytes)
        ramdisk = write(tmp, "ramdisk", ramdisk_bytes)
        dtb = write(tmp, "dtb", dtb_bytes)

        # 1. 往返：三段解回来必须与源文件逐字节相同。
        image = tmp / "boot.img"
        record = container.pack(kernel, ramdisk, dtb, image)
        parsed = container.unpack(image)
        check(parsed["kernel"] == kernel_bytes, "roundtrip: kernel differs")
        check(parsed["ramdisk"] == ramdisk_bytes, "roundtrip: ramdisk differs")
        check(parsed["dtb"] == dtb_bytes, "roundtrip: dtb differs")
        check(record["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest(),
              "pack record sha256 must describe the file it wrote")

        # 2. 页对齐：payload 各自向上补齐，文件在最后一个 payload 的页边界结束。
        expected = (PAGESIZE + container.align(len(kernel_bytes))
                    + container.align(len(ramdisk_bytes))
                    + container.align(len(dtb_bytes)))
        check(image.stat().st_size == expected,
              f"size must be {expected}, got {image.stat().st_size}")
        check(image.stat().st_size % PAGESIZE == 0, "container must end on a page boundary")
        for field, value in (("kernel", len(kernel_bytes)),
                             ("ramdisk", len(ramdisk_bytes)),
                             ("dtb", len(dtb_bytes))):
            check(record["payloads"][field]["bytes"] == value,
                  f"pack record {field} size wrong")
        # 头里的三段长度与地址也要对得上。
        data = image.read_bytes()
        check(struct.unpack_from("<I", data, 8)[0] == len(kernel_bytes),
              "header kernel size wrong")
        check(struct.unpack_from("<I", data, 16)[0] == len(ramdisk_bytes),
              "header ramdisk size wrong")
        check(struct.unpack_from("<I", data, 1648)[0] == len(dtb_bytes),
              "header dtb size wrong")
        check(struct.unpack_from("<I", data, 36)[0] == PAGESIZE, "header pagesize wrong")
        check(struct.unpack_from("<I", data, 40)[0] == 2, "header version must be 2")
        check(struct.unpack_from("<I", data, 1644)[0] == 1660, "header size must be 1660")
        check(struct.unpack_from("<I", data, 24)[0] == 0, "second stage must be empty")
        check(struct.unpack_from("<I", data, 1632)[0] == 0, "recovery_dtbo must be empty")
        check(not any(data[1660:PAGESIZE]), "header page padding must be zero")
        # sha1 id 只有 20 字节有效，其余补零。
        check(not any(data[596:608]), "sha1 id must be zero-padded to 32 bytes")

        # 3. 校验：源文件与容器内容不符时必须拒绝。
        other = write(tmp, "other-kernel", arm64_kernel(9000, b"X"))
        expect_error(lambda: container.verify(image, other, ramdisk, dtb),
                     "verify with a different kernel", failures)

        # 4. 拒绝 v1 头。
        v1 = tmp / "v1.img"
        broken = bytearray(data)
        struct.pack_into("<I", broken, 40, 1)
        v1.write_bytes(bytes(broken))
        expect_error(lambda: container.unpack(v1), "header v1", failures, "header v2")

        # 5. 拒绝多余 trailing：固件要求 Trailing.Bytes 为零。
        trailing = tmp / "trailing.img"
        trailing.write_bytes(data + b"\x00" * PAGESIZE)
        expect_error(lambda: container.unpack(trailing), "trailing data", failures)

        # 6. 拒绝非 arm64 内核。
        not_arm = tmp / "not-arm.img"
        container.pack(write(tmp, "plain-kernel", b"K" * 9000), ramdisk, dtb, not_arm)
        expect_error(lambda: container.unpack(not_arm), "non-arm64 kernel", failures)

        # 7. 拒绝非空 second / recovery_dtbo，这两段固件一律不读。
        for offset, label in ((24, "second size"), (1632, "recovery_dtbo size")):
            path = tmp / f"field-{offset}.img"
            payload = bytearray(data)
            struct.pack_into("<I", payload, offset, 4096)
            path.write_bytes(bytes(payload))
            expect_error(lambda p=path: container.unpack(p), label, failures)

        # 8. 拒绝头页填充里的脏字节。
        dirty = tmp / "dirty-padding.img"
        payload = bytearray(data)
        payload[1660] = 1
        dirty.write_bytes(bytes(payload))
        expect_error(lambda: container.unpack(dirty), "dirty header padding", failures)

        # 9. 空段一律拒绝：三段都是启动必需。
        expect_error(lambda: container.header_bytes(b"", ramdisk_bytes, dtb_bytes),
                     "empty kernel", failures)
        expect_error(lambda: container.header_bytes(kernel_bytes, b"", dtb_bytes),
                     "empty ramdisk", failures)
        expect_error(lambda: container.header_bytes(kernel_bytes, ramdisk_bytes, b""),
                     "empty dtb", failures)

        # 10. 设备实测镜像：解包再打包必须逐字节相同。
        if not ESP_IMAGE.is_file():
            print(f"SKIP: no ESP boot.img at {ESP_IMAGE}; "
                  "the byte-equality check against the device image did not run "
                  "(not a pass)")
        else:
            repacked = tmp / "repacked.img"
            original = ESP_IMAGE.read_bytes()
            sections = container.unpack(ESP_IMAGE)
            container.pack(write(tmp, "esp-kernel", sections["kernel"]),
                           write(tmp, "esp-ramdisk", sections["ramdisk"]),
                           write(tmp, "esp-dtb", sections["dtb"]),
                           repacked)
            produced = repacked.read_bytes()
            check(len(produced) == len(original),
                  f"repacked size {len(produced)} != {len(original)}")
            if produced != original:
                differing = [i for i in range(min(len(produced), len(original)))
                             if produced[i] != original[i]]
                check(False, "repacked image differs from the device image at "
                             f"{len(differing)} bytes, first at {differing[:8]}")
            check(container.verify(repacked, write(tmp, "esp-kernel2", sections["kernel"]),
                                   write(tmp, "esp-ramdisk2", sections["ramdisk"]),
                                   write(tmp, "esp-dtb2", sections["dtb"]))["payloads"]
                  is not None, "verify must accept the repacked device image")

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: boot container")
    return 0


if __name__ == "__main__":
    sys.exit(main())
