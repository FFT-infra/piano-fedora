#!/usr/bin/env python3
"""从锁定的上游分支原样导入已适配主线 7.2 的 Gunyah 驱动，准备派生内核 worktree。

默认只打印计划且零文件写入；--execute 才创建 worktree、落文件并提交。
输入 checkout 与封存内核产物不被修改，提交只发生在新 worktree 的 detached HEAD。
这里不改写任何驱动字节：上游分支本身就是适配后的形态，逐文件搬运即可。
"""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from piano_artifacts import inspect_kernel

ROOT = Path(__file__).resolve().parents[1]

SOURCE_LOCK_KEY = "gunyah-driver"
AUTHOR_NAME = "Piano Fedora Test"
AUTHOR_EMAIL = "dev@piano.invalid"
STATUS = "PREPARED_NOT_BUILT_NOT_DEVICE_VERIFIED"

IMPORT_FILES = (
    "Documentation/devicetree/bindings/firmware/gunyah-cma-mem.yaml",
    "Documentation/devicetree/bindings/firmware/gunyah-hypervisor.yaml",
    "Documentation/virt/gunyah/index.rst",
    "Documentation/virt/gunyah/message-queue.rst",
    "Documentation/virt/gunyah/port_adaptations.md",
    "arch/arm64/gunyah/Makefile",
    "arch/arm64/gunyah/gunyah_hypercall.c",
    "arch/arm64/gunyah/timer.c",
    "arch/arm64/include/asm/gunyah.h",
    "drivers/virt/gunyah/Kconfig",
    "drivers/virt/gunyah/Makefile",
    "drivers/virt/gunyah/gunyah.c",
    "drivers/virt/gunyah/gunyah_ioeventfd.c",
    "drivers/virt/gunyah/gunyah_irqfd.c",
    "drivers/virt/gunyah/gunyah_platform_hooks.c",
    "drivers/virt/gunyah/gunyah_qcom.c",
    "drivers/virt/gunyah/gunyah_qtvm.c",
    "drivers/virt/gunyah/gunyah_vcpu.c",
    "drivers/virt/gunyah/gunyah_vendor_hooks.h",
    "drivers/virt/gunyah/rsc_mgr.c",
    "drivers/virt/gunyah/rsc_mgr.h",
    "drivers/virt/gunyah/rsc_mgr_rpc.c",
    "drivers/virt/gunyah/trace_vcpu.h",
    "drivers/virt/gunyah/vm_mgr.c",
    "drivers/virt/gunyah/vm_mgr.h",
    "drivers/virt/gunyah/vm_mgr_cma_mem.c",
    "drivers/virt/gunyah/vm_mgr_mem.c",
    "include/linux/gunyah.h",
    "include/linux/gunyah_qtvm.h",
    "include/uapi/linux/gunyah.h",
    "tools/virt/gunyah/gh_trace.sh",
    "tools/virt/gunyah/gh_vmtest.py",
)

INTEGRATION_FILES = (
    "drivers/virt/Kconfig",
    "drivers/virt/Makefile",
    "arch/arm64/Kbuild",
)

INTEGRATION_APPEND = {
    "drivers/virt/Kconfig": '\nsource "drivers/virt/gunyah/Kconfig"\n',
    "drivers/virt/Makefile": "obj-y\t\t\t\t+= gunyah/\n",
    "arch/arm64/Kbuild": "obj-$(CONFIG_GUNYAH_HYPERCALLS)\t+= gunyah/\n",
}

GUNYAH_TARGETS = (
    "drivers/virt/gunyah",
    "arch/arm64/gunyah",
    "arch/arm64/include/asm/gunyah.h",
    "include/linux/gunyah.h",
    "include/linux/gunyah_qtvm.h",
    "include/uapi/linux/gunyah.h",
)


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def git(args, cwd, env=None, binary=False):
    base = dict(os.environ)
    base["GIT_CONFIG_GLOBAL"] = "/dev/null"
    base["GIT_CONFIG_SYSTEM"] = "/dev/null"
    if env:
        base.update(env)
    base["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=/dev/null", *args],
        cwd=str(cwd), env=base, capture_output=True, text=not binary,
    )
    if result.returncode != 0:
        error = result.stderr.decode(errors="replace") if binary else result.stderr
        raise ValueError("git %s failed: %s" % (" ".join(args), error.strip()))
    return result.stdout


