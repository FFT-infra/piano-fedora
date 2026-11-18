#!/usr/bin/env python3
"""解析具有全部所需 artifact 的成功 Actions run；过期或失败的 run 不可用。"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True))


def digest_artifact(chain):
    """这条链在当前仓库状态下的 input-digest artifact 名。

    发布链按内容复用上游产物，摘要就是契约。消费方要求上游 run 带着
    这个名字，才能证明它吃的输入和这里宣称的一致；只凭 run 里的产物名
    无法区分「同一个 manifest、不同的补丁树」。
    """
    script = Path(__file__).with_name("build-inputs.py")
    return subprocess.check_output(
        [sys.executable, str(script), "--chain", chain, "--artifact-name"],
        text=True).strip()


def resolve(repository, workflow, names, run_id=None, commit=None):
    # 用官方 workflow_id 比对，display name 可被改名或撞名，不作为身份。
    workflow_id = api(f"repos/{repository}/actions/workflows/{workflow}")["id"]
    if run_id:
        candidates = [api(f"repos/{repository}/actions/runs/{int(run_id)}")]
    else:
        # 按内容复用时不必是最新一次：摘要相同的 run 再旧也能用，扫深一点
        # 免得真正的候选被几十次无关的 host-check 挤出窗口。
        query = "status=success&per_page=100"
        if commit:
            # 发布必须绑定 tag 指向的 commit；同名 artifact 属于别的提交时不能拿来充数。
            query += "&head_sha=" + commit
        candidates = api(f"repos/{repository}/actions/workflows/{workflow}/runs?{query}")["workflow_runs"]
    for run in candidates:
        if run.get("workflow_id") != workflow_id:
            if run_id:
                raise ValueError(
                    f"run {run.get('id')} belongs to workflow {run.get('workflow_id')}, not {workflow}")
            continue
        if commit and run.get("head_sha") != commit:
            if run_id:
                raise ValueError(
                    f"run {run.get('id')} belongs to commit {run.get('head_sha')}, not {commit}")
            continue
        if run["status"] != "completed" or run["conclusion"] != "success":
            continue
        artifacts = api(f"repos/{repository}/actions/runs/{run['id']}/artifacts?per_page=100")["artifacts"]
        available = {a["name"] for a in artifacts if not a["expired"]}
        if set(names) <= available:
            return str(run["id"])
    raise ValueError(f"no successful {workflow} run has nonexpired artifacts {names}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    ap.add_argument("--workflow", required=True)
    ap.add_argument("--artifact", action="append", required=True)
    ap.add_argument("--run-id")
    ap.add_argument("--commit", help="只接受该 commit 上的 run（tag 发布用）")
    ap.add_argument("--require-digest", action="append", default=[], metavar="CHAIN",
                    help="上游 run 必须带这条链的 input-digest artifact")
    ap.add_argument("--output-key", default="run_id")
    args = ap.parse_args()
    if not args.repository:
        raise ValueError("repository is required")
    names = list(args.artifact) + [digest_artifact(chain) for chain in args.require_digest]
    value = resolve(args.repository, args.workflow, names, args.run_id, args.commit)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(args.output_key + "=" + value + "\n")
    print(value)


if __name__ == "__main__":
    main()
