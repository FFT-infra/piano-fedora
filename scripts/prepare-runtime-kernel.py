#!/usr/bin/env python3
"""在 Actions 上恢复同一内核 SDK，构建匹配的 v4l2loopback 与 UAPI。"""

import argparse
import difflib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from piano_artifacts import inspect_kernel, sha256

# syncconfig 会用当前主机的 rustc 重算这些探测项。Rust 未启用时它们不参与
# 内核或模块编译，构建机与运行机之间的小版本差异（1.99.0 对 1.99.1）不应
# 让封印配置比对失败。
HOST_TOOLCHAIN_PROBES = ("CONFIG_RUSTC_",)


def comparable_config(text):
    """去掉未启用 Rust 时的工具链探测项，其余内容保持逐字节比较。"""
    lines = text.splitlines()
    if any(line == "CONFIG_RUST=y" for line in lines):
        return "\n".join(lines)
    return "\n".join(line for line in lines if not line.startswith(HOST_TOOLCHAIN_PROBES))


def run(command, **kw):
    subprocess.run([str(x) for x in command], check=True, **kw)


def localversion_override(sealed_config, release):
    """外部模块编译要复现封存内核构建时传入的 LOCALVERSION。

    SunUEFI 的封存内核把 `-b<提交时间>-g<ABI 摘要>` 通过 make 的
    LOCALVERSION 写进 release（build_piano_full_kernel.py 里替换掉空值），
    内核自身的 UTS_RELEASE 因此带这段后缀。外部模块若仍以空 LOCALVERSION
    编译，vermagic 会少这一段，与封存内核配不上。这里从封存配置里取出
    CONFIG_LOCALVERSION，剩下的一段就是构建时传入的值。
    """
    match = re.search(r'^CONFIG_LOCALVERSION="(.*)"$', sealed_config, re.M)
    configured = match.group(1) if match else ""
    if not configured:
        return ""
    if configured not in release:
        raise ValueError(
            f"kernel release {release!r} does not contain the configured localversion {configured!r}")
    return release.split(configured, 1)[1]


