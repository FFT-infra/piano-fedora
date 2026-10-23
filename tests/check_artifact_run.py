#!/usr/bin/env python3
"""离线回归：resolve-artifact-run.py 必须把 run 绑到请求的工作流。

用 mock GitHub API 证明「同名 artifact 但属于另一工作流的 run」不会被接受，
同时保持动态选择与失败/过期 artifact 拒绝。测试不访问网络。
"""

import importlib.util
import os
import re
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "resolve_artifact_run", ROOT / "scripts/resolve-artifact-run.py")
resolve_artifact_run = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(resolve_artifact_run)


class FakeGitHub:
    """最小 gh api 路由。workflows/runs/artifacts 都是显式注入的固定状态。"""

    def __init__(self, workflows, runs, artifacts):
        self.workflows = workflows  # 文件名 -> {"id": int}
        self.runs = runs            # run id(int) -> run dict，含 workflow_id
        self.artifacts = artifacts  # run id(int) -> [{"name":.., "expired":..}]
        self.calls = []

    def __call__(self, path):
        self.calls.append(path)
        if "/actions/workflows/" in path and "/runs" not in path:
            return self.workflows[path.rsplit("/actions/workflows/", 1)[1]]
        if "/actions/workflows/" in path and "/runs?" in path:
            workflow = path.split("/actions/workflows/", 1)[1].split("/runs?", 1)[0]
            query = parse_qs(path.split("/runs?", 1)[1])
            wanted = self.workflows[workflow]["id"]
            runs = [r for r in self.runs.values() if r["workflow_id"] == wanted]
            if "head_sha" in query:
                runs = [r for r in runs if r.get("head_sha") == query["head_sha"][0]]
            return {"workflow_runs": runs}
        if "/actions/runs/" in path and path.endswith("/artifacts?per_page=100"):
            run = int(path.split("/actions/runs/", 1)[1].split("/artifacts", 1)[0])
            return {"artifacts": self.artifacts.get(run, [])}
        if "/actions/runs/" in path:
            return self.runs[int(path.rsplit("/actions/runs/", 1)[1])]
        raise AssertionError("unexpected api path: " + path)


def run(**kwargs):
    return {"id": kwargs["id"], "workflow_id": kwargs["workflow_id"],
            "head_sha": kwargs.get("head_sha"),
            "status": kwargs.get("status", "completed"),
            "conclusion": kwargs.get("conclusion", "success")}


def artifact(name, expired=False):
    return {"name": name, "expired": expired}


def expect_raises(backend, workflow, names, run_id, fragment, commit=None):
    resolve_artifact_run.api = backend
    try:
        resolve_artifact_run.resolve("o/r", workflow, names, run_id, commit)
    except ValueError as error:
        assert fragment in str(error), f"unexpected error text: {error}"
    else:
        raise AssertionError(f"accepted a run that should have been rejected: {workflow}/{run_id}")


def test_wrong_workflow_same_artifact_is_rejected():
    # build-image.yml 的 workflow_id 是 100；run 200 属于 build-kernel.yml(101)，
    # 但恰好带同名 artifact root-image-8192。旧实现只看 artifact 名就会接受。
    backend = FakeGitHub(
        workflows={"build-image.yml": {"id": 100}, "build-kernel.yml": {"id": 101}},
        runs={200: run(id=200, workflow_id=101)},
        artifacts={200: [artifact("root-image-8192")]})
    expect_raises(backend, "build-image.yml", ["root-image-8192"], "200", "workflow")


def test_explicit_run_id_matching_workflow_returns_str():
    backend = FakeGitHub(
        workflows={"build-image.yml": {"id": 100}},
        runs={200: run(id=200, workflow_id=100)},
        artifacts={200: [artifact("root-image-8192"), artifact("source-runs.json")]})
    resolve_artifact_run.api = backend
    value = resolve_artifact_run.resolve(
        "o/r", "build-image.yml", ["root-image-8192", "source-runs.json"], "200")
    assert value == "200" and isinstance(value, str), value


