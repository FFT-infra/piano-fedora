#!/usr/bin/env bash
# Reassemble and unpack the flat GitHub Release assets into the layout that
# flash-linux.sh and the manual instructions expect:
#
#   ./                  deploy-bundle files (ESP, Linux boot payload, manifests)
#   root-install/       prepared root transport, manifest, checksums
#
# GitHub rejects files of 2 GiB or more, so the large images are shipped as
# numbered pieces and the root transports are zstd-compressed. Run this script
# from the directory that holds the assets you downloaded; it checks them
# against RELEASE-SHA256SUMS itself:
#
#   bash unpack-release.sh [--desktop <name>]
#
# A release may carry several desktops. ESP, kernel and initramfs are shared and
# published once without a suffix; the root images and manifests are published as
# `<stem>-<desktop>.<rest>` (see scripts/release-assets.py). With only one
# desktop the flag can be left out; with several it is required.
set -euo pipefail

cd "$(dirname "$0")"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

DESKTOP="${PIANO_DESKTOP:-}"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --desktop) [ "$#" -ge 2 ] || die "--desktop needs a value"; DESKTOP=$2; shift 2 ;;
        --desktop=*) DESKTOP=${1#--desktop=}; shift ;;
        -h|--help) sed -n '1,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

for tool in sha256sum zstd; do
    command -v "$tool" >/dev/null || die "missing $tool (Fedora: sudo dnf install coreutils zstd)"
done

[ -f RELEASE-SHA256SUMS ] || die "RELEASE-SHA256SUMS is missing; download the platform assets and one desktop's root-install"
if [ -e root-install ]; then
    die "root-install/ already exists; unpack in a fresh directory"
fi
if [ -e pianoroot.f2fs.sparse.img ]; then
    die "this directory already holds a decompressed root transport; use a fresh directory"
fi

# The published name of a desktop asset: `-<desktop>` goes before the first `.`.
# scripts/release-assets.py is the same rule; tests/check_release_assets.py
# compares the two so they cannot drift.
published_name() {
    local name=$1 desktop=$2
    case "$name" in
        *.*) printf '%s-%s.%s' "${name%%.*}" "$desktop" "${name#*.}" ;;
        *) printf '%s-%s' "$name" "$desktop" ;;
    esac
}

# Desktop assets in this release, read from the one name that is unambiguous
# (`root-install-manifest-<desktop>.json`). Legacy single-desktop releases
# publish the unsuffixed names and keep working unchanged.
desktops=()
for manifest in root-install-manifest-*.json; do
    [ -f "$manifest" ] || continue
    desktops+=("${manifest#root-install-manifest-}")
done
for index in "${!desktops[@]}"; do
    desktops[index]=${desktops[index]%.json}
done
if [ "${#desktops[@]}" -eq 0 ] && [ -f root-install-manifest.json ]; then
    # 旧 Release：没有桌面后缀，也没有可选的档位。
    desktops=()
    DESKTOP=""
elif [ "${#desktops[@]}" -eq 1 ]; then
    if [ -z "$DESKTOP" ]; then
        DESKTOP=${desktops[0]}
    elif [ "$DESKTOP" != "${desktops[0]}" ]; then
        die "this release carries desktop '${desktops[0]}', not '${DESKTOP}'"
    fi
elif [ "${#desktops[@]}" -gt 1 ]; then
    if [ -z "$DESKTOP" ]; then
        die "this release carries several desktops (${desktops[*]}); pass --desktop <name>"
    fi
    found=0
    for name in "${desktops[@]}"; do
        [ "$name" = "$DESKTOP" ] || continue
        found=1
    done
    [ "$found" -eq 1 ] || die "desktop '${DESKTOP}' is not in this release (${desktops[*]})"
fi
if [ -n "$DESKTOP" ]; then
    say "Desktop: ${DESKTOP}"
fi

# resolve NAME: print the file in this directory that holds the canonical asset
# NAME, preferring the exact name (platform assets and legacy releases).
resolve() {
    local name=$1 published
    if [ -f "$name" ]; then
        printf '%s' "$name"
        return 0
    fi
    # 分片的平台资产（旧 Release 的大镜像）没有后缀，但也没有单文件。
    if compgen -G "$name.part-*" >/dev/null; then
        printf '%s' "$name"
        return 0
    fi
    [ -n "$DESKTOP" ] || return 1
    published=$(published_name "$name" "$DESKTOP")
    if [ -f "$published" ]; then
        printf '%s' "$published"
        return 0
    fi
    if compgen -G "$published.part-*" >/dev/null; then
        printf '%s' "$published"
        return 0
    fi
    return 1
}

# claim NAME: move the asset for NAME to NAME itself (no-op when already so).
claim() {
    local name=$1 source
    source=$(resolve "$name") || return 1
    [ "$source" = "$name" ] || mv "$source" "$name"
}

