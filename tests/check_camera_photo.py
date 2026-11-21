#!/usr/bin/env python3
"""相机用户态的离线契约。

源码不随包：spec 在构建期从 SunUEFI pin 取 `linux/userspace/camera`，再叠
`patches/0001` 的静态拍摄与 ancillary link 找镜头。这里锁住补丁里那些
设备上量出来的行为（控制面归 video 组、自起流补光与预览频闪两条路径），
以及 spec 确实按 `make` 编出两个二进制。上游那半边（4080x3060、8160x6120、
协议结构）在有 checkout 时一并核对。
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages" / "piano-camera"
PATCH = PKG / "patches" / "0001-still-capture-and-ancillary-lens.patch"
SPEC = PKG / "piano-camera.spec"
UPSTREAM = Path(os.environ.get("PIANO_TEST_SUNUEFI",
                               ROOT / "build/test-sources/sunuefi")) / \
    "linux/userspace/camera"


def added(text):
    return "\n".join(line[1:] for line in text.splitlines()
                     if line.startswith("+") and not line.startswith("+++"))


def main():
    patch = PATCH.read_text()
    for path in ("src/piano-camerad.c", "src/piano-camera-ctl.c",
                 "src/photo/protocol.c", "src/photo/protocol.h"):
        assert f"--- a/{path}" in patch and f"+++ b/{path}" in patch, \
            f"补丁没有改到 {path}"

    daemon = added(patch)
    for marker in ("camera_still_request", "camera_torch_start",
                   "camera_photo_request", "V4L2_FLASH_LED_MODE_TORCH",
                   "PIANO_PHOTO_STILL_FRAME", "ancillary",
                   '"capture %s%s"', "flash ? \" flash\""):
        assert marker in daemon, f"补丁缺少 {marker}"
    assert "topo_lens" in patch, "补丁没有动到找镜头的那段"
    assert "--flash" in patch, "客户端缺少 --flash"

    spec = SPEC.read_text()
    for dependency in ("libjpeg-turbo-devel", "libyuv-devel", "cargo", "rust"):
        assert f"BuildRequires:  {dependency}" in spec, f"spec 缺 {dependency}"
    assert "make -C src" in spec, "spec 没有用 src/Makefile 构建"
    assert "%{_prefix}/lib/piano/piano-camerad" in spec, "spec 没有安装守护进程"
    assert "%{_bindir}/piano-camera-ctl" in spec, "spec 没有安装客户端"
    assert "%patch 0 -p1 --fuzz=0" in spec, "spec 没有把补丁套到上游源码上"

    if UPSTREAM.is_dir():
        upstream = (UPSTREAM / "piano-camerad.c").read_text()
        # 后摄必须留在驱动实际接受的尺寸上，否则每次 S_FMT 都被钳回。
        assert "{ 4080, 3060, 30 }" in upstream, "上游后摄模式不是 4080x3060"
        assert "{ 8160, 6120, 10 }" in upstream, "上游全幅 Quad CFA 模式缺失"
        protocol = (UPSTREAM / "photo/protocol.c").read_text()
        assert "piano_photo_validate_image" in protocol, "回复记录没有校验"
        assert "F_ADD_SEALS" in protocol, "交付的帧没有封成只读 memfd"
    else:
        print("SKIP: 上游相机源码需要 PIANO_TEST_SUNUEFI")

    print("OK: 静态拍摄在补丁里，spec 用 make 编两个二进制")


if __name__ == "__main__":
    main()
