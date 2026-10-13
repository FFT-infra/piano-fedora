#!/usr/bin/env python3
"""从锁定厂商 checkout 导入完整 common Gunyah VMM 子集，准备派生内核 worktree。

默认只打印计划且零文件写入；--execute 才创建 worktree、落文件并提交。
输入 checkout 与封存内核产物不被修改，提交只发生在新 worktree 的 detached HEAD。
"""

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

from piano_artifacts import inspect_kernel

ROOT = Path(__file__).resolve().parents[1]

VENDOR_LOCK_KEY = "piano-vendor-kernel"
AUTHOR_NAME = "Piano Fedora Test"
AUTHOR_EMAIL = "dev@piano.invalid"
STATUS = "PREPARED_NOT_BUILT_NOT_DEVICE_VERIFIED"

IMPORT_FILES = (
    "drivers/virt/gunyah/Kconfig",
    "drivers/virt/gunyah/Makefile",
    "drivers/virt/gunyah/gunyah.c",
    "drivers/virt/gunyah/rsc_mgr.c",
    "drivers/virt/gunyah/rsc_mgr.h",
    "drivers/virt/gunyah/rsc_mgr_rpc.c",
    "drivers/virt/gunyah/vm_mgr.c",
    "drivers/virt/gunyah/vm_mgr.h",
    "drivers/virt/gunyah/vm_mgr_mem.c",
    "drivers/virt/gunyah/gunyah_platform_hooks.c",
    "drivers/virt/gunyah/gunyah_qcom.c",
    "drivers/virt/gunyah/gunyah_vcpu.c",
    "drivers/virt/gunyah/gunyah_irqfd.c",
    "drivers/virt/gunyah/gunyah_ioeventfd.c",
    "arch/arm64/gunyah/Makefile",
    "arch/arm64/gunyah/gunyah_hypercall.c",
    "arch/arm64/include/asm/gunyah.h",
    "include/linux/gunyah.h",
    "include/uapi/linux/gunyah.h",
)

INTEGRATION_FILES = (
    "drivers/virt/Kconfig",
    "drivers/virt/Makefile",
    "arch/arm64/Kbuild",
)

INTEGRATION_APPEND = {
    "drivers/virt/Kconfig": '\nsource "drivers/virt/gunyah/Kconfig"\n',
    "drivers/virt/Makefile": "obj-$(CONFIG_GUNYAH) += gunyah/\n",
    "arch/arm64/Kbuild": "obj-$(CONFIG_GUNYAH) += gunyah/\n",
}

