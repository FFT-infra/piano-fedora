#!/usr/bin/env python3
"""准备正式 Fedora 内核：重放 SunUEFI/Iris 源码后原样导入锁定的 Gunyah。"""

import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def prepare(sun):
    sun = sun.resolve()
    subprocess.run(["python3", sun / "tools/prepare_release_kernel.py", "--refresh"],
                   cwd=sun, check=True)
    directory = sun / "build/release-7.2.9"
    parent = json.loads((directory / "source-manifest.json").read_text())
    pin = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["gunyah-driver"]["commit"]
    spec = importlib.util.spec_from_file_location("gunyah", ROOT / "scripts/prepare-gunyah-source.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = Path(parent["worktree"])
    output = sun / "build/kernel-worktrees" / ("production-" + parent["actual_tree"][:12] + "-" + pin[:12])
    identity = {"source_commit": parent["actual_commit"], "source_tree": parent["actual_tree"]}
    # 现有输出不覆盖；输入和输出分别保留，便于失败后复查。
    if output.exists():
        raise ValueError("production worktree already exists; use a fresh build checkout: " + str(output))
    plan = module.build_plan(identity, source, sun / "upstream/gunyah-driver", output, pin)
    proof = module.execute_plan(plan)
    (directory / "kernel-gunyah-source.json").write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")
    result = {**parent, "worktree": str(output), "actual_commit": proof["derived"]["commit"],
              "actual_tree": proof["derived"]["tree"], "gunyah_driver_commit": pin}
    (directory / "production-source-manifest.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2))
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sunuefi", type=Path, required=True)
    prepare(ap.parse_args().sunuefi)


if __name__ == "__main__":
    main()
