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
    metadata = json.loads((source / ffmpeg.BSP_SERIES_FILE).read_text())
    shutil.copyfile(source / ffmpeg.BSP_SERIES_FILE, folder / ffmpeg.BSP_SERIES_FILE)
    for row in metadata["series"]:
        src = (source / row["path"]).resolve()
        dst = (folder / row["path"]).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
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
    assert ffmpeg.SPEC_RELEASE_NEW == "Release:        3.piano1%{?dist}\n"
    assert ffmpeg.RELEASE == f"3.piano1.fc{releasever}", "RPM release and product releasever disagree"
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
    subpackages = "".join(f"{anchor}Summary:        {fedora}\n"
                          for anchor, fedora in ffmpeg.REPLACES.items())
    synthetic = ("%global pkg_suffix -free\n"
                 "Name:           ffmpeg\nVersion:        9.0.2\nRelease:        3%{?dist}\n"
                 "Patch11:        ffmpeg-foo.patch\n"
                 "Patch1002:      ffmpeg-chromium.patch\n"
                 + subpackages +
                 "Requires:       libavcodec%{?pkg_suffix}%{_isa} = %{version}-%{release}\n"
                 "\n%autosetup -S git_am\n")
    raw = synthetic.encode()
    with pinned("SPEC_SHA256", hashlib.sha256(raw).hexdigest()):
        adapted = ffmpeg.adapt_spec(raw, names)
    assert "Release:        3.piano1%{?dist}\n" in adapted
    # 改名压过官方版：子包全部带 -piano 后缀，各自声明替代的 Fedora 原名。
    assert ffmpeg.SPEC_SUFFIX_NEW in adapted, adapted
    assert "%global pkg_suffix -free\n" not in adapted, adapted
    assert "Epoch:" not in adapted
    for anchor, fedora in ffmpeg.REPLACES.items():
        assert anchor in adapted, anchor
        assert f"Provides:       {fedora} = %{{version}}-%{{release}}\n" in adapted, adapted
        # 按名字提 ffmpeg-free 的安装请求只能靠 Obsoletes 落到 -piano 这里。
        assert f"Obsoletes:      {fedora} < %{{version}}-%{{release}}\n" in adapted, adapted
        assert f"Conflicts:      {fedora}%{{?_isa}}\n" in adapted, adapted
    # 改名之后包内精确版本依赖原样保留，不需要任何 epoch 前缀。
    assert ("Requires:       libavcodec%{?pkg_suffix}%{_isa} = "
            "%{version}-%{release}\n") in adapted, adapted
    # 编号必须排在 Fedora 现有的最大补丁号之后，%autopatch 才按顺序把 BSP 补丁放在最后。
    declared = "".join(f"Patch{2001 + index}:      {name}\n" for index, name in enumerate(names))
    assert "Patch1002:      ffmpeg-chromium.patch\n" + declared in adapted, adapted
    for broken in (synthetic.replace("Patch1002", "Patch1001"),
                   synthetic.replace("Release:        3%{?dist}\n", ""),
                   synthetic.replace("%global pkg_suffix -free\n", ""),
                   synthetic.replace("%package -n libswscale%{?pkg_suffix}\n", "")):
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
    if not (series / ffmpeg.BSP_SERIES_FILE).is_file():
        print(f"SKIP: BSP FFmpeg series unavailable at {series}; port checks not run")
        return
    ported = ffmpeg.port_series(sunuefi)
    assert set(ported) == {"sunuefi-" + name for name in ffmpeg.PORT_SHA256}
    for name, row in ported.items():
        assert row["text"].startswith("From: "), name
        # FFmpeg 9 的系列是上游原生写的，不该再有 7.x 的 refstruct 旧名。
        assert "ff_refstruct_" not in row["text"], name
        assert row["sha256"] == ffmpeg.PORT_SHA256[name.removeprefix("sunuefi-")], name
        assert row["sha256"] == row["bsp_sha256"], name
    name = "0004-v4l2m2m-ffmpeg9-stateful.patch"
    text = ported["sunuefi-" + name]["text"]
    # 补丁确实改的是 FFmpeg 9 的 v4l2m2m 源文件，而不是同名的随便什么文件。
    for target in ("libavcodec/v4l2_m2m_dec.c", "libavcodec/v4l2_context.c"):
        assert target in text, f"{name} does not touch {target}"
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        # 1) BSP 补丁被改动而系列摘要没跟上：必须停。
        _, folder = copy_series(series, work / "stale")
        target_file = (folder / "../../../linux/bsp/patches/ffmpeg" / name).resolve()
        tamper(target_file)
        try:
            ffmpeg.port_series(work / "stale")
        except ValueError:
            pass
        else:
            raise AssertionError("a BSP patch differing from its recorded digest was accepted")
        # 2) 连摘要一起改：仍然必须停，本仓库自己钉的那份没有跟着动。
        fresh, folder = copy_series(series, work / "fresh")
        target_file = (folder / "../../../linux/bsp/patches/ffmpeg" / name).resolve()
        tamper(target_file)
        for row in fresh["series"]:
            if Path(row["path"]).name == name:
                row["sha256"] = hashlib.sha256(target_file.read_bytes()).hexdigest()
        (folder / ffmpeg.BSP_SERIES_FILE).write_text(json.dumps(fresh))
        assert fresh["series"][0]["sha256"] != json.loads(
            (series / ffmpeg.BSP_SERIES_FILE).read_text())["series"][0]["sha256"]
        try:
            ffmpeg.port_series(work / "fresh")
        except ValueError:
            pass
        else:
            raise AssertionError("a silently changed BSP patch reproduced the reviewed digest")
        # 3) 系列名的是另一个源码包：补丁对不上 SRPM 里的 tar.xz，必须停。
        other, folder = copy_series(series, work / "other")
        other["source"]["sha256"] = "0" * 64
        (folder / ffmpeg.BSP_SERIES_FILE).write_text(json.dumps(other))
        try:
            ffmpeg.port_series(work / "other")
        except ValueError:
            pass
        else:
            raise AssertionError("a series targeting another tarball was accepted")


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
    print(f"OK: Fedora FFmpeg 9 series pinned for {len(ffmpeg.PORT_SHA256)} BSP patches"
          f", {len(ffmpeg.DECODERS)} v4l2m2m decoders and the RPM proof rejection path")
    return 0


if __name__ == "__main__":
    sys.exit(main())
