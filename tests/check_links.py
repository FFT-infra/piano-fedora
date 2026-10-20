#!/usr/bin/env python3
"""校验仓库内 Markdown 的相对链接可解析，锚点能在目标标题里找到。"""

import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^)]+)\)")
SKIP = ("http://", "https://", "mailto:", "#")
HEADING = re.compile(r"^#{1,6}\s+(.*)$")


def anchor(text):
    """按 GitHub 的标题锚点规则生成 slug。

    去掉行内标记与标点，保留字母、数字、空格、连字符与下划线，
    空格转连字符。中文标题的锚点因此是汉字原样，不带百分号编码。
    """
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    kept = [
        ch for ch in text
        if unicodedata.category(ch)[0] in ("L", "N") or ch in " -_"
    ]
    return "".join(kept).replace(" ", "-")


def headings(path):
    found = set()
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING.match(line)
        if match:
            found.add(anchor(match.group(1)))
    return found


def main():
    problems = []
    paths = subprocess.check_output([
        "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"
    ], cwd=ROOT).decode().split("\0")
    files = [ROOT / name for name in paths if name and (ROOT / name).is_file()]
    targets = {path.resolve() for path in files}
    for path in files:
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            target = target.strip()
            if not target or target.startswith(SKIP):
                continue
            name, _, fragment = target.partition("#")
            resolved = (path.parent / name).resolve()
            if not resolved.exists():
                problems.append(f"{path.relative_to(ROOT)}: broken link {target}")
                continue
            if not fragment or resolved not in targets:
                continue
            if fragment.lower() not in headings(resolved):
                problems.append(
                    f"{path.relative_to(ROOT)}: missing anchor #{fragment} in "
                    f"{resolved.relative_to(ROOT)}"
                )

    if problems:
        for item in problems:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print(f"OK: {len(files)} markdown files, links and anchors resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
