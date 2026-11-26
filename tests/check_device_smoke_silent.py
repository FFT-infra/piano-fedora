#!/usr/bin/env python3
"""冒烟的 PCM 探针必须推静音。

audiotestsrc 默认是 440 Hz 正弦，不写 wave=silence 就会从平板扬声器放出一声。
冒烟脚本自称只读、推静音，这里锁住它确实不出声。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
text = (ROOT / "scripts/verify-device-smoke.py").read_text()
start = text.index("def check_pcm_open")
body = text[start:text.index("\ndef ", start + 1)]
assert '"audiotestsrc"' in body, "PCM 探针不再走 audiotestsrc，这条检查要跟着改"
assert '"wave=silence"' in body, "PCM 探针没有指定 wave=silence，会从扬声器放出 440 Hz"
print("OK: 冒烟的 PCM 探针推的是静音")
