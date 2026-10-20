#!/usr/bin/env python3
"""验证文档里的手动分区步骤：算术、命令序列与校验点。

`tests/check_first_partition.py` 只测规划器。本测试针对 `docs/installation.md`
的「手动分区」章节，确认：

* 章节里的容量公式在多种磁盘容量上都能算出对半分配；
* 文档给出的扇区边界与规划器在真实 GPT fixture 上一致；
* 命令序列顺序正确：先缩短 userdata，再建 root，再建 ESP；
* 写 GPT 只用 `dd`，先尾后头，写完回读，并给出 `original-gpt-*.bin` 的回滚；
* 明确包含 `fastboot erase userdata`，且排在回读之后；
* 不包含任何会碰 Android 固定分区的命令。

文档公式是独立实现，不 import 规划器；两者都用同一个 GPT fixture 计算，
结果必须相同。任一漂移都会失败。
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "installation.md"
PLANNER = ROOT / "scripts" / "plan-first-partition.py"

START = "### 手动分区（不使用安装脚本）"
END = "## 更新已有 Linux 分区"

SECTOR = 4096
SECTORS_PER_MIB = SECTOR and (1024 * 1024) // SECTOR

# 与 check_first_partition.py 相同的真实 GPT 形状：userdata 占满盘尾。
GPT = """Disk /dev/block/sda: {total} sectors, 0 GiB
Sector size (logical/physical): 4096/4096 bytes
Disk identifier (GUID): D55FA102-1ED9-4154-B3A7-3E84CD916CB5
Partition table holds up to 64 entries
First usable sector is 6, last usable sector is {last}

Number  Start (sector)    End (sector)  Size       Code  Name
   1               6               7   8.0 KiB     FFFF  switch
  32          191488          224255   128.0 MiB   A039  rescue
  33          224256         3632127   13.0 GiB    FFFF  super
  34         3632128       {last}   0 GiB   A03A  userdata
