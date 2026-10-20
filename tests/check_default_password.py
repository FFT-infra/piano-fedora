#!/usr/bin/env python3
"""默认口令只在一处定义，文档与安装器不得写另一份字面量。

`scripts/finalize-fedora-rootfs.py` 的 `DEFAULT_PIANO_PASSWORD` 是产品默认值
的唯一来源。README、安装文档、SECURITY 与安装脚本都引用同一个值；任何一处
改成别的字符串，装出来的镜像和文档说的就不一致，用户照着文档登录会失败。
"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "scripts/finalize-fedora-rootfs.py"
REFERRERS = [
    ROOT / "README.md",
    ROOT / "README.en.md",
    ROOT / "SECURITY.md",
    ROOT / "docs/installation.md",
    ROOT / "docs/public-release-gaps.md",
    ROOT / "scripts/build-clean-rootfs.py",
    ROOT / "scripts/installer/flash-linux.sh",
    ROOT / "scripts/installer/flash-linux.bat",
    ROOT / "scripts/installer/README-INSTALL.txt",
]


def owner_password():
    tree = ast.parse(OWNER.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DEFAULT_PIANO_PASSWORD" for t in node.targets):
            return node.value.value
    raise AssertionError("finalize-fedora-rootfs.py no longer defines DEFAULT_PIANO_PASSWORD")


def main():
    password = owner_password()
    if len(password) < 8 or password != password.strip():
        print(f"FAIL: default password is unusable: {password!r}", file=sys.stderr)
        return 1
    missing = [str(path.relative_to(ROOT)) for path in REFERRERS if password not in path.read_text()]
    if missing:
        for name in missing:
            print(f"FAIL: {name} does not state the current default password", file=sys.stderr)
        return 1
    print(f"OK: 默认口令 {password} 只定义一次，{len(REFERRERS)} 处引用与之一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
