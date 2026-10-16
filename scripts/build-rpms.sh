#!/usr/bin/env bash
# ==============================================================================
# build-rpms.sh - Automated RPM matrix build & repository indexer for piano
# ==============================================================================
set -euo pipefail
export PATH="/usr/bin:/usr/sbin:$PATH"
export QA_RPATHS=23

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PACKAGES_DIR="$REPO_ROOT/packages"
OUT_DIR="${1:-$REPO_ROOT/out/rpms}"
BUILD_TMP="${TMPDIR:-$REPO_ROOT/../tmp/trash}/rpmbuild-work"

mkdir -p "$OUT_DIR"
mkdir -p "$BUILD_TMP"/{BUILD,BUILDROOT,RPMS,SOURCES,SPECS,SRPMS}

echo "=== Piano RPM Build Pipeline ==="
echo "Repo root    : $REPO_ROOT"
echo "Packages dir : $PACKAGES_DIR"
echo "Output dir   : $OUT_DIR"
echo "Build root   : $BUILD_TMP"
echo ""

HOST_ARCH="$(uname -m)"
PACKAGES_TO_BUILD=("piano-fedora-configs" "alsa-xiaomi-piano")
if [ "$HOST_ARCH" = "aarch64" ]; then
    PACKAGES_TO_BUILD+=("piano-camera" "xiaomi-piano-thp")
fi

for pkg in "${PACKAGES_TO_BUILD[@]}"; do
    if [ -d "$PACKAGES_DIR/$pkg" ]; then
        echo "--> Building package: $pkg"
        SPEC_FILE=$(find "$PACKAGES_DIR/$pkg" -maxdepth 1 -name "*.spec" | head -n 1)
        PKG_VERSION=$(grep -E '^Version:' "$SPEC_FILE" | awk '{print $2}')
        TAR_NAME="${pkg}-${PKG_VERSION}.tar.gz"
        
        tar -czf "$BUILD_TMP/SOURCES/$TAR_NAME" \
            --transform "s,^\.,${pkg}-${PKG_VERSION}," \
            -C "$PACKAGES_DIR/$pkg" .
            
        rpmbuild --define "_topdir $BUILD_TMP" --define "_annobin_gcc_plugin %{nil}" -bb "$SPEC_FILE"
    fi
done

# Collect generated RPMs
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
