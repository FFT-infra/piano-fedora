#!/usr/bin/env python3
"""Release 里每档一个自包含归档：命名、成员表与 bundle 核对。

四档镜像共用同一个 ESP、内核与 UEFI，差的是 root 文件系统。Release 把它们
按桌面打成一个归档，用户只下自己那一档：

    piano-<tag>-<desktop>.tar.zst[.part-NN]   归档，超过 2 GiB 时切片
    piano-<tag>-<desktop>.sha256              这一档已上传文件的摘要

归档解到当前目录，就是安装指南里的暂存目录。刷写脚本、boot-tools、平台
镜像、这一档的 manifest 与 root-install 全在里面，不需要再拼装第二次。

本文件是成员表的唯一来源：release.yml 按它装配归档，unpack-release.sh 与
文档按它核对，tests/check_release_assets.py 逐项比对。

用法：
    python3 scripts/release-assets.py archive-name <tag> <desktop>
    python3 scripts/release-assets.py sidecar-name <tag> <desktop>
    python3 scripts/release-assets.py bundled
    python3 scripts/release-assets.py excluded
    python3 scripts/release-assets.py members
    python3 scripts/release-assets.py check-bundle <deploy-bundle dir>
    python3 scripts/release-assets.py verify-name <path>
"""

import argparse
import hashlib
import re
import sys
from pathlib import Path

ARCHIVE_STEM = "piano"

# 从 deploy-bundle 原样复制进归档的文件。三份镜像刷设备或供校验，其余是
# 装机脚本与构建记录读的 manifest。
BUNDLE_MEMBERS = (
    "PianoUEFI-product.img",
    "boot_sunuefi.img",
    "sunuefi_esp.img",
    "bundle-manifest.json",
    "esp-manifest.json",
    "ffmpeg-manifest.json",
    "initramfs-manifest.json",
    "kernel-manifest.json",
    "mesa-manifest.json",
    "root-image-manifest.json",
    "rootfs-manifest.json",
    "runtime-manifest.json",
    "source-runs.json",
    "uefi-source.json",
)

# deploy-bundle 里有、但不进归档的文件。
#
# 两份 root 镜像与 root-install 里的 root 内容相同，也不能直接刷：装机的
# 那份由 prepare-install 写好设备配置再扩容。同一份 root 发三种形态只会让
# 人分不清该下哪个，还把归档撑大。`initramfs.tar.zst` 是 build-bundle 解包
# 用的中间产物，内容与 `boot_sunuefi.img` 里那份相同；
# `diagnostic-abl-storage.tar.zst` 是 ABL 只读探针的调试件，manifest 自标
# `device_tested: false`，装机脚本不读。`SHA256SUMS` 对应的清单在归档里重新
# 生成，只列归档里真实存在的文件。
EXCLUDED_BUNDLE_FILES = (
    "diagnostic-abl-storage.tar.zst",
    "initramfs.tar.zst",
    "pianoroot.f2fs.img.zst",
    "pianoroot.f2fs.sparse.img",
    "SHA256SUMS",
)

# 归档里的目录。每个目录都由 release.yml 现场装配：
#
#   boot-tools/      Android 侧选择器模块与 aarch64 原生重打包器
#   host-boot-tool/  x86-64 宿主机重打包器
#   scripts/         prepare-device-boot.py 与它的两个辅助模块
#   root-install/    这一档的 root 传输镜像、清单与摘要
ARCHIVE_DIRS = ("boot-tools", "host-boot-tool", "root-install", "scripts")

# 归档顶层的普通文件。`SHA256SUMS` 由 release.yml 在装配完后按目录里的实际
# 内容生成，不是从别处复制来的。
ARCHIVE_FILES = BUNDLE_MEMBERS + (
    "README-INSTALL.txt",
    "SHA256SUMS",
    "flash-linux.bat",
    "flash-linux.sh",
    "make-device-boot.sh",
)

_NAME = re.compile(r"[a-z0-9][a-z0-9.-]*")


