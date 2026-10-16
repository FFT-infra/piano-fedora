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
BUILD_TMP="${TMPDIR:-$HOME/Lab/Bridge/tmp/trash}/rpmbuild-work"

mkdir -p "$OUT_DIR"
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

        # Copy local patches, helper files and companion sources
        find "$PACKAGES_DIR/$pkg" -maxdepth 1 -type f ! -name "*.spec" ! -name "*.tar.*" -exec cp -p {} "$BUILD_TMP/SOURCES/" \;

        SOURCE0_LINE=$(grep -E '^[[:space:]]*Source0?:' "$SPEC_FILE" | head -n 1 | awk '{print $2}')
        if [[ "$SOURCE0_LINE" =~ ^https?:// ]]; then
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

        echo "--> Building RPM for $pkg..."
        rpmbuild --define "_topdir $BUILD_TMP" \
                 --define "_annobin_gcc_plugin %{nil}" \
                 -bb "$SPEC_FILE"

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
