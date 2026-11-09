#!/usr/bin/env python3
"""把 SunUEFI 声明的厂商固件叠加层装进固件目录。

SunUEFI 的 `config/release.json` 用 `firmware.overlays` 列出厂商叠层
（adsp / cdsp / fingerprint / display 校准），每份目录带一个 `manifest.json`，
逐文件记 bytes 与 sha256，可选的 `directory` 给出在固件树里的落点。上游
piano-firmware tarball 里没有 cdsp 与指纹固件，adsp 也是另一版 ROM，所以
整份清单都叠在上面，设备上的 remoteproc 才能起来。

清单也从 release.json 读，不在本脚本里另写一份：换 pin 时上游改了什么，
装进去的就是什么。
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


def apply(sunuefi: Path, firmware: Path) -> list[dict]:
    if not firmware.is_dir():
        raise ValueError("firmware directory does not exist: " + str(firmware))
    config = json.loads((sunuefi / "config/release.json").read_text())["firmware"]
    applied = []
    for entry in config["overlays"]:
        manifest_path = sunuefi / entry
        manifest = json.loads(manifest_path.read_text())
        directory = manifest_path.parent
        files = manifest["files"]
        # 固件树里的落点由清单声明，默认是根目录。display 校准要落在
        # qcom/sm8750 下，装到根部设备就找不到它。
        destination = firmware
        for part in manifest.get("directory", ".").split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise ValueError("invalid firmware directory: " + entry)
            destination = destination / part
            if destination.is_symlink():
                raise ValueError("firmware destination is a symlink: " + str(destination))
        # 整份核对通过再写第一个文件，避免装到一半才发现摘要不对。
        for name, item in files.items():
            if not name or "/" in name or name in (".", ".."):
                raise ValueError("invalid firmware filename: " + name)
            path = directory / name
            if path.is_symlink():
                raise ValueError("symlinked firmware file: " + name)
            content = path.read_bytes()
            if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("firmware checksum mismatch: " + entry + ": " + name)
        destination.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(directory / name, destination / name)
        applied.append({"manifest": entry, "component": manifest["component"],
                        "rom": manifest.get("rom", ""), "files": len(files)})
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sunuefi", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    args = parser.parse_args()
    applied = apply(args.sunuefi.resolve(), args.firmware.resolve())
    for row in applied:
        print("applied", row["component"], row["rom"], row["files"], "files", sep=" ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
