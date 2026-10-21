#!/usr/bin/env python3
"""Release 资产命名：哪些文件四档共用，哪些按桌面加后缀。

四档镜像共用同一个 ESP、内核、initramfs 与 UEFI，差的是 root 文件系统。
所以 Release 里的资产分两类：

- 平台资产：四档逐字节相同，只上传一份，名字不变。
- 桌面资产：每档各一份，名字在第一处 `.` 前插入 `-<desktop>`
  （`pianoroot.f2fs.img.zst` → `pianoroot-gnome.f2fs.img.zst`，
  `root-install-manifest.json` → `root-install-manifest-gnome.json`）。

`unpack-release.sh` 用同一套规则还原成规范名，两边由
`tests/check_release_assets.py` 逐项比对，防止命名分叉。规则本身只有一处
实现：`desktop_asset()` 与 `published_name()` 是同一个函数。

用法：
    python3 scripts/release-assets.py classify <name>
    python3 scripts/release-assets.py name <name> <desktop>
    python3 scripts/release-assets.py platform
"""

import argparse
import sys

# 四份 bundle 必须逐字节相同、只上传一份的文件。bundle-manifest.json、
# root-image-manifest.json、rootfs-manifest.json、source-runs.json 与
# SHA256SUMS 都在每档里各写各的，因此不在这个集合里。
#
# esp-manifest.json 也不在：它是上游 package_release.py 的产物，`files`
# 一节记录了输出目录里每个文件（含按桌面不同的 root.tar.zst）的大小与
# 摘要，四档的字节不同。拿它当共用资产会在发布门禁里被误判为平台分叉。
PLATFORM_ASSETS = (
    "PianoUEFI-product.img",
    "sunuefi_esp.img",
    "boot_sunuefi.img",
    "uefi-source.json",
    "kernel-manifest.json",
    "initramfs.tar.zst",
    "initramfs-manifest.json",
    "diagnostic-abl-storage.tar.zst",
    "runtime-manifest.json",
    "mesa-manifest.json",
    "ffmpeg-manifest.json",
)

# root-install 侧：每档各一份，名字走同一条规则。
DESKTOP_INSTALL_ASSETS = (
    "root-install-manifest.json",
    "root-install-SHA256SUMS",
    "root-install-pianoroot.f2fs.sparse.img.zst",
)


def published_name(name, desktop):
    """桌面资产在 Release 里的名字：第一处 `.` 前插入 `-<desktop>`。"""
    if not desktop:
        raise ValueError("desktop is required for a desktop asset")
    stem, dot, rest = name.partition(".")
    if dot:
        return f"{stem}-{desktop}.{rest}"
    return f"{name}-{desktop}"


def classify(name):
    """`platform` 表示四档共用一份；其余一律按桌面资产处理。

    默认按桌面资产处理是有意为之：新出现的文件不会被悄悄当成共用资产
    上传，漏进四档差异时发布门禁会直接失败。
    """
    return "platform" if name in PLATFORM_ASSETS else "desktop"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("classify", help="打印 platform 或 desktop")
    one.add_argument("name")
    named = sub.add_parser("name", help="打印桌面资产在 Release 里的名字")
    named.add_argument("name")
    named.add_argument("desktop")
    sub.add_parser("platform", help="打印全部平台资产，一行一个")
    args = parser.parse_args(argv)
    if args.command == "platform":
        print("\n".join(PLATFORM_ASSETS))
        return 0
    if args.command == "classify":
        print(classify(args.name))
        return 0
    try:
        print(published_name(args.name, args.desktop))
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
