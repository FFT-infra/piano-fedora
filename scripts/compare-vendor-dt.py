#!/usr/bin/env python3
"""对照厂商 DT 源码与出货 DTB，逐条核对 SunUEFI overlay 的目标节点。

出货的 board.dtb 与厂商 Android DT 不是同一条血缘，逐字节对齐不是目标。
这里回答一个具体问题：每个 overlay 要改的节点，在厂商硬件描述里存不存在、
改了哪些属性、和厂商原值相比是新增还是改写。

厂商源码只在这里读，不进任何出货产物。
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FRAGMENT = re.compile(r"^fragment@\d+$")

# 第一轮对照里厂商侧找不到的目标节点。这里登记的是已经看过、确认属于两条
# 血缘正常差异的项：厂商用下游命名、节点由别的 overlay 建立、或者厂商 DT
# 本来就不描述这块硬件。新增的差异不进这张表，报告里单独列成 unexplained。
KNOWN_MISSING = {
    ("piano-usb-typec.dtso", "/soc/pinctrl@f100000"),
    ("piano-fingerprint.dtso", "/soc/pinctrl@f100000"),
    ("piano-pen-dock.dtso", "/soc/pinctrl@f100000"),
    ("piano-pen-charger.dtso", "/soc/pinctrl@f100000"),
    ("piano-pen-charger.dtso", "/soc/pen-dock"),
    ("piano-fingerprint.dtso", "/soc/rsc@16500000/regulators-4"),
    ("piano-regdma-resource.dtso", "/soc/display-subsystem@ae00000/display-controller@ae01000"),
    ("piano-audio-dmic-clock.dtso", "/soc/codec@7660000"),
    ("piano-camera-flash.dtso", "/soc/qcom,camera-flash0"),
    ("piano-camera-flash.dtso", "/soc/cci-ml@ac7b000/i2c-bus@1/camera@10"),
    ("piano-camera-privacy-led.dtso", "/soc/cci-ml@ac7b000/i2c-bus@1/camera@10"),
    # 后置传感器在厂商树里挂在 cci 的下游节点名下，主线的 cci-ml 地址与总线
    # 编号都对不上，按单元地址找不到对应物。传感器自己由驱动创建。
    ("piano-camera-sensors.dtso", "/soc/cci-ml@ac7b000/i2c-bus@1/camera@10"),
    ("piano-camera-sensors.dtso", "/soc/cci-ml@ac7b000/i2c-bus@1/camera@10/port/endpoint"),
    ("piano-camera-privacy-led.dtso", "/soc/qcom,qupv3_2_geni_se@8c0000/i2c@884000/camera@10"),
    ("piano-cdsp-fastrpc.dtso", "/soc/remoteproc-cdsp@32300000/glink-edge/qcom,fastrpc"),
    ("piano-touch-panel.dtso", "/soc/display-subsystem@ae00000/dsi@ae94000/panel@0"),
    ("piano-touch-panel.dtso", "/soc/qcom,qupv3_1_geni_se@ac0000/spi@a88000/touchscreen@0"),
    # DP 与 BT 相关节点的下游名字不同：厂商是 qcom,mdss_mdp@ae00000、
    # clock-controller@af00000、pinctrl@f000000，主线分别叫
    # display-subsystem@ae00000、clock-controller-ml@af00000、
    # pinctrl@f100000。DP 的 ports 与蓝牙节点厂商树里没有对应物。
    ("piano-displayport.dtso", "/soc/display-subsystem@ae00000"),
    ("piano-displayport.dtso",
     "/soc/display-subsystem@ae00000/display-controller@ae01000/ports"),
    ("piano-displayport.dtso", "/soc/ssphy@88e8000/ports"),
    ("piano-displayport.dtso", "/soc/clock-controller-ml@af00000"),
    # DP 音频卡与互连绑定都只在主线侧存在：厂商 DT 没有 ASoC 声卡节点
    # （DP 的 dai-link 由主线声卡建立），interconnect 提供者是下游的单单元
    # MMSS 名字，和 sm8750-rpmh 的两单元绑定不是同一个节点。
    ("piano-displayport.dtso", "/soc/sound-ml"),
    ("piano-display-interconnects.dtso", "/soc/display-subsystem@ae00000"),
    ("piano-bluetooth-nvm.dtso",
     "/soc/qcom,qupv3_2_geni_se@8c0000/qcom,qup_uart@898000/bluetooth"),
    ("piano-ir-spi-controller.dtso", "/soc/pinctrl@f100000"),
} | {("piano-cdsp-fastrpc.dtso",
       f"/soc/remoteproc-cdsp@32300000/glink-edge/qcom,fastrpc/compute-cb@{index}")
      for index in range(1, 13)}


def classify_missing(overlays):
    """把厂商侧缺失的目标分成已登记和新增两类。"""
    entries = [(entry["overlay"], target["target"]) for entry in overlays
               for target in entry["targets"] if target.get("exists_in_vendor") is False]
    return ([item for item in entries if item in KNOWN_MISSING],
            [item for item in entries if item not in KNOWN_MISSING])


def tools(directory):
    names = {}
    for name in ("cpp", "dtc", "fdtoverlay"):
        candidate = Path(directory) / name if directory else None
        if candidate and candidate.is_file():
            names[name] = str(candidate)
        elif shutil.which(name):
            names[name] = shutil.which(name)
        else:
            raise SystemExit(f"missing tool: {name}")
    return names


def run(command, **kwargs):
    return subprocess.run(command, check=True, capture_output=True, text=True, **kwargs)


def read_fdt_module(sunuefi):
    sys.path.insert(0, str(sunuefi / "tools"))
    from compose_piano_dtb import read_fdt
    return read_fdt


def preprocess(cpp, source, include_paths, destination):
    run([cpp, "-nostdinc", "-undef", "-x", "assembler-with-cpp",
         *[f"-I{path}" for path in include_paths], str(source), "-o", str(destination)])
    return destination


def compile_dtb(tool, source, destination, symbols=True):
    command = [tool["dtc"], "-I", "dts", "-O", "dtb", "-o", str(destination)]
    if symbols:
        command.insert(1, "-@")
    run([*command, str(source)])
    return destination


# 厂商把显示、摄像头、音频的板级片段放在 techpack 里，Android 启动时由各自
# 的 DTBO 分区叠加。不合成它们，厂商侧就缺 display-subsystem、codec、cci 这些
# 子树，对照结果会把这些目标误报成"厂商没有"。
TECHPACK_OVERLAYS = (
    "arch/arm64/boot/dts/techpack/display-devicetree/display/piano-sde-display-mtp-overlay.dts",
    "arch/arm64/boot/dts/techpack/camera-devicetree/piano-sm8750-camera-sensor.dts",
    "arch/arm64/boot/dts/techpack/audio-devicetree/piano-audio-mtp.dts",
)


def compose_vendor(vendor, tool, work):
    dts = vendor / "arch/arm64/boot/dts/vendor/qcom"
    includes = [dts, vendor / "arch/arm64/boot/dts", vendor / "include",
                vendor / "techpack/camera-kernel"]
    base = compile_dtb(tool, preprocess(tool["cpp"], dts / "sun.dts", includes, work / "sun.pp"),
                       work / "sun.dtb")
    overlay = compile_dtb(tool, preprocess(tool["cpp"], dts / "piano-sm8750-overlay.dts",
                                          includes, work / "piano.pp"), work / "piano.dtbo")
    composed = work / "vendor.dtb"
    run([tool["fdtoverlay"], "-i", str(base), "-o", str(composed), str(overlay)])
    applied, skipped = ["piano-sm8750-overlay.dts"], []
    for index, relative in enumerate(TECHPACK_OVERLAYS):
        source = vendor / relative
        if not source.is_file():
            skipped.append({"source": relative, "reason": "missing from checkout"})
            continue
        try:
            prepared = preprocess(tool["cpp"], source, [source.parent, *includes],
                                  work / f"techpack-{index}.pp")
            dtbo = compile_dtb(tool, prepared, work / f"techpack-{index}.dtbo")
            derived = work / f"vendor-{index}.dtb"
            run([tool["fdtoverlay"], "-i", str(composed), "-o", str(derived), str(dtbo)])
            composed = derived
            applied.append(source.name)
        except subprocess.CalledProcessError as error:
            lines = (error.stderr or "").strip().splitlines()
            skipped.append({"source": relative, "reason": lines[-1] if lines else "failed"})
    return composed, applied, skipped


def shape(path):
    """把节点路径拆成 (父路径, 名字前缀, 单元地址)，用于在另一棵树里找对应节点。"""
    parent, _, name = path.rpartition("/")
    stem, _, address = name.partition("@")
    return parent or "/", stem, address


def vendor_lookalikes(tree, path):
    """厂商树里同一父节点下、同地址或同名字前缀的节点。

    两条血缘的节点名不同：出货用主线的 `display-subsystem@ae00000`，厂商用
    下游的 `qcom,mdss_mdp@ae00000`；TLMM 的基地址一边是 f100000，一边是
    f000000。直接把目标报成"厂商没有"没有信息量。
    """
    parent, stem, address = shape(path)
    same_address, same_stem = [], []
    for candidate in tree:
        if candidate == path:
            continue
        other_parent, other_stem, other_address = shape(candidate)
        if other_parent != parent:
            continue
        if address and other_address == address:
            same_address.append(candidate)
        elif other_stem == stem:
            same_stem.append(candidate)
    return (same_address or same_stem)[:3]


def symbols(read_fdt, data):
    tree = read_fdt(data)["tree"]
    node = tree.get("/__symbols__", {})
    result = {}
    for label, value in node.items():
        path = value.rstrip(b"\0").decode()
        handle = tree.get(path, {}).get("phandle") or tree.get(path, {}).get("linux,phandle")
        if handle and len(handle) == 4:
            result[label] = int.from_bytes(handle, "big")
    return result


def fragments(read_fdt, data):
    """把编译后的 overlay 拆成 (目标路径或 None, 目标标签或 None, 属性字典, 子节点名)。"""
    parsed = read_fdt(data)
    tree = parsed["tree"]
    result = []
    for path, props in tree.items():
        if path.count("/") != 1 or not FRAGMENT.match(path[1:]):
            continue
        target_path = props.get("target-path")
        label = None
        handle = None
        if target_path is not None:
            target = target_path.rstrip(b"\0").decode()
        elif "target" in props and len(props["target"]) == 4:
            handle = int.from_bytes(props["target"], "big")
            target = None
        else:
            continue
        body = tree.get(path + "/__overlay__", {})
        children = [key.rsplit("/", 1)[-1] for key in tree
                    if key.rsplit("/", 1)[0] == path + "/__overlay__"]
        result.append({"target": target, "handle": handle, "props": body, "children": children})
    return result


def compare_node(base_props, overlay_props):
    added = sorted(set(overlay_props) - set(base_props))
    overridden = sorted(name for name in overlay_props
                        if name in base_props and overlay_props[name] != base_props[name])
    same = sorted(name for name in overlay_props
                  if name in base_props and overlay_props[name] == base_props[name])
    return added, overridden, same


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vendor", type=Path, required=True)
    ap.add_argument("--sunuefi", type=Path, required=True)
    ap.add_argument("--dtc", type=Path)
    ap.add_argument("--dtb", type=Path)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if not args.output.resolve().is_relative_to(ROOT / "build"):
        raise SystemExit("report output must stay in the ignored build directory")
    args.output.mkdir(parents=True, exist_ok=True)
    tool = tools(args.dtc)
    read_fdt = read_fdt_module(args.sunuefi)
    shipped_path = args.dtb or args.sunuefi / "vendor/piano-linux/board.dtb"
    release = json.loads((args.sunuefi / "config/release.json").read_text())

    with tempfile.TemporaryDirectory(prefix="vendor-dt-") as directory:
        work = Path(directory)
        composed, applied, skipped = compose_vendor(args.vendor, tool, work)
        vendor_data = composed.read_bytes()
    shipped_data = shipped_path.read_bytes()
    vendor_tree = read_fdt(vendor_data)["tree"]
    shipped_tree = read_fdt(shipped_data)["tree"]
    vendor_symbols = symbols(read_fdt, vendor_data)
    shipped_symbols = symbols(read_fdt, shipped_data)

    names = list(release["kernel"]["additional_dtb_overlays"])
    if release["kernel"].get("cpu_model_overlay"):
        names.insert(0, release["kernel"]["cpu_model_overlay"])
    # 不发货的 overlay 不参与对照：它们的 include 由上游在 --kernel-source 下
    # 从内核源码生成，这里没有那棵源码树。
    skipped = set(json.loads((ROOT / "product.json").read_text()).get("dtb_overlays_not_shipped", []))
    names = [name for name in names if name not in skipped]

    overlays = []
    provided = {}
    for name in names:
        source = args.sunuefi / name
        with tempfile.TemporaryDirectory(prefix="overlay-") as directory:
            work = Path(directory)
            dts = args.vendor / "arch/arm64/boot/dts/vendor/qcom"
            dtsi = args.sunuefi / "linux/dts"
            compiled = compile_dtb(tool, preprocess(
                tool["cpp"], source, [dtsi, dts, args.vendor / "include"], work / "in.pp"),
                work / "out.dtbo")
            data = compiled.read_bytes()
        entry = {"overlay": source.name, "targets": []}
        for fragment in fragments(read_fdt, data):
            path = fragment["target"]
            if path is None and fragment["handle"] is not None:
                path = next((node for label, value in shipped_symbols.items()
                             if value == fragment["handle"]), None)
            record = {"target": path, "props": sorted(fragment["props"]),
                      "children": sorted(fragment["children"])}
            if path:
                root = path.rstrip("/") or "/"
                for child in fragment["children"]:
                    provided[(root + "/" if root != "/" else "/") + child] = source.name
            for key, tree in (("vendor", vendor_tree), ("shipped", shipped_tree)):
                node = tree.get(path) if path else None
                record[f"exists_in_{key}"] = node is not None
                if node is not None:
                    added, overridden, same = compare_node(node, fragment["props"])
                    record[f"{key}_added"] = added
                    record[f"{key}_overridden"] = overridden
                    record[f"{key}_same"] = same
            if path is None:
                record["unresolved"] = True
            elif not record["exists_in_vendor"]:
                record["vendor_lookalikes"] = vendor_lookalikes(vendor_tree, path)
            entry["targets"].append(record)
        overlays.append(entry)

    for entry in overlays:
        for target in entry["targets"]:
            if target.get("exists_in_shipped") is False and target["target"] in provided:
                target["provided_by_overlay"] = provided[target["target"]]

    known_missing, unexplainable = classify_missing(overlays)
    report = {
        "shipped": {"path": str(shipped_path), "bytes": len(shipped_data)},
        "vendor_composed_bytes": len(vendor_data),
        "vendor_composition": {"applied": applied, "skipped": skipped},
        "roots": {
            "vendor": {name: vendor_tree["/"].get(name, b"").rstrip(b"\0").decode(errors="replace")
                       for name in ("model", "compatible")},
            "shipped": {name: shipped_tree["/"].get(name, b"").rstrip(b"\0").decode(errors="replace")
                        for name in ("model", "compatible")},
        },
        "production_overlays": names,
        "overlays": overlays,
        "missing_in_vendor_known": known_missing,
        "missing_in_vendor_unexplained": unexplainable,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    lines = ["# 厂商 DT 对照", ""]
    lines.append(f"- 出货 DTB：`{report['shipped']['path']}`，{len(shipped_data)} 字节")
    lines.append(f"- 厂商组合 DTB：{len(vendor_data)} 字节，叠加 {len(applied)} 个 overlay")
    for entry in skipped:
        lines.append(f"- 未叠加 `{entry['source']}`：{entry['reason']}")
    lines.append(f"- 出货根节点 model：{report['roots']['shipped']['model']}")
    lines.append(f"- 厂商根节点 model：{report['roots']['vendor']['model']}")
    lines.append("")
    lines.append("fdtoverlay 只合并 fragment，overlay 根节点的 model/board-id 不会合并；"
                 "ABL 的 libufdt 会合并。根属性差异属于工具差异。")
    lines.append("")
    lines.append(f"厂商侧找不到的目标：{len(known_missing) + len(unexplainable)} 个，"
                 f"其中已登记 {len(known_missing)} 个，新增 {len(unexplainable)} 个。")
    if unexplainable:
        lines.append("")
        lines.append("新增的这几条要人工看：")
        lines += [f"- `{overlay}` → `{target}`" for overlay, target in unexplainable]
    lines.append("")
    lines.append("| overlay | 目标 | 厂商侧 | 出货基线有节点 | 厂商缺的属性 | 改写的厂商值 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for entry in overlays:
        for target in entry["targets"]:
            shipped = "是" if target.get("exists_in_shipped") else "否"
            if target.get("provided_by_overlay"):
                shipped = "由 `{}` 建立".format(target["provided_by_overlay"])
            if target.get("exists_in_vendor"):
                vendor = "同名节点"
            elif target.get("vendor_lookalikes"):
                vendor = "、".join(f"`{item}`" for item in target["vendor_lookalikes"])
            else:
                vendor = "无对应"
            lines.append("| `{}` | `{}` | {} | {} | {} | {} |".format(
                entry["overlay"], target["target"] or "未解析", vendor, shipped,
                ", ".join(target.get("vendor_added", [])[:6]) or "-",
                ", ".join(target.get("vendor_overridden", [])[:6]) or "-"))
    (args.output / "report.md").write_text("\n".join(lines) + "\n")

    targets = [target for entry in overlays for target in entry["targets"]]
    print(json.dumps({
        "overlays": len(overlays),
        "targets": len(targets),
        "missing_in_shipped": sum(1 for target in targets
                                  if target.get("exists_in_shipped") is False
                                  and not target.get("provided_by_overlay")),
        "provided_by_overlay": sum(1 for target in targets if target.get("provided_by_overlay")),
        "missing_in_vendor": sum(1 for target in targets if target.get("exists_in_vendor") is False),
        "missing_in_vendor_unexplained": len(unexplainable),
        "report": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
