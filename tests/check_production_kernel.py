#!/usr/bin/env python3
"""正式源码准备只重放 7.3 的封存序列，不再导入第二份 Gunyah 驱动。"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    production = load("production", "prepare-production-kernel.py")
    with tempfile.TemporaryDirectory(prefix="production-kernel-") as folder:
        sun = Path(folder) / "sunuefi"
        directory = sun / "build/release"
        directory.mkdir(parents=True)
        # 准备工具在这里只被 mock：本测试核对的是它之后那一步写什么。
        parent = {"status": "SOURCE_PREPARED_NOT_BUILT", "actual_commit": "a" * 40,
                  "actual_tree": "b" * 40, "worktree": str(sun / "build/kernel-worktrees/release-bbb"),
                  "kernel_version": "7.3.0"}
        (directory / "source-manifest.json").write_text(json.dumps(parent))
        with patch.object(production.subprocess, "run", return_value=None) as run:
            result = production.prepare(sun)
        command = run.call_args.args[0]
        assert command[0] == "python3" and str(command[1]).endswith("prepare_release_kernel.py"), command
        # 派生树必须原样沿用准备工具的身份：这里不再有第二遍提交。
        assert result["actual_commit"] == parent["actual_commit"]
        assert result["actual_tree"] == parent["actual_tree"]
        assert result["worktree"] == parent["worktree"]
        assert not (directory / "kernel-gunyah-source.json").exists(), \
            "7.3 embeds Gunyah in the sealed series; there is no second import to prove"
        manifest = json.loads((directory / "production-source-manifest.json").read_text())
        assert manifest == result
    workflow = (ROOT / ".github/workflows/build-kernel.yml").read_text()
    assert "prepare-production-kernel.py" in workflow
    # 序列自带 0006-gunyah，工作流不得再单独取厂商分支或搬运那份证明。
    assert "gunyah-driver" not in workflow
    assert "kernel-gunyah-source.json" not in workflow
    assert "experimental" not in workflow
    lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
    assert "gunyah-driver" not in lock, "the Gunyah driver pin outlived its only consumer"
    assert not (ROOT / "scripts/prepare-gunyah-source.py").exists()
    print("OK: production source is the replayed 7.3 series, with no second Gunyah import")


if __name__ == "__main__":
    main()
