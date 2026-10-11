#!/usr/bin/env python3
"""把实际成功 UEFI 构建的源码、补丁及输出摘要封入部署清单。"""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from piano_artifacts import sha256, uefi_patch_identities, verify_uefi_source
from importlib.util import spec_from_file_location, module_from_spec

ROOT = Path(__file__).resolve().parents[1]
spec = spec_from_file_location("artifact_runs", ROOT / "scripts/resolve-artifact-run.py")
runs = module_from_spec(spec)
spec.loader.exec_module(runs)


def seal(repository, run_id, image):
    run_id = runs.resolve(repository, "build-uefi.yml", ["uefi-product"], run_id)
    run = runs.api(f"repos/{repository}/actions/runs/{run_id}")
    head = run["head_sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("invalid UEFI producer commit")
    expected = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    # 旧 artifact 未封存源码身份时，使用原构建的不可变提交及真实日志回收证据。
    # 不从当前 main、可变 ref 名或二进制文件名猜它的源码。
    logs = subprocess.check_output(["gh", "run", "view", run_id, "-R", repository, "--log"], text=True)
    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", logs)
    commits = set(re.findall(r"(?m)(?:^|[\t ])sunuefi at ([0-9a-f]{40})\r?$", clean))
    if commits != {expected}:
        raise ValueError("UEFI checkout log differs from the pinned SunUEFI source")
    digest = sha256(image)
    if not re.search(r'"sha256"\s*:\s*"' + digest + r'"', clean):
        raise ValueError("UEFI image digest is absent from its successful build log")
    patches = uefi_patch_identities(ROOT)
    for name, wanted in patches.items():
        response = runs.api(f"repos/{repository}/contents/{name}?ref={head}")
        if response.get("encoding") != "base64":
            raise ValueError("cannot read the immutable UEFI patch source: " + name)
        actual = hashlib.sha256(base64.b64decode(response["content"])).hexdigest()
        if actual != wanted:
            raise ValueError("UEFI producer used different project patches: " + name)
    record = {"schema_version": 1, "run_id": run_id, "repository": repository,
              "workflow": ".github/workflows/build-uefi.yml", "producer_commit": head,
              "sunuefi_commit": expected, "project_source_sha256": patches,
              "product": {"sha256": digest, "bytes": image.stat().st_size},
              "evidence": {"kind": "successful-actions-build-log",
                           "log_sha256": hashlib.sha256(logs.encode()).hexdigest()},
              "device_tested": False}
    verify_uefi_source(ROOT, image, record, run_id)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not args.repository:
        raise ValueError("repository is required")
    record = seal(args.repository, args.run_id, args.image)
    with args.output.open("x") as stream:
        stream.write(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"uefi_run": record["run_id"], "sunuefi_commit": record["sunuefi_commit"],
                      "product_sha256": record["product"]["sha256"], "device_tested": False}))


if __name__ == "__main__":
    main()
