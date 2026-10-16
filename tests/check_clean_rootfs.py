#!/usr/bin/env python3
"""校验基于纯净 DNF 的 RootFS 装配脚本与计划契约。"""

import json
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
        for pkg in EXPECTED_PIANO_PACKAGES:
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
            repo_st = data.get("repo_status", {})
            check(repo_st.get("valid") is True, f"fixture repository must be valid: {repo_st}")
            check(len(repo_st.get("missing", [])) == 0, f"no missing packages allowed: {repo_st.get('missing')}")

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

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1

    print("OK: clean rootfs plan and contract behave as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
