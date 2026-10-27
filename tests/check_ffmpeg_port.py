#!/usr/bin/env python3
"""离线检查 Fedora FFmpeg 的 BSP 补丁移植与配方改写。

真实 RPM 构建需要 Koji 源包和完整构建依赖，这里只跑纯函数：移植规则是否仍复现
已审核的补丁、解码器清单与配方的改写是否仍然成立，以及"编过了"不等于"解码器在"
的拒绝路径。BSP checkout 不在时跳过依赖它的部分，其余照跑。
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_SPEC = importlib.util.spec_from_file_location(
    "fedora_ffmpeg", ROOT / "scripts/build-fedora-ffmpeg.py")
ffmpeg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ffmpeg)

SERIES = {"0001-v4l2m2m-follow-stateful-source-change.patch": (2, 1),
          "0002-v4l2m2m-accept-empty-event-queue.patch": (0, 0),
          "0003-v4l2m2m-preserve-encoder-drain-poll.patch": (0, 0)}


def pinned(attribute, value):
    """临时替换摘要常量，跑完恢复，避免测试之间互相影响。"""
    class Swap:
        def __enter__(self):
            self.saved = getattr(ffmpeg, attribute)
            setattr(ffmpeg, attribute, value)

        def __exit__(self, *exc):
            setattr(ffmpeg, attribute, self.saved)
    return Swap()


def tamper(path):
    """改一个字节并确认真的改了，否则负向控制会退化成空操作。"""
    before = path.read_bytes()
    path.write_bytes(before.replace(b"v4l2", b"v4L2", 1))
    assert path.read_bytes() != before, "the negative control did not change the patch"
    return path


def copy_series(source, sunuefi):
    """把 BSP series 摆成一个最小 SunUEFI 布局，供负向控制改动。"""
    folder = sunuefi / ffmpeg.BSP_SERIES
    folder.mkdir(parents=True)
    metadata = json.loads((source / "series.json").read_text())
    for name in ["series.json"] + [row["path"] for row in metadata["series"]]:
        shutil.copyfile(source / name, folder / name)
    return metadata, folder


def check_guards():
    # 产物必须留在 build/ 内，且非原生 root 环境要直接拒绝：两条都在下载源包之前。
    try:
        ffmpeg.build(Path("/nonexistent-sunuefi"), Path("/tmp/ffmpeg-outside-build"))
    except ValueError as error:
        assert str(error) in ("native root Fedora builder required",
                              "choose a fresh build-owned FFmpeg output"), error
    else:
        raise AssertionError("build accepted an output outside the build directory")
    releasever = json.loads((ROOT / "product.json").read_text())["target"]["releasever"]
    assert ffmpeg.SPEC_RELEASE_NEW == "Release:        1.piano1%{?dist}\n"
    assert ffmpeg.RELEASE == f"1.piano1.fc{releasever}", "RPM release and product releasever disagree"
    assert f"fc{releasever}" in ffmpeg.SOURCE_URL and ffmpeg.SOURCE_SHA256


def check_decoders():
    synthetic = b"aac\nh263\nh263p\nh264\nhdr\nhevc\nmpeg4\n"
    expected = ("aac\nh263\nh263p\nh264_v4l2m2m # hardware\nh264\n"
                "hdr\nhevc_v4l2m2m # hardware\nhevc\nmpeg4\n")
    with pinned("ENABLE_DECODERS_SHA256", hashlib.sha256(synthetic).hexdigest()):
        adapted = ffmpeg.adapt_decoders(synthetic)
    # 按字母序插到锚点之后，且 h264 这种前缀同名项不能被当成重复。
    assert adapted == expected, adapted
    # 摘要对不上就不许改：Fedora 换了清单必须重新审核，而不是照插。
    try:
        ffmpeg.adapt_decoders(synthetic)
    except ValueError:
        pass
    else:
        raise AssertionError("a changed enable_decoders digest was accepted")
    with pinned("ENABLE_DECODERS_SHA256", hashlib.sha256(adapted.encode()).hexdigest()):
        try:
            ffmpeg.adapt_decoders(adapted.encode())
        except ValueError:
            pass
        else:
            raise AssertionError("an already-enabled decoder was inserted twice")


def check_spec():
    names = sorted("sunuefi-" + name for name in ffmpeg.PORT_SHA256)
    synthetic = ("Name:           ffmpeg\nVersion:        8.1.3\nRelease:        1%{?dist}\n"
                 "Patch11:        ffmpeg-foo.patch\n"
                 "Patch1002:      ffmpeg-chromium.patch\n"
                 "Requires:       libavcodec%{?pkg_suffix}%{_isa} = %{version}-%{release}\n"
                 "\n%autosetup -S git_am\n")
    raw = synthetic.encode()
    with pinned("SPEC_SHA256", hashlib.sha256(raw).hexdigest()):
        adapted = ffmpeg.adapt_spec(raw, names)
    assert "Release:        1.piano1%{?dist}\n" in adapted
    # 官方版本号一旦超过 8.1.3，只有 epoch 能让补丁版继续排在它之上。
    assert f"Epoch:          {ffmpeg.EPOCH}\n" in adapted, adapted
    # 加了 epoch 之后，包内精确版本依赖必须一起带前缀，否则子包互相装不上：
    # rpm 认为 `= 8.1.3-1.piano1` 不等于 `= 1:8.1.3-1.piano1`。
    assert ("Requires:       libavcodec%{?pkg_suffix}%{_isa} = "
            "%{?epoch:%{epoch}:}%{version}-%{release}\n") in adapted, adapted
    # 编号必须排在 Fedora 现有的最大补丁号之后，%autopatch 才按顺序把 BSP 补丁放在最后。
    declared = "".join(f"Patch{2001 + index}:      {name}\n" for index, name in enumerate(names))
    assert "Patch1002:      ffmpeg-chromium.patch\n" + declared in adapted, adapted
    for broken in (synthetic.replace("Patch1002", "Patch1001"),
                   synthetic.replace("Release:        1%{?dist}\n", "")):
        try:
            with pinned("SPEC_SHA256", hashlib.sha256(broken.encode()).hexdigest()):
                ffmpeg.adapt_spec(broken.encode(), names)
        except ValueError:
            pass
        else:
            raise AssertionError("a spec without the expected layout was accepted")


def fake_payload(root, listing):
    binary = root / "usr/bin/ffmpeg"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\ncat <<'EOF'\n" + listing + "EOF\n")
    binary.chmod(0o755)
    return root


def check_proof():
    patched = (" V..... h264_v4l2m2m         V4L2 mem2mem H.264 decoder wrapper (codec h264)\n"
               " V..... hevc_v4l2m2m         V4L2 mem2mem HEVC decoder wrapper (codec hevc)\n")
    # 发行版自带的清单只有别的 v4l2m2m 解码器；字符串或符号名在不在都不算证据。
    stock = (" V..... h263_v4l2m2m         V4L2 mem2mem H.263 decoder wrapper (codec h263)\n"
             " V..... mpeg4_v4l2m2m        V4L2 mem2mem MPEG4 decoder wrapper (codec mpeg4)\n"
             " V..... libopenh264          OpenH264 H.264 decoder (codec h264)\n")
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        proof = ffmpeg.decoder_proof(fake_payload(work / "patched", patched))
        assert set(proof) == set(ffmpeg.DECODERS), proof
        assert proof["h264_v4l2m2m"].split()[1] == "h264_v4l2m2m"
        try:
            ffmpeg.decoder_proof(fake_payload(work / "stock", stock))
        except ValueError:
            pass
        else:
            raise AssertionError("a listing without the v4l2m2m decoders was accepted")


def check_workflow():
    workflow = (ROOT / ".github/workflows/build-ffmpeg.yml").read_text()
    assert "scripts/build-fedora-ffmpeg.py" in workflow
    assert "--output build/ffmpeg" in workflow
    # 打包步骤必须与脚本实际写出的文件名一致，否则 CI 到收尾才失败。
    for name in ("manifest.json", "installed-packages.tsv", "rpms"):
        assert name in workflow, name
    # rpm 6 把构建目录放进 %{NAME}-%{VERSION}-build，诊断路径必须用 glob 匹配，
    # 否则失败时 if-no-files-found: warn 会静默丢现场。
    for pattern in ("build/ffmpeg/rpmbuild/BUILD/**/ffbuild/config.log",
                    "build/ffmpeg/rpmbuild/BUILD/**/config_components.h"):
        assert pattern in workflow, pattern


def check_bsp_port(sunuefi):
    series = sunuefi / ffmpeg.BSP_SERIES
    if not (series / "series.json").is_file():
        print(f"SKIP: BSP FFmpeg series unavailable at {series}; port checks not run")
        return
    ported = ffmpeg.port_series(sunuefi)
    assert set(ported) == {"sunuefi-" + name for name in ffmpeg.PORT_SHA256}
    for name, row in ported.items():
        assert row["text"].startswith("From: "), name
        assert "ff_refstruct_" not in row["text"], name
        assert row["sha256"] == ffmpeg.PORT_SHA256[name.removeprefix("sunuefi-")], name
    changed = ported["sunuefi-0001-v4l2m2m-follow-stateful-source-change.patch"]["text"]
    # 8.x 的改名只碰这三个引用；数目对不上说明补丁本身换了内容。
    assert (changed.count("av_refstruct_unref"), changed.count("av_refstruct_ref")) == SERIES[
        "0001-v4l2m2m-follow-stateful-source-change.patch"]
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        name = "0001-v4l2m2m-follow-stateful-source-change.patch"
        # 1) BSP 补丁被改动而 series.json 没跟上：必须停。
        _, folder = copy_series(series, work / "stale")
        tamper(folder / name)
        try:
            ffmpeg.port_series(work / "stale")
        except ValueError:
            pass
        else:
            raise AssertionError("a BSP patch differing from its recorded digest was accepted")
        # 2) 连摘要一起改：仍然必须停，移植规则是被钉住的，不是"能替换就替换"。
        fresh, folder = copy_series(series, work / "fresh")
        target = tamper(folder / name)
        for row in fresh["series"]:
            if row["path"] == target.name:
                row["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
        (folder / "series.json").write_text(json.dumps(fresh))
        assert fresh["series"][0]["sha256"] != json.loads(
            (series / "series.json").read_text())["series"][0]["sha256"]
        try:
            ffmpeg.port_series(work / "fresh")
        except ValueError:
            pass
        else:
            raise AssertionError("a silently changed BSP patch reproduced the reviewed digest")
        # 3) 规则本身被改动同样要停：派生摘要绑定的是这套替换，不只是输入文件。
        with pinned("PORT_SUBSTITUTIONS", ()):
            try:
                ffmpeg.port_series(sunuefi)
            except ValueError:
                pass
            else:
                raise AssertionError("porting without the reviewed substitutions was accepted")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sunuefi", type=Path, default=Path(os.environ.get(
        "PIANO_TEST_SUNUEFI", ROOT / "build/test-sources/sunuefi")))
    args = ap.parse_args()
    check_guards()
    check_decoders()
    check_spec()
    check_proof()
    check_workflow()
    check_bsp_port(args.sunuefi)
    print(f"OK: Fedora FFmpeg port rules pinned for {len(ffmpeg.PORT_SHA256)} BSP patches"
          f", {len(ffmpeg.DECODERS)} v4l2m2m decoders and the RPM proof rejection path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
