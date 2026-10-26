#!/usr/bin/env python3
"""离线检查 rootfs 载荷脚本：模块安装、硬件入口、拒绝路径。

这是引导脚本的两条硬前置条件：`/sysroot/usr/lib/modules/$release` 与
`/sysroot/usr/lib/piano/piano-disk-hardware-prepare`。缺任一条启动就进救援。
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from kernel_fixture import make_kernel as sealed_fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
TOOL = ROOT / "scripts" / "stage-rootfs-payload.py"
RELEASE = "7.3.0-fixture"

# 与研究仓库同级；不存在时只跳过需要真实 SunUEFI 的用例。
SUNUEFI = Path(os.environ.get("PIANO_TEST_SUNUEFI", ROOT / "build/test-sources/sunuefi"))


def run(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def make_rootfs(tmp):
    """Fedora merged-usr，包括 Fedora 44 的 /usr/local/sbin 合并。"""
    rootfs = tmp / "rootfs"
    (rootfs / "usr" / "bin").mkdir(parents=True)
    (rootfs / "lib").symlink_to("usr/lib")
    (rootfs / "bin").symlink_to("usr/bin")
    (rootfs / "usr/local/bin").mkdir(parents=True)
    (rootfs / "usr/local/sbin").symlink_to("bin")
    return rootfs


def make_kernel(tmp):
    return sealed_fixture(tmp / "kernel", RELEASE)


def main():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    # 输入不存在：拒绝。
    check(run("--rootfs", "/nonexistent", "--kernel", "/nonexistent",
              "--sunuefi", "/nonexistent").returncode == 2,
          "missing inputs should be rejected")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        rootfs = make_rootfs(tmp)
        kernel = make_kernel(tmp)
        # 通用拒绝路径用自带 fixture，CI 无须具有研究仓库的兄弟目录。
        preflight_source = tmp / "preflight-source"
        preflight_source.mkdir()
        (preflight_source / "build.sh").write_text("#!/bin/sh\n")

        # 目录布局回归不依赖联网或上游 checkout。这里只替代来源摘要，
        # 实际复制、路径保护和目标链接仍走生产代码；来源完整性在后面核对。
        spec = importlib.util.spec_from_file_location("stage_payload", TOOL)
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        digests = {
            stage.CHECKER: stage.CHECKER_SHA256,
            stage.CONTEXT_CHECKER: stage.CONTEXT_CHECKER_SHA256,
            stage.HARDWARE_PREPARE: "a171ece910b51b7a9586c58277df78957709a4fff559245c9919d3f8432c925c",
            "linux/userspace/piano-debug-bootstrap": "d201ec73a70f3db8d80a9e424fe88ec4de90a31bf2cba49d1229970ea49427ba",
            "linux/userspace/piano-debug-stage": "b9f02b68f48d81fa40ca2cbd05e3ed4e3275a489e9d15388dd4fa382251960df",
            "linux/userspace/piano-linux-debug.service": "f52c5a0dba87318c6f19d92f1c3a24c30d074437ec4d97a32c9d03a5acdde999",
            "linux/userspace/piano-boot-task-snapshot": "fixture",
        }
        for relative in digests:
            source = preflight_source / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("#!/bin/sh\nexit 0\n")
        with patch.object(stage, "sha256", side_effect=lambda p: digests[p.relative_to(preflight_source).as_posix()]):
            stage.stage_hardware(rootfs, preflight_source)
            # 已有系统带项目自己创建的别名；替换链接本身不应被当成跟随链接。
            stage.stage_hardware(rootfs, preflight_source)
            alias = rootfs / "usr/lib/piano/piano-disk-hardware-prepare"
            check(alias.readlink() == Path("piano-ram-hardware-prepare"),
                  "existing managed alias must be replaceable")
            outside = tmp / "outside"
            outside.mkdir()
            sentinel = outside / "keep"
            sentinel.write_bytes(b"untouched")
            alias.unlink()
            alias.symlink_to(sentinel)
            stage.stage_hardware(rootfs, preflight_source)
            check(sentinel.read_bytes() == b"untouched" and
                  alias.readlink() == Path("piano-ram-hardware-prepare"),
                  "alias replacement must unlink the link without touching its target")
            parent_escape = make_rootfs(tmp / "parent-escape")
            (parent_escape / "usr/lib").mkdir()
            (parent_escape / "usr/lib/piano").symlink_to(outside, target_is_directory=True)
            try:
                stage.stage_hardware(parent_escape, preflight_source)
            except stage.StageError:
                pass
            else:
                check(False, "hardware staging accepted a symlink parent")
            check(set(outside.iterdir()) == {sentinel}, "parent rejection wrote outside root")
        rescue = rootfs / "usr/local/bin/piano-debug-bootstrap"
        check(rescue.is_file() and os.access(rescue, os.X_OK),
              "rescue helper must be installed in Fedora's canonical local bin")
        check((rootfs / "usr/local/sbin").is_symlink(),
              "staging must preserve Fedora's local sbin compatibility link")
        check((rootfs / "usr/local/sbin/piano-debug-bootstrap").samefile(rescue),
              "the upstream rescue path must resolve to the installed helper")

        # 实际 staging 流程必须把新模块、脚本、unit 和 provenance 一起交给标签恢复。
        # 来源在 home 下时 copy2/copytree 会保留 user_home_t；仅检查摘要看不出它。
        original_sha = stage.sha256
        labelled = make_rootfs(tmp / "labelled")
        def source_sha(path):
            path = Path(path)
            return digests[path.relative_to(preflight_source).as_posix()] if path.is_relative_to(preflight_source) else original_sha(path)
        with patch.object(stage, "sha256", side_effect=source_sha), \
             patch.object(stage, "relabel_live_payload", create=True,
                          return_value={"status": "TARGET_POLICY_APPLIED"}) as relabel:
            labelled_result = stage.stage(labelled, kernel, preflight_source)
            check(relabel.call_count == 1, "successful staging omitted SELinux label restoration")
            if relabel.call_count:
                label_root, targets = relabel.call_args.args
                check(label_root == labelled.resolve(), "SELinux relabel used the wrong root")
                check(set(targets) == {
                    f"usr/lib/modules/{RELEASE}", "usr/lib/piano",
                    "usr/local/bin/piano-debug-bootstrap", "usr/local/bin/piano-debug-stage",
                    "usr/lib/systemd/system/piano-linux-debug.service",
                    "usr/share/piano-provenance/kernel-modules.json",
                }, "SELinux relabel omitted a staged surface or exceeded the payload scope")
                check(labelled_result.get("selinux", {}).get("status") == "TARGET_POLICY_APPLIED",
                      "staging result omitted SELinux evidence")

        if hasattr(stage, "relabel_live_payload"):
            with patch.object(stage.subprocess, "run") as command:
                offline = stage.relabel_live_payload(labelled, [f"usr/lib/modules/{RELEASE}"])
                check(not command.called and offline["status"] == "DEFERRED_TO_IMAGE_LABELING",
                      "offline staging must defer to the image workflow's complete target-policy labelling")
            with patch.object(stage.subprocess, "run") as command:
                stage.relabel_live_payload(Path("/"), [f"usr/lib/modules/{RELEASE}"])
                check(command.call_count == 1 and command.call_args.args[0] == [
                    "/usr/sbin/setfiles", "-F", "/etc/selinux/targeted/contexts/files/file_contexts",
                    f"/usr/lib/modules/{RELEASE}"], "live staging must apply the target's policy with setfiles")
            with patch.object(stage.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "setfiles")):
                try:
                    stage.relabel_live_payload(Path("/"), [f"usr/lib/modules/{RELEASE}"])
                except stage.StageError:
                    pass
                else:
                    check(False, "failed SELinux labelling was accepted as successful staging")

        # 空目录不算系统树。
        empty = tmp / "empty"
        empty.mkdir()
        check(run("--rootfs", str(empty), "--kernel", str(kernel),
                  "--sunuefi", str(preflight_source)).returncode == 2,
              "a directory without usr/ should be rejected")

        # 内核 manifest 缺失：拒绝。
        bare = tmp / "bare-kernel"
        bare.mkdir()
        check(run("--rootfs", str(rootfs), "--kernel", str(bare),
                  "--sunuefi", str(preflight_source)).returncode == 2,
              "a kernel artifact without manifest should be rejected")

        # 目标穿过符号链接：拒绝。
        #
        # Fedora 的 /lib 与 /bin 指向 /usr，写错位置会静默落到宿主或别处。
        tricky = make_rootfs(tmp / "tricky")
        (tricky / "usr" / "lib").symlink_to("/etc")
        result = run("--rootfs", str(tricky), "--kernel", str(kernel),
                     "--sunuefi", str(preflight_source))
        check(result.returncode == 2 and "symlink" in result.stderr,
              f"a destination through a symlink should be rejected: {result.stderr.strip()}")

        if not (SUNUEFI / "build.sh").is_file():
            if failures:
                for item in failures:
                    print(f"FAIL: {item}", file=sys.stderr)
                return 1
            print("SKIP: SunUEFI checkout unavailable; reject paths checked only "
                  "(not a pass)")
            return 0

        # 真实路径：模块与硬件入口都要落位。
        good = make_rootfs(tmp / "good")
        result = run("--rootfs", str(good), "--kernel", str(kernel),
                     "--sunuefi", str(SUNUEFI))
        if result.returncode != 0:
            check(False, f"staging failed: {result.stderr.strip()}")
        else:
            modules = good / "usr" / "lib" / "modules" / RELEASE
            check(modules.is_dir(), "the kernel release tree must be staged")
            check(any(modules.rglob("*.ko")), "staged modules must include .ko files")
            # 开发链接不能进镜像。
            for name in ("build", "source"):
                check(not (modules / name).exists(),
                      f"development link {name} must not be staged")

            prepare = good / "usr" / "lib" / "piano" / "piano-ram-hardware-prepare"
            check(prepare.is_file(), "the hardware prepare program must be staged")
            check(prepare.stat().st_mode & 0o111, "hardware prepare must be executable")
            alias = good / "usr" / "lib" / "piano" / "piano-disk-hardware-prepare"
            check(alias.is_symlink(), "the disk-hardware-prepare alias must exist")
            check(alias.readlink().name == "piano-ram-hardware-prepare",
                  "the alias must point at the prepare program")
            for name in ("piano_dma_routes.py", "piano_dma_contexts.py"):
                check((good / "usr" / "lib" / "piano" / name).is_file(),
                      f"{name} must be staged for the prepare program")

            # 引导脚本的两条前置条件都要满足。
            check(modules.is_dir(),
                  "bootstrap precondition: matching module tree present")
            check(alias.exists(),
                  "bootstrap precondition: hardware prepare executable present")

            provenance = json.loads(
                (good / "usr" / "share" / "piano-provenance"
                 / "kernel-modules.json").read_text()
            )
            check(provenance["kernel_release"] == RELEASE,
                  "provenance must record the kernel release")
            check(provenance["device_tested"] is False,
                  "must not claim device testing")

        # 同一个 release 重复安装：拒绝。
        repeated = run("--rootfs", str(good), "--kernel", str(kernel),
                       "--sunuefi", str(SUNUEFI))
        check(repeated.returncode == 0,
              f"matching existing module tree should be idempotent: {repeated.stderr.strip()}")
        if repeated.returncode == 0:
            check(json.loads(repeated.stdout).get("modules_status") == "matched-existing",
                  "idempotent staging must identify the existing module tree")

        # 同 release 但模块字节不同：拒绝，不能把跨 run 的内核当成本次产物。
        drift_tree = make_rootfs(tmp / "module-drift")
        source_modules = kernel / "modules" / "lib" / "modules" / RELEASE
        target_modules = drift_tree / "usr/lib/modules" / RELEASE
        shutil.copytree(source_modules, target_modules)
        changed = next(target_modules.rglob("*.ko"))
        changed.write_bytes(changed.read_bytes() + b"drift")
        drift_result = run("--rootfs", str(drift_tree), "--kernel", str(kernel),
                           "--sunuefi", str(SUNUEFI))
        check(drift_result.returncode == 2,
              "a same-release module tree with different bytes must be rejected")

        # 封存产物带 build/source 构建树软链，安装树里被 prune 掉。
        # 两者都必须忽略这两个链接，否则每次比较都会报 missing=['build']。
        linked_tree = make_rootfs(tmp / "module-links")
        linked_modules = linked_tree / "usr/lib/modules" / RELEASE
        shutil.copytree(source_modules, linked_modules)
        kernel_links = kernel / "modules" / "lib" / "modules" / RELEASE
        for name in ("build", "source"):
            (kernel_links / name).symlink_to("/home/runner/work/kernel-build")
        linked_result = run("--rootfs", str(linked_tree), "--kernel", str(kernel),
                            "--sunuefi", str(SUNUEFI))
        check(linked_result.returncode == 0,
              "sealed build/source links must not count as a module difference: "
              f"{linked_result.stderr.strip()}")
        for name in ("build", "source"):
            (kernel_links / name).unlink()

        # 校验器摘要漂移：拒绝。
        tampered = tmp / "sunuefi"
        # staging 只读本仓库 tools/linux，不消费 upstream 子模块。不要把第三方
        # rootfs 的 systemd 链接复制进这个摘要拒绝 fixture；virtiofs 对尚未有
        # 目标的链接执行 listxattr 会返回 ENOENT，先于被测断言失败。
        shutil.copytree(SUNUEFI, tampered, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", "build", "artifacts", "upstream"))
        checker = tampered / "tools" / "check_piano_kernel_dma_routes.py"
        checker.write_bytes(checker.read_bytes() + b"\n# tampered\n")
        drift = make_rootfs(tmp / "drift")
        result = run("--rootfs", str(drift), "--kernel", str(kernel),
                     "--sunuefi", str(tampered))
        check(result.returncode == 2 and "digest changed" in result.stderr,
              f"a drifted checker must be rejected: {result.stderr.strip()}")

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: rootfs payload staging matches the DNF module tree and rejects drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
