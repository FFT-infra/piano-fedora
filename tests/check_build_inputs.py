#!/usr/bin/env python3
"""离线回归：构建链的输入摘要必须只随真正的输入变化。

摘要错了两个方向都出事：漏列一个输入，旧产物会被当成新的；多列一个
无关文件，纯文档提交也会让五条长链作废。这个检查同时覆盖两条：
- 仓库里声明的路径与锁定条目都存在，上游引用闭合、无环。
- 夹具仓库里无关改动不改摘要，相关改动一定改，上游变化向下游传播。
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "build_inputs", ROOT / "scripts/build-inputs.py")
build_inputs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(build_inputs)

PRODUCER_CHAINS = {
    "build-kernel.yml": "kernel",
    "build-mesa.yml": "mesa",
    "build-ffmpeg.yml": "ffmpeg",
    "build-device-runtime.yml": "runtime",
    "build-piano-rpms.yml": "rpms",
    "build-rootfs.yml": "rootfs",
}


def test_declared_inputs_exist():
    """真实仓库里每条链的路径与锁定条目都要能取到。"""
    for chain in build_inputs.CHAINS:
        build_inputs.payload(chain, ROOT)


def test_upstream_graph_is_closed_and_acyclic():
    chains = build_inputs.CHAINS
    for chain, spec in chains.items():
        for name in spec["upstream"]:
            assert name in chains, f"{chain} depends on unknown chain {name}"
    visiting, done = set(), set()

    def walk(chain):
        assert chain not in visiting, f"upstream cycle through {chain}"
        if chain in done:
            return
        visiting.add(chain)
        for name in chains[chain]["upstream"]:
            walk(name)
        visiting.remove(chain)
        done.add(chain)

    for chain in chains:
        walk(chain)


def test_artifact_name_format():
    value = "a" * 64
    assert build_inputs.artifact_name("kernel", value) == "input-digest-kernel-" + value


def fixture(root):
    root.mkdir()
    (root / "sources.lock.json").write_text(json.dumps(
        {"sources": {"sunuefi": {"url": "https://example.invalid/s", "commit": "c1"}}}))
    (root / "patches").mkdir()
    (root / "patches/a.patch").write_text("patch a\n")
    (root / "product.json").write_text('{"target": {}}\n')
    (root / "docs").mkdir()
    (root / "docs/notes.md").write_text("notes\n")
    return {
        # kernel：补丁 + 一个 pin。rpms：消费 kernel，另看 product.json。
        "kernel": {"locks": ["sunuefi"], "paths": ["patches"], "upstream": []},
        "rpms": {"locks": [], "paths": ["product.json"], "upstream": ["kernel"]},
    }


def test_digest_tracks_only_declared_inputs():
    saved = build_inputs.CHAINS
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "repo"
        build_inputs.CHAINS = fixture(root)
        try:
            kernel = build_inputs.digest("kernel", root)
            rpms = build_inputs.digest("rpms", root)
            # 重复计算必须稳定，否则发布链每次都会认为输入变了。
            assert build_inputs.digest("kernel", root) == kernel

            # 无关文档：两条链都不动。
            (root / "docs/notes.md").write_text("changed notes\n")
            assert build_inputs.digest("kernel", root) == kernel
            assert build_inputs.digest("rpms", root) == rpms

            # 声明目录里的已有文件：本链与下游都变。
            (root / "patches/a.patch").write_text("patch a, edited\n")
            edited = build_inputs.digest("kernel", root)
            assert edited != kernel
            assert build_inputs.digest("rpms", root) != rpms
            kernel, rpms = edited, build_inputs.digest("rpms", root)

            # 目录里新增文件：幂等的目录列表不能漏掉新输入。
            (root / "patches/b.patch").write_text("patch b\n")
            assert build_inputs.digest("kernel", root) != kernel
            kernel = build_inputs.digest("kernel", root)

            # 解释器产物不是输入：谁在这棵树里跑过一次 python，摘要都不能变，
            # 否则同一个 commit 在 CI 与本机会算出不同的值。
            (root / "patches/__pycache__").mkdir()
            (root / "patches/__pycache__/a.cpython-313.pyc").write_bytes(b"\x00pyc")
            assert build_inputs.digest("kernel", root) == kernel

            # 只属于 rpms 的输入：kernel 不动，rpms 变。
            (root / "product.json").write_text('{"target": {"releasever": "45"}}\n')
            assert build_inputs.digest("kernel", root) == kernel
            assert build_inputs.digest("rpms", root) != rpms
            rpms = build_inputs.digest("rpms", root)

            # 锁定条目变化（commit 前进）：kernel 变，rpms 跟着变。
            lock = json.loads((root / "sources.lock.json").read_text())
            lock["sources"]["sunuefi"]["commit"] = "c2"
            (root / "sources.lock.json").write_text(json.dumps(lock))
            assert build_inputs.digest("kernel", root) != kernel
            assert build_inputs.digest("rpms", root) != rpms
        finally:
            build_inputs.CHAINS = saved


def test_declared_but_missing_input_is_an_error():
    """静默跳过等于悄悄缩小摘要的覆盖面，必须报错。"""
    saved = build_inputs.CHAINS
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory) / "repo"
        build_inputs.CHAINS = fixture(root)
        try:
            (root / "patches/a.patch").unlink()
            (root / "patches").rmdir()
            try:
                build_inputs.digest("kernel", root)
            except build_inputs.InputError as error:
                assert "patches" in str(error), error
            else:
                raise AssertionError("a missing declared directory was silently skipped")

            (root / "patches").mkdir()
            build_inputs.CHAINS["kernel"]["locks"] = ["no-such-source"]
            try:
                build_inputs.digest("kernel", root)
            except build_inputs.InputError as error:
                assert "no-such-source" in str(error), error
            else:
                raise AssertionError("an unknown lock entry was silently ignored")
        finally:
            build_inputs.CHAINS = saved


def test_cli_records_the_same_digest():
    """workflow 里用 CLI 取摘要，测试用模块取；两条路径必须是同一个值。"""
    value = build_inputs.digest("kernel", ROOT)
    name = build_inputs.artifact_name("kernel", value)
    script = str(ROOT / "scripts/build-inputs.py")
    printed = subprocess.check_output(
        [sys.executable, script, "--chain", "kernel", "--artifact-name"],
        text=True).strip()
    assert printed == name, (printed, name)
    with tempfile.TemporaryDirectory() as directory:
        record = Path(directory) / "inputs.json"
        subprocess.check_output(
            [sys.executable, script, "--chain", "kernel",
             "--record", str(record)], text=True)
        data = json.loads(record.read_text())
    assert data["digest"] == value and data["artifact"] == name, data
    assert data["chain"] == "kernel" and data["files"], data


def test_chain_covers_its_local_imports():
    """链的脚本 import 的本地模块必须在同一条链的输入里。

    漏列会静默复用旧产物：改的正是那个模块，摘要却不动。
    """
    failures = []
    for chain, spec in build_inputs.CHAINS.items():
        declared = set()
        for relative in spec["paths"]:
            path = ROOT / relative
            if path.is_file():
                declared.add(path.resolve())
        for relative in spec["paths"]:
            path = ROOT / relative
            scripts = [path] if path.is_file() else sorted(path.rglob("*.py"))
            for script in scripts:
                if script.suffix != ".py":
                    continue
                for line in script.read_text(errors="replace").splitlines():
                    words = line.split()
                    if len(words) < 2 or words[0] not in ("import", "from"):
                        continue
                    module = words[1].split(".")[0]
                    local = ROOT / "scripts" / (module + ".py")
                    if local.is_file() and local.resolve() not in declared:
                        failures.append(f"{chain}: {script.relative_to(ROOT)} imports "
                                        f"{local.relative_to(ROOT)} which the chain omits")
    if failures:
        raise AssertionError("; ".join(sorted(set(failures))))


def test_producers_upload_their_own_chain_digest():
    """每条链的产物必须自带摘要 artifact，发布链才解析得到它。"""
    failures = []
    for filename, chain in PRODUCER_CHAINS.items():
        text = (ROOT / ".github/workflows" / filename).read_text()
        if f'--chain {chain} --artifact-name' not in text:
            failures.append(f"{filename} does not record the {chain} input digest")
        if "name: ${{ env.DIGEST_ARTIFACT }}" not in text:
            failures.append(f"{filename} does not upload the input digest artifact")
        if "--record build/inputs/inputs.json" not in text:
            failures.append(f"{filename} does not write the digest record")
    if failures:
        raise AssertionError("; ".join(failures))


def test_consumers_require_the_upstream_digest():
    """消费方必须核对上游摘要，否则摘要只是自说自话。"""
    rpms = (ROOT / ".github/workflows/build-piano-rpms.yml").read_text()
    runtime = (ROOT / ".github/workflows/build-device-runtime.yml").read_text()
    rootfs = (ROOT / ".github/workflows/build-rootfs.yml").read_text()
    image = (ROOT / ".github/workflows/build-image.yml").read_text()
    assert "--require-digest kernel" in rpms, "build-piano-rpms.yml accepts any kernel run"
    assert "--require-digest kernel" in runtime, "build-device-runtime.yml accepts any kernel run"
    for chain in ("rpms", "runtime", "mesa", "ffmpeg"):
        assert f"--require-digest {chain}" in rootfs, f"build-rootfs.yml accepts {chain} runs without a digest"
    assert "for chain in rootfs kernel runtime mesa ffmpeg" in image


def main():
    for check in (test_declared_inputs_exist,
                  test_upstream_graph_is_closed_and_acyclic,
                  test_artifact_name_format,
                  test_digest_tracks_only_declared_inputs,
                  test_declared_but_missing_input_is_an_error,
                  test_cli_records_the_same_digest,
                  test_chain_covers_its_local_imports,
                  test_producers_upload_their_own_chain_digest,
                  test_consumers_require_the_upstream_digest):
        check()
    print("OK: build input digests cover declared inputs, propagate upstream and are wired into CI")


if __name__ == "__main__":
    main()
