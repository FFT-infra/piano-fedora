#!/usr/bin/env python3
"""合成随 kernel-piano 发布的板级 DTB，或核对一份已合成的产物。

    compose-board-dtb.py --sunuefi build/sources/sunuefi --output build/board-dtb
    compose-board-dtb.py --verify build/board-dtb

合成走上游 package_release 的同一条链，产物与 build-bundle 写进 ESP 的那一份
逐字节相同：vendor/piano-linux/board.dtb 叠 CPU 型号 overlay 与 release.json
列出的板级 overlay（product.json 记为不发货的除外），再做主线绑定归一与面板
选择。输出目录里是 board.dtb 和记下它摘要的 board-dtb.json。

裸的 vendor DTB 没有 CMA 与 UFS MCQ overlay，写进 ESP 设备起不来。消费方因此
只认 manifest 对得上的合成产物，不直接拿 vendor 目录里的文件。

需要 PATH 上有 cpp、dtc 与 fdtoverlay。libfdt 低于 1.8.1 会覆盖已有的 panel
phandle，上游函数遇到会直接报错。
"""

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DTB_NAME = "board.dtb"
MANIFEST_NAME = "board-dtb.json"
STATUS = "BOARD_DTB_COMPOSED_NOT_DEVICE_VERIFIED"
VENDOR_DTB = "vendor/piano-linux/board.dtb"
KERNEL_INCLUDE = "upstream/linux-piano/include"
FDT_MAGIC = b"\xd0\x0d\xfe\xed"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_complete_fdt(data):
    return data[:4] == FDT_MAGIC and int.from_bytes(data[4:8], "big") == len(data)


def shipped_overlays(sunuefi):
    """上游策略里的 overlay，去掉 product.json 记为不发货的，顺序照上游。"""
    kernel = json.loads((sunuefi / "config/release.json").read_text())["kernel"]
    skipped = set(json.loads((ROOT / "product.json").read_text())["dtb_overlays_not_shipped"])
    return kernel["cpu_model_overlay"], [name for name in kernel["additional_dtb_overlays"]
                                         if name not in skipped]


def compose(sunuefi, output):
    for tool in ("cpp", "dtc", "fdtoverlay"):
        if not shutil.which(tool):
            raise ValueError(f"{tool} is not on PATH")
    include = sunuefi / KERNEL_INCLUDE
    if not (include / "dt-bindings").is_dir():
        raise ValueError(f"kernel dt-bindings are missing under {include}")
    sys.path.insert(0, str(sunuefi / "tools"))
    import package_release as package

    cpu_model, overlays = shipped_overlays(sunuefi)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compose-", dir=output) as scratch:
        work = Path(scratch)
        board = work / DTB_NAME
        shutil.copyfile(sunuefi / VENDOR_DTB, board)
        # include 目录显式传：上游只在给了 --kernel-source 时才预处理 overlay，
        # 本仓库对 package_release 的补丁把它设成默认值。显式传让结果不依赖
        # 这棵 checkout 有没有打过那条补丁。
        package.apply_cpu_model_overlay(board, sunuefi / cpu_model, work,
                                        include_dirs=(include,))
        for name in overlays:
            package.apply_cpu_model_overlay(board, sunuefi / name, work, cpu_only=False,
                                            include_dirs=(include,))
        package.normalize_mainline_bindings(board)
        panel = package.select_panel_vendor(board, "auto")
        data = board.read_bytes()
        if not is_complete_fdt(data):
            raise ValueError("composed board DTB is not a complete flattened device tree")
        (output / DTB_NAME).write_bytes(data)
    manifest = {
        "status": STATUS,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "base": {"path": VENDOR_DTB, "sha256": digest(sunuefi / VENDOR_DTB)},
        "overlays": [{"path": name, "sha256": digest(sunuefi / name)}
                     for name in (cpu_model, *overlays)],
        "panel": panel["selected"],
    }
    (output / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def verify(directory):
    manifest = json.loads((directory / MANIFEST_NAME).read_text())
    data = (directory / DTB_NAME).read_bytes()
    if manifest.get("status") != STATUS:
        raise ValueError(f"{directory / MANIFEST_NAME} is not a composed board DTB manifest")
    if manifest.get("sha256") != hashlib.sha256(data).hexdigest() or manifest.get("bytes") != len(data):
        raise ValueError(f"{directory / DTB_NAME} differs from its manifest")
    if not is_complete_fdt(data):
        raise ValueError(f"{directory / DTB_NAME} is not a complete flattened device tree")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sunuefi", type=Path, help="锁定的 SunUEFI checkout，带内核的 dt-bindings")
    mode.add_argument("--verify", type=Path, metavar="DIR", help="只核对 DIR 里已合成的产物")
    parser.add_argument("--output", type=Path, help="输出目录（与 --sunuefi 一起用）")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            manifest = verify(args.verify)
        else:
            if args.output is None:
                parser.error("--sunuefi needs --output")
            manifest = compose(args.sunuefi.resolve(), args.output)
    except (OSError, ValueError, KeyError) as exc:
        print(f"compose-board-dtb: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
