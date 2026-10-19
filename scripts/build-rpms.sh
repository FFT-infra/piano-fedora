#!/usr/bin/env bash
# ==============================================================================
# build-rpms.sh - Automated RPM matrix build & repository indexer for piano
# ==============================================================================
set -euo pipefail
export PATH="/usr/bin:/usr/sbin:$PATH"
export PKG_CONFIG_PATH="/usr/lib64/pkgconfig:/usr/share/pkgconfig"
export QA_RPATHS=23

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PACKAGES_DIR="$REPO_ROOT/packages"
OUT_DIR="${1:-$REPO_ROOT/out/rpms}"
BUILD_TMP="${TMPDIR:-/tmp}/rpmbuild-work"
# COPR rebuilds user-space packages from source. Keep the SRPMs beside the
# binaries so the publish step can hand them to `copr-cli build` without
# re-deriving the spec inputs.
SRPM_DIR="${SRPM_DIR:-$REPO_ROOT/out/srpms}"

# kernel-piano does not compile a kernel; it packages the pinned build-kernel
# artifact. Point KERNEL_PAYLOAD at that tree (the directory holding Image,
# System.map, config and lib/modules/), or leave it unset and the resolver below
# will look in the usual artifact locations. If nothing usable is found the
# build stops instead of emitting a kernel RPM with a 0-byte vmlinuz.
KERNEL_PAYLOAD="${KERNEL_PAYLOAD:-}"

