#!/usr/bin/env bash
# Download, verify and unpack one desktop's release archive.
#
# Every desktop ships as one self-contained tar.zst. Unpacked into an empty
# directory it becomes the staging folder the manual instructions and
# flash-linux.sh expect: the flash scripts, boot-tools, the platform images,
# this desktop's manifests and root-install/.
#
#   bash unpack-release.sh [--tag <tag>] [--desktop <name>] [--dir <empty dir>]
#
# With --tag the script also downloads the archive: it fetches
# piano-<tag>-<desktop>.sha256, then every piece that file lists, skipping any
# piece already on disk. Without --tag it works on the pieces you downloaded
# yourself.
#
# The archive's own SHA256SUMS lists only the files the archive holds, so
# `sha256sum -c SHA256SUMS` runs clean here and in the manual path.
set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

REPO="${PIANO_RELEASE_REPO:-FFT-infra/piano-fedora}"
TAG=""
DESKTOP="${PIANO_DESKTOP:-}"
DIR="."

while [ "$#" -gt 0 ]; do
    case "$1" in
        --tag) [ "$#" -ge 2 ] || die "--tag needs a value"; TAG=$2; shift 2 ;;
        --tag=*) TAG=${1#--tag=}; shift ;;
        --desktop) [ "$#" -ge 2 ] || die "--desktop needs a value"; DESKTOP=$2; shift 2 ;;
        --desktop=*) DESKTOP=${1#--desktop=}; shift ;;
        --dir) [ "$#" -ge 2 ] || die "--dir needs a value"; DIR=$2; shift 2 ;;
        --dir=*) DIR=${1#--dir=}; shift ;;
        -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done

for tool in sha256sum zstd tar; do
    command -v "$tool" >/dev/null || die "missing $tool (Fedora: sudo dnf install coreutils zstd tar)"
done
if [ -n "$TAG" ]; then
    command -v curl >/dev/null || die "missing curl; needed to download the release"
    case "$TAG" in
        -*|*/*) die "invalid tag: $TAG" ;;
    esac
fi

# The per-desktop checksum file is the per-desktop marker the release carries
# at the top level, and its name spells the desktop out. With one desktop in
# the directory the flag can be left out.
detect_desktop() {
    local found="" sidecar name
    for sidecar in piano-*-*.sha256; do
        [ -f "$sidecar" ] || continue
        name=${sidecar#piano-}
        name=${name%.sha256}
        name=${name##*-}
        [ -n "$name" ] || continue
        if [ -z "$found" ]; then
            found=$name
        elif [ "$found" != "$name" ]; then
            die "several desktops are present (${found}, ${name}); pass --desktop <name>"
        fi
    done
    printf '%s' "$found"
}

mkdir -p "$DIR"
cd "$DIR"
if [ -e root-install ] || [ -e SHA256SUMS ] || [ -e boot-tools ]; then
    die "this directory already holds an unpacked release; use an empty one"
fi

if [ -z "$DESKTOP" ]; then
    DESKTOP=$(detect_desktop)
fi
if [ -z "$DESKTOP" ]; then
    if [ -n "$TAG" ]; then
        die "pass --desktop <name>; it names the archive to download"
    fi
    die "no piano-<tag>-<desktop>.sha256 here; download one desktop's pieces first, or pass --tag and --desktop"
fi
say "Desktop: ${DESKTOP}"

sidecar=$(ls piano-*-"${DESKTOP}".sha256 2>/dev/null | head -n 1 || true)
say "[1/4] Downloading the archive pieces..."
if [ -n "$TAG" ]; then
    base="https://github.com/${REPO}/releases/download/${TAG}"
    sidecar="piano-${TAG}-${DESKTOP}.sha256"
    [ -f "$sidecar" ] || curl -fsSL -o "$sidecar" "${base}/${sidecar}"
    while read -r _ name; do
        [ -n "${name:-}" ] || continue
        # A piece already on disk is verified below; skipping it lets an
        # interrupted run resume instead of starting over.
        [ -f "$name" ] && continue
        say "  ${name}"
        curl -fsSL -o "$name" "${base}/${name}"
    done < "$sidecar"
else
    say "  using the pieces already here"
fi

say "[2/4] Verifying the pieces..."
[ -n "$sidecar" ] || die "no piano-<tag>-${DESKTOP}.sha256 to verify the pieces against"
sha256sum -c "$sidecar"

say "[3/4] Unpacking the archive..."
archive=${sidecar%.sha256}.tar.zst
if ls "$archive".part-* >/dev/null 2>&1; then
    cat "$archive".part-* | zstd -dc | tar -x
else
    [ -f "$archive" ] || die "missing ${archive} and its .part-* pieces"
    zstd -dc "$archive" | tar -x
fi

say "[4/4] Verifying the unpacked files..."
sha256sum -c SHA256SUMS
(cd root-install && sha256sum -c SHA256SUMS)

say ""
say "Ready: this directory is the documented staging folder."
say "Next: run ./make-device-boot.sh to generate device-boot/boot_linux.img,"
say "then follow README-INSTALL.txt."
