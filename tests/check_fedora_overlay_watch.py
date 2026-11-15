#!/usr/bin/env python3
"""联网巡检接线：报告渲染与 workflow 都把重编包漂移算进跟踪 issue。"""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "overlay_releases", ROOT / "scripts" / "check_fedora_overlay_releases.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

RESULT = {"schema_version": 1, "releasever": "45", "packages": [
    {"overlay": "mesa-piano", "name": "mesa", "base_version": "26.2.6",
     "stable_version": "26.2.6", "moved": False, "status": "verified",
     "drop_when": "Fedora 提供 26.3", "rebase_note": "按新 SRPM 重基"},
    {"overlay": "piano-pipewire-gstreamer", "name": "pipewire",
     "base_version": "1.6.11", "stable_version": "1.6.12", "moved": True,
     "status": "verified", "drop_when": "带上 97699d70",
     "rebase_note": "先重基 | 再逐项核对"},
    {"overlay": "fedora-desktop-patches", "name": "kwin", "base_version": "6.8.2",
     "status": "unverified", "error": "HTTP 503", "moved": False,
     "drop_when": "进 Fedora 官方包", "rebase_note": "核对 PACKAGES"},
], "moved": ["pipewire"], "unverified": ["kwin"]}


report = check.render_report(RESULT)
assert "pipewire" in report and "1.6.12" in report, "漂移的包要进报告"
assert "mesa" not in report, "没有漂移的包不该进表"
assert "\\|" in report, "竖线要转义，否则表格被拆散"
assert "未能核验" in report and "HTTP 503" in report, "未核验要单独列出"
assert "人工判断" in report, "报告不能自动判断 drop_when"

# 巡检自己失败时，workflow 也要产出一段占位报告，不能静默跳过。
workflow = (ROOT / ".github" / "workflows" / "upstream-watch.yml").read_text()
assert "check_fedora_overlay_releases.py" in workflow
assert "fedora-overlay-report.md" in workflow
assert "cat build/fedora-overlay-report.md >> build/upstream-drift.md" in workflow
assert ".fedora_overlays.moved" in workflow, "重编包漂移要算进 issue 开关"
# 巡检整体失败时 JSON 可能没写出来：report 与 JSON 都要有兜底，且失败不能被
# 当成「无漂移」把 issue 关掉。
assert "if [ ! -f build/fedora-overlay-releases.json ]" in workflow
assert '"status": "failed"' in workflow
assert '.fedora_overlays.status == "failed"' in workflow
assert "total=$((drift + overlays + failed))" in workflow

print("OK: Fedora 重编包巡检的报告与 workflow 接线完整")