def apply_patch_series(series, target):
    """按 series.json 的 sha256 校验后套用，参数与上游 runtime 构建一致。

    锁定的 v4l2loopback 缺两个消费端要用的能力：`S_FMT` 会把请求钳回生产端
    尺寸，`VIDIOC_SUBSCRIBE_EVENT` 不认 `V4L2_EVENT_SOURCE_CHANGE`。上游的
    三份补丁解决这两点，本脚本此前直接编未打补丁的源码。缺补丁时相机的
    高像素路径在设备上退化成预览尺寸，且没有报错。
    """
    applied = []
    for entry in json.loads(series.read_text())["patches"]:
        patch = series.parent / entry["file"]
        if sha256(patch) != entry["sha256"]:
            raise ValueError("v4l2loopback patch checksum differs: " + str(patch))
        run(["patch", "--batch", "--forward", "--fuzz=0",
             "--no-backup-if-mismatch", "-p1", "-i", patch], cwd=target)
        applied.append({"file": entry["file"], "sha256": entry["sha256"]})
    return applied


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    for name in ("sunuefi", "kernel", "sdk", "output"):
        ap.add_argument("--" + name, required=True, type=Path)
    args = ap.parse_args()
    sun, kernel, sdk, output = [getattr(args, name).resolve() for name in ("sunuefi", "kernel", "sdk", "output")]
    record, kernel_hash = inspect_kernel(kernel)
    if sha256(sdk / "kernel-sdk-manifest.json") != kernel_hash:
        raise ValueError("SDK does not belong to the selected sealed kernel")
    output.mkdir(parents=True, exist_ok=False)
    run(["python3", sun / "tools/prepare_release_kernel.py", "--refresh"], cwd=sun)
    # 7.3 的封存序列自带 Gunyah 驱动（序列里的 0006），派生树就是准备工具
    # 重放出来的那棵，不再有第二遍导入和对应的重放证明。
    prepared = json.loads((sun / "build/release/source-manifest.json").read_text())
    if (prepared["actual_commit"], prepared["actual_tree"]) != (record["source_commit"], record["source_tree"]):
        raise ValueError("replayed kernel source differs from the artifact")
    archive = next(sdk.glob("kernel-sdk-*.tar.zst"))
    restored = output / "sdk"
    restored.mkdir()
    run(["tar", "-I", "zstd", "-xf", archive, "-C", restored])
    if sha256(restored / ".config") != record["config_sha256"]:
        raise ValueError("SDK configuration differs from the sealed kernel")
    sealed_config = (restored / ".config").read_text()
    # 编译外部模块使用 SunUEFI 固定下载的 LLVM 工具链（支持 -mtune=oryon-1），Fedora 只编用户态程序。
    # SunUEFI 把 tools/host/ 改名成 tools/desktop/，模块本身没动。
    sys.path.insert(0, str(sun / "tools"))
    from desktop import prepare_kernel_llvm
    kernel_env = prepare_kernel_llvm.environment()
    build = sun / "build/kernels/fedora-runtime"
    build.mkdir(parents=True)
    shutil.copy2(restored / ".config", build / ".config")
    shutil.copy2(restored / "Module.symvers", build / "Module.symvers")
    # LOCALVERSION 必须显式给出：置空时 setlocalversion 会补一个 "+"，而带上
    # 封存内核的尾缀才能让模块 vermagic 与内核 release 一致。
    localversion = localversion_override(sealed_config, record["kernel_release"])
    command = ["make", "-C", prepared["worktree"], "O=" + str(build),
               "ARCH=arm64", "LLVM=1", "LLVM_IAS=1", "LOCALVERSION=" + localversion]
    run([*command, "-j4", "modules_prepare"], env=kernel_env)
    # 模块构建目录里重建的 release 必须与封存内核一致，否则后面 vermagic
    # 对不上只报一句笼统的错，看不出是哪一段版本串丢了。
    generated = (build / "include/config/kernel.release")
    if generated.is_file():
        rebuilt = generated.read_text().strip()
        if rebuilt != record["kernel_release"]:
            raise ValueError(
                f"module build kernel release {rebuilt!r} != sealed {record['kernel_release']!r}")
    after_config = (build / ".config").read_text()
    if comparable_config(after_config) != comparable_config(sealed_config):
        # syncconfig 会重写 .config；把差异打出来，否则只能看到一条哈希不符。
        changed = difflib.unified_diff(
            comparable_config(sealed_config).splitlines(),
            comparable_config(after_config).splitlines(),
            "sealed", "after modules_prepare", lineterm="", n=0)
        raise ValueError("module preparation changed the kernel configuration:\n"
                         + "\n".join(list(changed)[:40]))
    uapi = output / "uapi"
    run([*command, "headers_install", "INSTALL_HDR_PATH=" + str(uapi)], env=kernel_env)
    loop = output / "v4l2loopback"
    shutil.copytree(sun / "upstream/v4l2loopback", loop,
                    ignore=shutil.ignore_patterns(".git"))
    patches = apply_patch_series(sun / "patches/v4l2loopback/0.15.4/series.json", loop)
    run([*command, "-j4", "M=" + str(loop), "modules"], env=kernel_env)
    module = loop / "v4l2loopback.ko"
    release = subprocess.check_output(["modinfo", "-F", "vermagic", str(module)], text=True, env=kernel_env)
    if release.split()[0] != record["kernel_release"]:
        raise ValueError(
            f"external module vermagic {release.split()[0]!r} "
            f"differs from the selected kernel {record['kernel_release']!r}")
    proof = {"kernel_manifest_sha256": kernel_hash,
             "kernel_release": record["kernel_release"], "kernel_commit": record["source_commit"],
             "module_sha256": sha256(module), "vermagic": release.strip(),
             "v4l2loopback_patches": patches,
             "sdk_config_sha256": sha256(build / ".config"),
             "module_symvers_sha256": sha256(build / "Module.symvers"), "device_tested": False}
    (output / "manifest.json").write_text(json.dumps(proof, indent=2) + "\n")


if __name__ == "__main__":
    main()
