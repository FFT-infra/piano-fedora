#!/usr/bin/env python3
"""设备编解码验收脚本的前置检查契约。

`make test-device-media` 在平板本体上跑。2026-10-21 实机第一次跑时，B 帧那一步
直接抛 `returned non-zero exit status 8`：Fedora 的 `ffmpeg-free` 没编译
`libx264`/`libx265`，而脚本默认用它们生成素材。文档早就写了“素材在别的机器
上生成，用 `--fixture-dir` 传过去”，脚本却没说清失败原因。

这里只验证脚本结构与判定逻辑：真跑编码、真读 Iris 中断要平板，不在 CI 里做。
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEDIA = ROOT / "scripts" / "verify-device-media.py"


def load():
    spec = importlib.util.spec_from_file_location("verify_device_media", MEDIA)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# `ffmpeg -encoders` 的真实输出形状：一行 ` V....D libx264 ...`。
# 前缀是一个前导空格加能力字母，不是固定列宽。
FFMPEG_FREE = """\
Encoders:
 V..... = Video
 ------
 V....D libopenh264          OpenH264 H.264 (codec h264)
 V..... h264_v4l2m2m         V4L2 mem2mem H.264 encoder (codec h264)
 V....D h264_vaapi           H.264/AVC (VAAPI) (codec h264)
 V..... hevc_v4l2m2m         V4L2 mem2mem HEVC encoder (codec h264)
"""


def test_script_is_importable():
    module = load()
    for name in ("encoder_available", "checksums", "interrupts"):
        assert hasattr(module, name), f"verify-device-media.py lost {name}()"


def test_encoder_lookup_matches_exact_names():
    """`h264` 不能命中 `h264_vaapi`，否则缺 libx264 时会被误判为可用。"""
    module = load()
    assert not module.encoder_available("libx264", FFMPEG_FREE)
    assert not module.encoder_available("libx265", FFMPEG_FREE)
    assert module.encoder_available("libopenh264", FFMPEG_FREE)
    assert module.encoder_available("h264_vaapi", FFMPEG_FREE)
    assert module.encoder_available("hevc_v4l2m2m", FFMPEG_FREE)


def test_missing_encoder_is_checked_before_generating():
    """缺编码器要在启动 ffmpeg 之前拦下，不能把 exit status 8 抛给操作者。

    断言顺序而不是实现：`encoder_available` 的调用必须早于生成素材的 `run()`。
    """
    text = MEDIA.read_text()
    guard = text.index("if not encoder_available(software_encoder):")
    generate = text.index("run(name + '-generate'")
    assert guard < generate, "encoder 前置检查必须在 -generate 之前"
    block = text[guard:generate]
    assert "--fixture-dir" in block, "报错要指出该用 --fixture-dir 传外部素材"


def test_fixture_path_skips_the_encoder_entirely():
    """带 --fixture-dir 时不应再查本机编码器：素材是别处编好的。"""
    module = load()
    assert module.encoder_available("libx264", FFMPEG_FREE) is False
    text = MEDIA.read_text()
    fixture_branch = text.index("if args.fixture_dir:")
    fixture_body = text[fixture_branch:text.index("else:", fixture_branch)]
    assert "encoder_available" not in fixture_body, (
        "fixture 分支不能依赖本机编码器")
    assert "copyfile" in fixture_body, "fixture 分支负责把素材复制进证据目录"


def main():
    test_script_is_importable()
    test_encoder_lookup_matches_exact_names()
    test_missing_encoder_is_checked_before_generating()
    test_fixture_path_skips_the_encoder_entirely()
    print("OK: 设备编解码验收在缺 libx264/libx265 时给出可操作的报错，fixture 路径不受影响")


if __name__ == "__main__":
    main()
