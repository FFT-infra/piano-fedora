#!/usr/bin/env python3
"""在 TMPDIR 的小 git fixtures 上实测 Gunyah 源码准备步骤。

不编内核、不冒充设备验收：只验证导入闭包、可复现提交、拒绝路径与 plan 零写入。
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from kernel_fixture import make_kernel  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "prepare_gunyah_source", ROOT / "scripts" / "prepare-gunyah-source.py")
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)

DRIVER_COMMIT = "fd503a492d381687c143b7085ea79ebce58dcdc7"
DRIVER_EPOCH = 1776686208
SOURCE_EPOCH = 1791901380

# 驱动 fixture 与导入清单同源：清单加了文件，这里跟着有，不会两份列表漂移。
SOURCE_FILES = {name: "/* fixture " + name + " */\n" for name in prep.IMPORT_FILES}

INTEGRATION_FILES = {
    "drivers/virt/Kconfig": "# SPDX-License-Identifier: GPL-2.0-only\n\nmenu \"Virt\"\n\nendmenu\n",
    "drivers/virt/Makefile": "# SPDX-License-Identifier: GPL-2.0-only\nobj-$(CONFIG_VMGENID)\t+= vmgenid.o\n",
    "arch/arm64/Kbuild": "# SPDX-License-Identifier: GPL-2.0-only\n\nobj-y\t+= kernel/\n",
}


def git(args, cwd, env=None):
    base = dict(os.environ)
    base["GIT_CONFIG_GLOBAL"] = "/dev/null"
    base["GIT_CONFIG_SYSTEM"] = "/dev/null"
    base["GIT_AUTHOR_NAME"] = base["GIT_COMMITTER_NAME"] = "Fixture"
    base["GIT_AUTHOR_EMAIL"] = base["GIT_COMMITTER_EMAIL"] = "fixture@example.invalid"
    if env:
        base.update(env)
    base["GIT_OPTIONAL_LOCKS"] = "0"
    result = subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=str(cwd),
                   check=True, capture_output=True, text=True, env=base)
    return result.stdout


def make_repo(root, files, epoch):
    root.mkdir(parents=True)
    git(["init", "-q"], root)
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    git(["add", "-A"], root)
    env = {"GIT_AUTHOR_DATE": "@%d +0000" % epoch, "GIT_COMMITTER_DATE": "@%d +0000" % epoch}
    git(["commit", "-q", "-m", "fixture"], root, env=env)
    return git(["rev-parse", "HEAD^{commit}"], root).strip()


def check(condition, message, failures):
    if not condition:
        failures.append(message)


def expect_failure(call, message, failures):
    try:
        call()
    except ValueError:
        return
    failures.append(message)


def main():
    failures = []
    base = Path(tempfile.mkdtemp(prefix="gunyah-source-"))
    try:
        # driver fixture 先落地，用其真实 commit 作为 lock 的固定 pin。
        driver = base / "driver"
        vendor_commit = make_repo(driver, SOURCE_FILES, DRIVER_EPOCH)
        check(len(vendor_commit) == 40, "driver fixture commit 非法", failures)

        project = base / "project"
        (project / "build").mkdir(parents=True)
        (project / "sources.lock.json").write_text(json.dumps({"sources": {
            prep.SOURCE_LOCK_KEY: {"url": "https://example.invalid/driver.git",
                                   "ref": "pr/gunyah", "commit": vendor_commit,
                                   "role": "fixture"}}}))
        kernel = make_kernel(base / "sealed", "7.2.9-fixture")

        source_files = dict(INTEGRATION_FILES)
        source_files["README"] = "kernel fixture\n"
        source = base / "kernel-source"
        source_commit = make_repo(source, source_files, SOURCE_EPOCH)
        source_tree = git(["rev-parse", "HEAD^{tree}"], source).strip()
        manifest_path = kernel / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["source_commit"] = source_commit
        manifest["source_tree"] = source_tree
        manifest_path.write_text(json.dumps(manifest))

        before_source = {p: (source / p).read_bytes() for p in INTEGRATION_FILES}
        before_vendor = {p: (driver / p).read_bytes() for p in SOURCE_FILES}

        # 导入清单必须覆盖三个集成点之外的全部驱动文件，且不夹带厂商树里的旧实现。
        check(not any(name.startswith("drivers/virt/gunyah/gh_") for name in prep.IMPORT_FILES),
              "导入清单夹带了厂商旧实现 gh_*", failures)
        check(len(set(prep.IMPORT_FILES)) == len(prep.IMPORT_FILES), "导入清单有重复项", failures)

        # 默认 plan 零写入。
        out1 = project / "build" / "kernel-worktrees" / "gunyah-a"
        indexes = {repo: (repo / ".git/index").read_bytes() for repo in (source, driver)}
        plan = prep.run(kernel, source, driver, out1, project=project, execute=False)
        check(plan is None and not out1.exists(), "plan 模式写入了文件", failures)
        check(not out1.parent.exists(), "plan 创建了输出父目录", failures)
        check(all((repo / ".git/index").read_bytes() == data for repo, data in indexes.items()),
              "plan 修改了输入 index", failures)

        # 两次独立 execute 得到相同 commit/tree。
        out2 = project / "build" / "kernel-worktrees" / "gunyah-b"
        r1 = prep.run(kernel, source, driver, out1, project=project, execute=True)
        r2 = prep.run(kernel, source, driver, out2, project=project, execute=True)
        check(r1["derived"]["commit"] == r2["derived"]["commit"]
              and r1["derived"]["tree"] == r2["derived"]["tree"],
              "两次独立准备未得到相同 commit/tree", failures)
        check(all(git(["status", "--porcelain"], out).strip() == "" for out in (out1, out2)),
              "派生 worktree 不干净", failures)

        # 落盘文件与集成追加。
        check((out1 / "drivers/virt/gunyah/gunyah.c").is_file(), "导入文件缺失", failures)
        for rel, append in prep.INTEGRATION_APPEND.items():
            check((out1 / rel).read_text().endswith(append), rel + " 未按上游行接入", failures)
        # 原样搬运：派生树里的每个导入文件与上游逐字节相同，本仓库不改写驱动。
        check(all((out1 / name).read_bytes() == (driver / name).read_bytes()
                  for name in prep.IMPORT_FILES),
              "导入文件与上游不逐字节相同", failures)

        # manifest 落在输出旁边、不进内核 git 树。
        mpath = Path(r1["manifest_path"]) if isinstance(r1, dict) else Path(plan["manifest_path"])
        check(mpath.is_file() and mpath.parent == out1.parent, "manifest 位置错误", failures)
        check(not (out1 / mpath.name).exists(), "manifest 污染了内核 worktree", failures)

        # 输入不变。
        check(before_source == {p: (source / p).read_bytes() for p in INTEGRATION_FILES},
              "输入内核 checkout 被修改", failures)
        check(before_vendor == {p: (driver / p).read_bytes() for p in SOURCE_FILES},
              "driver checkout 被修改", failures)
        check(git(["status", "--porcelain"], source).strip() == "", "输入内核 checkout 变脏", failures)

        # 拒绝：已存在输出、错误 pin、dirty、已有实现。
        expect_failure(lambda: prep.run(kernel, source, driver, out1, project=project, execute=True),
                       "已存在输出未被拒绝", failures)

        wrong = base / "wrong-driver"
        make_repo(wrong, SOURCE_FILES, DRIVER_EPOCH + 5)
        expect_failure(lambda: prep.run(kernel, source, wrong,
                                        project / "build" / "kernel-worktrees" / "x",
                                        project=project, execute=False),
                       "浮动 pin 未被拒绝", failures)

        dirty = base / "dirty-driver"
        make_repo(dirty, SOURCE_FILES, DRIVER_EPOCH)
        (dirty / "scratch").write_text("dirty\n")
        expect_failure(lambda: prep.run(kernel, source, dirty,
                                        project / "build" / "kernel-worktrees" / "y",
                                        project=project, execute=False),
                       "dirty driver 未被拒绝", failures)

        existing = base / "existing-kernel"
        existing_files = dict(source_files)
        existing_files["drivers/virt/gunyah/gunyah.c"] = "/* preexisting */\n"
        make_repo(existing, existing_files, SOURCE_EPOCH)
        expect_failure(lambda: prep.verify_no_existing_implementation(existing),
                       "已有 Gunyah 实现未被拒绝", failures)

        integrated = base / "integrated-kernel"
        integrated_files = dict(source_files)
        integrated_files["drivers/virt/Kconfig"] += 'source "drivers/virt/gunyah/Kconfig"\n'
        make_repo(integrated, integrated_files, SOURCE_EPOCH)
        expect_failure(lambda: prep.verify_no_existing_implementation(integrated),
                       "已接入 Gunyah 的 Kconfig 未被拒绝", failures)

        expect_failure(lambda: prep.run(kernel, source, driver, base / "outside",
                                        project=project, execute=False),
                       "build/ 之外的输出未被拒绝", failures)

        # 拒绝路径必须在 worktree 创建之前完成，已有文件逐字节保留。
        def reject_output(candidate, message, execute=True):
            worktrees = git(["worktree", "list", "--porcelain"], source)
            proof = candidate.parent / (candidate.name + ".manifest.json")
            old_proof = proof.read_bytes() if proof.is_file() else None
            expect_failure(lambda: prep.run(kernel, source, driver, candidate,
                                            project=project, execute=execute), message, failures)
            check(git(["worktree", "list", "--porcelain"], source) == worktrees,
                  message + "（创建了 worktree）", failures)
            if old_proof is not None:
                check(proof.read_bytes() == old_proof, message + "（覆盖了 manifest）", failures)

        reject_output(base / "impostor" / "build" / "escape", "项目外伪装 build 路径未拒绝",
                      execute=False)
        linked = project / "build" / "linked"
        linked.symlink_to(project / "build" / "missing-target", target_is_directory=True)
        reject_output(linked, "symlink 输出未拒绝", execute=False)
        check(linked.is_symlink() and not linked.exists(), "symlink 输出被修改", failures)

        occupied = project / "build" / "occupied"
        occupied_manifest = occupied.with_name(occupied.name + ".manifest.json")
        occupied_manifest.write_bytes(b"existing manifest\n")
        reject_output(occupied, "预存在 manifest 未拒绝")
        check(not occupied.exists(), "manifest 拒绝路径创建了输出", failures)

        late = project / "build" / "late"
        late_plan = prep.build_plan(manifest, source, driver, late, vendor_commit)
        late_manifest = Path(late_plan["manifest_path"])
        late_manifest.write_bytes(b"created after plan\n")
        worktrees = git(["worktree", "list", "--porcelain"], source)
        expect_failure(lambda: prep.execute_plan(late_plan), "execute 未重查 manifest", failures)
        check(not late.exists() and late_manifest.read_bytes() == b"created after plan\n"
              and git(["worktree", "list", "--porcelain"], source) == worktrees,
              "execute manifest 拒绝路径有写入", failures)

        for repo, names in ((driver, prep.IMPORT_FILES), (source, prep.INTEGRATION_FILES)):
            for index, name in enumerate(names):
                original = (repo / name).read_bytes()
                git(["update-index", "--assume-unchanged", name], repo)
                try:
                    (repo / name).write_bytes(original + b"/* hidden modification */\n")
                    check(git(["status", "--porcelain"], repo).strip() == "",
                          "assume-unchanged fixture 的 status 应干净", failures)
                    candidate = project / "build" / (repo.name + "-hidden-" + str(index))
                    reject_output(candidate, "隐藏修改未拒绝: " + name, execute=False)
                    check(not candidate.exists(), "隐藏修改拒绝路径创建了输出", failures)
                finally:
                    (repo / name).write_bytes(original)
                    git(["update-index", "--no-assume-unchanged", name], repo)
    finally:
        shutil.rmtree(base)

    if failures:
        for message in failures:
            print("FAIL: " + message, file=sys.stderr)
        return 1
    print("OK: gunyah source preparation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