def verify_no_existing_implementation(kernel_source):
    for rel in GUNYAH_TARGETS:
        if (kernel_source / rel).exists():
            raise ValueError("kernel source already has a Gunyah implementation: " + rel)
    for rel in INTEGRATION_FILES:
        if "gunyah" in (kernel_source / rel).read_text().lower():
            raise ValueError("kernel source already integrates Gunyah in " + rel)


def check_output(output):
    """拒绝已有输出、悬空链接和已有旁置 manifest；只读操作。"""
    output = Path(output)
    if output.is_symlink() or output.exists():
        raise ValueError("output already exists or is a symlink: " + str(output))
    output = output.resolve()
    manifest = output.parent / (output.name + ".manifest.json")
    if manifest.is_symlink() or manifest.exists():
        raise ValueError("manifest already exists or is a symlink: " + str(manifest))
    return output, manifest


def build_plan(kernel_manifest, kernel_source, source, output, expected_source_commit):
    """只读校验并计算完整计划，不写任何文件。"""
    kernel_source = Path(kernel_source).resolve()
    source = Path(source).resolve()
    output, manifest_path = check_output(output)
    if git(["status", "--porcelain"], kernel_source).strip():
        raise ValueError("kernel source worktree is dirty")
    if git(["status", "--porcelain"], source).strip():
        raise ValueError("driver source checkout is dirty")
    parent_commit = git(["rev-parse", "HEAD^{commit}"], kernel_source).strip()
    parent_tree = git(["rev-parse", "HEAD^{tree}"], kernel_source).strip()
    if parent_commit != kernel_manifest["source_commit"] or parent_tree != kernel_manifest["source_tree"]:
        raise ValueError("kernel source HEAD/tree differs from the sealed kernel")
    source_commit = git(["rev-parse", "HEAD^{commit}"], source).strip()
    if source_commit != expected_source_commit:
        raise ValueError("driver source checkout is not at the pinned commit")
    verify_no_existing_implementation(kernel_source)

    files = []
    for rel in IMPORT_FILES:
        origin = source / rel
        if not origin.is_file():
            raise ValueError("driver source checkout is missing " + rel)
        raw = origin.read_bytes()
        if raw != git(["show", source_commit + ":" + rel], source, binary=True):
            raise ValueError("driver bytes differ from the pinned commit: " + rel)
        files.append({"path": rel, "content": raw, "source_sha256": sha256_bytes(raw)})

    integration = []
    for rel in INTEGRATION_FILES:
        origin = kernel_source / rel
        if not origin.is_file():
            raise ValueError("kernel source is missing " + rel)
        before = origin.read_bytes()
        if before != git(["show", parent_commit + ":" + rel], kernel_source, binary=True):
            raise ValueError("kernel integration bytes differ from the sealed source: " + rel)
        after = before + INTEGRATION_APPEND[rel].encode()
        integration.append({"path": rel, "content": after,
                            "before_sha256": sha256_bytes(before),
                            "after_sha256": sha256_bytes(after)})

    parent_epoch = int(git(["show", "-s", "--format=%ct", parent_commit], kernel_source).strip())
    source_epoch = int(git(["show", "-s", "--format=%ct", source_commit], source).strip())
    epoch = max(parent_epoch, source_epoch)
    message = ("Gunyah driver source preparation\n\n"
               "kernel-source: " + parent_commit + "\n"
               "driver-source: " + source_commit + "\n")
    return {"kernel_source": str(kernel_source), "source": str(source), "output": str(output),
            "parent_commit": parent_commit, "parent_tree": parent_tree,
            "source_commit": source_commit, "epoch": epoch, "message": message,
            "files": files, "integration": integration,
            "manifest_path": str(manifest_path)}


