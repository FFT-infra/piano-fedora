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
    python3 scripts/release-assets.py platform-name <name>
    python3 scripts/release-assets.py platform-compressed
    python3 scripts/release-assets.py manifest-members
    python3 scripts/release-assets.py archive-members
"""

import argparse
import sys

# 四份 bundle 必须逐字节相同、只上传一份的文件。
#
# 每档各写各的不在这个集合里：bundle-manifest.json、root-image-manifest.json、
# rootfs-manifest.json、source-runs.json 与 SHA256SUMS 走
# DESKTOP_BUNDLE_ARCHIVE。esp-manifest.json 同理——它是上游
# package_release.py 的产物，`files` 一节记录了输出目录里每个文件（含按
# 桌面不同的 root.tar.zst）的大小与摘要，四档字节不同，拿它当共用资产会在
# 发布门禁里被误判为平台分叉。
#
# initramfs.tar.zst 与 diagnostic-abl-storage.tar.zst 曾经在这里，现在不发：
# 前者是 build-bundle 解包用的中间产物，内容与 boot_sunuefi.img 里那份
# 逐字节相同；后者是 ABL 只读探针的调试件，manifest 自标 device_tested:
# false，装机脚本不读。两者都从对应的 workflow artifact 取。
PLATFORM_ASSETS = (
    "PianoUEFI-product.img",
    "sunuefi_esp.img",
    "boot_sunuefi.img",
    "uefi-source.json",
    "kernel-manifest.json",
    "initramfs-manifest.json",
    "runtime-manifest.json",
    "mesa-manifest.json",
    "ffmpeg-manifest.json",
)

# 三份镜像压缩发布，Release 里的名字是规范名加 `.zst`。
#
# ESP 是 512 MiB 的 ext4，绝大部分扇区是零，zstd -10 后只剩 36 MiB；另外
# 两份也各能省一半。不压缩时每个用户白下 510 MiB，比资产条数重要得多。
# 装机脚本读的仍是解压后的规范名，由 unpack-release.sh 还原；从源码构建时
# deploy-bundle 里本来就是解压状态，脚本会跳过。
PLATFORM_COMPRESSED = (
    "PianoUEFI-product.img",
    "sunuefi_esp.img",
    "boot_sunuefi.img",
)

# 每档的 bundle manifest 与 root-install 的两份小文件不单独发，打成一个 tar
# 发布。装机脚本要读它们（flash-linux.sh 用 bundle-manifest 与
# root-image-manifest 证明 root-install 来自这个 bundle，install-f2fs.py 的
# 首装还要读 source-runs），但它们全是给脚本读的 JSON 与摘要，没有一个需要
# 单独点开。逐个发会让每档占 9 个资产条目，四档就是 36 个。
DESKTOP_BUNDLE_ARCHIVE = "manifests.tar.zst"

# 打进 DESKTOP_BUNDLE_ARCHIVE 的成员，相对 deploy-bundle 根目录。这是唯一
# 一处定义：release.yml 的 tar 命令与 unpack-release.sh 的核对都按它来，
# tests/check_release_assets.py 逐项比对，防止两边分叉。
MANIFEST_MEMBERS = (
    "bundle-manifest.json",
    "esp-manifest.json",
    "root-image-manifest.json",
    "rootfs-manifest.json",
    "source-runs.json",
    "SHA256SUMS",
)

# 同一个归档里还带这一档 root-install 的两份小文件。它们来自 prepare-install
# 而不是 deploy-bundle，所以单独列；`root-install/` 前缀让解包后直接落在
# 装机脚本要求的位置，不需要再挪一次。
ROOT_INSTALL_MEMBERS = (
    "root-install/root-install-manifest.json",
    "root-install/SHA256SUMS",
)

# root-install 侧：只有 root 传输镜像单独发。清单与摘要进了归档。
DESKTOP_INSTALL_ASSETS = (
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


def platform_published_name(name):
    """平台资产在 Release 里的名字：三份镜像加 `.zst`，其余同名。

    `unpack-release.sh` 不查这张表，它只认「同名文件在就用、`.zst` 在就解压」，
    所以这条规则在 shell 侧没有第二份实现。
    """
    return f"{name}.zst" if name in PLATFORM_COMPRESSED else name


def classify(name):
    """`platform` 表示四档共用一份；`manifest-archive` 表示打 tar；
    其余一律按桌面资产处理。

    默认按桌面资产处理是有意为之：新出现的文件不会被悄悄当成共用资产
    上传，漏进四档差异时发布门禁会直接失败。
    """
    if name in PLATFORM_ASSETS:
        return "platform"
    if name in MANIFEST_MEMBERS:
        return "manifest-archive"
    return "desktop"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    one = sub.add_parser("classify", help="打印 platform、manifest-archive 或 desktop")
    one.add_argument("name")
    named = sub.add_parser("name", help="打印桌面资产在 Release 里的名字")
    named.add_argument("name")
    named.add_argument("desktop")
    sub.add_parser("platform", help="打印全部平台资产，一行一个")
    sub.add_parser("platform-compressed", help="打印压缩发布的平台资产，一行一个")
    named_platform = sub.add_parser("platform-name", help="打印平台资产在 Release 里的名字")
    named_platform.add_argument("name")
    sub.add_parser("manifest-members", help="打印打进 manifests.tar.zst 的成员，一行一个")
    sub.add_parser("archive-members", help="打印归档里的全部成员，含 root-install 那两份")
    args = parser.parse_args(argv)
    if args.command == "platform":
        print("\n".join(PLATFORM_ASSETS))
        return 0
    if args.command == "platform-compressed":
        print("\n".join(PLATFORM_COMPRESSED))
        return 0
    if args.command == "manifest-members":
        print("\n".join(MANIFEST_MEMBERS))
        return 0
    if args.command == "archive-members":
        print("\n".join(MANIFEST_MEMBERS + ROOT_INSTALL_MEMBERS))
        return 0
    if args.command == "classify":
        print(classify(args.name))
        return 0
    if args.command == "platform-name":
        print(platform_published_name(args.name))
        return 0
    try:
        print(published_name(args.name, args.desktop))
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
