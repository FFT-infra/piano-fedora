#!/bin/bash
# 从主机跑平板上的只读冒烟，把 JSON 报告拉回本地。
#
# 探针必须在设备上跑：内核 release、systemd 单元状态、触控 proc 节点与 ALSA
# 默认输出在主机上都看不到。设备通常只能口令登录、也没有 scp 需要的环境，
# 所以脚本走 ssh stdin 传文件，不依赖 scp。
#
# 用法：
#   scripts/run-device-smoke.sh --host <ssh 目标> [--out DIR] [--skip-pcm]
#
# 例：
#   scripts/run-device-smoke.sh --host piano --out ~/Lab/Bridge/tmp/smoke-$(date +%F)
set -euo pipefail

HOST=""
OUT=""
EXTRA=()

while (($#)); do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --out) OUT="$2"; shift 2 ;;
        --skip-pcm) EXTRA+=(--skip-pcm); shift ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [[ -z "$HOST" ]]; then
    echo "ERROR: --host is required" >&2
    exit 2
fi

root=$(cd "$(dirname "$0")/.." && pwd)
if [[ -z "$OUT" ]]; then
    OUT="$(mktemp -d)"
fi
mkdir -p "$OUT"

remote_dir="piano-smoke-$$"

# stdin 传文件：设备只有口令登录时 scp 不可用，ssh 的 stdin 可以。
ssh -o ConnectTimeout=10 "$HOST" "mkdir -p $remote_dir && cat > $remote_dir/verify-device-smoke.py" \
    < "$root/scripts/verify-device-smoke.py"

set +e
ssh -o ConnectTimeout=10 "$HOST" \
    "python3 $remote_dir/verify-device-smoke.py --report $remote_dir/report.json ${EXTRA[*]:-}" \
    > "$OUT/run.log" 2>&1
status=$?
set -e

ssh -o ConnectTimeout=10 "$HOST" "cat $remote_dir/report.json" > "$OUT/report.json" 2>/dev/null || true
ssh -o ConnectTimeout=10 "$HOST" "rm -rf $remote_dir" || true

if [[ -s "$OUT/report.json" ]]; then
    python3 - "$OUT/report.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1]))
for row in report["checks"]:
    print(f"{row['status'].upper():4} {row['check']}: {row['detail']}")
print(f"kernel: {report['kernel']}")
PY
fi

echo "报告目录: $OUT" >&2
exit "$status"
