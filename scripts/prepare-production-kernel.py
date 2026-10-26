#!/usr/bin/env python3
"""准备正式 Fedora 内核：重放 SunUEFI 的封存 mail 序列，得到唯一的派生树。"""

import argparse
import json
import subprocess
from pathlib import Path


def prepare(sun):
    sun = sun.resolve()
    subprocess.run(["python3", sun / "tools/prepare_release_kernel.py", "--refresh"],
                   cwd=sun, check=True)
    # 7.3 把 Gunyah 驱动放进封存序列本身（0006），准备工具的产物就是最终源码，
    # 不需要再导入一遍厂商分支，也就没有第二份派生清单。
    directory = sun / "build/release"
    parent = json.loads((directory / "source-manifest.json").read_text())
    result = {**parent, "worktree": parent["worktree"]}
    (directory / "production-source-manifest.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sunuefi", type=Path, required=True)
    prepare(ap.parse_args().sunuefi)


if __name__ == "__main__":
    main()
