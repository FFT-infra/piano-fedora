#!/usr/bin/env python3
"""在平板本体上跑只读冒烟：内核、服务、触控、音频、ESP 审计。

脚本在设备上执行，由 `scripts/run-device-smoke.sh` 从主机推过去：

    python3 scripts/verify-device-smoke.py --report -   # JSON 到 stdout

只读。不改服务状态、不写 ESP、不装包。唯一会碰硬件的是 PCM 打开测试：
向默认输出写 1 秒静音，验证混音器到 ALSA 这条链能开流。

退出码：全部通过 0，有失败项 1。SKIP 不算失败，但会写进报告。
"""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

MANIFEST_DIR = Path("/usr/share/piano")
ESP_STATE_DIR = Path("/var/lib/piano/kernel-esp")
TOUCH_STREAM = Path("/proc/nvt_thp_stream")
CAMERA_UNITS = ("piano-camera.service", "piano-camerad.service")


def run(command, timeout=30, env=None):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                                env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    return result.returncode, (result.stdout + result.stderr).strip()


def session_env():
    """PipeWire 与 WirePlumber 是用户会话总线上的，ssh 非登录 shell 没有这些变量。"""
    env = dict(os.environ)
    runtime = f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", runtime)
    env.setdefault("PIPEWIRE_RUNTIME_DIR", runtime)
    return env


def installed_kernel_releases():
    releases = []
    for path in sorted(MANIFEST_DIR.glob("kernel-manifest-*.json")):
        try:
            releases.append(json.loads(path.read_text())["kernel_release"])
        except (OSError, ValueError, KeyError):
            continue
    return releases


def check_kernel():
    running = platform.release()
    installed = installed_kernel_releases()
    if not installed:
        return "fail", f"running {running}; no kernel manifest under {MANIFEST_DIR}"
    if running not in installed:
        return "fail", (f"running {running} has no installed manifest "
                        f"(installed: {', '.join(installed)})")
    modules = Path("/lib/modules") / running
    if not modules.is_dir():
        return "fail", f"module tree missing: {modules}"
    return "pass", f"{running}; module tree present"


def check_failed_units():
    code, output = run(["systemctl", "--failed", "--no-pager", "--plain", "--no-legend"])
    if code is None:
        return "skip", output
    lines = [line.split()[0] for line in output.splitlines() if line.strip()]
    if lines:
        return "fail", "failed units: " + ", ".join(lines)
    return "pass", "systemctl --failed is empty"


def check_service(unit):
    code, output = run(["systemctl", "is-active", unit])
    if code is None:
        return "skip", output
    state = output.splitlines()[0].strip() if output else "unknown"
    return ("pass" if state == "active" else "fail"), f"{unit}: {state}"


def check_touch():
    if not TOUCH_STREAM.exists():
        return "fail", f"{TOUCH_STREAM} missing; the touch driver did not bind"
    return check_service("piano-touch.service")


def check_audio_sink():
    if not shutil.which("wpctl"):
        return "skip", "wpctl not installed"
    code, output = run(["wpctl", "status"], env=session_env())
    if code is None:
        return "skip", output
    # Sinks 段里带 `*` 的那一行是默认输出。设备名是中文，不能按英文关键字匹配。
    in_sinks = False
    for line in output.splitlines():
        if "Sinks:" in line:
            in_sinks = True
            continue
        if in_sinks and line.strip().startswith(("Sources:", "Filters:", "Streams:")):
            break
        if in_sinks and "*" in line:
            return "pass", line.strip(" │├└─* ")
    return "fail", "wpctl reports no default sink"


def check_pcm_open():
    """向默认输出推 10 帧静音，验证 PipeWire 到 ALSA 的这条链能开流。

    走 GStreamer 的 pipewiresink 而不是 aplay：镜像里没有 pipewire-alsa，
    `aplay -D default` 会落到裸 hw 设备上失败，那验的是 ALSA 配置，不是
    扬声器这条链。pipewiresink 由 piano-pipewire-gstreamer 提供，一定会装。
    """
    if not shutil.which("gst-launch-1.0"):
        return "skip", "gst-launch-1.0 not installed"
    code, output = run(["gst-launch-1.0", "-q", "audiotestsrc", "wave=silence", "num-buffers=10",
                        "!", "audio/x-raw,rate=48000,channels=2", "!", "pipewiresink"],
                       timeout=20, env=session_env())
    if code is None:
        return "skip", output
    if code:
        return "fail", output or f"gst-launch exited {code}"
    return "pass", "pipewiresink accepted 10 silent buffers"


def check_esp_audit():
    release = platform.release()
    record = ESP_STATE_DIR / f"{release}.json"
    if not record.is_file():
        return "skip", f"{record} not written (kernel not upgraded on this device yet)"
    try:
        data = json.loads(record.read_text())
    except (OSError, ValueError) as exc:
        return "fail", f"{record}: {exc}"
    if data.get("release") != release:
        return "fail", f"{record} names release {data.get('release')}, running {release}"
    return "pass", f"ESP update recorded for {release}"


CHECKS = [
    ("kernel", check_kernel),
    ("failed_units", check_failed_units),
    *((unit, lambda unit=unit: check_service(unit)) for unit in CAMERA_UNITS),
    ("touch", check_touch),
    ("audio_sink", check_audio_sink),
    ("pcm_open", check_pcm_open),
    ("esp_audit", check_esp_audit),
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", default="-", help="JSON 报告路径，- 表示 stdout")
    parser.add_argument("--skip-pcm", action="store_true", help="不跑 PCM 打开测试")
    args = parser.parse_args(argv)

    results = []
    for name, probe in CHECKS:
        if name == "pcm_open" and args.skip_pcm:
            results.append({"check": name, "status": "skip", "detail": "--skip-pcm"})
            continue
        try:
            status, detail = probe()
        except Exception as exc:  # 冒烟脚本要跑完整套，单个探针炸掉不终止
            status, detail = "fail", f"{type(exc).__name__}: {exc}"
        results.append({"check": name, "status": status, "detail": detail})

    failures = [row for row in results if row["status"] == "fail"]
    report = {"kernel": platform.release(), "checks": results,
              "passed": not failures}
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.report == "-":
        sys.stdout.write(text)
    else:
        Path(args.report).write_text(text, encoding="utf-8")
        for row in results:
            print(f"{row['status'].upper():4} {row['check']}: {row['detail']}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
