#!/usr/bin/env python3
"""用真实 git 派生树验证实验 runtime 的源码重放及拒绝路径。"""
import copy
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
from check_gunyah_source import (DRIVER_EPOCH, INTEGRATION_FILES, SOURCE_EPOCH,
                                 SOURCE_FILES, git, make_repo, prep)


def main():
    spec = importlib.util.spec_from_file_location("runtime_kernel", ROOT / "scripts/prepare-runtime-kernel.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    with tempfile.TemporaryDirectory(prefix="runtime-source-") as directory:
        base = Path(directory)
        sun = base / "sunuefi"
        driver = sun / "upstream/gunyah-driver"
        pin = make_repo(driver, SOURCE_FILES, DRIVER_EPOCH)
        source = base / "kernel-source"
        commit = make_repo(source, INTEGRATION_FILES, SOURCE_EPOCH)
        tree = git(["rev-parse", "HEAD^{tree}"], source).strip()
        parent = {"source_commit": commit, "source_tree": tree}
        plan = prep.build_plan(parent, source, driver, base / "original", pin)
        original = prep.execute_plan(plan)
        proof_path = base / "source.json"
        proof_path.write_text(json.dumps(original))
        prepared = {"actual_commit": commit, "actual_tree": tree, "worktree": str(source)}
        record = {"source_commit": original["derived"]["commit"],
                  "source_tree": original["derived"]["tree"]}
        read_text = Path.read_text

        def fixture_lock(path, *args, **kwargs):
            if path == ROOT / "sources.lock.json":
                return json.dumps({"sources": {"gunyah-driver": {"commit": pin}}})
            return read_text(path, *args, **kwargs)

        with patch.object(Path, "read_text", fixture_lock):
            result = runtime.replay_gunyah_source(sun, prepared, record, proof_path, base / "runtime")
            assert result["actual_commit"] == record["source_commit"]
            assert result["actual_tree"] == record["source_tree"]
            assert git(["status", "--porcelain"], Path(result["worktree"])).strip() == ""
            for key in ("parent_kernel", "driver_source", "derived"):
                bad = copy.deepcopy(original)
                bad[key]["commit"] = "0" * 40
                proof_path.write_text(json.dumps(bad))
                output = base / ("reject-" + key)
                try:
                    runtime.replay_gunyah_source(sun, prepared, record, proof_path, output)
                except ValueError:
                    assert not output.exists(), "错误身份在拒绝前创建了 worktree"
                else:
                    raise AssertionError("错误源码身份被接受: " + key)
            bad = copy.deepcopy(original)
            bad["files"][0]["source_sha256"] = "0" * 64
            proof_path.write_text(json.dumps(bad))
            try:
                runtime.replay_gunyah_source(sun, prepared, record, proof_path, base / "bad-file")
            except ValueError:
                pass
            else:
                raise AssertionError("伪造的文件摘要清单被接受")
        assert git(["status", "--porcelain"], source).strip() == ""
        assert git(["status", "--porcelain"], driver).strip() == ""
    print("OK: experimental runtime source replay, identity/hash rejection, clean inputs")


if __name__ == "__main__":
    main()
