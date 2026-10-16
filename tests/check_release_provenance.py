#!/usr/bin/env python3
"""回归测试：release bundle 不得再静默打包历史镜像。

实机教训（2026-10-17）：`package-release-bundle.sh` 的候选列表里排着一串
历史目录（`out/device-boot-a485-*`、`out/device-backup/*`、
`mipad8p-piano/out/offline-bringup-20261011-r2/*`、
`out/android-matching-core-*`），脚本取第一个存在的文件就复制。结果是
`out/release-bundle/` 里的 rootfs 与 2026-10-11 的镜像逐字节相同，
ESP 是 10-14 的设备读回，内核还是 10-11 的 `gd33a42990baf`，
而 PR 声称这些资产来自新的纯净装配链。

本测试只读脚本源码，不构建、不下载、不接触设备：

* 回退候选必须触发陈旧检查，不能直接复制；
* 脚本必须拒绝把 build/ 之外的路径当作当前流水线产物；
* 必须写出 PROVENANCE.txt 记录每个资产的真实来源；
* 显式传入的路径（--boot-img 等）仍然受信任，不被守卫拦住。
"""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "package-release-bundle.sh"

# Historical directories the old candidate list would happily copy from.
HISTORICAL = (
    "out/device-boot-a485-20261015",
    "out/device-backup/readonly-20261014",
    "out/artifacts/uefi-a485-",
    "out/android-matching-core-20261015",
    "out/deploy-stage-20261014",
    "mipad8p-piano/out/offline-bringup-20261011-r2",
)


def main():
    failures = []
    if not SCRIPT.is_file():
        print(f"FAIL: missing {SCRIPT.relative_to(ROOT)}", file=sys.stderr)
        return 1
    body = SCRIPT.read_text(encoding="utf-8")
    # Comment lines explain the history; only executable lines matter.
    code = "\n".join(line for line in body.splitlines()
                     if not line.lstrip().startswith("#"))

    if "reject_stale_source" not in code:
        failures.append("package-release-bundle.sh has no staleness guard")
    if "PROVENANCE.txt" not in code:
        failures.append("package-release-bundle.sh does not write PROVENANCE.txt")
    if "--allow-stale" not in body:
        failures.append("package-release-bundle.sh has no --allow-stale escape hatch")

    # Every historical candidate must be gated: it may still appear in the
    # candidate list, but the resolved value has to pass through the guard.
    for hist in HISTORICAL:
        if hist not in code:
            continue
        # Find the resolve_candidate call block that mentions this candidate.
        idx = code.index(hist)
        window = code[max(0, idx - 1200):idx + 400]
        if "reject_stale_source" not in window:
            failures.append(
                f"historical candidate {hist} is not covered by reject_stale_source")

    # The guard must treat only build/ as current pipeline output.
    guard = code[code.find("reject_stale_source()"):] if "reject_stale_source()" in code else ""
    if "build/clean-distros" not in guard or "build/bundle" not in guard:
        failures.append("staleness guard does not whitelist build/ pipeline output")

    # resolve_candidate must distinguish explicit paths from fallbacks.
    if not re.search(r"printf\s+'%s\\texplicit", code):
        failures.append("resolve_candidate does not mark explicit paths")
    if not re.search(r"printf\s+'%s\\tfallback", code):
        failures.append("resolve_candidate does not mark fallback paths")

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: release bundle 只接受当前流水线产物，历史镜像会被拒绝并记录来源")
    return 0


if __name__ == "__main__":
    sys.exit(main())