GUNYAH_TARGETS = (
    "drivers/virt/gunyah",
    "arch/arm64/gunyah",
    "arch/arm64/include/asm/gunyah.h",
    "include/linux/gunyah.h",
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


def common_kconfig(text):
    """保留官方 common 段：menuconfig GUNYAH_DRIVERS 之前的全部内容。"""
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == "menuconfig GUNYAH_DRIVERS":
            return "".join(lines[:index])
    raise ValueError("vendor Kconfig is missing menuconfig GUNYAH_DRIVERS")


def common_makefile(text):
    """保留 SPDX 与 gunyah_rsc_mgr-y 及其之后的 common 段。"""
    lines = text.splitlines(keepends=True)
    if not lines or "SPDX" not in lines[0]:
        raise ValueError("vendor Makefile is missing its SPDX line")
    for index, line in enumerate(lines):
        if line.startswith("gunyah_rsc_mgr-y"):
            return lines[0] + "".join(lines[index:])
    raise ValueError("vendor Makefile is missing gunyah_rsc_mgr-y")


def arch_makefile(text):
    """arch Makefile 仅保留 obj-$(CONFIG_GUNYAH) 一行。"""
    for line in text.splitlines(keepends=True):
        if line.startswith("obj-$(CONFIG_GUNYAH)"):
            return line
    raise ValueError("vendor arch Makefile is missing obj-$(CONFIG_GUNYAH)")


def fix_remove(text):
    """7.2 void 回调：唯一的 .remove_new 换成 .remove。"""
    if text.count(".remove_new") != 1:
        raise ValueError("rsc_mgr.c must contain exactly one .remove_new")
    return text.replace(".remove_new", ".remove")


def transformed(path, data):
    if path == "drivers/virt/gunyah/Kconfig":
        return common_kconfig(data.decode()).encode()
    if path == "drivers/virt/gunyah/Makefile":
        return common_makefile(data.decode()).encode()
    if path == "arch/arm64/gunyah/Makefile":
        return arch_makefile(data.decode()).encode()
    if path == "drivers/virt/gunyah/rsc_mgr.c":
        return fix_remove(data.decode()).encode()
    return data


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


def build_plan(kernel_manifest, kernel_source, vendor, output, expected_vendor_commit):
    """只读校验并计算完整计划，不写任何文件。"""
    kernel_source = Path(kernel_source).resolve()
    vendor = Path(vendor).resolve()
    output, manifest_path = check_output(output)
    if git(["status", "--porcelain"], kernel_source).strip():
        raise ValueError("kernel source worktree is dirty")
    if git(["status", "--porcelain"], vendor).strip():
        raise ValueError("vendor checkout is dirty")
    parent_commit = git(["rev-parse", "HEAD^{commit}"], kernel_source).strip()
    parent_tree = git(["rev-parse", "HEAD^{tree}"], kernel_source).strip()
    if parent_commit != kernel_manifest["source_commit"] or parent_tree != kernel_manifest["source_tree"]:
        raise ValueError("kernel source HEAD/tree differs from the sealed kernel")
    vendor_commit = git(["rev-parse", "HEAD^{commit}"], vendor).strip()
    if vendor_commit != expected_vendor_commit:
        raise ValueError("vendor checkout is not at the pinned commit")
    verify_no_existing_implementation(kernel_source)

    files = []
    for rel in IMPORT_FILES:
        source = vendor / rel
        if not source.is_file():
            raise ValueError("vendor checkout is missing " + rel)
        raw = source.read_bytes()
        if raw != git(["show", vendor_commit + ":" + rel], vendor, binary=True):
            raise ValueError("vendor bytes differ from the pinned commit: " + rel)
        effective = transformed(rel, raw)
        files.append({"path": rel, "content": effective,
                      "vendor_sha256": sha256_bytes(raw),
                      "effective_sha256": sha256_bytes(effective)})

    integration = []
    for rel in INTEGRATION_FILES:
        source = kernel_source / rel
        if not source.is_file():
            raise ValueError("kernel source is missing " + rel)
        before = source.read_bytes()
        if before != git(["show", parent_commit + ":" + rel], kernel_source, binary=True):
            raise ValueError("kernel integration bytes differ from the sealed source: " + rel)
        after = before + INTEGRATION_APPEND[rel].encode()
        integration.append({"path": rel, "content": after,
                            "before_sha256": sha256_bytes(before),
                            "after_sha256": sha256_bytes(after)})

    parent_epoch = int(git(["show", "-s", "--format=%ct", parent_commit], kernel_source).strip())
    vendor_epoch = int(git(["show", "-s", "--format=%ct", vendor_commit], vendor).strip())
    epoch = max(parent_epoch, vendor_epoch)
    message = ("Gunyah VMM source preparation\n\n"
               "kernel-source: " + parent_commit + "\n"
               "vendor: " + vendor_commit + "\n")
    return {"kernel_source": str(kernel_source), "vendor": str(vendor), "output": str(output),
            "parent_commit": parent_commit, "parent_tree": parent_tree,
            "vendor_commit": vendor_commit, "epoch": epoch, "message": message,
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
        "schema_version": 1,
        "status": STATUS,
        "parent_kernel": {"commit": plan["parent_commit"], "tree": plan["parent_tree"]},
        "vendor": {"commit": plan["vendor_commit"]},
        "derived": {"commit": derived_commit, "tree": derived_tree},
        "author": AUTHOR_NAME + " <" + AUTHOR_EMAIL + ">",
        "commit_epoch": plan["epoch"],
        "files": [{"path": e["path"], "vendor_sha256": e["vendor_sha256"],
                   "effective_sha256": e["effective_sha256"]} for e in plan["files"]],
        "integration": [{"path": e["path"], "before_sha256": e["before_sha256"],
                         "after_sha256": e["after_sha256"]} for e in plan["integration"]],
    }
    with manifest_path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def plan_summary(plan):
    return {"parent_commit": plan["parent_commit"], "parent_tree": plan["parent_tree"],
            "vendor_commit": plan["vendor_commit"], "output": plan["output"],
            "commit_epoch": plan["epoch"], "files": len(plan["files"]),
            "integration": len(plan["integration"]), "status": "PLAN_ONLY_NO_WRITES"}


def run(kernel_artifact, kernel_source, vendor, output, project=ROOT, execute=False):
    project = Path(project)
    lock = json.loads((project / "sources.lock.json").read_text())
    entry = lock.get("sources", {}).get(VENDOR_LOCK_KEY)
    if not entry or not entry.get("commit"):
        raise ValueError("sources.lock.json is missing the pinned " + VENDOR_LOCK_KEY + " commit")
    manifest, _ = inspect_kernel(kernel_artifact)
    output = Path(output)
    resolved = output.resolve()
    if not resolved.is_relative_to(project.resolve() / "build"):
        raise ValueError("output must live under the project build/ directory")
    plan = build_plan(manifest, kernel_source, vendor, output, entry["commit"])
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
    parser.add_argument("--vendor", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    run(args.kernel, args.kernel_source, args.vendor, args.output,
        project=ROOT, execute=args.execute)


if __name__ == "__main__":
    main()
