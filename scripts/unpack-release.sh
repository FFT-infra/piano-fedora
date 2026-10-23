#!/usr/bin/env bash
# Reassemble and unpack the flat GitHub Release assets into the layout that
# flash-linux.sh and the manual instructions expect:
#
#   ./                  deploy-bundle files (ESP, Linux boot payload, manifests)
#   root-install/       prepared root transport, manifest, checksums
#
# GitHub rejects files of 2 GiB or more, so the large images are shipped as
# numbered pieces and the root transports are zstd-compressed. Run this script
# from the directory that holds every downloaded asset:
#
#   sha256sum -c RELEASE-SHA256SUMS
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

[ -f RELEASE-SHA256SUMS ] || die "RELEASE-SHA256SUMS is missing; download every asset of this release"
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
sha256sum -c RELEASE-SHA256SUMS
mkdir -p root-install

say "[2/5] Rebuilding the sealed and prepared root images..."
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
stream_parts pianoroot.f2fs.img.zst pianoroot.f2fs.img.zst
unpack_zstd pianoroot.f2fs.sparse.img.zst pianoroot.f2fs.sparse.img
unpack_zstd root-install-pianoroot.f2fs.sparse.img.zst root-install/pianoroot.f2fs.sparse.img

say "[3/5] Restoring the documented directory layout..."
bundle_files=(
    PianoUEFI-product.img SHA256SUMS boot_sunuefi.img bundle-manifest.json
    diagnostic-abl-storage.tar.zst esp-manifest.json ffmpeg-manifest.json
    initramfs-manifest.json initramfs.tar.zst kernel-manifest.json
    mesa-manifest.json root-image-manifest.json rootfs-manifest.json
    runtime-manifest.json source-runs.json sunuefi_esp.img uefi-source.json
)
for name in "${bundle_files[@]}"; do
    claim "$name" || die "missing release asset: $name"
done
claim root-install-manifest.json || die "missing release asset: root-install-manifest.json"
claim root-install-SHA256SUMS || die "missing release asset: root-install-SHA256SUMS"
mv root-install-manifest.json root-install/
mv root-install-SHA256SUMS root-install/SHA256SUMS

say "[4/5] Checking the reassembled bundle against its own checksums..."
sha256sum -c SHA256SUMS

say "[5/5] Checking the prepared root against its own checksums..."
(cd root-install && sha256sum -c SHA256SUMS)

say ""
say "Ready: this directory is the documented staging folder."
if [ -n "$DESKTOP" ]; then
    say "Desktop: ${DESKTOP} (root-install/pianoroot.f2fs.sparse.img is this desktop's root)."
fi
say "Next: read README-INSTALL.txt, generate device-boot/boot_linux.img with"
say "the matching piano-boot-tools release asset, then run flash-linux.sh."
