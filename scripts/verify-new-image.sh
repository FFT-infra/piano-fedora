#!/usr/bin/env bash
# 校验新装配出来的 rootfs 镜像是否满足 initramfs 交接的全部前提。
# 只读：挂载镜像、比对哈希、列文件，不修改镜像。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
DEFAULT_IMG="$REPO_ROOT/build/clean-distros/fedora-44-gnome/pianoroot-fedora44-gnome-f2fs.img"

IMG="${1:-$DEFAULT_IMG}"
MNT="${2:-/mnt/piano-verify-new}"

# 内核 release 以镜像同目录的 root-image-manifest.json 为准。
# 读不到就停下，不猜一个版本号。
if [ -z "${ESP_KERNEL:-}" ]; then
    manifest="$(dirname "$IMG")/root-image-manifest.json"
    if [ ! -f "$manifest" ]; then
        echo "kernel manifest not found: $manifest" >&2
        echo "build the image first, or pass ESP_KERNEL=<release>" >&2
        exit 2
    fi
    ESP_KERNEL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("kernel_release",""))' "$manifest")"
    if [ -z "$ESP_KERNEL" ]; then
        echo "kernel_release missing from $manifest" >&2
        echo "pass ESP_KERNEL=<release> if the manifest predates the field" >&2
        exit 2
    fi
fi

fail=0
note() { printf '%-46s %s\n' "$1" "$2"; }
bad()  { printf '%-46s %s\n' "$1" "FAIL: $2"; fail=1; }

[ -f "$IMG" ] || { echo "image not found: $IMG" >&2; exit 2; }

sudo -n mkdir -p "$MNT"
sudo -n mount -t f2fs -o loop,ro,norecovery "$IMG" "$MNT"
trap 'sudo -n umount "$MNT" 2>/dev/null || true' EXIT

echo "=== 1. root policy 文件 ==="
for f in etc/piano/root-policy.json \
         etc/udev/rules.d/01-piano-protect-android.rules \
         etc/fstab; do
    if sudo -n test -f "$MNT/$f"; then note "$f" "present"; else bad "$f" "missing"; fi
done

echo
echo "=== 2. root-policy.json 内容 ==="
sudo -n cat "$MNT/etc/piano/root-policy.json" 2>/dev/null || bad "root-policy.json" "unreadable"

echo
echo "=== 3. DMA 校验器哈希 vs prepare 里的常量 ==="
routes=$(sudo -n sha256sum "$MNT/usr/lib/piano/piano_dma_routes.py" | awk '{print $1}')
ctx=$(sudo -n sha256sum "$MNT/usr/lib/piano/piano_dma_contexts.py" | awk '{print $1}')
pin_routes=$(sudo -n grep -oP "CHECKER_SHA256 = '\K[0-9a-f]+" "$MNT/usr/lib/piano/piano-ram-hardware-prepare")
pin_ctx=$(sudo -n grep -oP "CONTEXT_CHECKER_SHA256 = '\K[0-9a-f]+" "$MNT/usr/lib/piano/piano-ram-hardware-prepare")
note "routes file"  "$routes"
note "routes pin"   "$pin_routes"
note "contexts file" "$ctx"
note "contexts pin"  "$pin_ctx"
[ "$routes" = "$pin_routes" ] || bad "routes hash" "file != pinned constant"
[ "$ctx" = "$pin_ctx" ] || bad "contexts hash" "file != pinned constant"

echo
echo "=== 4. 内核 release 一致性 ==="
mods=$(sudo -n ls "$MNT/usr/lib/modules")
count=$(printf '%s\n' "$mods" | grep -c .)
note "modules dir" "$mods"
[ "$count" -eq 1 ] || bad "modules" "expected exactly one release, found $count"
[ "$mods" = "$ESP_KERNEL" ] || bad "modules" "does not match ESP kernel $ESP_KERNEL"

echo
echo "=== 5. 关键文件 ==="
for f in usr/lib/piano/piano-ram-hardware-prepare \
         usr/lib/piano/piano-disk-hardware-prepare \
         usr/libexec/piano-boot-request-autoconfig \
         usr/bin/piano-next-boot; do
    if sudo -n test -e "$MNT/$f"; then note "$f" "present"; else bad "$f" "missing"; fi
done

echo
echo "=== 6. RPM 归属 ==="
sudo -n chroot "$MNT" rpm -qf /usr/lib/piano/piano-ram-hardware-prepare \
    /usr/lib/piano/piano_dma_routes.py /usr/lib/piano/piano_dma_contexts.py 2>&1 | sed 's/^/  /'
pkgs=$(sudo -n chroot "$MNT" rpm -qa 2>/dev/null | wc -l)
note "installed RPMs" "$pkgs"

echo
if [ "$fail" -eq 0 ]; then
    echo "RESULT: PASS"
else
    echo "RESULT: FAIL" >&2
fi
exit "$fail"
