#!/usr/bin/env bash
# Generate device-boot/boot_linux.img from this tablet's own stock BOOT.
#
# The combined BOOT keeps the tablet's current Android kernel, so it cannot be
# built by anyone else: the release ships the tools, not the image. This script
# reads the stock BOOT off the tablet and repacks it on the host.
#
#   ./make-device-boot.sh [--serial <serial>] [--slot a|b]
#
# Requirements: adb with root on the tablet, python3, and the files this script
# ships next to (boot-tools/, host-boot-tool/, scripts/). It writes
# device-boot/ and refuses to overwrite an existing one.
#
# The stock BOOT is read through adb, never through `fastboot fetch`: the stock
# ABL does not implement that subcommand and returns a 0-byte file.
set -euo pipefail

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
say() { printf '%s\n' "$*"; }

BOOT_PARTITION_BYTES=$((96 * 1024 * 1024))
SERIAL=""
SLOT="a"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --serial) [ "$#" -ge 2 ] || die "--serial needs a value"; SERIAL=$2; shift 2 ;;
        --serial=*) SERIAL=${1#--serial=}; shift ;;
        --slot) [ "$#" -ge 2 ] || die "--slot needs a value"; SLOT=$2; shift 2 ;;
        --slot=*) SLOT=${1#--slot=}; shift ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
case "$SLOT" in
    a|b) ;;
    *) die "--slot takes a or b, not ${SLOT}" ;;
esac

HERE=$(cd "$(dirname "$0")" && pwd -P)
cd "$HERE"

for tool in adb python3 sha256sum; do
    command -v "$tool" >/dev/null || die "missing ${tool}"
done
for path in scripts/prepare-device-boot.py boot-tools host-boot-tool \
            PianoUEFI-product.img; do
    [ -e "$path" ] || die "missing ${path}; run this script from an unpacked release archive"
done
[ -e device-boot ] && die "device-boot/ already exists; move it away before regenerating"
[ -e stock-recovery ] && die "stock-recovery/ already exists; move it away before regenerating"

adb_args=()
[ -n "$SERIAL" ] || {
    count=$(adb devices | awk 'NR > 1 && $2 == "device"' | wc -l)
    [ "$count" -eq 1 ] || die "found ${count} adb devices; pass --serial <serial>"
}
[ -z "$SERIAL" ] || adb_args=(-s "$SERIAL")
partition="/dev/block/by-name/boot_${SLOT}"

say "Reading ${partition} through adb..."
mkdir -p stock-recovery
device_digest=$(adb "${adb_args[@]}" shell su -c "sha256sum ${partition}" \
    | awk '{print $1}' | tr -d '\r')
case "$device_digest" in
    [0-9a-f][0-9a-f]*) ;;
    *) die "could not read the digest of ${partition}; root adb is required" ;;
esac
adb "${adb_args[@]}" exec-out su -c "dd if=${partition} bs=1048576" \
    > stock-recovery/boot.img

size=$(wc -c < stock-recovery/boot.img | tr -d ' ')
[ "$size" = "$BOOT_PARTITION_BYTES" ] || \
    die "read ${size} bytes, expected ${BOOT_PARTITION_BYTES}; the read was truncated"
host_digest=$(sha256sum stock-recovery/boot.img | awk '{print $1}')
[ "$host_digest" = "$device_digest" ] || die "the read does not match the tablet: ${host_digest} != ${device_digest}"
say "Stock BOOT: ${host_digest} (${size} bytes)"

say "Repacking with the tablet's own BOOT..."
python3 -I scripts/prepare-device-boot.py \
    --stock-boot stock-recovery/boot.img --source-sha256 "$device_digest" \
    --boot-tools boot-tools --host-tool host-boot-tool --bundle . \
    --slot "_${SLOT}" --output device-boot

say ""
say "Ready: device-boot/boot_linux.img"
say "Flash it to boot_${SLOT}, then follow README-INSTALL.txt."
