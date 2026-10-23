#!/bin/bash
# 从主机驱动平板上的编解码验收。
#
# 脚本本体必须在设备上跑：它读 /proc/interrupts 判断 Iris 是否真的在干活，
# 在主机上跑没有任何意义。但目标镜像的 ffmpeg-free 没编译 libx264/libx265，
# 设备上也没装 make，所以由主机准备素材、推过去、执行、再把证据拉回来。
set -euo pipefail

if [ $# -lt 2 ]; then
    echo "用法: $0 <ssh 主机> <输出目录> [素材目录]" >&2
    echo "  例: $0 piano build/media-check" >&2
    exit 2
fi

host=$1
out=$2
fixture_src=${3:-}

root=$(cd "$(dirname "$0")/.." && pwd)
stage=$(mktemp -d)
remote=/tmp/piano-media-$$
trap 'rm -rf "$stage"' EXIT

have_encoder() {
    # `ffmpeg -encoders` 的行形如 ` V....D libx264 ...`，$1 是能力字母，$2 是名字。
    ffmpeg -hide_banner -encoders 2>/dev/null | awk '$1 ~ /^V/ {print $2}' | grep -qx "$1"
}

generate() {
    local codec=$1 encoder=$2
    shift 2
    ffmpeg -nostdin -hide_banner -loglevel error -y \
        -f lavfi -i testsrc2=size=1920x1080:rate=30 \
        -c:v "$encoder" -preset fast -bf 3 -g 60 "$@" \
        -frames:v 300 "$stage/$codec-bframes3.mp4"
    # 设备上的 ffprobe 把开源码流的 pict_type 报成 `?`，必须在主机算出清单带过去。
    ffprobe -v error -select_streams v:0 -show_entries frame=pict_type \
        -of csv=p=0 "$stage/$codec-bframes3.mp4" \
        | tr -d ',' | tr -s '[:space:]' '\n' | grep -v '^$' \
        | python3 -c 'import json, sys; types = sys.stdin.read().split(); json.dump(types, open(sys.argv[1], "w"))' \
            "$stage/$codec-bframes3-types.json"
}

if [ -n "$fixture_src" ]; then
    cp -p "$fixture_src/h264-bframes3.mp4" "$fixture_src/h264-bframes3-types.json" \
          "$fixture_src/hevc-bframes3.mp4" "$fixture_src/hevc-bframes3-types.json" "$stage/"
elif have_encoder libx264 && have_encoder libx265; then
    generate h264 libx264
    generate hevc libx265 -x265-params pools=2:frame-threads=2
else
    echo "本机 ffmpeg 没有 libx264/libx265，也没有给素材目录。" >&2
    echo "换一台带这两个编码器的主机，或用第三个参数指向已有的素材目录。" >&2
    exit 2
fi

ssh "$host" "mkdir -p $remote/fixtures"
scp -q "$root/scripts/verify-device-media.py" "$host:$remote/verify-device-media.py"
scp -q "$stage"/*.mp4 "$stage"/*.json "$host:$remote/fixtures/"

# 失败了也要把证据拉回来，report 里才有 current_stage 和 error。
set +e
ssh "$host" "python3 $remote/verify-device-media.py --output $remote/evidence \
    --fixture-dir $remote/fixtures --fixture-types $remote/fixtures"
status=$?
set -e

mkdir -p "$out"
scp -q -r "$host:$remote/evidence/." "$out/"
ssh "$host" "rm -rf $remote"

echo "证据目录: $out" >&2
exit "$status"
