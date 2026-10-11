#!/usr/bin/env python3
"""函数级回归：ABL 取证先落 RAM 文件，再逐行转 kmsg。

不启动设备、不写 ttyGS0。只把 f2fs-disk-bootstrap 里的
capture_boot_evidence 函数抽出来，在 BusyBox sh 里用桩采集器执行，
验证 stdout/stderr 都进文件、log() 副作用可被捕获、采集失败仍保留
诊断、并且本函数自身不含无界等待。
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "initramfs" / "f2fs-disk-bootstrap"


def extract_function(text, name):
    match = re.search(rf"^{re.escape(name)}\(\) \{{\n.*?^\}}", text, re.M | re.S)
    assert match, f"bootstrap lost its {name} function"
    return match.group()


def run_case(busybox, directory, name, collector, expect_status):
    evidence = directory / f"evidence-{name}.txt"
    capture = directory / f"kmsg-{name}.log"
    collector_path = directory / f"collector-{name}"
    collector_path.write_text(collector)
    collector_path.chmod(0o755)
    capture.write_text("")
    text = BOOTSTRAP.read_text()
    function = extract_function(text, "capture_boot_evidence")
    # log() 只在本测试里被替换成文件捕获，模拟 kmsg 副作用。
    harness = (
        "log() { printf 'LOG:%s\\n' \"$*\" >>\"$LOG_CAPTURE\"; }\n"
        "boot_collector=$PIANO_COLLECT_BOOT\n"
        "boot_evidence_file=$PIANO_BOOT_EVIDENCE_FILE\n"
        + function
        + "\ncapture_boot_evidence\nprintf 'STATUS:%s\\n' \"$?\"\n"
    )
    process = subprocess.run(
        [busybox, "sh", "-c", harness],
        capture_output=True, text=True, timeout=10,
        env={
            **os.environ,
            "PIANO_COLLECT_BOOT": str(collector_path),
            "PIANO_BOOT_EVIDENCE_FILE": str(evidence),
            "LOG_CAPTURE": str(capture),
        },
    )
    assert process.returncode == 0, process.stderr
    status = re.search(r"STATUS:(\d+)", process.stdout)
    assert status, process.stdout
    assert int(status.group(1)) == expect_status, (
        f"{name}: capture status {status.group(1)} != {expect_status}")
    return evidence, capture


def main():
    busybox = shutil.which("busybox")
    if not busybox:
        print("SKIP: boot evidence regression requires BusyBox")
        return
    text = BOOTSTRAP.read_text()
    function = extract_function(text, "capture_boot_evidence")
    # 只看代码，注释里的词不算副作用。
    code = "\n".join(line for line in function.splitlines() if not line.lstrip().startswith("#"))
    # 函数自身不得引入无界等待，也不得直接写 ttyGS0。
    assert not re.search(r"(?<![\w-])wait(?![\w-])", code), "unbounded wait in evidence capture"
    assert "ttyGS0" not in code, "evidence capture must not write ttyGS0 directly"
    assert re.search(r"umask 077", function), "evidence file must be created 0600"
    assert re.search(r">\"\$boot_evidence_file\" 2>&1", function), (
        "collector stdout and stderr must share the evidence file")
    assert re.search(r"<\"\$boot_evidence_file\"", function), (
        "kmsg forwarding must read the saved file, not a live pipe")

    with tempfile.TemporaryDirectory() as path:
        directory = Path(path)
        # 正常：stdout 与 stderr 都要留存，状态 0。
        evidence, capture = run_case(
            busybox, directory, "ok",
            '#!/bin/sh\nprintf "OUT-LINE\\n"\nprintf "ERR-LINE\\n" >&2\n',
            0)
        saved = evidence.read_text()
        assert "OUT-LINE" in saved and "ERR-LINE" in saved, saved
        assert evidence.stat().st_mode & 0o777 == 0o600, oct(evidence.stat().st_mode)
        logged = capture.read_text()
        assert f"BOOT_EVIDENCE_SAVED: file={evidence} collect_status=0" in logged, logged
        assert "LOG:OUT-LINE" in logged and "LOG:ERR-LINE" in logged, logged

        # 采集失败：非零状态向上返回，但已写入的诊断仍留存并转发。
        evidence, capture = run_case(
            busybox, directory, "fail",
            '#!/bin/sh\nprintf "PARTIAL\\n"\nprintf "BOOM\\n" >&2\nexit 7\n',
            7)
        saved = evidence.read_text()
        assert "PARTIAL" in saved and "BOOM" in saved, saved
        logged = capture.read_text()
        assert f"BOOT_EVIDENCE_SAVED: file={evidence} collect_status=7" in logged, logged
        assert "LOG:PARTIAL" in logged and "LOG:BOOM" in logged, logged

    print("OK: ABL boot evidence is saved to RAM, forwarded to kmsg and kept on failure")


if __name__ == "__main__":
    sys.exit(main())
