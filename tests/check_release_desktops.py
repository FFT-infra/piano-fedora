#!/usr/bin/env python3
"""离线回归：Release 的桌面绑定解析。

发布默认不填 run id：`--desktop all` 把每一档放进去，run 按 tag 的 commit
解析。`desktop_runs` 是要手填时的入口。解析、去重、未知桌面与占位档的拒绝
都在 `scripts/release-desktops.py`；这里注入假的 resolver，不访问 GitHub。
"""

import importlib.machinery
import importlib.util
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


def expect_error(spec, fragment, desktop="gnome", known=None, commit="a" * 40):
    calls = []
    try:
        release_desktops.plan(spec=spec, desktop=desktop, known=known or BUILDABLE,
                              commit=commit, repository="o/r",
                              resolve_fn=fake_resolve(calls))
    except release_desktops.ReleaseError as error:
        assert fragment in str(error), f"unexpected error text: {error}"
    else:
        raise AssertionError(f"accepted a bad desktop_runs spec: {spec!r}")
    assert not calls, f"a rejected spec still contacted the resolver: {spec!r}"


def test_all_desktops_resolve_by_commit_with_no_run_ids():
    """默认路径：没有 desktop_runs，每档各解析一对 run，全部绑在同一个 commit。"""
    calls = []
    rows = release_desktops.plan(spec="", desktop="all", known=BUILDABLE,
                                 commit="b" * 40, repository="o/r",
                                 resolve_fn=fake_resolve(calls))
    assert [row["desktop"] for row in rows] == list(BUILDABLE), rows
    assert len(calls) == 2 * len(BUILDABLE), calls
    bundle_calls = [c for c in calls if c["workflow"] == "build-bundle.yml"]
    prepare_calls = [c for c in calls if c["workflow"] == "prepare-install.yml"]
    assert len(bundle_calls) == len(prepare_calls) == len(BUILDABLE)
    # artifact 名带档位：四档共用一份 deploy-bundle 的旧假设已经不存在。
    for call, desktop in zip(bundle_calls, BUILDABLE):
        assert call["names"] == [f"deploy-bundle-{desktop}"], call
        assert call["run_id"] is None and call["commit"] == "b" * 40, call
    for call, desktop in zip(prepare_calls, BUILDABLE):
        assert set(call["names"]) == {f"root-install-{desktop}", "boot-tools",
                                      "host-boot-tool"}, call
        assert call["run_id"] is None and call["commit"] == "b" * 40, call


def test_explicit_run_ids_are_kept_and_still_bound_to_the_commit():
    calls = []
    spec = ",".join(f"{name}=bundle-{name}:prepare-{name}" for name in BUILDABLE)
    rows = release_desktops.plan(spec=spec, known=BUILDABLE, commit="c" * 40,
                                 repository="o/r", resolve_fn=fake_resolve(calls))
    assert [row["bundle_run"] for row in rows] == [f"bundle-{n}" for n in BUILDABLE]
    assert [row["prepare_run"] for row in rows] == [f"prepare-{n}" for n in BUILDABLE]
    assert len(calls) == 2 * len(BUILDABLE), calls
    assert all(call["commit"] == "c" * 40 for call in calls), calls


def test_entries_without_run_ids_resolve_by_commit():
    """`desktop_runs` 里只写档名也是合法的：那一档交给 commit 解析。"""
    calls = []
    rows = release_desktops.plan(spec="gnome,kde", known=BUILDABLE, commit="d" * 40,
                                 repository="o/r", resolve_fn=fake_resolve(calls))
    assert [row["desktop"] for row in rows] == ["gnome", "kde"], rows
    assert all(call["run_id"] is None for call in calls), calls


def test_a_failing_run_stops_the_release():
    calls = []
    try:
        release_desktops.plan(spec="gnome=b1:p1,kde=missing:p2", known=BUILDABLE,
                              commit="e" * 40, repository="o/r",
                              resolve_fn=fake_resolve(calls))
    except release_desktops.ReleaseError as error:
        assert error.args[0].startswith("kde:"), error
    else:
        raise AssertionError("a desktop with a missing run was accepted")


def test_bad_specs_are_rejected_before_any_lookup():
    expect_error("gnome=b:p,gnome=c:q", "repeats desktop 'gnome'")
    expect_error("denial=b:p", "unknown or unbuildable desktop 'denial'")
    expect_error("kde-typo=b:p", "unknown or unbuildable desktop 'kde-typo'")
    expect_error("gnome=bp", "needs both a build-bundle and a prepare-install run")
    expect_error("gnome=b:", "needs both a build-bundle and a prepare-install run")
    expect_error("gnome=:p", "needs both a build-bundle and a prepare-install run")
    # 只有逗号/空白也等于空列表；完全留空则走单档默认路径。
    expect_error(" , ", "desktop_runs is empty")


def test_missing_commit_is_refused():
    """没有 commit 时 resolver 会扫最近 50 个成功 run，发布不能走那条捷径。"""
    calls = []
    try:
        release_desktops.plan(spec="", desktop="all", known=BUILDABLE,
                              commit="", repository="o/r",
                              resolve_fn=fake_resolve(calls))
    except release_desktops.ReleaseError as error:
        assert "tag commit" in str(error), error
    else:
        raise AssertionError("resolved runs without a commit")
    assert not calls, "contacted the resolver without a commit"


def test_single_desktop_defaults_still_work():
    calls = []
    rows = release_desktops.plan(spec="", desktop="kde", known=BUILDABLE,
                                 commit="f" * 40, repository="o/r",
                                 resolve_fn=fake_resolve(calls))
    assert len(rows) == 1 and rows[0]["desktop"] == "kde", rows
    assert len(calls) == 2 and all(call["run_id"] is None for call in calls), calls
    try:
        release_desktops.plan(spec="", desktop="denial", known=BUILDABLE,
                              commit="f" * 40, repository="o/r",
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
    test_all_desktops_resolve_by_commit_with_no_run_ids()
    test_explicit_run_ids_are_kept_and_still_bound_to_the_commit()
    test_entries_without_run_ids_resolve_by_commit()
    test_a_failing_run_stops_the_release()
    test_bad_specs_are_rejected_before_any_lookup()
    test_missing_commit_is_refused()
    test_single_desktop_defaults_still_work()
    test_cli_writes_the_plan_and_rejects_bad_input()
    print(f"OK: 默认不填 run id，每档按 tag commit 解析（{','.join(BUILDABLE)}）")


if __name__ == "__main__":
    main()
