#!/usr/bin/env python3
"""核对 Iris、DPU、功放与触屏补丁进入 7.3 的封存序列，且改动补丁字节会被摘要门槛拒绝。

7.3 的正式内核不再合并 stable 分支：它从公开基线开始重放
`patches/linux/7.3/series.json` 里的整条 mail 序列，最终树必须等于
`target_tree`。因此这几条补丁必须作为序列的行存在，而不是往
`merge_stable` 的元组里追加键。
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SERIES = Path("patches/linux/7.3/series.json")
# 补丁文件名 -> 它必须改到的内核文件。
EXPECTED = {
    "0025-dpu-defer-slave-cleanup.patch": "drivers/gpu/drm/msm/disp/dpu1/dpu_encoder.c",
    "0026-media-iris-decode-order-and-deblocking.patch":
        "drivers/media/platform/qcom/iris/iris_hfi_gen2.c",
    "0027-asoc-piano-speaker-gain-limit.patch": "sound/soc/qcom/sc8280xp.c",
    "0028-input-nt36532e-panel-vendor-misc.patch":
        "drivers/input/touchscreen/nt36532e/nt36xxx.c",
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sunuefi", type=Path)
    args = parser.parse_args()
    sun = args.sunuefi or Path(os.environ.get("PIANO_TEST_SUNUEFI", ROOT / "build/test-sources/sunuefi"))
    tool = sun / "tools/prepare_release_kernel.py"
    manifest = sun / SERIES
    if not tool.is_file() or not manifest.is_file():
        print("SKIP: Iris source consumption needs a 7.3 SunUEFI checkout")
        return 0

    spec = importlib.util.spec_from_file_location("iris_prepare", tool)
    prepare = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prepare)
    # 准备工具自己加载并核对整条序列：行数、sha256、原始提交头、父子链、
    # 提交日期与末尾树。这里借它的实现，不另写一套解析。
    series, patches, _ = prepare.load_series(sun, prepare.DEFAULT_TARGET)
    policy = prepare.TARGETS[prepare.DEFAULT_TARGET]
    names = [row["file"] for _, row in patches]
    for name in EXPECTED:
        assert name in names, f"{name} is archived but not in the sealed series"
    # 补丁必须排在公开基线之后，且真的改到它该改的那个内核文件。
    for path, row in patches:
        if path.name not in EXPECTED:
            continue
        body = path.read_text()
        target = EXPECTED[path.name]
        assert f"diff --git a/{target} b/{target}" in body, \
            f"{path.name} does not touch {target}"
    # 7.3 没有 stable 合并：整条序列就是封存树，末行的树必须等于 target_tree。
    assert "stable_merged_tree" not in policy, "7.3 policy still claims a stable merge"
    assert patches[-1][1]["tree"] == policy["target_tree"], "series does not end at target_tree"
    # 目录里的每条补丁都要能被准备工具接受；改动一个字节必须被拒绝。
    # 父子链要求整条序列齐全，所以复制整个目录，不挑行。
    with tempfile.TemporaryDirectory(prefix="iris-source-") as directory:
        root = Path(directory).resolve()
        destination = root / SERIES.parent
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(manifest.parent, destination)
        shutil.copyfile(manifest, root / SERIES)
        _, copied_rows, _ = prepare.load_series(root, prepare.DEFAULT_TARGET)
        assert [row["file"] for _, row in copied_rows] == names
        for name in EXPECTED:
            path = root / SERIES.parent / name
            original = path.read_bytes()
            path.write_bytes(original + b"\nchanged\n")
            try:
                prepare.load_series(root, prepare.DEFAULT_TARGET)
            except ValueError:
                pass
            else:
                raise AssertionError("modified series patch accepted: " + name)
            path.write_bytes(original)
    # 与真实准备入口一致：kernel series 要在 apply-patches 里重放。补丁的
    # 目标摘要钉的是上游原文，所以重放必须从 HEAD 的原文开始，而不是从
    # 这个可能已经打过补丁的工作树开始。
    series = json.loads((ROOT / "patches/kernel/series.json").read_text())
    with tempfile.TemporaryDirectory(prefix="iris-apply-") as directory:
        base = Path(directory).resolve()
        (base / "build.sh").write_text("#!/bin/sh\n")
        for row in series["patches"]:
            for relative in list(row.get("targets", {})) + list(row.get("creates", [])):
                original = subprocess.run(["git", "-C", str(sun), "show", f"HEAD:{relative}"],
                                          capture_output=True)
                if original.returncode == 0:
                    (base / relative).parent.mkdir(parents=True, exist_ok=True)
                    (base / relative).write_bytes(original.stdout)
        verify = subprocess.run(
            ["python3", str(ROOT / "scripts/apply-patches.py"), "--sunuefi", str(base),
             "--check", "--only", "kernel"],
            capture_output=True, text=True)
        assert verify.returncode == 0, verify.stderr.strip()
    print("OK: Iris/DPU/amplifier/touch patches are rows of the sealed 7.3 series; "
          "byte changes and dropped rows are rejected")


if __name__ == "__main__":
    main()
