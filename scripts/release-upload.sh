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

# 本文件所在的 scripts/ 目录：命名规则只在 release-assets.py 一处实现，
# 这里按脚本自身位置找它，不依赖调用方的工作目录。
release_assets="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/release-assets.py"

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

# release_upload_platform PATH — upload one platform asset under the name
# scripts/release-assets.py assigns. The three images ship zstd-compressed:
# the ESP alone is 512 MiB of mostly zero sectors and compresses to 36 MiB, so
# shipping it raw costs every user half a gigabyte for nothing. Small JSON
# manifests go out unchanged. unpack-release.sh restores the documented names.
release_upload_platform() {
    local path=$1 name stage
    name=$(python3 "$release_assets" platform-name "$(basename "$path")")
    case "$name" in
        *.zst)
            stage="$STAGE/$name"
            zstd -10 -f -T0 --no-progress "$path" -o "$stage"
            # 压缩必须可逆：解压回来的字节要和 deploy-bundle 里那份一致，
            # 否则装机脚本按 SHA256SUMS 校验时会拒绝。
            test "$(sha256sum "$path" | awk '{print $1}')" = \
                 "$(zstd -dc "$stage" | sha256sum | awk '{print $1}')" || {
                echo "compressed platform asset $name does not round-trip" >&2
                exit 2
            }
            release_upload "$stage" "$name"
            ;;
        *)
            release_upload "$path" "$name"
            ;;
    esac
}
