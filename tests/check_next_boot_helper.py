#!/usr/bin/env python3
"""离线验证 piano-next-boot-helper 的槽位判定与参数处理。

helper 只做一件事：从主备 GPT 和高通 A/B 属性里找出当前活动 BOOT，
读它的 generation，再作为期望值写进
`piano-boot-request set --target ... --expect-generation`。
它不切活动槽、不写 GPT，也不读 `/etc/piano/boot-request.json`——
上游在 7dbabdc 里删掉了那套配置文件机制。

这里用合成的 GPT 镜像检查 `read_gpt` 与 `select_active_boot`，用假的
原生工具检查参数拒绝路径；不接触真实块设备。真机行为由安装后的
`piano-next-boot` 与双系统往返验收覆盖。
"""

import importlib.machinery
import importlib.util
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packages/piano-fedora-configs/usr/libexec/piano-next-boot-helper"


def load_helper():
    # helper 没有 .py 后缀，spec_from_file_location 推断不出 loader。
    loader = importlib.machinery.SourceFileLoader("next_boot_helper", str(HELPER))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("next_boot_helper", loader))
    loader.exec_module(module)
    return module


def attr(priority, active=True, retries=6, successful=True, unbootable=False):
    """高通 A/B 槽属性：priority48..49, active50, retries51..53, successful54, unbootable55。"""
    return ((priority & 3) << 48 | int(active) << 50 | (retries & 7) << 51
            | int(successful) << 54 | int(unbootable) << 55)


def build_disk(sector, entries_spec, *, corrupt_backup=False, corrupt_primary_header=False,
               corrupt_entries=False):
    """造一份主备一致的 GPT 镜像，entries_spec 是 (名字, 起始, 结束, 属性) 列表。"""
    count = 8
    entries = bytearray(128 * count)
    for index, (name, first, last, attributes) in enumerate(entries_spec):
        at = index * 128
        entries[at:at + 16] = bytes([index + 1]) * 16
        entries[at + 16:at + 32] = bytes([0xA0 + index]) * 16
        struct.pack_into("<3Q", entries, at + 32, first, last, attributes)
        entries[at + 56:at + 56 + len(name) * 2] = name.encode("utf-16le")
    blocks = (len(entries) + sector - 1) // sector
    last_lba = 255
    # 摘要覆盖干净的表；要模拟损坏就只改写进磁盘的那份。
    crc = zlib.crc32(entries)
    on_disk = bytearray(entries)
    if corrupt_entries:
        on_disk[0] ^= 1
    disk = bytearray(sector * 256)
    for lba, alternate, table in ((1, last_lba, 2), (last_lba, 1, last_lba - blocks)):
        header = bytearray(sector)
        header[:8] = b"EFI PART"
        struct.pack_into("<II", header, 8, 0x10000, 92)
        struct.pack_into("<4Q", header, 24, lba, alternate, 16, 240)
        header[56:72] = b"d" * 16
        struct.pack_into("<Q3I", header, 72, table, count, 128, crc)
        struct.pack_into("<I", header, 16, zlib.crc32(header[:92]))
        if corrupt_primary_header and lba == 1:
            header[20] ^= 1
        if corrupt_backup and lba == last_lba:
            header[20] ^= 1
        disk[lba * sector:(lba + 1) * sector] = header
        disk[table * sector:table * sector + len(on_disk)] = on_disk
    return disk


def read(disk):
    return lambda size, offset: bytes(disk[offset:offset + size])


def main():
    helper = load_helper()
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    # 这套配置机制已经整条删除；helper 不能再依赖它。
    source = HELPER.read_text()
    for gone in ("boot-request.json", "qbootctl", "/etc/piano"):
        check(gone not in source, f"helper still references {gone}")

    # 两个扇区尺寸都要能解析，且主备一致才算数。
    for sector in (512, 4096):
        # 活动槽由高通属性决定：priority 高的、active 且未标记不可启动的。
        disk = build_disk(sector, [("boot_a", 32, 63, attr(2)),
                                   ("boot_b", 64, 95, attr(3))])
        _, boots = helper.read_gpt(read(disk), sector, len(disk))
        check(set(boots) == {"boot_a", "boot_b"}, f"sector {sector}: both BOOT partitions must be found")
        check(helper.select_active_boot(boots) == "boot_b", f"sector {sector}: priority 3 must win")
        check(boots["boot_a"][:2] == (32, 63) and boots["boot_b"][:2] == (64, 95),
              f"sector {sector}: partition ranges must be read back")

        # 优先级并列时拒绝猜测。
        tied = dict(boots)
        tied["boot_a"] = (*boots["boot_a"][:2], attr(3), boots["boot_a"][3])
        try:
            helper.select_active_boot(tied)
        except ValueError:
            pass
        else:
            failures.append(f"sector {sector}: equal priorities must be rejected")

        # priority 为 0、未 active、或已标记不可启动，都不算候选。
        for label, attributes in (("zero priority", attr(0)),
                                  ("inactive", attr(3, active=False)),
                                  ("unbootable", attr(3, unbootable=True)),
                                  ("no retries and not successful",
                                   attr(3, retries=0, successful=False))):
            broken = {"boot_a": (*boots["boot_a"][:2], attributes, boots["boot_a"][3]),
                      "boot_b": (*boots["boot_b"][:2], attributes, boots["boot_b"][3])}
            try:
                helper.select_active_boot(broken)
            except ValueError:
                pass
            else:
                failures.append(f"sector {sector}: {label} must not be selected")

        # 主备任何一侧损坏都要拒绝，不能拿一半元数据去改启动选择。
        for label, kwargs in (("corrupt primary header", {"corrupt_primary_header": True}),
                              ("corrupt backup header", {"corrupt_backup": True}),
                              ("corrupt entries", {"corrupt_entries": True})):
            bad = build_disk(sector, [("boot_a", 32, 63, attr(2)),
                                      ("boot_b", 64, 95, attr(3))], **kwargs)
            try:
                helper.read_gpt(read(bad), sector, len(bad))
            except ValueError:
                pass
            else:
                failures.append(f"sector {sector}: {label} must be rejected")

        # 缺 boot_b 或分区重叠都不能当成正常布局。
        for label, spec in (("missing boot_b", [("boot_a", 32, 63, attr(2))]),
                            ("overlapping", [("boot_a", 32, 70, attr(2)),
                                             ("boot_b", 64, 95, attr(3))])):
            try:
                helper.read_gpt(read(build_disk(sector, spec)), sector, len(build_disk(sector, spec)))
            except ValueError:
                pass
            else:
                failures.append(f"sector {sector}: {label} must be rejected")

    # 参数与权限路径在碰真实设备之前就该失败。
    def expect_error(fragment, *argv):
        try:
            helper.main(list(argv))
        except ValueError as error:
            check(fragment in str(error), f"{argv}: expected {fragment!r}, got {error!r}")
        else:
            failures.append(f"accepted: {argv}")

    expect_error("用法", "uefi")
    expect_error("用法", "linux", "--force")
    expect_error("用法", "linux", "--reboot", "--extra")
    expect_error("用法")
    expect_error("只读", "status", "--reboot")

    print("\n".join("FAIL: " + message for message in failures))
    if failures:
        return 1
    print("OK: 活动槽由只读 GPT 与高通属性判定，主备损坏/并列/缺槽都拒绝，"
          "参数与只读语义符合新 helper")
    return 0


if __name__ == "__main__":
    sys.exit(main())
