#!/usr/bin/env python3
"""离线检查：用户态触控设备的身份、TOUCH_MINOR 与掌托上报。

实机证据（2026-11-18，Fedora 45 KDE）：笔有 `LIBINPUT_DEVICE_GROUP=piano-touchscreen`，
触控没有。设备身份是 `BUS_VIRTUAL` + `2717:3653` + `Piano THP Touchscreen`，
既不在 BSP udev 规则 `79-piano-pen-touch-group.rules` 的名字里，也不在
`spi|0603|6532` 的匹配范围内，因此 libinput 不会把这个触控屏和笔归到同一组，
笔在屏幕上写字时没有仲裁。

同一轮源码核对还发现两处缺口：没有 `ABS_MT_TOUCH_MINOR`，掌托锁存时核心会把
所有接触清空，用户态直接当成抬手上报。

这里只读源码，不编译、不碰设备。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = (ROOT / "runtime/touch/uinput-physical.patch").read_text()
BRIDGE = (ROOT / "runtime/touch/piano-finger-bridge.cpp").read_text()
# 与研究仓库同级；不存在时跳过需要真实 SunUEFI 的逐字节比对。
UPSTREAM = Path(os.environ.get("PIANO_TEST_SUNUEFI", ROOT / "build/test-sources/sunuefi"))


def fail(message):
    print("FAIL: " + message)
    sys.exit(1)


def require(condition, message):
    if not condition:
        fail(message)


# 身份必须与 BSP udev 规则、libwacom 的 DeviceMatch 对得上。
require("setup.id.bustype = BUS_SPI;" in PATCH, "uinput device is not on BUS_SPI")
require("setup.id.vendor = 0x0603;" in PATCH, "uinput vendor is not 0x0603")
require("setup.id.product = 0x6532;" in PATCH, "uinput product is not 0x6532")
require('"piano NT36532 THP touchscreen"' in PATCH,
        "uinput name does not match 79-piano-pen-touch-group.rules")
require(not any(line.startswith("+") and "BUS_VIRTUAL" in line
                for line in PATCH.splitlines()),
        "uinput device still claims BUS_VIRTUAL")

# TOUCH_MINOR 要同时出现在 capability、setup 与事件里，少一处内核都不会转给用户。
require(PATCH.count("ABS_MT_TOUCH_MINOR") >= 3,
        "ABS_MT_TOUCH_MINOR is missing from capability, setup or event path")
require("slot->contact.minor" in PATCH, "touch report does not emit the minor axis")
require("slot.contact.minor = minorAxis(slot.contact);" in BRIDGE,
        "bridge never computes the minor axis from the contact nodes")

# 掌托按槽上报 MT_TOOL_PALM，而不是固定的 MT_TOOL_FINGER。
require("slot->palm ? MT_TOOL_PALM : MT_TOOL_FINGER" in PATCH,
        "tool type is not per slot")
require("if (!slot->palm)" in PATCH and "finger = true;" in PATCH,
        "touch report never tracks whether a real finger is present")
require("event(EV_KEY, BTN_TOUCH, finger);" in PATCH
        and "event(EV_KEY, BTN_TOOL_FINGER, finger);" in PATCH,
        "BTN_TOUCH/BTN_TOOL_FINGER still follow the raw slot count")

# 核心清空接触时，桥接层必须把上一帧的手指按掌托继续按住，让 libinput 取消手势。
require("pipeline->palmActive()" in BRIDGE, "bridge ignores the palm latch")
require("slot.palm = true;" in BRIDGE and "input->report(lastFingers);" in BRIDGE,
        "bridge releases on palm instead of re-reporting MT_TOOL_PALM")
require("reportFingers({})" in BRIDGE, "bridge has no explicit release path")

# reset 表示数据流消失，必须先清掉 palm 状态再回调 sink，否则会把 cancel 当成释放路径
# 之外的语义，设备上留下按住的接触。
hunks = PATCH.split("\n@@ ")
reset_hunk = next((h for h in hunks if "void LivePipeline::reset()" in h), None)
require(reset_hunk is not None, "patch does not touch LivePipeline::reset")
added = "\n".join(line[1:] for line in reset_hunk.splitlines() if line.startswith("+"))
require("palm_active_ = false;" in added and "sink_({});" in added,
        "LivePipeline::reset does not clear the palm flag and release in one place")
require(added.index("palm_active_ = false;") < added.index("sink_({});"),
        "LivePipeline::reset calls the sink before clearing palm_active_")

print("OK: uinput identity matches the BSP udev rule, minor axis is reported, "
      "and a palm cancels contacts with MT_TOOL_PALM")

# 触控服务的起点随上游换过：edcf272 起手指与笔都由内核解码，piano-touch.service
# 只做 BlueZ 笔初始化，touch-input 与 pen-bluetooth 已删除。本包拥有这份 unit，
# 内容必须与 pin 的 BSP 副本逐字节一致，否则装配时 runtime 被 prune 掉的那份
# 与这里装上的不同字节会分叉，设备上跑的是一个指向不存在脚本的 unit。
UNIT = ROOT / "packages/xiaomi-piano-thp/usr/lib/systemd/system/piano-touch.service"
unit = UNIT.read_text()
require("ExecStart=/usr/lib/piano/pen-control.py --follow" in unit,
        "piano-touch.service no longer starts the pen Bluetooth initializer")
require("touch-input" not in unit and "piano-touch-view" not in unit,
        "piano-touch.service still starts a userspace THP reader")
require("Description=Initialize the Piano pen over Bluetooth" in unit,
        "piano-touch.service description does not match the kernel-decoded design")
if UPSTREAM.is_dir():
    pinned = UPSTREAM / "linux/bsp/common/usr/lib/systemd/system/piano-touch.service"
    require(pinned.is_file(), "pinned BSP has no piano-touch.service")
    require(pinned.read_bytes() == UNIT.read_bytes(),
            "packaged piano-touch.service differs from the pinned BSP copy")
else:
    print("SKIP: 与 pin 的逐字节比对需要 PIANO_TEST_SUNUEFI")
