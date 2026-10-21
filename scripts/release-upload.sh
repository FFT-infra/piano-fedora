#!/usr/bin/env bash
# Helpers shared by the release workflow's upload steps. Source it, do not run it:
#
#   . scripts/release-upload.sh
#
# Inputs come from the environment set by release.yml:
#   TAG, STAGE, GITHUB_REPOSITORY, and a GH_TOKEN that `gh` already reads.
#
# Every uploaded file is recorded in $STAGE/RELEASE-SHA256SUMS under its
# published name, so one checksum file covers the whole release.

release_max_asset=2147000000   # GitHub's per-file limit is 2 GiB; keep headroom.

# release_plan_value DESKTOP FIELD — read build/release-plan.json (written by
# scripts/release-desktops.py) without a second JSON parser in the workflow.
release_plan_value() {
    python3 -c 'import json, sys; print({row["desktop"]: row for row in json.load(open("build/release-plan.json"))}[sys.argv[1]][sys.argv[2]])' "$1" "$2"
}

release_record() {
    printf '%s  %s\n' "$(sha256sum "$1" | awk '{print $1}')" "$2" >> "$STAGE/RELEASE-SHA256SUMS"
}

# release_upload PATH [NAME] — upload under NAME when the file on disk is not
# already called that.
release_upload() {
    local path=$1 name=${2:-$(basename "$1")} source
    source=$path
    if [ "$(basename "$1")" != "$name" ]; then
        cp "$path" "$STAGE/$name"
        source="$STAGE/$name"
    fi
    release_record "$source" "$name"
    gh release upload "$TAG" "$source"
    rm -f "$source"
}

# release_upload_split PATH [NAME] — slice anything at or above the asset limit
# into 1,900 MiB pieces under NAME.part-NN.
release_upload_split() {
    local path=$1 name=${2:-$(basename "$1")} base part
    if [ "$(stat -c %s "$path")" -lt "$release_max_asset" ]; then
        release_upload "$path" "$name"
        return
    fi
    base="$STAGE/$name"
    split -b 1900M -d -a 2 "$path" "$base.part-"
    rm -f "$path"
    for part in "$base".part-*; do
        release_upload "$part"
    done
}
