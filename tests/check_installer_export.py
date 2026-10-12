#!/usr/bin/env python3
"""校验已导出的交付目录：摘要与就地改写痕迹。

交付目录是别人拿去写盘的输入。`SHA256SUMS` 由导出脚本在最后一步写成，
所以在导出的那个文件系统里，目录内任何文件的 mtime 都不应晚于它。
晚于它就说明 SHA256SUMS 写完之后有人动过这个目录。只比摘要不足以发现这类改动：
把字节改回去、或只碰了一个未被登记的文件，`sha256sum -c` 都照过。

mtime 这一条只在导出的那个文件系统里成立。Actions artifact 上传再下载会把
mtime 重写成解包时刻（本仓库 r2 交付包的实测：CI 构建窗口 23:31–23:34 CST，
下载后的文件 mtime 是 23:56），所以下载回来的副本只能用 --digests-only 核对内容。

    tests/check_installer_export.py                     # 自检：临时目录里构造通过与拒绝两种情形
    tests/check_installer_export.py DIR                 # 校验一个刚导出的交付目录
    tests/check_installer_export.py DIR --digests-only  # 校验下载解包后的副本
"""

import hashlib
import os
import re
import sys
import tempfile
from pathlib import Path

SUMS = "SHA256SUMS"
LINE = re.compile(r"^([0-9a-f]{64}) [ *](.+)$")


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def read_sums(root):
    """读 SHA256SUMS，返回 {相对路径: 摘要}。格式不对直接报错。"""
    manifest = root / SUMS
    if not manifest.is_file():
        raise ValueError(f"missing {SUMS}")
    entries = {}
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        match = LINE.match(line)
        if not match:
            raise ValueError(f"{SUMS}:{number}: unparsable line")
        digest, name = match.groups()
        if name.startswith("/") or ".." in Path(name).parts:
            raise ValueError(f"{SUMS}:{number}: unsafe path {name!r}")
        if name in entries:
            raise ValueError(f"{SUMS}:{number}: duplicate entry {name!r}")
        entries[name] = digest
    if not entries:
        raise ValueError(f"{SUMS}: no entries")
    return entries


def verify(root, digests_only=False):
    """返回问题列表。空列表表示这个目录与它自己的 SHA256SUMS 一致。"""
    root = Path(root)
    if not root.is_dir():
        return [f"not a directory: {root}"]
    try:
        entries = read_sums(root)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    for name, digest in sorted(entries.items()):
        target = root / name
        if target.is_symlink() or not target.is_file():
            problems.append(f"listed file is missing or not regular: {name}")
        elif sha256(target) != digest:
            problems.append(f"digest differs from {SUMS}: {name}")
    if digests_only:
        return problems
    # 导出之后写进来的文件（改写的、新增的）mtime 一定晚于 SHA256SUMS。
    sums_mtime = (root / SUMS).stat().st_mtime_ns
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.stat().st_mtime_ns > sums_mtime:
            problems.append("written after " + SUMS + ": " + path.relative_to(root).as_posix())
    return problems


def stage(root, files, newer=()):
    """写一份最小交付目录：先写文件并把 mtime 压到过去，最后写 SHA256SUMS。"""
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    sums = root / SUMS
    sums.write_text("".join(
        sha256(root / name) + "  " + name + "\n" for name in sorted(files)), encoding="utf-8")
    base = sums.stat().st_mtime_ns
    for name in files:
        os.utime(root / name, ns=(base - 10**9, base - 10**9))
    for name in newer:
        os.utime(root / name, ns=(base + 10**9, base + 10**9))
    return sums


def selftest():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    files = {"scripts/install-f2fs.py": "print('install')\n",
             "sources.lock.json": "{}\n",
             "sunuefi/tools/install_piano.py": "print('piano')\n"}
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        stage(root, files)
        check(verify(root) == [], "a clean export must pass")
        # 就地改写：摘要与 mtime 各暴露一次。
        (root / "scripts/install-f2fs.py").write_text("print('patched')\n", encoding="utf-8")
        found = verify(root, digests_only=True)
        check(any("digest differs" in item for item in found),
              "an in-place rewrite must fail the digest check")
        found = verify(root, digests_only=False)
        check(any("written after" in item for item in found),
              "an in-place rewrite must fail the mtime check")
        # 只动 mtime、字节不变：摘要这一关过不了它，mtime 必须单独拦住。
        stage(root, files, newer=["scripts/install-f2fs.py"])
        check(verify(root, digests_only=True) == [],
              "a touched but unchanged file must not be reported as a digest mismatch")
        check(any("written after" in item for item in verify(root)),
              "a file touched after the export must fail the mtime check")
        # 导出之后新增一个未被登记的文件。
        stage(root, files)
        (root / "scripts/extra.py").write_text("print('extra')\n", encoding="utf-8")
        check(any("scripts/extra.py" in item for item in verify(root)),
              "a file added after the export must fail the mtime check")
        # 被登记的文件消失。
        stage(root, files)
        (root / "sources.lock.json").unlink()
        check(any("missing" in item for item in verify(root)), "a deleted listed file must fail")
        # 摘要清单本身不可解析，或带路径跳转。
        for text in ("not a sums file\n", "0" * 64 + "  ../escape\n", "0" * 64 + "  /abs\n"):
            stage(root, files)
            (root / SUMS).write_text(text, encoding="utf-8")
            check(bool(verify(root)), f"a rejected manifest must fail: {text.strip()!r}")

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: export verification accepts a clean tree and rejects rewritten digests,"
          " late mtimes, additions, deletions and malformed manifests")
    return 0


def main(argv):
    args = [item for item in argv[1:] if item != "--digests-only"]
    if len(args) > 1 or any(item.startswith("-") for item in argv[1:] if item != "--digests-only"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if not args:
        return selftest()
    problems = verify(args[0], digests_only="--digests-only" in argv)
    if problems:
        for item in problems:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    note = " (digests only)" if "--digests-only" in argv else ""
    print(f"OK: {len(read_sums(Path(args[0])))} files match {SUMS}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
