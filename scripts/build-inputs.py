#!/usr/bin/env python3
"""算一条构建链的输入摘要。

发布链原来按 commit 解析上游产物：任何一次提交都让五条长链作废，哪怕
真正相关的文件没变。这里把「这条链用到了什么」变成一个 sha256，产物
只要带上同名的小 artifact，下游就能按内容而不是按 commit 复用。

摘要由三部分组成：

- `locks`：`sources.lock.json` 里这条链消费的上游条目（整条记录）。
- `paths`：仓库内的文件或目录，逐文件取 sha256。
- `upstream`：上游链的摘要，递归合入。

漏掉一个输入会让旧产物被当成新的，所以目录与脚本按构建器实际读到的
范围列，`tests/check_build_inputs.py` 校验每项都存在、无关改动不改变
摘要、相关改动一定改变摘要。

用法：
    scripts/build-inputs.py --chain kernel
    scripts/build-inputs.py --chain kernel --record build/inputs.json
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 每条链的输入。`paths` 里给目录就展开成目录下全部文件，文件不存在
# 视为错误：静默跳过等于悄悄缩小摘要的覆盖面。
CHAINS = {
    # 内核与 SDK：SunUEFI 与内核两个 pin、全部补丁、准备脚本。
    "kernel": {
        "locks": ["sunuefi", "linux-piano"],
        "paths": [
            "patches",
            "scripts/apply-patches.py",
            "scripts/prepare-production-kernel.py",
            "scripts/seed-kernel-source.py",
            ".github/workflows/build-kernel.yml",
        ],
        "upstream": [],
    },
    # Mesa：补丁来自锁定的 piano-mesa，SRPM 与改法写在脚本里。
    "mesa": {
        "locks": ["piano-mesa"],
        "paths": [
            "product.json",
            "scripts/build-fedora-mesa.py",
            "scripts/piano_artifacts.py",
            "scripts/checkout-sources.py",
            ".github/workflows/build-mesa.yml",
        ],
        "upstream": [],
    },
    # FFmpeg：BSP 补丁系列随 SunUEFI 走，SRPM 与端口规则写在脚本里。
    "ffmpeg": {
        "locks": ["sunuefi"],
        "paths": [
            "product.json",
            "scripts/build-fedora-ffmpeg.py",
            "scripts/piano_artifacts.py",
            "scripts/checkout-sources.py",
            ".github/workflows/build-ffmpeg.yml",
            ".github/workflows/host-check.yml",
        ],
        "upstream": [],
    },
    # Fedora 矩阵 RPM：全部包定义与锁定上游，并消费内核产物。
    "rpms": {
        "locks": [
            "sunuefi", "linux-piano", "dtc", "piano-firmware", "piano-sensors",
            "fastrpc", "librevm", "crosvm", "piano-integration",
        ],
        # build-rpms.sh 会把下面几个脚本与 initramfs 片段打进 RPM 载荷。
        "paths": [
            "packages",
            "patches",
            "product.json",
            "scripts/build-rpms.sh",
            "scripts/compose-board-dtb.py",
            "scripts/package_sets.py",
            "scripts/package-sets.json",
            "scripts/build-initramfs.py",
            "scripts/piano_artifacts.py",
            "scripts/piano-kernel-esp-update",
            "initramfs/f2fs-disk-bootstrap",
            "scripts/checkout-sources.py",
            "scripts/seed-kernel-source.py",
            "scripts/prepare-runtime-kernel.py",
            ".github/workflows/build-piano-rpms.yml",
        ],
        "upstream": ["kernel"],
    },
    # 设备运行时：锁定源码、设备程序与配置，并消费内核产物。
    "runtime": {
        "locks": [
            "sunuefi", "linux-piano", "fastrpc", "piano-sensors",
            "librevm", "crosvm", "piano-integration",
        ],
        "paths": [
            "packages",
            "patches",
            "product.json",
            # 包归属契约：构建时逐条核对，改它等于改产物。
            "runtime/matrix-owned-paths.txt",
            "scripts/build-device-runtime.py",
            "scripts/piano_artifacts.py",
            # 运行时的触控与相机增量都在这两个模块里，构建器 import 它们。
            "scripts/piano_device_integration.py",
            "scripts/piano_runtime_extras.py",
            "scripts/checkout-sources.py",
            "scripts/prepare-runtime-kernel.py",
            "scripts/seed-kernel-source.py",
            ".github/workflows/build-device-runtime.yml",
        ],
        "upstream": ["kernel"],
    },
    # Fedora 基础树：配方与装配脚本，并消费矩阵、运行时与两条图形栈。
    "rootfs": {
        "locks": [],
        "paths": [
            "product.json",
            "scripts/build-clean-rootfs.py",
            "scripts/finalize-fedora-rootfs.py",
            "scripts/package_sets.py",
            "scripts/package-sets.json",
            "scripts/piano_desktop.py",
            "scripts/piano_artifacts.py",
            "scripts/apply-rootfs-policy.py",
            "scripts/relabel-selinux.py",
            "scripts/build-f2fs-image.py",
            "scripts/tree_metadata.py",
            "scripts/profiles",
            "runtime/matrix-owned-paths.txt",
            ".github/workflows/build-rootfs.yml",
        ],
        "upstream": ["rpms", "runtime", "mesa", "ffmpeg"],
    },
}


class InputError(Exception):
    """输入不满足约定，属于可预期失败。"""


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expand(root, relative):
    path = Path(root) / relative
    if path.is_file():
        return [path]
    if path.is_dir():
        # 解释器产物不是仓库内容：谁在这棵树里跑过一次 python，摘要就变，
        # 于是同一个 commit 在 CI 与本机会算出不同的值，按内容复用失效。
        return sorted(p for p in path.rglob("*")
                      if p.is_file() and "__pycache__" not in p.parts
                      and p.suffix != ".pyc")
    raise InputError(f"declared input is missing: {relative}")


# 摘要只认决定产物的字段。checkout-sources.py 只按 url 取源、按 commit
# 检出；role 是说明文字，checked_date 是核对日期。把它们算进摘要，改一句
# 注释就会让整条内核链作废，得重编一次内核。
LOCKED_FIELDS = ("url", "commit")


def lock_entries(root, keys):
    lockfile = Path(root) / "sources.lock.json"
    try:
        sources = json.loads(lockfile.read_text(encoding="utf-8"))["sources"]
    except (OSError, ValueError, KeyError) as error:
        raise InputError(f"cannot read {lockfile}: {error}") from error
    entries = {}
    for key in keys:
        if key not in sources:
            raise InputError(f"unknown lock entry: {key}")
        entries[key] = {field: sources[key].get(field) for field in LOCKED_FIELDS}
    return entries


def payload(chain, root=ROOT, upstream=None):
    """返回这条链连同其上游的可序列化摘要输入。"""
    if chain not in CHAINS:
        raise InputError("unknown chain: " + chain)
    spec = CHAINS[chain]
    files = {}
    for relative in spec["paths"]:
        for path in expand(root, relative):
            files[path.relative_to(root).as_posix()] = sha256_file(path)
    resolved = {}
    for name in spec["upstream"]:
        supplied = (upstream or {}).get(name)
        if supplied is None:
            supplied = digest(name, root, upstream)
        resolved[name] = supplied
    return {
        "chain": chain,
        "locks": lock_entries(root, spec["locks"]),
        "files": files,
        "upstream": resolved,
    }


def digest(chain, root=ROOT, upstream=None):
    data = payload(chain, root, upstream)
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def artifact_name(chain, value):
    return f"input-digest-{chain}-{value}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--chain", required=True, choices=sorted(CHAINS))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--record", type=Path,
                        help="把摘要记录写成 JSON，供 artifact 一起上传")
    parser.add_argument("--artifact-name", action="store_true",
                        help="只打印应上传的小 artifact 名")
    args = parser.parse_args(argv)

    try:
        value = digest(args.chain, args.root)
        if args.artifact_name:
            print(artifact_name(args.chain, value))
            return 0
        data = payload(args.chain, args.root)
        data["digest"] = value
        data["artifact"] = artifact_name(args.chain, value)
        if args.record:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(json.dumps(data, indent=2, sort_keys=True,
                                              ensure_ascii=False) + "\n")
        print(value)
        return 0
    except InputError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
