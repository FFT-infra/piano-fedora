#!/bin/sh
# Remove the optional extras meta packages and disable the extras repository.
# It does not downgrade KWin/GTK3 automatically; the printed commands do that
# after a dry-run.
set -eu

REPO_ID="copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras"

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root" >&2
    exit 1
fi

dnf remove -y piano-gnome-extras piano-kde-extras || true
dnf config-manager setopt "${REPO_ID}.enabled=0"
if command -v dconf >/dev/null 2>&1; then
    dconf update
fi

cat <<'EOF'
Extras removed. To return same-name packages to Fedora's versions, run:

  dnf distro-sync --repo fedora --dry-run kwin gtk3
  dnf distro-sync --repo fedora kwin gtk3
  dnf swap gnome-settings-daemon-piano gnome-settings-daemon --repo fedora

Run the dry-run first; it prints every package the sync would change.
EOF
