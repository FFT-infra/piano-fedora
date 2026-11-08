#!/usr/bin/env python3
"""厂商 DT 对照脚本的契约：目标路径解析、同名节点回退、属性差异分类。"""

import importlib.util
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUN = Path(os.environ.get("PIANO_TEST_SUNUEFI", ROOT / "build/test-sources/sunuefi"))


def load():
    spec = importlib.util.spec_from_file_location("compare_vendor_dt",
                                                  ROOT / "scripts/compare-vendor-dt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    module = load()

    assert module.shape("/soc/pinctrl@f100000") == ("/soc", "pinctrl", "f100000")
    assert module.shape("/") == ("/", "", "")

    tree = {"/soc/pinctrl@f000000": {}, "/soc/pinctrl@f100000": {}, "/soc/mdss@ae00000": {},
            "/soc/other@ae00000": {}}
    # 同父节点、同地址优先
    assert module.vendor_lookalikes(tree, "/soc/display-subsystem@ae00000") == ["/soc/mdss@ae00000",
                                                                             "/soc/other@ae00000"]
    # 地址不同但名字前缀相同，仍然能找到对应物
    assert module.vendor_lookalikes(tree, "/soc/pinctrl@f100000") == ["/soc/pinctrl@f000000"]
    # 父节点不同不算对应
    assert module.vendor_lookalikes({"/other/pinctrl@f000000": {}}, "/soc/pinctrl@f100000") == []

    added, overridden, same = module.compare_node({"a": b"1", "b": b"2"},
                                                  {"b": b"3", "c": b"4"})
    assert (added, overridden, same) == (["c"], ["b"], []), (added, overridden, same)
    assert module.compare_node({}, {"x": b"1"}) == (["x"], [], [])

    # 厂商侧缺失分两类：登记过的正常差异，和需要人工看的新增项。
    fixtures = [
        {"overlay": "piano-fingerprint.dtso",
         "targets": [{"target": "/soc/rsc@16500000/regulators-4", "exists_in_vendor": False},
                     {"target": "/soc/xiaomi_fingerprint", "exists_in_vendor": True}]},
        {"overlay": "piano-new.dtso",
         "targets": [{"target": "/soc/unknown@1", "exists_in_vendor": False}]},
    ]
    known, unexplained = module.classify_missing(fixtures)
    assert known == [("piano-fingerprint.dtso", "/soc/rsc@16500000/regulators-4")], known
    assert unexplained == [("piano-new.dtso", "/soc/unknown@1")], unexplained
    assert all(isinstance(pair, tuple) and len(pair) == 2 for pair in module.KNOWN_MISSING)

    if not shutil.which("dtc") or not shutil.which("cpp"):
        print("SKIP: fragment parsing needs dtc and cpp")
        return 0
    if not (SUN / "tools/compose_piano_dtb.py").is_file():
        print("SKIP: fragment parsing reuses the pinned SunUEFI FDT reader")
        return 0
    source = """
/dts-v1/;
/plugin/;
/ {
    fragment@0 {
        target-path = "/soc/pinctrl@f100000";
        __overlay__ { size = <0x100>; added; };
    };
    fragment@1 {
        target = <&label>;
        __overlay__ { status = "okay"; };
    };
};
"""
    with tempfile.TemporaryDirectory(prefix="vendor-dt-test-") as directory:
        work = Path(directory)
        (work / "in.dts").write_text(source)
        subprocess.run(["dtc", "-@", "-I", "dts", "-O", "dtb", "-o", str(work / "out.dtbo"),
                        str(work / "in.dts")], check=True, capture_output=True)
        parsed = module.fragments(module.read_fdt_module(SUN), (work / "out.dtbo").read_bytes())
    paths = [item["target"] for item in parsed]
    assert "/soc/pinctrl@f100000" in paths, paths
    first = next(item for item in parsed if item["target"] == "/soc/pinctrl@f100000")
    assert first["props"] == {"added": b"", "size": b"\x00\x00\x01\x00"}
    labels = [item for item in parsed if item["handle"] is not None]
    assert len(labels) == 1, parsed
    print("OK: vendor DT comparison resolves paths, lookalikes and attribute deltas")


if __name__ == "__main__":
    main()