resolve_kernel_payload() {
    if [ -n "$KERNEL_PAYLOAD" ]; then
        echo "$KERNEL_PAYLOAD"
        return 0
    fi
    local candidate
    for candidate in \
        "$REPO_ROOT/build/kernels/release-7.2.9" \
        "$REPO_ROOT/out/artifacts/kernel-a485-38560291716/kernel-release-7.2.9/extracted" \
        "$REPO_ROOT/out/artifacts/gunyah-a485-38568504093/extracted" \
        "$REPO_ROOT/out/artifacts/kernel-38407594802"; do
        if [ -s "$candidate/Image" ] && [ -d "$candidate/lib/modules" ]; then
            echo "$candidate"
            return 0
        fi
        # Artifacts unpack modules under modules/lib/modules instead of lib/modules.
        if [ -s "$candidate/Image" ] && [ -d "$candidate/modules/lib/modules" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

# The board DTB is not part of the kernel artifact: SunUEFI builds it into its
# vendor tree and the firmware loads it from the ESP. kernel-piano ships a copy
# under /boot/dtb/qcom so an installed system can be inspected without unpacking
# the ESP. Point BOARD_DTB at it, or let the resolver find the pinned checkout.
resolve_board_dtb() {
    if [ -n "${BOARD_DTB:-}" ]; then
        echo "$BOARD_DTB"
        return 0
    fi
    local candidate
    for candidate in \
        "$REPO_ROOT/out/esp-files-a485-20261015/board.dtb" \
        "$REPO_ROOT/build/test-sunuefi/vendor/piano-linux/board.dtb" \
        "$REPO_ROOT/build/sources/sunuefi/vendor/piano-linux/board.dtb"; do
        if [ -s "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done
    return 1
}

mkdir -p "$OUT_DIR"
mkdir -p "$SRPM_DIR"
mkdir -p "$BUILD_TMP"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

echo "=== Piano RPM Build Pipeline ==="
echo "Repo root    : $REPO_ROOT"
echo "Packages dir : $PACKAGES_DIR"
echo "Output dir   : $OUT_DIR"
echo "Build root   : $BUILD_TMP"
echo ""

HOST_ARCH="$(uname -m)"
PACKAGES_TO_BUILD=(
    "piano-fedora-configs"
    "alsa-xiaomi-piano"
    "xiaomi-piano-firmware"
)

if [ "$HOST_ARCH" = "aarch64" ]; then
    PACKAGES_TO_BUILD+=(
        "piano-camera"
        "xiaomi-piano-thp"
        "sm8750-video-accel"
        "uperf-linux"
        "libssc"
        "iio-sensor-proxy-ssc"
        "kernel-piano"
    )
fi

if [ -n "${ONLY_PKG:-}" ]; then
    PACKAGES_TO_BUILD=("$ONLY_PKG")
fi

for pkg in "${PACKAGES_TO_BUILD[@]}"; do
    if [ -d "$PACKAGES_DIR/$pkg" ]; then
        echo "=========================================="
        echo "--> Processing package: $pkg"
        SPEC_FILE=$(find "$PACKAGES_DIR/$pkg" -maxdepth 1 -name "*.spec" | head -n 1)
        if [ -z "$SPEC_FILE" ]; then
            echo "Error: No spec file found for $pkg" >&2
            exit 1
        fi

        # rpmbuild leaves the previous run's RPM in BUILD_TMP/RPMS, and the
        # collection step at the end copies everything it finds there. Without
        # clearing the package's own output first, a rebuild that fails to
        # produce a new RPM silently re-publishes the stale one — which is how a
        # fixed spec still shipped the old Obsoletes.
        find "$BUILD_TMP/RPMS" -type f -name "${pkg}-*.rpm" -delete 2>/dev/null || true

        # kernel-piano is a packaging-only spec around a prebuilt kernel tree.
        # Stage that tree as Source0 before the generic source handling runs.
        if [ "$pkg" = "kernel-piano" ]; then
            PAYLOAD=$(resolve_kernel_payload) || {
                echo "Error: no kernel payload found for kernel-piano." >&2
                echo "       Set KERNEL_PAYLOAD to a tree containing Image, System.map," >&2
                echo "       config and lib/modules/ (or modules/lib/modules/)." >&2
                exit 1
            }
            echo "--> Using kernel payload: $PAYLOAD"
            KERNEL_VER=$(grep -E '^[[:space:]]*%global[[:space:]]+KERNEL_VER' "$SPEC_FILE" | awk '{print $3}')
            STAGE=$(mktemp -d)
            # Normalise both artifact layouts into the flat tree the spec expects.
            cp -a "$PAYLOAD/Image" "$PAYLOAD/System.map" "$PAYLOAD/config" "$STAGE/"
            if [ -d "$PAYLOAD/lib/modules" ]; then
                mkdir -p "$STAGE/lib"
                cp -a "$PAYLOAD/lib/modules" "$STAGE/lib/"
            else
                cp -a "$PAYLOAD/modules/lib" "$STAGE/lib"
            fi
            [ -d "$PAYLOAD/dtb" ] && cp -a "$PAYLOAD/dtb" "$STAGE/"
            [ -f "$PAYLOAD/board.dtb" ] && cp -a "$PAYLOAD/board.dtb" "$STAGE/"
            # Fall back to the pinned SunUEFI board DTB when the kernel artifact
            # does not carry one (the usual case for build-kernel outputs).
            if [ ! -f "$STAGE/board.dtb" ] && [ ! -d "$STAGE/dtb" ]; then
                DTB=$(resolve_board_dtb) || {
                    echo "Error: no board DTB found for kernel-piano." >&2
                    echo "       Set BOARD_DTB to SunUEFI's vendor/piano-linux/board.dtb." >&2
                    exit 1
                }
                echo "--> Using board DTB: $DTB"
                cp -a "$DTB" "$STAGE/board.dtb"
            fi
            [ -f "$PAYLOAD/manifest.json" ] && cp -a "$PAYLOAD/manifest.json" "$STAGE/"
            tar -I 'zstd -6 -T0' -cf "$BUILD_TMP/SOURCES/kernel-payload-${KERNEL_VER}.tar.zst" -C "$STAGE" .
            rm -rf "$STAGE"
            echo "    Staged $(stat -c%s "$BUILD_TMP/SOURCES/kernel-payload-${KERNEL_VER}.tar.zst") bytes"
            # kernel-piano is published to COPR too, but the generated .repo
            # excludes it: a user-space DNF update cannot replace the Image the
            # ESP boots, so a kernel from COPR would desync ESP and rootfs.
            rpmbuild --define "_topdir $BUILD_TMP" \
                     --define "_annobin_gcc_plugin %{nil}" \
                     -ba "$SPEC_FILE"
            continue
        fi

        # Copy local patches, helper files and companion sources
        find "$PACKAGES_DIR/$pkg" -maxdepth 1 -type f ! -name "*.spec" ! -name "*.tar.*" -exec cp -p {} "$BUILD_TMP/SOURCES/" \;

        SOURCE0_LINE=$(grep -E '^[[:space:]]*Source0?:' "$SPEC_FILE" | head -n 1 | awk '{print $2}')
        # %{url} expands to an https URL in every spec that uses it. Matching
        # only on a literal http prefix silently treated those as local
        # sources, so rpmbuild stopped at %prep with "No such file".
        if [[ "$SOURCE0_LINE" =~ ^https?:// || "$SOURCE0_LINE" == *'%{url}'* ]]; then
            echo "--> Fetching upstream remote sources with spectool..."
            spectool -g -C "$BUILD_TMP/SOURCES" "$SPEC_FILE"
        else
            PKG_VERSION=$(grep -E '^[[:space:]]*Version:' "$SPEC_FILE" | awk '{print $2}')
            TAR_NAME="${pkg}-${PKG_VERSION}.tar.gz"
            echo "--> Archiving local package sources to $TAR_NAME..."
            tar -czf "$BUILD_TMP/SOURCES/$TAR_NAME" \
                --transform "s,^\.,${pkg}-${PKG_VERSION}," \
                -C "$PACKAGES_DIR/$pkg" .
        fi

        echo "--> Building RPM and SRPM for $pkg..."
        rpmbuild --define "_topdir $BUILD_TMP" \
                 --define "_annobin_gcc_plugin %{nil}" \
                 -ba "$SPEC_FILE"

        # If we just built libssc, ensure host has it installed for downstream iio-sensor-proxy-ssc
        if [ "$pkg" = "libssc" ] && [ -d "$BUILD_TMP/RPMS/aarch64" ]; then
            LIBSSC_RPM=$(find "$BUILD_TMP/RPMS/aarch64" -name "libssc-[0-9]*.rpm" | head -n 1)
            if [ -n "$LIBSSC_RPM" ] && sudo -n true 2>/dev/null; then
                echo "--> Installing host prerequisite libssc for dependent builds..."
                sudo rpm -Uvh --force "$LIBSSC_RPM" 2>/dev/null || true
            fi
        fi
    fi
done

# Collect generated RPMs
echo ""
echo "=== Collecting RPM artifacts ==="
find "$BUILD_TMP/RPMS" -type f -name "*.rpm" -exec cp -v {} "$OUT_DIR/" \;
find "$BUILD_TMP/SRPMS" -type f -name "*.src.rpm" -exec cp -v {} "$SRPM_DIR/" \;

echo ""
echo "=== Checking repository indexer ==="
if command -v createrepo_c &>/dev/null; then
    echo "--> Generating DNF repository metadata in $OUT_DIR..."
    createrepo_c --update "$OUT_DIR"
    echo "--> Repository metadata ready."
elif command -v createrepo &>/dev/null; then
    echo "--> Generating DNF repository metadata (createrepo)..."
    createrepo --update "$OUT_DIR"
else
    echo "--> NOTE: createrepo_c not found on build host; skipping repo indexing."
fi

echo ""
echo "=== Built Packages in $OUT_DIR ==="
ls -lh "$OUT_DIR"/*.rpm 2>/dev/null || echo "No RPMs found."
echo ""
echo "Build pipeline completed successfully."
