#!/usr/bin/env python3
"""校验仓库内 Markdown 的相对链接可解析，锚点能在目标标题或锚点标签里找到。

检查范围与规则：
1. 标准 Markdown 相对链接与锚点解析：覆盖仓库内所有 git 跟踪的 *.md 文件，支持标准标题 slug 及显式 <a id="..."> 锚点。
2. 反引号路径引用校验：仅校验 Markdown 中形如 `docs/*.md` 与 `scripts/*.py` 的路径引用
   （按 '#' 剥离锚点，白名单跳过已知上游文档路径；不覆盖 config/*.json、packages/*、tests/*.py 等）。
3. 孤儿文档检查：仅断言 docs/*.md 目录下的文档均有入向 Markdown 链接（README 与 docs/README.md 豁免）。
4. 两击可达检查：从根 README.md / README.en.md 出发进行 BFS 遍历，断言所有 docs/*.md 的深度 <= 2
   （根目录的 CONTRIBUTING.md、SECURITY.md 等非 docs/*.md 文件不在两击可达约束目标集内）。
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
HTML_ANCHOR = re.compile(r'<a\s+(?:id|name)=["\']([^"\']+)["\']', re.IGNORECASE)

UPSTREAM_DOC_WHITELIST = {
    ("docs/known-issues.md", "docs/status.md"),
    ("docs/known-issues.md", "docs/ufs-dedicated-area-audit.md"),
    ("docs/known-issues.md", "docs/piano-full-ddr-producer-gaps.md"),
    ("docs/upstream-parity.md", "docs/status.md"),
    ("docs/upstream.md", "docs/devel/hardware/panel-selection.md"),
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
        for match in HTML_ANCHOR.finditer(line):
            found.add(match.group(1).lower())
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