def test_explicit_run_id_rejects_expired_artifact():
    backend = FakeGitHub(
        workflows={"build-image.yml": {"id": 100}},
        runs={200: run(id=200, workflow_id=100)},
        artifacts={200: [artifact("root-image-8192", expired=True)]})
    expect_raises(backend, "build-image.yml", ["root-image-8192"], "200", "nonexpired")


def test_explicit_run_id_rejects_failed_run():
    backend = FakeGitHub(
        workflows={"build-image.yml": {"id": 100}},
        runs={200: run(id=200, workflow_id=100, conclusion="failure")},
        artifacts={200: [artifact("root-image-8192")]})
    expect_raises(backend, "build-image.yml", ["root-image-8192"], "200", "nonexpired")


def test_dynamic_selection_skips_wrong_workflow_failed_and_expired():
    backend = FakeGitHub(
        workflows={"build-image.yml": {"id": 100}},
        runs={200: run(id=200, workflow_id=101),
              201: run(id=201, workflow_id=100, conclusion="failure"),
              202: run(id=202, workflow_id=100, status="in_progress", conclusion=None),
              203: run(id=203, workflow_id=100),
              204: run(id=204, workflow_id=100)},
        artifacts={200: [artifact("root-image-8192")],
                   201: [artifact("root-image-8192")],
                   202: [artifact("root-image-8192")],
                   203: [artifact("root-image-8192", expired=True)],
                   204: [artifact("root-image-8192")]})
    resolve_artifact_run.api = backend
    value = resolve_artifact_run.resolve("o/r", "build-image.yml", ["root-image-8192"])
    assert value == "204", value


def test_dynamic_selection_without_match_raises():
    backend = FakeGitHub(
        workflows={"build-image.yml": {"id": 100}},
        runs={201: run(id=201, workflow_id=100, conclusion="failure")},
        artifacts={201: [artifact("root-image-8192")]})
    expect_raises(backend, "build-image.yml", ["root-image-8192"], None, "nonexpired")


def test_commit_scoped_selection_skips_other_commits():
    """tag 发布只接受 tag 指向提交上的 run；别的提交有同名 artifact 也不能用。"""
    backend = FakeGitHub(
        workflows={"build-bundle.yml": {"id": 100}},
        runs={200: run(id=200, workflow_id=100, head_sha="a" * 40),
              201: run(id=201, workflow_id=100, head_sha="b" * 40)},
        artifacts={200: [artifact("deploy-bundle-kde")],
                   201: [artifact("deploy-bundle-kde")]})
    resolve_artifact_run.api = backend
    value = resolve_artifact_run.resolve(
        "o/r", "build-bundle.yml", ["deploy-bundle-kde"], commit="b" * 40)
    assert value == "201", value


def test_explicit_run_id_rejects_commit_mismatch():
    backend = FakeGitHub(
        workflows={"build-bundle.yml": {"id": 100}},
        runs={200: run(id=200, workflow_id=100, head_sha="a" * 40)},
        artifacts={200: [artifact("deploy-bundle-kde")]})
    expect_raises(backend, "build-bundle.yml", ["deploy-bundle-kde"], "200",
                  "belongs to commit", commit="b" * 40)


def test_cli_writes_output_key_and_prints_run_id():
    backend = FakeGitHub(
        workflows={"build-image.yml": {"id": 100}},
        runs={200: run(id=200, workflow_id=100)},
        artifacts={200: [artifact("root-image-8192")]})
    resolve_artifact_run.api = backend
    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "github-output"
        previous_argv, previous_env = sys.argv, dict(os.environ)
        try:
            sys.argv = ["resolve-artifact-run.py", "--repository", "o/r",
                        "--workflow", "build-image.yml", "--artifact", "root-image-8192",
                        "--run-id", "200", "--output-key", "root_run"]
            os.environ["GITHUB_OUTPUT"] = str(output)
            resolve_artifact_run.main()
            assert output.read_text() == "root_run=200\n", output.read_text()
        finally:
            sys.argv = previous_argv
            os.environ.clear()
            os.environ.update(previous_env)