say "[1/5] Verifying the downloaded assets..."
# RELEASE-SHA256SUMS 列的是这个 Release 的全部资产，四档桌面全在里面。装机只需要
# 平台资产加自己所选那一档，另外三档的 root 各 1-3 GiB，没有理由让用户也下一遍。
# 所以只对真的下载到磁盘上的行校验：缺的行过滤掉，但后面 claim 到缺失资产时会
# 明确报 "missing release asset"。分片合并前磁盘上只有 part-*，同名整文件不存在，
# 这两种情况都算已下载。只用 sha256sum 与 shell，不引入 python 依赖。
: > .checksums.present
while read -r sum name; do
    [ -n "${name:-}" ] || continue
    if [ -f "$name" ] || compgen -G "$name.part-*" >/dev/null; then
        printf '%s  %s\n' "$sum" "$name" >> .checksums.present
    fi
done < RELEASE-SHA256SUMS
[ -s .checksums.present ] || die "no release assets found here; download at least the platform assets and one desktop's root-install"
sha256sum -c .checksums.present
rm -f .checksums.present
mkdir -p root-install

say "[2/5] Unpacking the prepared root image..."
# stream_parts OUTPUT NAME: concatenate NAME.part-* (or keep NAME) as OUTPUT
stream_parts() {
    local output=$1 name=$2 base source
    source=$(resolve "$name") || die "missing $name or its .part-* pieces"
    if compgen -G "$source.part-*" >/dev/null; then
        cat "$source".part-* > "$output"
        rm -f "$source".part-*
    elif [ "$source" != "$output" ]; then
        mv "$source" "$output"
    fi
}
# unpack_zstd NAME OUTPUT: stream parts or a single file through zstd
unpack_zstd() {
    local name=$1 output=$2 source
    source=$(resolve "$name") || die "missing $name or its .part-* pieces"
    if compgen -G "$source.part-*" >/dev/null; then
        cat "$source".part-* | zstd -dc > "$output"
        rm -f "$source".part-*
    else
        zstd -dc "$source" > "$output"
        rm -f "$source"
    fi
}
# 装机只需要 root-install 这一份。Release 不再发布完整镜像与封存镜像：它们和
# 这份 root 内容相同，README 也写明不能直接刷，只是还原与检查用的存档副本。
# 需要它们就自己跑 prepare-install，或从 workflow artifact 取。
unpack_zstd root-install-pianoroot.f2fs.sparse.img.zst root-install/pianoroot.f2fs.sparse.img

say "[3/5] Restoring the documented directory layout..."
# Release 只排除两份 root 镜像（完整镜像与封存镜像），bundle 里其余文件照发：
# flash-linux.sh 要用 bundle-manifest.json 与 root-image-manifest.json 证明
# root-install 来自这个 bundle，install-f2fs.py 的首装还要读 source-runs.json。
bundle_files=(
    PianoUEFI-product.img SHA256SUMS boot_sunuefi.img
    bundle-manifest.json diagnostic-abl-storage.tar.zst esp-manifest.json
    ffmpeg-manifest.json initramfs-manifest.json initramfs.tar.zst
    kernel-manifest.json mesa-manifest.json root-image-manifest.json
    rootfs-manifest.json runtime-manifest.json source-runs.json
    sunuefi_esp.img uefi-source.json
)
for name in "${bundle_files[@]}"; do
    claim "$name" || die "missing release asset: $name"
done
claim root-install-manifest.json || die "missing release asset: root-install-manifest.json"
claim root-install-SHA256SUMS || die "missing release asset: root-install-SHA256SUMS"
mv root-install-manifest.json root-install/
mv root-install-SHA256SUMS root-install/SHA256SUMS

say "[4/5] Checking the reassembled bundle against its own checksums..."
# 这份清单来自 build-bundle，列的是整个 deploy-bundle，里面有几份不随 Release
# 分发的文件：完整镜像、封存镜像与五份溯源 manifest。缺的行跳过，只校验本
# 目录里真有的文件。校验通过后把清单改写成这一个子集——手动刷机会在暂存目录
# 里跑 `sha256sum -c SHA256SUMS`，留着一张列了不存在文件的清单只会让它失败。
# 分片在[2/5]已经合并，这里只认真实存在的文件。
: > .checksums.present
while read -r sum name; do
    [ -n "${name:-}" ] || continue
    if [ -f "$name" ]; then
        printf '%s  %s\n' "$sum" "$name" >> .checksums.present
    fi
done < SHA256SUMS
[ -s .checksums.present ] || die "SHA256SUMS matched nothing in this directory"
sha256sum -c .checksums.present
mv .checksums.present SHA256SUMS

say "[5/5] Checking the prepared root against its own checksums..."
(cd root-install && sha256sum -c SHA256SUMS)

say ""
say "Ready: this directory is the documented staging folder."
if [ -n "$DESKTOP" ]; then
    say "Desktop: ${DESKTOP} (root-install/pianoroot.f2fs.sparse.img is this desktop's root)."
fi
say "Next: read README-INSTALL.txt, generate device-boot/boot_linux.img with"
say "the matching piano-boot-tools release asset, then run flash-linux.sh."
