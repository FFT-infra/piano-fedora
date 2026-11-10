#!/usr/bin/env python3
"""相机用户态的离线契约。

相机源码是上游 `linux/userspace/camera` 的副本（`src/ORIGIN.md` 记来源），
这里锁住四件事：两个二进制共用一个协议头、守护进程的控制面仍归 video 组、
静态拍摄两条路径都在（自起流的常亮补光、带预览时的上游频闪），以及 spec
按 `make` 编出这两个二进制。后摄模式是设备上量出来的 4080x3060，全幅
8160x6120 走 RAW10，由 Rust worker 合成。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages" / "piano-camera"
SRC = PKG / "src"
DAEMON = SRC / "piano-camerad.c"
CTL = SRC / "piano-camera-ctl.c"
PROTOCOL = SRC / "photo" / "protocol.h"
PROTOCOL_C = SRC / "photo" / "protocol.c"
SPEC = PKG / "piano-camera.spec"


def main():
    protocol = PROTOCOL.read_text()
    assert "struct piano_photo_result" in protocol, "回复记录结构缺失"
    # 控制 socket 路径在守护进程里定义，客户端按同一常量拼接请求。
    assert "PIANO_PHOTO_STILL_FRAME" in protocol, "静态拍摄缺自己的记录标志"
    protocol_c = PROTOCOL_C.read_text()
    assert "piano_photo_validate_image" in protocol_c, "回复记录没有校验"
    assert "F_ADD_SEALS" in protocol_c, "交付的帧没有封成只读 memfd"

    daemon = DAEMON.read_text()
    assert '#include "photo/timing.h"' in daemon, "守护进程没有走上游的 photo 记录"
    for marker in ("camera_still_request", "camera_torch_start",
                   "camera_photo_request", "cam_start",
                   "V4L2_FLASH_LED_MODE_TORCH"):
        assert marker in daemon, f"守护进程缺少 {marker}"
    assert "CONTROL_PATH" in daemon and '"/run/piano-camerad/control.sock"' in daemon, \
        "控制 socket 路径变了"
    assert '"video"' in daemon and "chmod(CONTROL_PATH, 0660)" in daemon, \
        "控制 socket 没有交给 video 组"
    # 后摄必须留在驱动实际接受的尺寸上，否则每次 S_FMT 都被钳回。
    assert "{ 4080, 3060, 30 }" in daemon, "后摄模式不是 4080x3060"
    assert "{ 8160, 6120, 10 }" in daemon, "全幅 Quad CFA 模式缺失"

    ctl = CTL.read_text()
    assert "photo/jpeg.h" in ctl, "客户端没有复用协议头"
    assert "SCM_RIGHTS" not in ctl and "piano_photo_receive" in ctl, \
        "客户端应通过协议函数收帧，而不是自己拼 SCM_RIGHTS"
    assert '"capture %s%s"' in ctl and "flash ? \" flash\"" in ctl, "客户端没有发 capture 请求"
    assert "--flash" in ctl, "客户端缺少 --flash"

    spec = SPEC.read_text()
    for dependency in ("libjpeg-turbo-devel", "libyuv-devel", "cargo", "rust"):
        assert f"BuildRequires:  {dependency}" in spec, f"spec 缺 {dependency}"
    assert "make -C src" in spec, "spec 没有用 src/Makefile 构建"
    assert "%{_prefix}/lib/piano/piano-camerad" in spec, "spec 没有安装守护进程"
    assert "%{_bindir}/piano-camera-ctl" in spec, "spec 没有安装客户端"
    print("OK: 静态拍摄共用协议头，控制面归 video 组，spec 用 make 编两个二进制")


if __name__ == "__main__":
    main()
