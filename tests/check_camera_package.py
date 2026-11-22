#!/usr/bin/env python3
"""相机包的上游 pin 与构建矩阵契约。

spec 的 `sunuefi_commit` 必须与 `sources.lock.json` 相同，否则设备上跑的
camerad 与内核来自两份不同的上游。构建矩阵要能把 `patches/` 里的补丁放进
SOURCES，缺了 `%patch 0` 会在 rpmbuild 阶段才炸。
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packages" / "piano-camera" / "piano-camera.spec"
BUILD = ROOT / "scripts" / "build-rpms.sh"
sys.path.insert(0, str(ROOT / "scripts"))
import package_sets


def main():
    spec = SPEC.read_text()
    pin = re.search(r"%global sunuefi_commit\s+([0-9a-f]{40})", spec)
    assert pin, "spec 没有 sunuefi_commit"
    lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    assert pin.group(1) == lock, "相机包 pin 与 sources.lock.json 不一致"
    assert "Project-SunUEFI-%{sunuefi_commit}.tar.gz" in spec, "Source1 不是按 pin 取的归档"
    assert "test -f" in spec or "test -s" in spec, "取到空的上游树时应报错退出"

    build = BUILD.read_text()
    assert "piano-camera" in package_sets.matrix(), "构建矩阵没有包含 piano-camera"
    assert '-name "*.patch"' in build, "build-rpms 不会把包内 patches/ 放进 SOURCES"
    print("OK: 相机包 pin 与锁定一致，补丁进入 SOURCES")


if __name__ == "__main__":
    main()
