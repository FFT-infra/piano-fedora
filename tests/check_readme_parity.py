#!/usr/bin/env python3
"""检查中文 README 与英文 README 的结构对应与互链。

两版是同一份事实集的两种语言。没有结构检查时，改完中文版很容易忘记英文版，
读者看到的两份入口就慢慢分叉了。这里只比较骨架：一级到三级标题的数量、
两版顶部的切换链接，以及两版都指向同一组文档。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CN = ROOT / "README.md"
EN = ROOT / "README.en.md"

HEADING = re.compile(r"^(#{1,3})\s+(.*)$", re.MULTILINE)
DOC_LINK = re.compile(r"\]\((docs/[^)#]+)")


def headings(path):
    return [len(level) for level in HEADING.findall(path.read_text(encoding="utf-8"))]


def main():
    failures = []
    for path in (CN, EN):
        if not path.is_file():
            print(f"FAIL: missing {path.name}", file=sys.stderr)
            return 1

    cn = CN.read_text(encoding="utf-8")
    en = EN.read_text(encoding="utf-8")

    # The switch line has to exist in both files and point at the other one. It
    # sits near the title, but a notice block may come first, so search the head
    # of the file instead of a fixed line number.
    head_cn = cn.splitlines()[:20]
    head_en = en.splitlines()[:20]
    if not any("(README.en.md)" in line and "|" in line for line in head_cn):
        failures.append("README.md: the language switch line must link README.en.md "
                        "near the top of the file")
    if not any("(README.md)" in line and "|" in line for line in head_en):
        failures.append("README.en.md: the language switch line must link README.md "
                        "near the top of the file")

    # Same skeleton: a section added to one side only means the other side is stale.
    if headings(CN) != headings(EN):
        failures.append(f"heading structure differs: zh={headings(CN)} en={headings(EN)}")

    # Both versions must point at the same docs, in the same order.
    if DOC_LINK.findall(cn) != DOC_LINK.findall(en):
        failures.append("README.md and README.en.md link different documentation")

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: 中英文 README 结构一致且互链")
    return 0


if __name__ == "__main__":
    sys.exit(main())
