#!/usr/bin/env python3
"""离线检查的统一入口：逐个跑 `tests/check_*.py`，汇总结果。

`make test` 调这里。单个文件就是一个脚本，用断言表达契约，退出码非零即失败；
文件内打印 `SKIP: …` 的行表示这一项在本机没有条件跑。

    python3 scripts/run-checks.py              # 全部
    python3 scripts/run-checks.py --list       # 只列文件名
    python3 scripts/run-checks.py --pattern camera
    python3 scripts/run-checks.py --timeout 300

`PIANO_TEST_SUNUEFI` 指向真实上游树时，`check_installer.py` 会带上
`--sunuefi`；没设时不带，文件自己决定要不要 SKIP。
"""

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
SKIP = re.compile(r"^SKIP:", re.M)

# 少数文件接受参数。环境变量没设时保持原样，让文件自己决定要不要 SKIP。
def extra_args(name):
    if name == "check_installer.py":
        sunuefi = os.environ.get("PIANO_TEST_SUNUEFI")
        if sunuefi:
            return ["--sunuefi", sunuefi]
    return []


def discover(pattern=None):
    files = sorted(TESTS.glob("check_*.py"))
    if pattern:
        files = [path for path in files if pattern in path.name]
    return files


def run_one(path, timeout):
    started = time.monotonic()
    try:
        result = subprocess.run([sys.executable, str(path), *extra_args(path.name)],
                                capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", time.monotonic() - started, f"timed out after {timeout}s\n"
    elapsed = time.monotonic() - started
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode:
        return "FAILED", elapsed, output
    skips = SKIP.findall(output)
    return ("SKIPPED" if skips else "PASSED"), elapsed, output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="只列出将被执行的检查")
    parser.add_argument("--pattern", help="只跑文件名含该子串的检查")
    parser.add_argument("--timeout", type=float, default=120,
                        help="单个文件的超时秒数（默认 120）")
    args = parser.parse_args(argv)

    files = discover(args.pattern)
    if not files:
        print("no checks matched", file=sys.stderr)
        return 2
    if args.list:
        print("\n".join(path.name for path in files))
        return 0

    failures, skipped = [], []
    started = time.monotonic()
    for path in files:
        status, elapsed, output = run_one(path, args.timeout)
        print(f"{status} {path.name} ({elapsed:.1f}s)", flush=True)
        if status in ("FAILED", "TIMEOUT"):
            failures.append(path.name)
            print(output.rstrip(), flush=True)
        elif status == "SKIPPED":
            skipped.append(path.name)
            for line in output.splitlines():
                if line.startswith("SKIP:"):
                    print(f"  {line}", flush=True)

    total = time.monotonic() - started
    print(f"\nResult: {len(files)} files, {len(skipped)} skipped, "
          f"{len(failures)} failing ({total:.1f}s)")
    if failures:
        print("failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
