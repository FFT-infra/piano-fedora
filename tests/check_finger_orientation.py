#!/usr/bin/env python3
"""离线检查手指输入的 Y 轴映射：桥接层必须无条件反转。

2026-10-24 实机回归：7.3 起桥接层按内核 panel_vendor 决定是否反转，这台
设备 panel_vendor 读出 boe，于是跳过了反转，屏上触控上下颠倒。判据本身是
错的——panel_vendor 是 lcd-id 绑带电平，与 UEFI 按原厂 ABL 选出的显示面板
可以不一致；本机就是这个情况（panel_vendor=boe，显示面板 compatible 是
xiaomi,piano-csot-nt36532）。

唯一一次带真手指的方向测量（2026-10-07，从上边缘划到下边缘，处理器 Y 递减）
要求反转，且那次测量没有区分面板型号。所以反转是无条件的，panel_vendor 只
用来选固件和提示校准文件不匹配。

这里只读源码，不编译、不碰设备。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "runtime/touch/piano-finger-bridge.cpp"
text = BRIDGE.read_text()


def fail(message):
    print("FAIL: " + message)
    sys.exit(1)


# 桥接层调 panelSlots 时传的面板类型必须是 CSOT，即那条唯一会反转 Y 的分支。
calls = re.findall(r"nvt::panelSlots\s*\(([^;]*)\)", text)
assert calls, "bridge does not call panelSlots"
for arguments in calls:
    assert "PanelVendor::Csot" in arguments, (
        "panelSlots must select the reversing branch unconditionally; "
        "found: nvt::panelSlots(" + arguments.strip() + ")")

# 不能再有一个由 panel_vendor 决定的变量喂给坐标映射。
if re.search(r"\bvendor\b\s*=\s*identity\.vendor", text):
    fail("bridge still derives the coordinate mapping from panel_vendor")

# panel_vendor 仍要读，它保留选固件和校准告警的用途。
if "readPanelVendor" not in text:
    fail("bridge no longer reads panel_vendor for the firmware/calibration check")

print("OK: finger Y axis is reversed unconditionally; panel_vendor no longer "
      "decides the coordinate mapping")