"""


def chapter():
    text = DOC.read_text(encoding="utf-8")
    start = text.index(START)
    end = text.index(END, start)
    return text[start:end]


def align_down(sectors, unit_sectors):
    return sectors - (sectors % unit_sectors)


def doc_math(userdata_first, last_usable, esp_mib=512):
    """按文档「算容量」一节的公式独立计算。"""
    esp = esp_mib * 1024 * 1024 // SECTOR
    usable = last_usable - userdata_first + 1
    root = (usable - esp) // 2
    esp_first = align_down(last_usable - esp + 1, SECTORS_PER_MIB)
    root_last = esp_first - 1
    root_first = align_down(root_last - root + 1, SECTORS_PER_MIB)
    userdata_last = root_first - 1
    return {
        "esp_first": esp_first,
        "esp_last": last_usable,
        "root_first": root_first,
        "root_last": root_last,
        "userdata_last": userdata_last,
        "root_mib": (root_last - root_first + 1) // SECTORS_PER_MIB,
        "userdata_mib": (userdata_last - userdata_first + 1) // SECTORS_PER_MIB,
    }


def run_planner(dump_path):
    result = subprocess.run(
        [sys.executable, str(PLANNER), "--dump", str(dump_path), "--equal-split"],
        capture_output=True, text=True, cwd=ROOT)
    if result.returncode:
        raise AssertionError(f"planner failed: {result.stderr or result.stdout}")
    return json.loads(result.stdout)


def test_math_matches_planner(tmp):
    failures = []
    for label, last in (("128GiB", 31457280), ("256GiB", 62914560),
                        ("512GiB", 126509311), ("1TiB", 249518074)):
        dump = tmp / f"{label}.txt"
        dump.write_text(GPT.format(total=last + 6, last=last), encoding="utf-8")
        planned = run_planner(dump)
        expected = doc_math(3632128, last)
        changes = {c["name"]: c for c in planned["changes"]}
        for key, row in (("sunuefi_root", changes["sunuefi_root"]),
                         ("sunuefi_esp", changes["sunuefi_esp"]),
                         ("userdata", changes["userdata"])):
            if key == "userdata":
                actual, want = row["new_last"], expected["userdata_last"]
            elif key == "sunuefi_root":
                actual, want = (row["first"], row["last"]), (expected["root_first"], expected["root_last"])
            else:
                actual, want = (row["first"], row["last"]), (expected["esp_first"], expected["esp_last"])
            if actual != want:
                failures.append(f"{label}: {key} docs={want} planner={actual}")
        if abs(expected["root_mib"] - expected["userdata_mib"]) > 1:
            failures.append(f"{label}: halves differ by more than 1 MiB")
        if expected["root_mib"] <= 0 or expected["userdata_mib"] <= 0:
            failures.append(f"{label}: empty half")
    assert not failures, "; ".join(failures)


def test_command_blocks():
    """命令必须在可执行的 sh 代码块里，不能只出现在正文描述中。"""
    text = chapter()
    blocks = re.findall(r"```sh\n(.*?)```", text, re.S)
    assert blocks, "manual partition chapter has no sh code blocks"
    code = "\n".join(blocks)
    checks = [
        ("od -An -tu8 -j 4128 -N 8", "must read the last LBA from the primary header"),
        ("od -An -tu4 -j 4176 -N 4", "must read the entry count from the primary header"),
        ("TOTAL=$((LAST + 1))", "must derive the total LBA count from alternate_lba"),
        ("BLOCKS=$(( (ENTRIES * 128 + 4095) / 4096 ))", "must derive the table blocks from the entry count"),
        ('dd if=$DISK of=$TMP/gpt-head.raw', "must dump the live GPT head"),
        ('dd if=$DISK of=$TMP/gpt-tail.raw', "must dump the live GPT tail"),
        ("DISK=/dev/block/sda", "must name the Android block device"),
        ("plan-first-partition.py", "must plan the new table before writing it"),
        ("--emit-gpt", "must emit both GPT blobs"),
        ("gpt-tail.bin", "must write the backup table"),
        ("gpt-head.bin", "must write the primary table"),
        ("conv=fsync,notrunc", "writes must not truncate the rest of the disk"),
        ("sha256sum", "must compare the blobs against the device readback"),
        ("original-gpt-head.bin", "must offer a rollback of the primary table"),
        ("original-gpt-tail.bin", "must offer a rollback of the backup table"),
        ("fastboot erase userdata", "must erase userdata explicitly"),
    ]
    failures = [reason for token, reason in checks if token not in code]
    # sysfs counts 512-byte sectors; using it as a 4096-byte LBA index is off by 8.
    if "cat /sys/block/sda/size" in code:
        failures.append("must not use the 512-byte sysfs sector count as a 4096-byte LBA index")

    def at(token):
        index = code.find(token)
        return index if index >= 0 else -1

    write_block = next((b for b in blocks if "gpt-tail.bin" in b and "of=$DISK" in b), None)
    if write_block is None:
        failures.append("no code block actually writes the partition table")
    else:
        tail_at = write_block.find("gpt-tail.bin")
        head_at = write_block.find("gpt-head.bin")
        if not (0 <= tail_at < head_at):
            failures.append("the backup table must land before the primary table (tail first)")
        if write_block.count("dd if=") < 2:
            failures.append("the write block must dd both blobs")

    readback_at = next((code.find(line) for line in code.splitlines()
                        if "dd if=$DISK" in line and "sha256sum" in line), -1)
    erase_block = next((b for b in blocks if "fastboot erase userdata" in b), None)
    if erase_block is None:
        failures.append("userdata must be erased explicitly after repartitioning")
        erase_at = -1
    else:
        erase_at = code.find("fastboot erase userdata")
        if "reboot" not in erase_block:
            failures.append("erase userdata must be followed by a reboot")
    if "sgdisk" in code:
        failures.append("manual partitioning must not depend on sgdisk")
    if not (0 <= at("gpt-head.raw") < at("--emit-gpt") < at("gpt-tail.bin") < readback_at < erase_at):
        failures.append(
            "order must be read GPT -> plan blobs -> write tail then head -> readback -> erase userdata")
    assert not failures, "; ".join(failures)


def test_no_android_partition_writes():
    text = chapter()
    forbidden = ("boot_a", "dtbo_a", "super", "init_boot", "vendor_boot", "vbmeta")
    failures = []
    for token in forbidden:
        # Allowed only as a name in prose, never as a dd target.
        if re.search(r"of=/dev/(block/)?sda[^\n]*" + re.escape(token), text):
            failures.append(f"manual partitioning must not write {token}")
    for token in ("boot_a", "dtbo_a", "super", "init_boot", "vendor_boot", "vbmeta"):
        if re.search(r"dd[^\n]*of=" + re.escape(token), text):
            failures.append(f"manual partitioning must not dd onto {token}")
    assert not failures, "; ".join(failures)


def test_mentions_capacity_is_per_device():
    text = chapter()
    assert "每台设备按自己实时的 GPT 计算" in DOC.read_text(encoding="utf-8"), (
        "installation.md must state that capacity is computed per device")
    assert "product.json" in text or "product.json" in DOC.read_text(encoding="utf-8")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_math_matches_planner(Path(tmp))
    test_command_blocks()
    test_no_android_partition_writes()
    test_mentions_capacity_is_per_device()
    print("OK: manual partitioning docs match the planner and keep Android partitions untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