def _checked(value, what):
    if not value or not _NAME.fullmatch(value) or ".." in value:
        raise ValueError(f"invalid {what}: {value!r}")
    return value


def archive_name(tag, desktop):
    """`piano-<tag>-<desktop>.tar.zst`。"""
    return f"{ARCHIVE_STEM}-{_checked(tag, 'tag')}-{_checked(desktop, 'desktop')}.tar.zst"


def sidecar_name(tag, desktop):
    """`piano-<tag>-<desktop>.sha256`，列这一档已上传的分片。"""
    return f"{ARCHIVE_STEM}-{_checked(tag, 'tag')}-{_checked(desktop, 'desktop')}.sha256"


def members():
    """归档顶层条目，文件与目录各按名字排序。"""
    return sorted(ARCHIVE_FILES) + sorted(ARCHIVE_DIRS)


def verify_name(path):
    """`archive` 表示这个相对路径属于归档，`excluded` 表示不属于。"""
    top = path.strip("/").split("/")[0]
    if top in ARCHIVE_FILES or top in ARCHIVE_DIRS:
        return "archive"
    return "excluded"


def write_sums(directory):
    """按目录里的实际内容写 `SHA256SUMS`，返回行数。

    清单只列真实存在的文件。机读清单里混进未发布的成员，手动路径的
    `sha256sum -c SHA256SUMS` 就会恒返回 1。
    """
    root = Path(directory)
    rows = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path == root / "SHA256SUMS":
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8 << 20), b""):
                digest.update(chunk)
        rows.append((str(path.relative_to(root)), digest.hexdigest()))
    (root / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(rows)))
    return len(rows)


def check_bundle(directory):
    """核对 deploy-bundle 的文件集合，返回缺失与未知两份名单。

    发布只能装配它认识的成员。bundle 里多出一个文件说明上游改了产物，
    这里必须失败，不能悄悄漏发。
    """
    present = {item.name for item in Path(directory).iterdir() if item.is_file()}
    expected = set(BUNDLE_MEMBERS) | set(EXCLUDED_BUNDLE_FILES)
    return sorted(expected - present), sorted(present - expected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    named = sub.add_parser("archive-name", help="打印这一档的归档名")
    named.add_argument("tag")
    named.add_argument("desktop")
    sidecar = sub.add_parser("sidecar-name", help="打印这一档的摘要文件名")
    sidecar.add_argument("tag")
    sidecar.add_argument("desktop")
    sub.add_parser("bundled", help="打印从 deploy-bundle 复制进归档的文件")
    sub.add_parser("excluded", help="打印 deploy-bundle 里不进归档的文件")
    sub.add_parser("members", help="打印归档顶层条目")
    check = sub.add_parser("check-bundle", help="核对 deploy-bundle 的文件集合")
    check.add_argument("directory")
    verify = sub.add_parser("verify-name", help="打印 archive 或 excluded")
    verify.add_argument("path")
    sums = sub.add_parser("write-sums", help="按目录实际内容写 SHA256SUMS")
    sums.add_argument("directory")
    args = parser.parse_args(argv)
    try:
        if args.command == "archive-name":
            print(archive_name(args.tag, args.desktop))
        elif args.command == "sidecar-name":
            print(sidecar_name(args.tag, args.desktop))
        elif args.command == "bundled":
            print("\n".join(BUNDLE_MEMBERS))
        elif args.command == "excluded":
            print("\n".join(EXCLUDED_BUNDLE_FILES))
        elif args.command == "members":
            print("\n".join(members()))
        elif args.command == "verify-name":
            print(verify_name(args.path))
        elif args.command == "write-sums":
            print(f"OK: SHA256SUMS lists {write_sums(args.directory)} files")
        else:
            missing, unknown = check_bundle(args.directory)
            if missing:
                print("deploy-bundle lacks: " + " ".join(missing), file=sys.stderr)
            if unknown:
                print("deploy-bundle carries unclassified files: " + " ".join(unknown),
                      file=sys.stderr)
            return 1 if missing or unknown else 0
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
