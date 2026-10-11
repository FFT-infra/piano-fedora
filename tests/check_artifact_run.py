#!/usr/bin/env python3
"""离线回归：resolve-artifact-run.py 必须把 run 绑到请求的工作流。

用 mock GitHub API 证明「同名 artifact 但属于另一工作流的 run」不会被接受，
同时保持动态选择与失败/过期 artifact 拒绝。测试不访问网络。
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

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
        if path.endswith("/runs?status=success&per_page=50"):
            workflow = path.split("/actions/workflows/", 1)[1].split("/runs", 1)[0]
            wanted = self.workflows[workflow]["id"]
            return {"workflow_runs": [r for r in self.runs.values() if r["workflow_id"] == wanted]}
        if "/actions/runs/" in path and path.endswith("/artifacts?per_page=100"):
            run = int(path.split("/actions/runs/", 1)[1].split("/artifacts", 1)[0])
            return {"artifacts": self.artifacts.get(run, [])}
        if "/actions/runs/" in path:
            return self.runs[int(path.rsplit("/actions/runs/", 1)[1])]
        raise AssertionError("unexpected api path: " + path)


def run(**kwargs):
    return {"id": kwargs["id"], "workflow_id": kwargs["workflow_id"],
            "status": kwargs.get("status", "completed"),
            "conclusion": kwargs.get("conclusion", "success")}


def artifact(name, expired=False):
    return {"name": name, "expired": expired}


def expect_raises(backend, workflow, names, run_id, fragment):
    resolve_artifact_run.api = backend
    try:
        resolve_artifact_run.resolve("o/r", workflow, names, run_id)
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


def main():
    for check in (test_wrong_workflow_same_artifact_is_rejected,
                  test_explicit_run_id_matching_workflow_returns_str,
                  test_explicit_run_id_rejects_expired_artifact,
                  test_explicit_run_id_rejects_failed_run,
                  test_dynamic_selection_skips_wrong_workflow_failed_and_expired,
                  test_dynamic_selection_without_match_raises,
                  test_cli_writes_output_key_and_prints_run_id):
        check()
    print("OK: artifact run resolution binds to the requested workflow and rejects bad inputs")


if __name__ == "__main__":
    main()