def execute_plan(plan):
    """在新 worktree 落文件并提交，作者/日期固定为可复现值。"""
    output, manifest_path = check_output(plan["output"])
    if manifest_path != Path(plan["manifest_path"]):
        raise ValueError("manifest path differs from the planned output")
    output.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--detach", str(output), plan["parent_commit"]], plan["kernel_source"])
    for entry in (*plan["files"], *plan["integration"]):
        target = output / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(entry["content"])
    env = {
        "GIT_AUTHOR_NAME": AUTHOR_NAME, "GIT_AUTHOR_EMAIL": AUTHOR_EMAIL,
        "GIT_COMMITTER_NAME": AUTHOR_NAME, "GIT_COMMITTER_EMAIL": AUTHOR_EMAIL,
        "GIT_AUTHOR_DATE": "@%d +0000" % plan["epoch"],
        "GIT_COMMITTER_DATE": "@%d +0000" % plan["epoch"],
    }
    git(["add", "-A"], output)
    git(["commit", "-m", plan["message"]], output, env=env)
    derived_commit = git(["rev-parse", "HEAD^{commit}"], output).strip()
    derived_tree = git(["rev-parse", "HEAD^{tree}"], output).strip()
    if git(["status", "--porcelain"], output).strip():
        raise ValueError("derived worktree is dirty after commit")
    manifest = {
        "schema_version": 2,
        "status": STATUS,
        "parent_kernel": {"commit": plan["parent_commit"], "tree": plan["parent_tree"]},
        "driver_source": {"commit": plan["source_commit"]},
        "derived": {"commit": derived_commit, "tree": derived_tree},
        "author": AUTHOR_NAME + " <" + AUTHOR_EMAIL + ">",
        "commit_epoch": plan["epoch"],
        "files": [{"path": e["path"], "source_sha256": e["source_sha256"]} for e in plan["files"]],
        "integration": [{"path": e["path"], "before_sha256": e["before_sha256"],
                         "after_sha256": e["after_sha256"]} for e in plan["integration"]],
    }
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def plan_summary(plan):
    return {"parent_commit": plan["parent_commit"], "parent_tree": plan["parent_tree"],
            "driver_source_commit": plan["source_commit"], "output": plan["output"],
            "commit_epoch": plan["epoch"], "files": len(plan["files"]),
            "integration": len(plan["integration"]), "status": "PLAN_ONLY_NO_WRITES"}


def run(kernel_artifact, kernel_source, source, output, project=ROOT, execute=False):
    project = Path(project)
    lock = json.loads((project / "sources.lock.json").read_text())
    entry = lock.get("sources", {}).get(SOURCE_LOCK_KEY)
    if not entry or not entry.get("commit"):
        raise ValueError("sources.lock.json is missing the pinned " + SOURCE_LOCK_KEY + " commit")
    manifest, _ = inspect_kernel(kernel_artifact)
    output = Path(output)
    resolved = output.resolve()
    if not resolved.is_relative_to(project.resolve() / "build"):
        raise ValueError("output must live under the project build/ directory")
    plan = build_plan(manifest, kernel_source, source, output, entry["commit"])
    if not execute:
        print(json.dumps(plan_summary(plan), indent=2, sort_keys=True))
        return None
    result = execute_plan(plan)
    result["manifest_path"] = plan["manifest_path"]
    print(json.dumps({"derived_commit": result["derived"]["commit"],
                      "derived_tree": result["derived"]["tree"],
                      "manifest": plan["manifest_path"], "status": STATUS},
                     indent=2, sort_keys=True))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--kernel-source", required=True, type=Path)
    parser.add_argument("--driver-source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run(args.kernel, args.kernel_source, args.driver_source, args.output,
        project=ROOT, execute=args.execute)


if __name__ == "__main__":
    main()
