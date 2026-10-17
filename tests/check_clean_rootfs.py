#!/usr/bin/env python3
"""校验基于纯净 DNF 的 RootFS 装配脚本与计划契约。"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "build-clean-rootfs.py"

EXPECTED_PIANO_PACKAGES = {
    "piano-fedora-configs",
    "alsa-xiaomi-piano",
    "piano-camera",
    "xiaomi-piano-thp",
    "sm8750-video-accel",
    "uperf-linux",
    "libssc",
    "iio-sensor-proxy-ssc",
    "xiaomi-piano-firmware",
}

# The device kernel ships from the same local repo. It is listed separately
# because a "clean" rootfs without it boots with no modules at all.
EXPECTED_KERNEL_PACKAGE = "kernel-piano"


def run(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=ROOT
    )


def main():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    # 1. 基础 plan 测试（使用包含模拟 rpm 的 fixture 仓库，确保在无预编译产物的 CI 环境中也能稳定通过）
    with tempfile.TemporaryDirectory() as fixture_dir:
        fixture_path = Path(fixture_dir)
        (fixture_path / "repodata").mkdir(parents=True, exist_ok=True)
        (fixture_path / "repodata" / "repomd.xml").write_text("<repomd/>\n", encoding="utf-8")
        for pkg in EXPECTED_PIANO_PACKAGES | {EXPECTED_KERNEL_PACKAGE}:
            (fixture_path / f"{pkg}-0.1.0-1.fc44.noarch.rpm").touch()

        res = run("--plan", "--releasever", "44", "--rpms-dir", str(fixture_path))
        check(res.returncode == 0, f"plan with fixture should succeed, got {res.returncode}: {res.stderr}")
        if res.returncode == 0:
            data = json.loads(res.stdout)
            check(data.get("status") == "PLAN_READY", "plan status must be PLAN_READY")
            check(data.get("distro") == "fedora", "distro must be fedora")
            check(data.get("releasever") == "44", "releasever must be 44")
            check(data.get("desktop") == "gnome", "desktop must be gnome")
            check(EXPECTED_PIANO_PACKAGES.issubset(set(data.get("piano_packages", []))),
                  "all 9 expected piano RPM packages must be scheduled in plan")
            check(EXPECTED_KERNEL_PACKAGE in data.get("all_packages", []),
                  "kernel-piano must be scheduled in the clean assembly; without it "
                  "the image boots with an empty /usr/lib/modules")
            repo_st = data.get("repo_status", {})
            check(repo_st.get("valid") is True, f"fixture repository must be valid: {repo_st}")
            check(len(repo_st.get("missing", [])) == 0, f"no missing packages allowed: {repo_st.get('missing')}")
            check(EXPECTED_KERNEL_PACKAGE in repo_st.get("packages", {}),
                  "kernel-piano must be resolved from the local RPM repo")

    # 1.1 默认 plan 测试（不传 --rpms-dir，检查返回值与状态结构）
    res_def = run("--plan", "--releasever", "44")
    check(res_def.returncode == 0, f"default plan should succeed, got {res_def.returncode}: {res_def.stderr}")
    if res_def.returncode == 0:
        data_def = json.loads(res_def.stdout)
        check(data_def.get("status") == "PLAN_READY", "default plan status must be PLAN_READY")
        if (ROOT / "out" / "rpms" / "repodata" / "repomd.xml").is_file():
            repo_st_def = data_def.get("repo_status", {})
            check(repo_st_def.get("valid") is True, f"local out/rpms must be valid: {repo_st_def}")

    # 2. 拒绝非法版本号
    check(run("--plan", "--releasever", "rawhide").returncode == 2, "non-numeric releasever must exit 2")

    # 3. 拒绝未知桌面环境
    check(run("--plan", "--desktop", "dde").returncode == 2, "unknown desktop must exit 2")

    # 4. 仓库路径缺失检测
    with tempfile.TemporaryDirectory() as empty_dir:
        res_missing = run("--plan", "--rpms-dir", empty_dir)
        check(res_missing.returncode == 0, "plan with empty repo should exit 0 but mark valid=False")
        if res_missing.returncode == 0:
            data_missing = json.loads(res_missing.stdout)
            check(data_missing.get("repo_status", {}).get("valid") is False,
                  "empty rpms-dir must be reported as invalid")

    # 5. 守护进程依赖校验：piano-ram-hardware-prepare 及其 DMA 模块必须内置于 piano-fedora-configs
    configs_pkg = ROOT / "packages" / "piano-fedora-configs"
    prepare_bin = configs_pkg / "usr" / "lib" / "piano" / "piano-ram-hardware-prepare"
    alias_link = configs_pkg / "usr" / "lib" / "piano" / "piano-disk-hardware-prepare"
    dma_routes = configs_pkg / "usr" / "lib" / "piano" / "piano_dma_routes.py"
    dma_ctx = configs_pkg / "usr" / "lib" / "piano" / "piano_dma_contexts.py"
    spec_file = configs_pkg / "piano-fedora-configs.spec"

    check(prepare_bin.is_file() and os.access(prepare_bin, os.X_OK),
          "piano-ram-hardware-prepare must exist and be executable in piano-fedora-configs")
    check(alias_link.is_symlink() and alias_link.readlink() == Path("piano-ram-hardware-prepare"),
          "piano-disk-hardware-prepare symlink must exist in piano-fedora-configs")
    check(dma_routes.is_file(), "piano_dma_routes.py must exist in piano-fedora-configs")
    check(dma_ctx.is_file(), "piano_dma_contexts.py must exist in piano-fedora-configs")
    if spec_file.is_file():
        spec_text = spec_file.read_text(encoding="utf-8")
        check("piano-ram-hardware-prepare" in spec_text,
              "piano-fedora-configs.spec must package piano-ram-hardware-prepare")
        check("piano-disk-hardware-prepare" in spec_text,
              "piano-fedora-configs.spec must package piano-disk-hardware-prepare")
        # Fedora merged-usr makes /usr/local/sbin a symlink to bin, so shipping
        # both spellings of the same file makes rpm 6 abort the transaction with
        # "file ... conflicts between attempted installs of <pkg> and <pkg>".
        # Only the canonical /usr/local/bin path may appear in %files.
        check("/usr/local/bin/piano-debug-stage" in spec_text,
              "piano-fedora-configs.spec must package piano-debug-stage at /usr/local/bin")
        check("\n/usr/local/sbin/piano-debug-stage\n" not in spec_text,
              "piano-fedora-configs.spec must not list /usr/local/sbin/piano-debug-stage "
              "(merged-usr alias of /usr/local/bin, duplicates the same inode)")
        check("\n/usr/local/sbin/piano-debug-bootstrap\n" not in spec_text,
              "piano-fedora-configs.spec must not list /usr/local/sbin/piano-debug-bootstrap "
              "(merged-usr alias of /usr/local/bin, duplicates the same inode)")

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1

    print("OK: clean rootfs plan and contract behave as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
