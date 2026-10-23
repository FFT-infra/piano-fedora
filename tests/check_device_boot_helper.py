#!/usr/bin/env python3
"""离线回归：make-device-boot.sh 只用 adb 读原厂 BOOT，并在生成前核对两条摘要。

脚本随归档发布，用户第一次真正用它时手上多半已经有了刷了一半的目录。
它必须：

* 不出现 `fastboot fetch`：原厂 ABL 不实现这个子命令，实测只拿回 0 字节文件；
* 要求读回正好 96 MiB，截断的读取不能进重打包；
* 在生成任何文件之前比对设备侧与宿主侧两条摘要；
* 产出 `device-boot/boot_linux.img`，不覆盖已有的 `device-boot/`。

这里只做静态检查与一次 `--help`，不接触设备。
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/make-device-boot.sh"
WORKFLOW = ROOT / ".github/workflows/release.yml"


def code_lines(text):
    """去掉注释行，只留会执行的部分。"""
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def test_reads_through_adb_not_fastboot_fetch():
    text = SCRIPT.read_text()
    code = code_lines(text)
    assert "fastboot" not in code, (
        "the stock BOOT is read through adb; `fastboot fetch boot_a` returns a "
        "0-byte file on the stock ABL")
    # 注释里必须写明为什么不这么做，否则下一个维护者还会把它加回去。
    assert "fastboot fetch" in text, (
        "the script must keep the note explaining why fastboot fetch is not used")
    assert 'exec-out su -c "dd if=${partition} bs=1048576"' in code, (
        "make-device-boot.sh must read the full 96 MiB partition through adb")
    assert "sha256sum ${partition}" in code, (
        "make-device-boot.sh must hash the partition on the device")


def test_requires_a_complete_96_mib_read():
    text = SCRIPT.read_text()
    assert "BOOT_PARTITION_BYTES=$((96 * 1024 * 1024))" in text, (
        "the partition size must be stated once, as 96 MiB")
    assert '[ "$size" = "$BOOT_PARTITION_BYTES" ]' in text, (
        "make-device-boot.sh must refuse a truncated read")
    # 两处比较都必须在 prepare-device-boot.py 之前。
    first_hash = text.index('[ "$host_digest" = "$device_digest" ]')
    repack = text.index("python3 -I scripts/prepare-device-boot.py")
    assert first_hash < repack, (
        "the host and device digests must be compared before repacking")
    assert text.index('[ "$size" = "$BOOT_PARTITION_BYTES" ]') < repack


def test_writes_the_documented_output_and_refuses_to_overwrite():
    text = SCRIPT.read_text()
    assert "--output device-boot" in text, (
        "make-device-boot.sh must write device-boot/, where flash-linux.sh looks")
    assert '[ -e device-boot ] && die' in text, (
        "make-device-boot.sh must refuse to overwrite an existing device-boot/")
    # 生成脚本靠归档里的 boot-tools 与 host-boot-tool，不能自己去下载。
    for path in ("boot-tools", "host-boot-tool", "scripts/prepare-device-boot.py"):
        assert path in text, path
    assert "curl" not in text and "wget" not in text, (
        "make-device-boot.sh must work offline from the unpacked archive")


def test_slot_is_validated():
    text = SCRIPT.read_text()
    assert 'case "$SLOT" in' in text and "a|b)" in text, "the slot must be a or b"
    assert 'partition="/dev/block/by-name/boot_${SLOT}"' in text
    assert '--slot "_${SLOT}"' in text


def test_archive_ships_the_script():
    workflow = WORKFLOW.read_text()
    assert "scripts/make-device-boot.sh" in workflow, (
        "release.yml must copy make-device-boot.sh into every archive")
    assert re.search(r'chmod \+x[^\n]*make-device-boot\.sh', workflow), (
        "make-device-boot.sh must be executable in the archive")


def test_help_runs_without_a_device():
    result = subprocess.run(["bash", str(SCRIPT), "--help"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert result.returncode == 0, result.stdout
    assert "boot_linux.img" in result.stdout
    assert "fastboot fetch" in result.stdout  # 说明为什么不这么做


def test_repacker_imports_under_isolated_python():
    """`python3 -I` 隐含 `-P`，脚本目录不在 sys.path 里。

    prepare-device-boot.py 依赖同级的 piano_artifacts 与 piano_sparse。没有
    把脚本目录补回 sys.path 时，`--help` 会在 import 阶段就报
    ModuleNotFoundError，用户第一次刷机卡在 Step 2b。这里真的跑一次，不靠
    字符串匹配。
    """
    result = subprocess.run(
        ["python3", "-I", str(ROOT / "scripts/prepare-device-boot.py"), "--help"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert result.returncode == 0, result.stdout
    assert "--stock-boot" in result.stdout
    assert "ModuleNotFoundError" not in result.stdout


def main():
    test_reads_through_adb_not_fastboot_fetch()
    test_requires_a_complete_96_mib_read()
    test_writes_the_documented_output_and_refuses_to_overwrite()
    test_slot_is_validated()
    test_archive_ships_the_script()
    test_help_runs_without_a_device()
    test_repacker_imports_under_isolated_python()
    print("OK: make-device-boot.sh 只用 adb，96 MiB 与两条摘要都核对后才生成")


if __name__ == "__main__":
    main()
