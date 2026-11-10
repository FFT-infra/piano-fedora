#!/usr/bin/env python3
"""静态拍摄链路的离线契约。

`piano-camera-ctl` 只负责收帧和编码，相机硬件归 `piano-camerad`。这里锁住
三件事：两端的协议常量是同一个头文件、守护进程真的把 socket 交给 video 组、
RPM 里两个二进制和 libjpeg 依赖都在。后摄传感器模式是设备上量出来的
4080x3060：驱动把 3072 钳回 3060，写回读校验会以 EPIPE 失败。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages" / "piano-camera"
DAEMON = PKG / "src" / "piano-camerad.c"
CTL = PKG / "src" / "piano-camera-ctl.c"
PROTOCOL = PKG / "src" / "photo-protocol.h"
SPEC = PKG / "piano-camera.spec"


def main():
    protocol = PROTOCOL.read_text()
    assert '"/run/piano-camerad/control.sock"' in protocol, "控制 socket 路径不在协议头里"
    assert "struct piano_photo_reply" in protocol, "回复记录结构缺失"

    daemon = DAEMON.read_text()
    assert '#include "photo-protocol.h"' in daemon, "守护进程没有使用共享协议头"
    for name in ("ctl_init", "ctl_event", "camera_capture", "flash_arm", "memfd_image"):
        assert re.search(r"static [a-z ]+%s\s*\(" % name, daemon), f"守护进程缺少 {name}"
    assert "getgrnam(\"video\")" in daemon, "控制 socket 没有交给 video 组"
    assert "chmod(sa.sun_path, 0660)" in daemon, "控制 socket 权限不是 0660"

    rear = re.search(r'\.name = "rear".*?\.in_w = (\d+), \.in_h = (\d+)', daemon, re.S)
    assert rear, "后摄配置缺失"
    assert rear.groups() == ("4080", "3060"), \
        "后摄传感器模式必须是驱动实际接受的 4080x3060，否则 S_FMT 读回校验失败"

    ctl = CTL.read_text()
    assert "PIANO_PHOTO_SOCKET" in ctl and "photo-protocol.h" in ctl, "客户端没有复用协议头"
    assert "SCM_RIGHTS" in ctl, "客户端没有通过 SCM_RIGHTS 收帧"
    assert "--flash" in ctl and "jpeg_set_quality" in ctl, "客户端缺少闪光参数或 JPEG 编码"

    spec = SPEC.read_text()
    assert "BuildRequires:  libjpeg-turbo-devel" in spec, "spec 缺 libjpeg 构建依赖"
    assert "-ljpeg -o piano-camera-ctl" in spec, "spec 没有构建 piano-camera-ctl"
    assert "%{_bindir}/piano-camera-ctl" in spec, "spec 没有安装 piano-camera-ctl"
    print("OK: 静态拍摄两端共用协议头，socket 归 video 组，spec 构建两个二进制")


if __name__ == "__main__":
    main()
