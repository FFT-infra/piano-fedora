#!/usr/bin/env python3
"""校验仓库内 Markdown 的相对链接可解析，锚点能在目标标题里找到。

包含：
1. 标准 Markdown 相对链接与锚点解析。
2. 反引号内文档与脚本路径校验（白名单跳过已知上游路径，按 '#' 剥离锚点）。
3. docs/*.md 孤儿文档检查（仅计 Markdown 链接）。
4. 从根 README 出发 BFS 遍历，断言到达所有 docs/*.md 深度 <= 2（两击可达）。
"""

import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\]\(([^)]+)\)")
BACKTICK = re.compile(r"`((?:docs|scripts)/[^`\s]+\.(?:md|py))(?:#[^`]+)?`")
SKIP = ("http://", "https://", "mailto:", "#")
HEADING = re.compile(r"^#{1,6}\s+(.*)$")

UPSTREAM_DOC_WHITELIST = {
    ("docs/known-issues.md", "docs/status.md"),
    ("docs/known-issues.md", "docs/ufs-dedicated-area-audit.md"),
    ("docs/known-issues.md", "docs/piano-full-ddr-producer-gaps.md"),
    ("docs/upstream-parity.md", "docs/status.md"),
    ("docs/upstream.md", "docs/devel/panel-selection.md"),
}
ORPHAN_EXEMPT = {"README.md", "README.en.md", "docs/README.md"}


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


def check_backticks(files, problems):
    for path in files:
        rel = str(path.relative_to(ROOT))
        content = path.read_text(encoding="utf-8")
        for match in BACKTICK.findall(content):
            target_path = match.partition("#")[0].strip()
            if (rel, target_path) in UPSTREAM_DOC_WHITELIST:
                continue
            if not (ROOT / target_path).exists():
                problems.append(f"{rel}: broken backtick path `{target_path}`")


def check_orphans(files, docs_files, problems):
    if not docs_files:
        return
    linked_targets = set()
    for path in files:
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            target = target.strip()
            if not target or target.startswith(SKIP):
                continue
            name, _, _ = target.partition("#")
            resolved = (path.parent / name).resolve()
            linked_targets.add(resolved)
    for doc in docs_files:
        rel_str = str(doc.relative_to(ROOT))
        if rel_str in ORPHAN_EXEMPT:
            continue
        if doc not in linked_targets:
            problems.append(f"{rel_str}: orphan document has no incoming Markdown links")


def check_two_click_reachability(docs_files, problems):
    if not docs_files:
        return
    roots = [ROOT / name for name in ("README.md", "README.en.md") if (ROOT / name).is_file()]
    for root in roots:
        visited = {root.resolve(): 0}
        queue = [(root.resolve(), 0)]
        while queue:
            curr, depth = queue.pop(0)
            if depth >= 2:
                continue
            if not curr.is_file():
                continue
            for target in LINK.findall(curr.read_text(encoding="utf-8")):
                target = target.strip()
                if not target or target.startswith(SKIP):
                    continue
                name, _, _ = target.partition("#")
                resolved = (curr.parent / name).resolve()
                if resolved.is_file():
                    if resolved not in visited or depth + 1 < visited[resolved]:
                        visited[resolved] = depth + 1
                        queue.append((resolved, depth + 1))
        for doc in docs_files:
            if doc not in visited:
                problems.append(f"{doc.relative_to(ROOT)}: unreachable from {root.name}")
            elif visited[doc] > 2:
                problems.append(f"{doc.relative_to(ROOT)}: depth {visited[doc]} from {root.name} exceeds max 2 clicks")


def main():
    problems = []
    paths = subprocess.check_output([
        "git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"
    ], cwd=ROOT).decode().split("\0")
    files = [ROOT / name for name in paths if name and (ROOT / name).is_file()]
    targets = {path.resolve() for path in files}
    docs_dir = ROOT / "docs"
    docs_files = {p.resolve() for p in docs_dir.glob("*.md")} if docs_dir.is_dir() else set()

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

    check_backticks(files, problems)
    check_orphans(files, docs_files, problems)
    check_two_click_reachability(docs_files, problems)

    if problems:
        for item in problems:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print(f"OK: {len(files)} markdown files, links, backticks, orphans, and 2-click reachability verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
