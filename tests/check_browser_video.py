#!/usr/bin/env python3
"""浏览器硬解验收脚本的判定契约。

`scripts/verify-browser-video.py` 在平板上跑，这里锁住判定本身：

硬解必须同时有 iris 中断增长和 /dev/video0 持有者。只满足一个不算：软件
解码同样会拉高 GPU 中断，光看中断会把软解判成硬解。

实测的差别是这个脚本存在的原因：Fedora 的 Chromium 链接系统
libavcodec，而 ffmpeg-free 用 --disable-decoder 去掉了软件 H.264，装上
libavcodec-freeworld 后能播但仍走软解；Chrome 自带 FFmpeg 与 V4L2 解码
路径，Firefox 有自己的 VA-API 路径，两者默认硬解。
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-browser-video.py"


def load():
    spec = importlib.util.spec_from_file_location("verify_browser_video", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    if not SCRIPT.is_file():
        print("FAIL: verify-browser-video.py must exist")
        return 1

    module = load()

    check(hasattr(module, "hardware_decoded"),
          "the verdict must live in a named function so it can be tested")
    if hasattr(module, "hardware_decoded"):
        verdict = module.hardware_decoded
        check(verdict(iris_interrupts=727, holders=[("1", "chrome")]) is True,
              "interrupts plus an open video node is hardware decode")
        check(verdict(iris_interrupts=0, holders=[("1", "chrome")]) is False,
              "an open video node without interrupts is not hardware decode")
        check(verdict(iris_interrupts=727, holders=[]) is False,
              "interrupts without an open video node are just GPU work")
        check(verdict(iris_interrupts=0, holders=[]) is False,
              "no evidence at all is not hardware decode")

    for name in ("chromium", "chrome", "firefox"):
        check(name in module.BROWSERS,
              f"{name} must be a known browser, not skipped as unknown")

    if failures:
        for item in failures:
            print(f"FAIL: {item}")
        return 1
    print("OK: browser video verdict needs both interrupts and an open video node")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