def test_every_resolved_workflow_is_registered_on_default_branch():
    """workflow_dispatch only appears for workflows that exist on main.

    A release job cannot request build-piano-rpms.yml directly while that file
    exists only on the PR branch: GitHub returns 404 and the first release step
    fails. Reusable workflows must be invoked from a workflow registered on the
    default branch, and downstream artifact lookups must name that registered
    workflow.
    """
    default = {"host-check.yml", "build-kernel.yml", "build-device-runtime.yml",
               "build-mesa.yml", "build-rootfs.yml", "build-image.yml",
               "build-uefi.yml", "build-bundle.yml", "prepare-install.yml",
               "package-install.yml", "build-ffmpeg.yml", "verify-f2fs.yml"}
    reusable = {"build-ffmpeg.yml", "build-piano-rpms.yml", "package-install.yml"}
    failures = []
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        text = path.read_text()
        for workflow in set(re.findall(
                r"resolve-artifact-run\.py[^\n]*(?:\\\n[^\n]*)*?--workflow\s+([A-Za-z0-9_.-]+\.yml)",
                text)):
            if workflow not in default:
                failures.append(f"{path.name} resolves unregistered workflow {workflow}")
    host = (ROOT / ".github/workflows/host-check.yml").read_text()
    if "uses: ./.github/workflows/build-piano-rpms.yml" not in host:
        failures.append("host-check.yml does not invoke the reusable RPM matrix")
    if "  piano-rpms:\n" not in host:
        failures.append("host-check.yml has no piano-rpms job for the artifact")
    rpms = (ROOT / ".github/workflows/build-piano-rpms.yml").read_text()
    if "workflow_call:" not in rpms:
        failures.append("build-piano-rpms.yml is not reusable from the registered host-check workflow")
    rootfs = (ROOT / ".github/workflows/build-rootfs.yml").read_text()
    resolve_step = rootfs.split("Resolve the exact RPM inputs", 1)[1].split("Install podman", 1)[0]
    if "--workflow host-check.yml" not in resolve_step or "--workflow build-piano-rpms.yml" in resolve_step:
        failures.append("build-rootfs.yml must resolve piano-rpms from the registered host-check run")
    # build-kernel publishes modules under modules/lib/modules. The matrix must
    # accept both layouts, otherwise the job dies before any RPM is built.
    unpack = rpms.split("Unpack the matching kernel payload", 1)[1].split("- name:", 1)[0]
    if "modules/lib/modules" not in unpack:
        failures.append("build-piano-rpms.yml rejects the build-kernel modules layout")
    # build-rpms.sh fetches remote Source0 with spectool, which ships in
    # rpmdevtools. Without it the matrix dies on the first remote-source spec.
    if "rpmdevtools" not in rpms:
        failures.append("build-piano-rpms.yml does not install spectool (rpmdevtools)")
    # build-rootfs runs on an Ubuntu runner that has no dnf, and the three
    # component manifests live inside their tarballs. Both mistakes made the
    # step die without running anything.
    build_step = rootfs.split("Build the audited clean rootfs", 1)[1].split("- name:", 1)[0]
    if "podman run" not in build_step or "build-clean-rootfs.py" not in build_step:
        failures.append("build-rootfs.yml must run dnf inside the Fedora image")
    if build_step.index("tar -I zstd") > build_step.index("runtime-extracted/manifest.json"):
        failures.append("build-rootfs.yml checks the manifest before extracting it")
    if "rm -rf build/local-rpms/repodata" not in build_step:
        failures.append("build-rootfs.yml updates stale repodata from the artifact")
    if '--privileged' not in build_step:
        failures.append("build-rootfs.yml cannot write security.selinux without --privileged")
    # record["output"] is a container path; the host assertion must use $OUT.
    if 'pathlib.Path(record["output"])' in build_step:
        failures.append("build-rootfs.yml asserts against the container path")
    if failures:
        raise AssertionError("; ".join(failures))


