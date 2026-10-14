#!/usr/bin/env python3
"""离线回归：FFmpeg 产物必须真的接进 rootfs 消费链，且诊断路径对得上 VPATH 布局。

只跑纯函数与文本断言，不构建 RPM、不 chroot 真机。finalizer 的 chroot 用 mock
替换，喂入 rpm -q 与 ffmpeg -decoders 的固定输出，证明「NEVRA 一致 + 解码器注册」
两条都必须满足，官方未改包或漏装解码器的镜像会被拒绝。
"""

import fnmatch
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_SPEC = importlib.util.spec_from_file_location(
    "finalizer", ROOT / "scripts/finalize-fedora-rootfs.py")
finalizer = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(finalizer)


def step_body(workflow, name):
    """取出某个 step 的 run 脚本体，缩进去掉一层。"""
    lines = (ROOT / workflow).read_text().splitlines()
    start = lines.index("      - name: " + name)
    run = lines.index("        run: |", start) + 1
    body = []
    for line in lines[run:]:
        if line.strip() and not line.startswith("          "):
            break
        body.append(line[10:])
    assert body, f"{name} step is empty"
    return "\n".join(body)


def check_artifact_consumed():
    """镜像 workflow 必须解析、下载并安装 piano-fedora-ffmpeg，否则重建白做。"""
    # build-ffmpeg.yml 是 workflow_call 复用件，未在 registry 单独注册；实际 run 的
    # workflow_id 是 host-check。解析必须按 host-check 走，不能按未注册的 build-ffmpeg。
    resolve = step_body(".github/workflows/build-image.yml", "Resolve successful input runs")
    assert "--workflow host-check.yml" in resolve, "the FFmpeg run is not resolved via host-check"
    assert "--workflow build-ffmpeg.yml" not in resolve, \
        "build-ffmpeg.yml is a reusable callee, not a registered workflow id"
    assert "--artifact piano-fedora-ffmpeg" in resolve, "the FFmpeg artifact name is not requested"
    workflow = (ROOT / ".github/workflows/build-image.yml").read_text()
    assert "name: piano-fedora-ffmpeg" in workflow, "the FFmpeg artifact is not downloaded"
    install = step_body(".github/workflows/build-image.yml", "Install the native Fedora device RPMs")
    assert "/workspace/build/ffmpeg/rpms/*.rpm" in install, \
        "the rebuilt FFmpeg RPMs are never installed into the rootfs"
    finalize = step_body(".github/workflows/build-image.yml", "Finalize services and the rootfs identity")
    assert "--ffmpeg build/ffmpeg" in finalize, "the finalizer does not verify the installed FFmpeg"


def check_diagnostics_paths():
    """诊断 artifact 的 BUILD 路径要匹配 rpm 6 的 %{NAME}-%{VERSION}-build 布局。"""
    workflow = (ROOT / ".github/workflows/build-ffmpeg.yml").read_text()
    start = workflow.index("name: ffmpeg-diagnostics")
    block = workflow[start:workflow.index("retention-days", start)]
    patterns = [line.strip() for line in block.splitlines()
                if line.strip().startswith("build/ffmpeg/rpmbuild/BUILD/")]
    assert patterns, "no BUILD diagnostics paths are declared"
    # 真实落点带一层 -build 目录；glob 必须匹配它，否则失败时不上传且不报错。
    real = {
        "build/ffmpeg/rpmbuild/BUILD/ffmpeg-8.1.3-build/ffmpeg-8.1.3/ffbuild/config.log":
            "build/ffmpeg/rpmbuild/BUILD/**/ffbuild/config.log",
        "build/ffmpeg/rpmbuild/BUILD/ffmpeg-8.1.3-build/ffmpeg-8.1.3/config_components.h":
            "build/ffmpeg/rpmbuild/BUILD/**/config_components.h",
    }
    for path, needed in real.items():
        assert any(fnmatch.fnmatch(path, pattern) for pattern in patterns), \
            f"diagnostics pattern does not cover the real VPATH path: {path}"
        assert needed in patterns, f"missing diagnostics pattern: {needed}"


def manifest():
    names = ("ffmpeg-free", "libavcodec-free", "libavdevice-free", "libavfilter-free",
             "libavformat-free", "libavutil-free", "libswresample-free", "libswscale-free")
    packages = {name: {"file": name + ".rpm", "nevra": f"{name}-8.1.3-1.piano1.fc44.aarch64"}
                for name in names}
    return {"status": "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED", "releasever": "44",
            "arch": "aarch64",
            "decoders": {"h264_v4l2m2m": " V..... h264_v4l2m2m",
                         "hevc_v4l2m2m": " V..... hevc_v4l2m2m"},
            "packages": packages}


def fake_chroot(decoders="h264_v4l2m2m hevc_v4l2m2m", nevra_override=None):
    """按命令返回 rpm -q 的 NEVRA 或 ffmpeg -decoders 的清单。"""
    def chroot(root, *command):
        if command[0] == "rpm":
            name = command[-1]
            return (nevra_override or {}).get(name, f"{name}-8.1.3-1.piano1.fc44.aarch64")
        assert command[0].endswith("ffmpeg"), command
        return "\n".join(f" V..... {name}          wrapper" for name in decoders.split())
    return chroot


def check_install_verification():
    root = Path("/nonexistent-rootfs")
    good = manifest()
    with patch.object(finalizer, "chroot", fake_chroot()):
        finalizer.verify_ffmpeg_install(root, good)
    # 包集合必须恰好是那 8 个：空集合或漏包都不能靠 NEVRA 循环绕过。
    for bad in ({}, {"ffmpeg-free": good["packages"]["ffmpeg-free"]}):
        with patch.object(finalizer, "chroot", fake_chroot()):
            try:
                finalizer.verify_ffmpeg_install(root, {**good, "packages": bad})
            except ValueError as error:
                assert "package set" in str(error), error
            else:
                raise AssertionError("an incomplete FFmpeg package set was accepted")
    # 官方未改包（release 不是 piano1）必须停。
    with patch.object(finalizer, "chroot",
                      fake_chroot(nevra_override={"ffmpeg-free": "ffmpeg-free-8.1.3-1.fc44.aarch64"})):
        try:
            finalizer.verify_ffmpeg_install(root, good)
        except ValueError as error:
            assert "differs" in str(error), error
        else:
            raise AssertionError("an unpatched ffmpeg-free package was accepted")
    # 缺解码器（例如 -decoders 里只有 h264）必须停。
    with patch.object(finalizer, "chroot", fake_chroot(decoders="h264_v4l2m2m")):
        try:
            finalizer.verify_ffmpeg_install(root, good)
        except ValueError as error:
            assert "decoders" in str(error), error
        else:
            raise AssertionError("a build without the v4l2m2m decoders was accepted")
    # 身份不对（非 piano 构建）必须停。
    for changed in ({"status": "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED", "releasever": "43"},
                    {"arch": "x86_64"}):
        with patch.object(finalizer, "chroot", fake_chroot()):
            try:
                finalizer.verify_ffmpeg_install(root, {**good, **changed})
            except ValueError:
                pass
            else:
                raise AssertionError("a foreign FFmpeg manifest was accepted")


def main():
    check_artifact_consumed()
    check_diagnostics_paths()
    check_install_verification()
    print("OK: FFmpeg artifact is installed and verified in the rootfs chain; "
          "diagnostics paths match the VPATH build layout")


if __name__ == "__main__":
    main()
