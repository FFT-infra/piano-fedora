#!/usr/bin/env python3
"""离线回归：多桌面 Release 的 `desktop_runs` 解析。

每个桌面各有一对 build-bundle 与 prepare-install 的 run。解析、去重、未知
桌面与占位档的拒绝都在 `scripts/release-desktops.py`；这里注入假的
resolver，不访问 GitHub。
"""

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/release-desktops.py"


def load():
    loader = importlib.machinery.SourceFileLoader("release_desktops", str(SCRIPT))
    spec = importlib.util.spec_from_loader("release_desktops", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


release_desktops = load()
BUILDABLE = release_desktops.known_desktops()


def fake_resolve(calls):
    def resolve(repository, workflow, names, run_id=None, commit=None):
        calls.append({"workflow": workflow, "names": list(names),
                      "run_id": run_id, "commit": commit})
        if run_id == "missing":
            raise ValueError(f"no successful {workflow} run has nonexpired artifacts {names}")
        return run_id or f"resolved-{workflow.split('-')[1].split('.')[0]}"
    return resolve


def expect_error(spec, fragment, desktop="gnome", known=None):
    calls = []
    try:
        release_desktops.plan(spec=spec, desktop=desktop, known=known or BUILDABLE,
                              commit="a" * 40, repository="o/r",
                              resolve_fn=fake_resolve(calls))
    except release_desktops.ReleaseError as error:
        assert fragment in str(error), f"unexpected error text: {error}"
    else:
        raise AssertionError(f"accepted a bad desktop_runs spec: {spec!r}")
    assert not calls, f"a rejected spec still contacted the resolver: {spec!r}"


def test_every_desktop_pair_is_resolved_independently():
    calls = []
    spec = ",".join(f"{name}=bundle-{name}:prepare-{name}" for name in BUILDABLE)
    rows = release_desktops.plan(spec=spec, known=BUILDABLE, commit="b" * 40,
                                 repository="o/r", resolve_fn=fake_resolve(calls))
    assert [row["desktop"] for row in rows] == list(BUILDABLE), rows
    # 每档两次解析：bundle 带 deploy-bundle，prepare 带三个安装 artifact。
    assert len(calls) == 2 * len(BUILDABLE), calls
    bundle_calls = [c for c in calls if c["workflow"] == "build-bundle.yml"]
    prepare_calls = [c for c in calls if c["workflow"] == "prepare-install.yml"]
    assert len(bundle_calls) == len(prepare_calls) == len(BUILDABLE)
    for call in bundle_calls:
        assert call["names"] == ["deploy-bundle"], call
        assert call["commit"] == "b" * 40, call
    for call in prepare_calls:
        assert set(call["names"]) == {"root-install", "boot-tools", "host-boot-tool"}, call
        assert call["commit"] == "b" * 40, call
    # 注入的 run 原样保留，没写 run id 才交给 resolver 解析。
    assert [row["bundle_run"] for row in rows] == [f"bundle-{n}" for n in BUILDABLE]
    assert [row["prepare_run"] for row in rows] == [f"prepare-{n}" for n in BUILDABLE]


def test_a_failing_run_stops_the_release():
    calls = []
    spec = "gnome=b1:p1,kde=missing:p2"
    try:
        release_desktops.plan(spec=spec, known=BUILDABLE, commit="c" * 40,
                              repository="o/r", resolve_fn=fake_resolve(calls))
    except release_desktops.ReleaseError as error:
        assert error.args[0].startswith("kde:"), error
    else:
        raise AssertionError("a desktop with a missing run was accepted")


def test_bad_specs_are_rejected_before_any_lookup():
    expect_error("gnome=b:p,gnome=c:q", "repeats desktop 'gnome'")
    expect_error("denial=b:p", "unknown or unbuildable desktop 'denial'")
    expect_error("kde-typo=b:p", "unknown or unbuildable desktop 'kde-typo'")
    expect_error("gnome", "is not <desktop>=<bundle_run>:<prepare_run>")
    expect_error("gnome=bp", "needs both a build-bundle and a prepare-install run")
    expect_error("gnome=b:", "needs both a build-bundle and a prepare-install run")
    expect_error("gnome=:p", "needs both a build-bundle and a prepare-install run")
    # 只有逗号/空白也等于空列表；完全留空则走单档默认路径。
    expect_error(" , ", "desktop_runs is empty")


def test_single_desktop_defaults_still_work():
    calls = []
    rows = release_desktops.plan(spec="", desktop="kde", known=BUILDABLE,
                                 commit="d" * 40, repository="o/r",
                                 resolve_fn=fake_resolve(calls))
    assert len(rows) == 1 and rows[0]["desktop"] == "kde", rows
    # 单档不传 run 时，两对 run 都按 commit 解析。
    assert len(calls) == 2 and all(call["run_id"] is None for call in calls), calls
    assert len({call["workflow"] for call in calls}) == 2, calls
    calls = []
    try:
        release_desktops.plan(spec="", desktop="denial", known=BUILDABLE,
                              commit="d" * 40, repository="o/r",
                              resolve_fn=fake_resolve(calls))
    except release_desktops.ReleaseError as error:
        assert "unbuildable" in str(error), error
    else:
        raise AssertionError("the placeholder desktop was accepted as a release target")


def test_cli_writes_the_plan_and_rejects_bad_input():
    help_result = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert help_result.returncode == 0 and "--desktop-runs" in help_result.stdout
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "plan.json"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--desktop-runs", "denial=b:p",
             "--commit", "e" * 40, "--repository", "o/r", "--output", str(output)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert result.returncode == 2, result.stdout
        assert "denial" in result.stdout, result.stdout
        assert not output.exists(), "a rejected plan was written anyway"


def main():
    test_every_desktop_pair_is_resolved_independently()
    test_a_failing_run_stops_the_release()
    test_bad_specs_are_rejected_before_any_lookup()
    test_single_desktop_defaults_still_work()
    test_cli_writes_the_plan_and_rejects_bad_input()
    print(f"OK: desktop_runs 每档独立解析，未知/占位/重复档位与缺失 run 都被拒绝（{','.join(BUILDABLE)}）")


if __name__ == "__main__":
    main()