def test_per_desktop_artifacts_do_not_match_each_other():
    """四档各上传一份 artifact，档位写进名字里。

    旧布局是四档共用一个名为 deploy-bundle 的 artifact，只能靠手填 run 区分。
    现在名字带档位，解析就按名字走：只要 deploy-bundle 的 run 不能给 kde 用，
    两个档位的 run 也不会互相顶替。
    """
    backend = FakeGitHub(
        workflows={"build-bundle.yml": {"id": 100}},
        runs={200: run(id=200, workflow_id=100, head_sha="a" * 40)},
        artifacts={200: [artifact("deploy-bundle")]})
    resolve_artifact_run.api = backend
    try:
        resolve_artifact_run.resolve(
            "o/r", "build-bundle.yml", ["deploy-bundle-kde"], commit="a" * 40)
    except ValueError as error:
        assert "deploy-bundle-kde" in str(error), error
    else:
        raise AssertionError("an unsuffixed deploy-bundle satisfied a per-desktop lookup")

    backend = FakeGitHub(
        workflows={"build-bundle.yml": {"id": 100}},
        runs={200: run(id=200, workflow_id=100, head_sha="a" * 40)},
        artifacts={200: [artifact("deploy-bundle-kde")]})
    resolve_artifact_run.api = backend
    assert resolve_artifact_run.resolve(
        "o/r", "build-bundle.yml", ["deploy-bundle-kde"], commit="a" * 40) == "200"
    try:
        resolve_artifact_run.resolve(
            "o/r", "build-bundle.yml", ["deploy-bundle-gnome"], commit="a" * 40)
    except ValueError:
        pass
    else:
        raise AssertionError("the kde bundle satisfied a gnome lookup")


def test_workflows_upload_and_download_per_desktop_artifacts():
    """上传名与下载名必须一起改，只改一边会让下一步拿不到文件。"""
    bundle = (ROOT / ".github/workflows/build-bundle.yml").read_text()
    prepare = (ROOT / ".github/workflows/prepare-install.yml").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()
    pkg = (ROOT / ".github/workflows/package-install.yml").read_text()
    assert "name: deploy-bundle-${{ inputs.desktop }}" in bundle, (
        "build-bundle.yml must name its artifact after the desktop")
    assert "name: root-install-${{ inputs.desktop }}" in prepare
    assert "name: deploy-bundle-${{ inputs.desktop }}" in prepare, (
        "prepare-install.yml must download the per-desktop bundle")
    assert '--name "deploy-bundle-${desktop}"' in release
    assert '--name "root-install-${desktop}"' in release
    assert "name: root-install-${{ inputs.desktop }}" in pkg
    # run 标题带档位，Actions 列表与 gh run list 一眼能分出四档。
    assert "run-name: build-bundle ${{ inputs.desktop }}" in bundle
    assert "run-name: prepare-install ${{ inputs.desktop }}" in prepare
    # 没有一处还按旧名下载。
    for text, workflow in ((bundle, "build-bundle"), (prepare, "prepare-install"),
                           (release, "release"), (pkg, "package-install")):
        assert '--name deploy-bundle\n' not in text.replace(" ", " "), workflow
        assert "name: deploy-bundle\n" not in text, workflow


def main():
    for check in (test_wrong_workflow_same_artifact_is_rejected,
                  test_explicit_run_id_matching_workflow_returns_str,
                  test_explicit_run_id_rejects_expired_artifact,
                  test_explicit_run_id_rejects_failed_run,
                  test_dynamic_selection_skips_wrong_workflow_failed_and_expired,
                  test_dynamic_selection_without_match_raises,
                  test_commit_scoped_selection_skips_other_commits,
                  test_explicit_run_id_rejects_commit_mismatch,
                  test_cli_writes_output_key_and_prints_run_id,
                  test_per_desktop_artifacts_do_not_match_each_other,
                  test_workflows_upload_and_download_per_desktop_artifacts,
                  test_every_resolved_workflow_is_registered_on_default_branch):
        check()
    print("OK: artifact run resolution binds to the requested workflow and rejects bad inputs")


if __name__ == "__main__":
    main()
