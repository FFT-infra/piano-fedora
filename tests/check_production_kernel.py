#!/usr/bin/env python3
"""正式源码准备无需预编译内核，并与 SDK 重放产生相同身份。"""
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "tests")]
from check_gunyah_source import make_repo, git, SOURCE_FILES, INTEGRATION_FILES, DRIVER_EPOCH, SOURCE_EPOCH


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / file)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def main():
    production = load("production", "prepare-production-kernel.py")
    runtime = load("runtime", "prepare-runtime-kernel.py")
    with tempfile.TemporaryDirectory(prefix="production-kernel-") as folder:
        sun = Path(folder) / "sunuefi"
        pin = make_repo(sun / "upstream/gunyah-driver", SOURCE_FILES, DRIVER_EPOCH)
        source = sun / "build/kernel-worktrees/parent"
        commit = make_repo(source, INTEGRATION_FILES, SOURCE_EPOCH)
        parent = {"worktree": str(source), "actual_commit": commit,
                  "actual_tree": git(["rev-parse", "HEAD^{tree}"], source).strip()}
        directory = sun / "build/release-7.2.9"
        directory.mkdir()
        (directory / "source-manifest.json").write_text(json.dumps(parent))
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == ROOT / "sources.lock.json":
                return json.dumps({"sources": {"gunyah-driver": {"commit": pin}}})
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", read), patch.object(production.subprocess, "run", wraps=production.subprocess.run) as run:
            # Only mock upstream prep: the import and both git commits are real.
            actual_run = run._mock_wraps
            def dispatch(command, **kwargs):
                if command[0] == "python3":
                    return None
                return actual_run(command, **kwargs)
            run.side_effect = dispatch
            result = production.prepare(sun)
            record = {"source_commit": result["actual_commit"], "source_tree": result["actual_tree"]}
            replay = runtime.replay_gunyah_source(sun, parent, record,
                        directory / "kernel-gunyah-source.json", sun / "build/runtime")
            assert replay["actual_tree"] == result["actual_tree"]
            assert replay["actual_commit"] == result["actual_commit"]
            assert (directory / "production-source-manifest.json").is_file()
            # Reruns must preserve the existing source and evidence.
            try:
                production.prepare(sun)
            except ValueError as error:
                assert "already exists" in str(error)
            else:
                raise AssertionError("existing production source was overwritten")
    workflow = (ROOT / ".github/workflows/build-kernel.yml").read_text()
    assert "prepare-production-kernel.py" in workflow
    assert "kernel-gunyah-source.json" in workflow
    assert "experimental" not in workflow
    print("OK: production import without a built kernel, SDK identity replay, output preservation")


if __name__ == "__main__":
    main()
