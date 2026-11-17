#!/usr/bin/env python3
"""离线检查：分发的面板校准只装手指电极矩阵，不夹带笔的事实。

`stage_payload` 用上游提取器重算原厂 INI，再要求结果与本仓库分发的
`piano-<lcd>.json` 逐字节相等。提取器会按 INI 里的笔 profile 附带笔的
事实：旧键名是 `pen`，按 HID 选型后新增 `m80p`（vendor id 1 那一支）。
两边键集合不一致时整条 runtime 构建就停在「matrix finger calibration
differs from the factory INI」，而这个失败只在 CI 里跑真实 INI 时才出现，
本地没有私有 INI 复现不了。

这里只读源码，不编译、不碰设备。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "piano_device_integration.py"
SHIPPED = ROOT / "packages" / "xiaomi-piano-thp" / "usr" / "share" / "piano" / "touch"


def main():
    text = MODULE.read_text(encoding="utf-8")
    # 两个键都必须剔除。提取器加第三个笔键名时，这里要一起加。
    for key in ("pen", "m80p"):
        assert f'extracted.pop("{key}", None)' in text, \
            f"stage_payload 没有剔除笔事实键 {key}，提取器产出该键时构建会失败"

    # 分发的文件里也不能有笔的字段：它们的来源是同包的 pen/calibration/*.ini。
    import json
    for name in ("piano-csot.json", "piano-boe.json"):
        path = SHIPPED / name
        assert path.is_file(), f"缺少分发校准 {name}"
        data = json.loads(path.read_text(encoding="utf-8"))
        for key in ("pen", "m80p"):
            assert key not in data, f"{name} 夹带了笔事实键 {key}"
    print("OK: 分发的面板校准只含手指电极矩阵，笔事实键在重算时被剔除")


if __name__ == "__main__":
    sys.exit(main())
