#!/usr/bin/env python3
"""端到端验证文档里的手动刷机命令序列（用桩 fastboot，不接触设备）。

需求原文（2026-10-16 20:17）：“可以有一键式的，但是一定要有怎么自己手动刷入的
（包括用到 release 的哪些内容等等），并且要验证。”

`tests/check_flash_script_guard.py` 只做静态检查（脚本里没有 erase、只写三个
分区）。本测试再进一步：把 `docs/installation.md` 手动章节里的命令**真的
提取出来执行一遍**，配一个桩 `fastboot`，验证

* 文档里的命令逐条可执行，没有笔误或引用了不存在的资产名；
* 写入顺序是 set_active b -> boot_b -> sunuefi_esp -> sunuefi_root；
* 全程没有任何 erase/-w/format；
* 校验命令（sha256sum -c）能在真实的发布包目录结构上通过。

桩 fastboot 会把每次调用记进日志并返回合理输出，不触碰任何真实设备。
"""

from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "installation.md"

# The manual chapter is delimited by these headings.
START = "## 三之二、 手动刷机"
END = "## 四、 首次安装与底层分区规划"

STUB = r"""#!/usr/bin/env python3
import os, sys
args = sys.argv[1:]
log = os.environ["STUB_LOG"]
with open(log, "a") as fh:
    fh.write(" ".join(args) + "\n")

if not args:
    print("usage: fastboot ...", file=sys.stderr)
    sys.exit(1)

cmd = None
for a in args:
    if not a.startswith("-") and a not in ("-s",):
        cmd = a
        break

if cmd == "devices":
    print("12440448\tfastboot")
elif cmd == "getvar":
    name = args[-1]
    table = {
        "product": "piano",
        "current-slot": "a",
        "unlocked": "yes",
        "is-userspace": "no",
        "partition-size:sunuefi_esp": str(537899008),
        "partition-size:sunuefi_root": str(515225600000),
        "partition-size:boot_b": str(100663296),
    }
    print(f"{name}: {table.get(name, 'unknown')}")
    print("Finished. Total time: 0.001s")
elif cmd == "fetch":
    # The documented readback: copy the source image to the requested file.
    part, out = args[-2], args[-1]
    src = {"sunuefi_esp": os.environ.get("ESP_IMG", "")}.get(part, "")
    if src and os.path.exists(src):
        import shutil as sh
        sh.copyfile(src, out)
    else:
        print("FAILED (remote: 'unknown partition')", file=sys.stderr)
        sys.exit(1)
    print("Sending fetch command... OKAY")
    print("Finished. Total time: 0.001s")
elif cmd in ("flash", "set_active", "reboot"):
    print(f"Sending '{cmd}' command... OKAY")
    print("Finished. Total time: 0.001s")
else:
    print(f"unknown command {cmd}", file=sys.stderr)
    sys.exit(1)
"""


def extract_manual_chapter(text):
    start = text.index(START)
    end = text.index(END, start)
    return text[start:end]


def extract_shell_blocks(chapter):
    """Return the contents of every ```sh fenced block in the chapter."""
    return re.findall(r"```sh\n(.*?)```", chapter, re.DOTALL)


def main():
    failures = []
    if not DOC.is_file():
        print(f"FAIL: missing {DOC.relative_to(ROOT)}", file=sys.stderr)
        return 1
    chapter = extract_manual_chapter(DOC.read_text(encoding="utf-8"))
    blocks = extract_shell_blocks(chapter)
    if not blocks:
        print("FAIL: manual chapter has no shell blocks", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # A stub fastboot plus fake release assets, so every documented command
        # can actually run. Sizes are small; the point is the command sequence.
        bindir = work / "bin"
        bindir.mkdir()
        stub = bindir / "fastboot"
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)

        boot = work / "boot_sunuefi.img"
        esp = work / "sunuefi_esp.img"
        root_zst = work / "pianoroot-fedora44-gnome-f2fs.img.zst"
        for path, data in ((boot, b"BOOT" * 64), (esp, b"ESP" * 128)):
            path.write_bytes(data)
        # A real zstd stream so `zstd -d` works in the documented step.
        raw = work / "raw.img"
        raw.write_bytes(b"ROOTFS" * 512)
        subprocess.run(["zstd", "-q", "-f", str(raw), "-o", str(root_zst)], check=True)
        raw.unlink()

        # SHA256SUMS exactly as package-release-bundle.sh writes it.
        sums = work / "SHA256SUMS"
        subprocess.run(
            f"cd {work} && sha256sum ./boot_sunuefi.img ./sunuefi_esp.img "
            f"./pianoroot-fedora44-gnome-f2fs.img.zst > SHA256SUMS",
            shell=True, check=True)

        log = work / "fastboot.log"
        env = {
            **os.environ,
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "STUB_LOG": str(log),
            "ESP_IMG": str(esp),
        }

        ran = 0
        for block in blocks:
            for raw_line in block.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                # Only the fastboot/zstd/sha256sum flow is executed; the
                # readback helpers that need mtype are checked textually below.
                if line.startswith(("fastboot", "zstd", "sha256sum")):
                    res = subprocess.run(line, shell=True, cwd=work, env=env,
                                         capture_output=True, text=True)
                    ran += 1
                    if res.returncode != 0:
                        failures.append(
                            f"documented command failed: {line}\n"
                            f"    stdout: {res.stdout.strip()[:200]}\n"
                            f"    stderr: {res.stderr.strip()[:200]}")

        if ran == 0:
            failures.append("no documented commands were executed")

        # Verify the write sequence the stub recorded.
        calls = [l.split() for l in log.read_text().splitlines() if l.strip()]
        verbs = []
        for args in calls:
            if "flash" in args:
                i = args.index("flash")
                verbs.append(("flash", args[i + 1] if i + 1 < len(args) else ""))
            elif "set_active" in args:
                i = args.index("set_active")
                verbs.append(("set_active", args[i + 1] if i + 1 < len(args) else ""))

        # The flash sequence, optionally followed by the documented rollback
        # (`set_active a`) from the "回滚到 Android" step.
        expected = [("set_active", "b"), ("flash", "boot_b"),
                    ("flash", "sunuefi_esp"), ("flash", "sunuefi_root")]
        if verbs[:len(expected)] != expected:
            failures.append(f"documented write order is {verbs}, expected {expected}")
        tail = verbs[len(expected):]
        if tail and tail != [("set_active", "a")]:
            failures.append(f"unexpected writes after the flash sequence: {tail}")

        # Nothing destructive may appear, even in the manual path.
        text_calls = log.read_text()
        for forbidden in ("erase", " -w", "format", "flashing"):
            if re.search(rf"\b{forbidden.strip()}\b", text_calls):
                failures.append(f"manual flow issued forbidden command: {forbidden}")

        # The readback step must be present and must be shown to be conditional.
        if "fastboot fetch" not in chapter:
            failures.append("manual chapter has no readback step")
        if "partition-size:sunuefi_esp" not in chapter:
            failures.append("manual chapter does not gate the readback on partition size")

        # The asset list must name the files the commands actually use.
        for asset in ("boot_sunuefi.img", "sunuefi_esp.img",
                      "pianoroot-fedora44-gnome-f2fs.img.zst", "SHA256SUMS"):
            if asset not in chapter:
                failures.append(f"manual chapter does not list asset {asset}")

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: 手动刷机章节的命令序列可执行、顺序正确、无破坏性命令，"
          "校验与读回步骤齐全")
    return 0


if __name__ == "__main__":
    sys.exit(main())
