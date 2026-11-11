#!/bin/sh
# Enable the optional extras repository and install one desktop meta package.
# The default image does not need this script.
set -eu

PROJECT="xinyang26/piano-fedora-extras"
REPO_ID="copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras"

case "${1:-}" in
    gnome) meta="piano-gnome-extras" ;;
    kde)   meta="piano-kde-extras" ;;
    *)     echo "usage: $0 gnome|kde" >&2; exit 2 ;;
esac

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 1
fi

dnf copr enable -y "$PROJECT"
# DNF5 priority: lower number wins. Fedora repos default to 99, so the
# patched same-name KWin/GTK3 stay selected even after Fedora moves ahead.
dnf config-manager setopt "${REPO_ID}.priority=1"
dnf install -y --allowerasing "$meta"
if command -v dconf >/dev/null 2>&1; then
    dconf update
fi
echo "installed $meta; log out and back in before testing the desktop changes"
