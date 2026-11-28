#!/usr/bin/env python3
"""合成随 kernel-piano 发布的板级 DTB，或核对一份已合成的产物。

    compose-board-dtb.py --sunuefi build/sources/sunuefi --kernel build/kernel \\
        --kernel-source <内核源码树> --output build/board-dtb
    compose-board-dtb.py --verify build/board-dtb

合成走上游 package_release 的同一组函数：vendor/piano-linux/board.dtb 叠 CPU 型号
overlay 与 release.json 列出的全部板级 overlay，再做主线绑定归一与面板选择。
输出目录里是 board.dtb 和记下它摘要的 board-dtb.json。build-bundle 把包里这一份
原样写进 ESP，不再自己合成。

piano-tsens.dtso 引用的 sm8750-thermal-zones.dtsi 不在任何仓库里：上游从编出这个
内核的源码树里取 sm8750.dtsi 的 thermal-zones 节点生成它。--kernel 给内核产物
（取 manifest.json 的 source_commit），--kernel-source 给停在这个提交上的源码树，
对不上就不合成。dt-bindings 头也取自这棵树。

裸的 vendor DTB 没有 CMA 与 UFS MCQ overlay，写进 ESP 设备起不来；没有 SoC 温区的
合成产物过热时内核不降频。消费方因此只认 manifest 对得上、且记着内核源码的产物。

需要 PATH 上有 cpp、dtc、fdtoverlay 与 git。libfdt 低于 1.8.1 会覆盖已有的 panel
phandle，上游函数遇到会直接报错。
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DTB_NAME = "board.dtb"
MANIFEST_NAME = "board-dtb.json"
STATUS = "BOARD_DTB_COMPOSED_NOT_DEVICE_VERIFIED"
VENDOR_DTB = "vendor/piano-linux/board.dtb"
FDT_MAGIC = b"\xd0\x0d\xfe\xed"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def is_complete_fdt(data):
    return data[:4] == FDT_MAGIC and int.from_bytes(data[4:8], "big") == len(data)


def release_overlays(sunuefi):
    """上游策略里的 CPU 型号 overlay 与板级 overlay，顺序照上游。"""
    kernel = json.loads((sunuefi / "config/release.json").read_text())["kernel"]
    return kernel["cpu_model_overlay"], list(kernel["additional_dtb_overlays"])


def thermal_bindings(tree, handles):
    """每个温区都要接上传感器，每条降频映射都要指向能降频的设备。

    fdtoverlay 只保证 label 解析得了。内核换了温区名字时，overlay 会建出一个只有
    触发点、没有传感器的空温区，合成照样成功，设备上那个温区注册不了。
    """
    def target(value):
        return handles.get(int.from_bytes(value[:4], "big"))

    zones = [path for path in tree if path.startswith("/thermal-zones/") and path.count("/") == 2]
    maps = [path for path in tree if path.startswith("/thermal-zones/") and "/cooling-maps/" in path]
    for zone in zones:
        sensor = target(tree[zone].get("thermal-sensors", b""))
        if sensor is None or "#thermal-sensor-cells" not in tree[sensor]:
            raise ValueError(f"thermal zone {zone} has no sensor provider")
    for entry in maps:
        device = target(tree[entry].get("cooling-device", b""))
        if device is None or "#cooling-cells" not in tree[device]:
            raise ValueError(f"cooling map {entry} does not reach a cooling device")
    if not maps:
        raise ValueError("composed board DTB binds no cooling device to a thermal zone")
    return {"zones": len(zones), "cooling_maps": len(maps)}


def compose(sunuefi, kernel, kernel_source, output):
    for tool in ("cpp", "dtc", "fdtoverlay", "git"):
        if not shutil.which(tool):
            raise ValueError(f"{tool} is not on PATH")
    commit = json.loads((kernel / "manifest.json").read_text())["source_commit"]
    sys.path.insert(0, str(sunuefi / "tools"))
    import package_release as package

    cpu_model, overlays = release_overlays(sunuefi)
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="compose-", dir=output) as scratch:
        work = Path(scratch)
        board = work / DTB_NAME
        shutil.copyfile(sunuefi / VENDOR_DTB, board)
        # 上游函数先核对源码树停在 commit 上、两份输入与提交里的一致，再生成
        # sm8750-thermal-zones.dtsi；返回的 include 目录是 work 与源码树的 include。
        include_dirs, kernel_record = package.prepare_dtb_includes(kernel_source, commit, work)
        package.apply_cpu_model_overlay(board, sunuefi / cpu_model, work, include_dirs=include_dirs)
        for name in overlays:
            package.apply_cpu_model_overlay(board, sunuefi / name, work, cpu_only=False,
                                            include_dirs=include_dirs)
        package.normalize_mainline_bindings(board)
        panel = package.select_panel_vendor(board, "auto")
        data = board.read_bytes()
        if not is_complete_fdt(data):
            raise ValueError("composed board DTB is not a complete flattened device tree")
        parsed = package.read_fdt(data)
        thermal = thermal_bindings(parsed["tree"], parsed["phandles"])
        (output / DTB_NAME).write_bytes(data)
    manifest = {
        "status": STATUS,
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "base": {"path": VENDOR_DTB, "sha256": digest(sunuefi / VENDOR_DTB)},
        "overlays": [{"path": name, "sha256": digest(sunuefi / name)}
                     for name in (cpu_model, *overlays)],
        "kernel_source": kernel_record,
        "thermal": thermal,
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
    source = manifest.get("kernel_source")
    if not isinstance(source, dict) or not re.fullmatch("[0-9a-f]{40}", str(source.get("kernel_commit"))):
        raise ValueError(f"{directory / MANIFEST_NAME} records no kernel source; "
                         "the tree was composed without the SoC thermal zones")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sunuefi", type=Path, help="锁定的 SunUEFI checkout")
    mode.add_argument("--verify", type=Path, metavar="DIR", help="只核对 DIR 里已合成的产物")
    parser.add_argument("--kernel", type=Path, help="内核产物目录，带 manifest.json")
    parser.add_argument("--kernel-source", type=Path, help="停在内核产物 source_commit 上的源码树")
    parser.add_argument("--output", type=Path, help="输出目录")
    args = parser.parse_args(argv)
    try:
        if args.verify:
            manifest = verify(args.verify)
        else:
            if None in (args.kernel, args.kernel_source, args.output):
                parser.error("--sunuefi needs --kernel, --kernel-source and --output")
            manifest = compose(args.sunuefi.resolve(), args.kernel.resolve(),
                               args.kernel_source.resolve(), args.output)
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None)
        print(f"compose-board-dtb: {exc}" + (f"\n{detail}" if detail else ""), file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
