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
#   bash unpack-release.sh
set -euo pipefail

cd "$(dirname "$0")"

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

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

say "[1/4] Verifying the downloaded assets..."
sha256sum -c RELEASE-SHA256SUMS
mkdir -p root-install

say "[2/4] Rebuilding the sealed and prepared root images..."
# stream_parts BASE OUTPUT: concatenate BASE.part-* (or keep a single BASE) as OUTPUT
stream_parts() {
    local base=$1 output=$2
    if compgen -G "$base.part-*" >/dev/null; then
        cat "$base".part-* > "$output"
        rm -f "$base".part-*
    elif [ -f "$base" ]; then
        [ "$base" = "$output" ] || mv "$base" "$output"
    else
        die "missing $base or its .part-* pieces"
    fi
}
# unpack_zstd BASE OUTPUT: stream parts or a single file through zstd
unpack_zstd() {
    local base=$1 output=$2
    if compgen -G "$base.part-*" >/dev/null; then
        cat "$base".part-* | zstd -dc > "$output"
        rm -f "$base".part-*
    elif [ -f "$base" ]; then
        zstd -dc "$base" > "$output"
        rm -f "$base"
    else
        die "missing $base or its .part-* pieces"
    fi
}
stream_parts pianoroot.f2fs.img.zst pianoroot.f2fs.img.zst
unpack_zstd pianoroot.f2fs.sparse.img.zst pianoroot.f2fs.sparse.img
unpack_zstd root-install-pianoroot.f2fs.sparse.img.zst root-install/pianoroot.f2fs.sparse.img

say "[3/4] Restoring the documented directory layout..."
bundle_files=(
    PianoUEFI-product.img SHA256SUMS boot_sunuefi.img bundle-manifest.json
    diagnostic-abl-storage.tar.zst esp-manifest.json ffmpeg-manifest.json
    initramfs-manifest.json kernel-manifest.json mesa-manifest.json
    root-image-manifest.json rootfs-manifest.json runtime-manifest.json
    source-runs.json sunuefi_esp.img uefi-source.json
)
for name in "${bundle_files[@]}"; do
    [ -f "$name" ] || die "missing release asset: $name"
done
mv root-install-manifest.json root-install/
mv root-install-SHA256SUMS root-install/SHA256SUMS

say "[4/4] Checking the reassembled bundle against its own checksums..."
sha256sum -c SHA256SUMS
(cd root-install && sha256sum -c SHA256SUMS)

say ""
say "Ready: this directory is the documented staging folder."
say "Next: read README-INSTALL.txt, generate device-boot/boot_linux.img with"
say "the matching piano-boot-tools release asset, then run flash-linux.sh."
