#!/usr/bin/env python3
"""导出自包含安装入口与固定上游的只读 GPT/BOOT 消费代码，不带任何设备数据。"""

import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess

from piano_artifacts import sha256, uefi_patch_identities
from piano_sparse import require

ROOT = Path(__file__).resolve().parents[1]

SCRIPTS = ("install-f2fs.py", "update-f2fs.py", "plan-first-partition.py", "prepare-device-boot.py",
           "verify-native-boot.py", "apply-patches.py", "piano_artifacts.py", "piano_sparse.py")
TOOLS = ("install_piano.py", "provision_piano_bluetooth.py", "provision_piano_ssh.py", "compose_piano_dtb.py")


def copy_verified(source, dest):
    """复制前后各算一次摘要，并核对输出与源逐字节一致。

    交付件是别人拿去写盘的输入。导出期间源文件被改写时必须在这里失败，而不是
    产出一个和源码不符的包，等到事后复算 SHA256SUMS 才发现。
    """
    require(source.is_file() and not source.is_symlink(), "expected regular export input: " + source.name)
    before = sha256(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    require(sha256(dest) == before and sha256(source) == before,
            "export input changed while copying: " + source.name)
    return before


def require_committed(names):
    """导出的是提交内容，不是工作树当前状态。

    交付包曾被就地改过一轮而没人复算 SHA256SUMS，工作树一脏包就跟着脏。
    """
    changed = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", *names],
                             capture_output=True, text=True, check=True).stdout.strip()
    require(not changed, "export inputs are not the committed tree:\n" + changed)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sunuefi", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location("installer_export", ROOT / "scripts/install-f2fs.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    installer.load_source(args.sunuefi)
    output = args.output.absolute()
    require(not output.exists() and not output.is_symlink(), "installer output already exists")
    sources = [("sources.lock.json", "sources.lock.json"), ("docs/installation.md", "INSTALL.md")]
    sources += [("scripts/" + name, "scripts/" + name) for name in SCRIPTS]
    sources += [(name, name) for name in uefi_patch_identities(ROOT)]
    require_committed([name for name, _ in sources])
    (output / "scripts").mkdir(parents=True)
    for name, target in sources:
        copy_verified(ROOT / name, output / target)
    tools = output / "sunuefi/tools"
    tools.mkdir(parents=True)
    for name in TOOLS:
        copy_verified(args.sunuefi / "tools" / name, tools / name)
    pin = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    (output / "sunuefi/source-manifest.json").write_text(json.dumps({"commit": pin,
        "files": {"tools/" + p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in sorted(tools.iterdir())}}, indent=2) + "\n")
    files = [p for p in sorted(output.rglob("*")) if p.is_file()]
    (output / "SHA256SUMS").write_text("".join(sha256(p) + "  " + p.relative_to(output).as_posix() + "\n" for p in files))
    print(json.dumps({"status": "INSTALLER_EXPORTED_NOT_DEVICE_VERIFIED", "files": len(files), "sunuefi_commit": pin}))


if __name__ == "__main__":
    main()
