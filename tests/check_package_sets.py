#!/usr/bin/env python3
"""包集合单一真源的契约。

`scripts/package-sets.json` 是包名的唯一真源：构建矩阵、COPR 发布顺序、
rootfs 装配与设备验收都从它派生。这里锁住派生结果，防止某一处退回手写
清单。

派生关系本身由 `tests/check_clean_rootfs.py` 与各包的离线测试核对；本文件
负责集合内部的一致性：顺序、内核唯一、矩阵与发布清单不重叠。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package_sets


def check_shape():
    data = package_sets.load()
    assert data["schema_version"] == 1, "schema_version 变了要同步读取方"
    matrix = package_sets.matrix(data)
    assert len(matrix) == len(set(matrix)), "矩阵里有重复包名"
    assert package_sets.kernel(data) in matrix, "内核必须来自矩阵"
    runtime = package_sets.runtime(data)
    graphics = package_sets.graphics_binaries(data)
    overlap = set(matrix) & (set(runtime) | set(graphics))
    assert not overlap, f"矩阵与运行时/图形栈重叠: {sorted(overlap)}"
    published = package_sets.published(data)
    assert len(published) == len(set(published)), "发布清单里有重复包名"


def check_arch_filter():
    """只有 aarch64 的包在别的架构上被过滤掉，无 arches 字段的始终构建。"""
    data = package_sets.load()
    arm = package_sets.matrix(data, "aarch64")
    assert arm == package_sets.matrix(data), "矩阵里的包都应在 aarch64 上构建"
    x86 = package_sets.matrix(data, "x86_64")
    assert "piano-fedora-configs" in x86, "noarch 包不能因为架构被过滤掉"
    assert "kernel-piano" not in x86, "内核只在 aarch64 上构建"


def check_consumers_derive():
    """消费方必须 import package_sets，不能再抄一份包名清单。"""
    for script in ("build-clean-rootfs.py", "finalize-fedora-rootfs.py",
                   "verify-copr-device.py", "publish-copr.py"):
        text = (ROOT / "scripts" / script).read_text(encoding="utf-8")
        assert "package_sets" in text, f"{script} 没有从 package_sets 取包清单"
    build = (ROOT / "scripts" / "build-rpms.sh").read_text(encoding="utf-8")
    assert "package_sets.py" in build, "build-rpms.sh 没有从 package_sets 取矩阵"


def check_extras_consistency():
    data = package_sets.load()
    order = package_sets.extras(data, "order")
    desktop = package_sets.extras(data, "desktop")
    meta = package_sets.extras(data, "meta")
    assert set(desktop) <= set(order), "桌面组件不在 extras 构建顺序里"
    assert set(meta) <= set(order), "meta 包不在 extras 构建顺序里"
    for name, deps in data["extras"]["depends"].items():
        assert set(deps) <= set(order), f"{name} 依赖了 extras 里没有的包"


def main():
    check_shape()
    check_arch_filter()
    check_consumers_derive()
    check_extras_consistency()
    print("OK: package-sets.json 是包集合的唯一真源，各处派生一致")


if __name__ == "__main__":
    main()
