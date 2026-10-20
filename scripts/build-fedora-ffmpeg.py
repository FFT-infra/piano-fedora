#!/usr/bin/env python3
"""重建固定 Fedora FFmpeg 源 RPM，启用 v4l2m2m H.264/HEVC 解码器并加入 BSP 补丁。

Fedora 的 ffmpeg-free 用 `--disable-decoder="h264,hevc,vc1,vvc"` 关掉原生解码器，
且 enable_decoders 里没有 h264_v4l2m2m/hevc_v4l2m2m，所以官方包只有编码器没有
硬件解码器。本脚本在原配方上做两件事：把两个 v4l2m2m 解码器加回解码器清单，
并套上 SunUEFI BSP 的 stateful decoder 补丁（Iris 解码器需要它才能正常收尾）。
"""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import urllib.request
from pathlib import Path

from piano_artifacts import sha256

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://kojipkgs.fedoraproject.org/packages/ffmpeg/8.1.3/1.fc44/src/ffmpeg-8.1.3-1.fc44.src.rpm"
SOURCE_SHA256 = "68d2b839582508b17218377481bef903a1de30e818118351b62dee9a5aa525ca"
SPEC_SHA256 = "b140dab3173f8a7546f494dbdd813f5f97105e3eef0bb405bafda74c40af4244"
TARBALL_SHA256 = "7138d28c96d9d3e3af4ee3d8cad72741f8ffb40da90c1112235dea3ecd3178a3"
ENABLE_DECODERS_SHA256 = "84ee7d0501f5c9d501f9518f5f9d2458f529ae519b51df0bd9751c66dfaa03b6"
SPEC_RELEASE_OLD = "Release:        1%{?dist}\n"
SPEC_RELEASE_NEW = "Release:        1.piano1%{?dist}\n"
SPEC_PATCH_ANCHOR = "Patch1002:      ffmpeg-chromium.patch\n"
RELEASE = "1.piano1.fc44"
# 只有运行时库和 CLI，-devel 与 freeworld 不装进设备镜像。
PACKAGES = {"ffmpeg-free", "libavcodec-free", "libavdevice-free", "libavfilter-free",
            "libavformat-free", "libavutil-free", "libswresample-free", "libswscale-free"}
# 解码器名 -> 在 enable_decoders 里插到哪一行之后（该文件按字母序排列）。
DECODERS = {"h264_v4l2m2m": "h263p", "hevc_v4l2m2m": "hdr"}

# BSP 补丁固定在 7.1.5/sunuefi3。作者取自 SunUEFI 仓库里这些补丁的提交者；
# 补丁本身只有 Subject:，而 Fedora 的 %autosetup -S git_am 需要完整邮件头，
# 缺 From: 时 git am 会以空作者失败。
BSP_SERIES = "linux/bsp/patches/ffmpeg"
BSP_VERSION = "7.1.5"
BSP_REVISION = "sunuefi4"
PORT_MAIL_HEADER = "From: slwyts <slwyts@foxmail.com>\n"
# FFmpeg 8 把 refstruct API 从 libavcodec 移到 libavutil 并改名为 av_refstruct_*，
# v4l2_m2m_dec.c 里那行注释的拼写也在 8.x 修正。三处都是纯文本替换，无语义冲突：
# v4l2_context.c 在 7.1.5 与 8.1.3 之间逐字节相同，v4l2_m2m_dec.c 只差那行注释，
# v4l2_buffers.c 只差改名。派生结果用 PORT_SHA256 钉住，规则被改动即失败。
PORT_SUBSTITUTIONS = (
    ("ff_refstruct_unref", "av_refstruct_unref"),
    ("ff_refstruct_ref", "av_refstruct_ref"),
    ("/* cant recover */", "/* can't recover */"),
)
PORT_SHA256 = {
    "0001-v4l2m2m-follow-stateful-source-change.patch":
        "d6086642e7a54aa071625e8e1cf01ce826f609f2cb3e1c47aaedd23a7ee4c5f8",
    "0002-v4l2m2m-accept-empty-event-queue.patch":
        "256d3639ed669c6e1b2124ea27019b51a0534dcad4809914876df666bf097419",
    "0003-v4l2m2m-preserve-encoder-drain-poll.patch":
        "9a90d78017753b1a9041ba011d3697f05ab1a81c1c40be91082fefa2388889e6",
    "0005-v4l2m2m-orphan-capture-ffmpeg7.patch":
        "43ee39ee05f18802851f109f17f8244f2f31cda372e166bf3f32855c6b825155",
}
# Fedora 的 %autosetup -S git_am 用 git am 提交补丁，需要 committer 身份；
# Koji 构建机有完整域名可以自动推断，容器里没有，所以显式给出。
COMMITTER_NAME = "piano fedora build"
COMMITTER_EMAIL = "piano-fedora-build@localhost"


def run(*command, env=None):
    subprocess.run([str(x) for x in command], check=True, env=env)


def query(*command):
    return subprocess.check_output([str(x) for x in command], text=True).strip()


def port_series(sun):
    """从锁定的 BSP checkout 派生 8.1.3 补丁，逐条核对来源摘要。"""
    folder = sun / BSP_SERIES
    bsp = json.loads((folder / "series.json").read_text())
    if (bsp.get("upstream_version"), bsp.get("local_revision")) != (BSP_VERSION, BSP_REVISION):
        raise ValueError("BSP FFmpeg series changed version; re-check the patches before porting")
    ported = {}
    for row in bsp["series"]:
        raw = (folder / row["path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("BSP patch differs from its recorded digest: " + row["path"])
        # 补丁没有 From: 时 git am 无法确定作者，必须补上邮件头再套 8.x 的改名。
        # 上游新补丁已自带 From:，重复的邮件头会让 git am 读到两个作者。
        text = ("" if raw.startswith(b"From: ") else PORT_MAIL_HEADER) + raw.decode()
        for old, new in PORT_SUBSTITUTIONS:
            text = text.replace(old, new)
        digest = hashlib.sha256(text.encode()).hexdigest()
        if digest != PORT_SHA256.get(row["path"]):
            raise ValueError("port rule no longer reproduces the reviewed patch: " + row["path"])
        ported["sunuefi-" + row["path"]] = {"text": text, "bsp_sha256": row["sha256"],
                                            "sha256": digest}
    if set(ported) != {"sunuefi-" + name for name in PORT_SHA256}:
        raise ValueError("BSP series and the reviewed port disagree on the patch set")
    return ported


def adapt_decoders(raw, names=DECODERS):
    """把 v4l2m2m 解码器插回 enable_decoders；该文件按字母序排列。"""
    if hashlib.sha256(raw).hexdigest() != ENABLE_DECODERS_SHA256:
        raise ValueError("Fedora enable_decoders changed; review the decoder list")
    text = raw.decode()
    lines = text.splitlines(keepends=True)
    for decoder, anchor in names.items():
        if any(line.split()[:1] == [decoder] for line in lines):
            raise ValueError("decoder already in the Fedora list: " + decoder)
        hits = [index for index, line in enumerate(lines) if line.rstrip("\n") == anchor]
        if len(hits) != 1:
            raise ValueError("enable_decoders anchor is not unique: " + anchor)
        lines.insert(hits[0] + 1, f"{decoder} # hardware\n")
    return "".join(lines)


def adapt_spec(raw, names):
    if hashlib.sha256(raw).hexdigest() != SPEC_SHA256:
        raise ValueError("Fedora FFmpeg spec changed; review the recipe")
    text = raw.decode()
    if text.count(SPEC_RELEASE_OLD) != 1 or text.count(SPEC_PATCH_ANCHOR) != 1:
        raise ValueError("unexpected Fedora FFmpeg spec layout")
    declared = "".join(f"Patch{2001 + index}:      {name}\n" for index, name in enumerate(names))
    return text.replace(SPEC_RELEASE_OLD, SPEC_RELEASE_NEW).replace(
        SPEC_PATCH_ANCHOR, SPEC_PATCH_ANCHOR + declared, 1)


def decoder_proof(payload):
    """运行装出来的 ffmpeg，确认两个 v4l2m2m 解码器真的注册了。

    只看源码或符号名不够：编码器与解码器同名，字符串存在不能证明解码器被编进来。
    """
    proof = {}
    environment = {**os.environ, "LD_LIBRARY_PATH": str(payload / "usr/lib64")}
    listing = subprocess.run([str(payload / "usr/bin/ffmpeg"), "-hide_banner", "-decoders"],
                             env=environment, capture_output=True, text=True, check=True).stdout
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) > 1 and fields[1] in DECODERS:
            proof[fields[1]] = line.strip()
    if set(proof) != set(DECODERS):
        raise ValueError("built ffmpeg lacks the v4l2m2m decoders: " + repr(sorted(proof)))
    return proof


def build(sun, output):
    if platform.machine() not in ("aarch64", "arm64") or os.geteuid() != 0:
        raise ValueError("native root Fedora builder required")
    if output.exists() or not output.is_relative_to(ROOT / "build"):
        raise ValueError("choose a fresh build-owned FFmpeg output")
    ported = port_series(sun)
    output.mkdir(parents=True)
    srpm = output / "ffmpeg.src.rpm"
    with urllib.request.urlopen(SOURCE_URL, timeout=120) as incoming, srpm.open("xb") as stream:
        shutil.copyfileobj(incoming, stream)
    if sha256(srpm) != SOURCE_SHA256:
        raise ValueError("Fedora FFmpeg source RPM changed")
    top = output / "rpmbuild"
    for directory in ("SOURCES", "SPECS", "BUILD", "BUILDROOT", "RPMS", "SRPMS"):
        (top / directory).mkdir(parents=True)
    run("rpm", "-i", "--nodeps", "--define", "_topdir " + str(top), srpm)
    spec = top / "SPECS/ffmpeg.spec"
    if sha256(top / "SOURCES/ffmpeg-8.1.3.tar.xz") != TARBALL_SHA256:
        raise ValueError("FFmpeg tarball differs from the upstream release")
    spec.write_text(adapt_spec(spec.read_bytes(), ported))
    enable_list = top / "SOURCES/enable_decoders"
    enable_list.write_text(adapt_decoders(enable_list.read_bytes()))
    for name, patch in ported.items():
        (top / "SOURCES" / name).write_text(patch["text"])
    # 复用 Fedora 的完整依赖和打包配方，不手列 FFmpeg 的大量构建依赖。
    run("dnf", "-y", "builddep", "--define", "_topdir " + str(top),
        "--spec", spec, "--srpm", srpm)
    environment = {**os.environ, "GIT_COMMITTER_NAME": COMMITTER_NAME,
                   "GIT_COMMITTER_EMAIL": COMMITTER_EMAIL}
    run("rpmbuild", "-bb", "--define", "_topdir " + str(top),
        "--define", "_smp_build_ncpus 4", "--define", "_smp_mflags -j4",
        "--define", "debug_package %{nil}", spec, env=environment)
    rpms = output / "rpms"
    rpms.mkdir()
    selected = {}
    for path in (top / "RPMS").rglob("*.rpm"):
        name, version, release, arch = query(
            "rpm", "-qp", "--qf", "%{NAME} %{VERSION} %{RELEASE} %{ARCH}", path).split()
        if name not in PACKAGES:
            continue
        if name in selected or (version, release, arch) != ("8.1.3", RELEASE, "aarch64"):
            raise ValueError("unexpected FFmpeg RPM identity: " + path.name)
        shutil.copyfile(path, rpms / path.name)
        selected[name] = {"file": path.name, "nevra": f"{name}-{version}-{release}.{arch}"}
    if selected.keys() != PACKAGES:
        raise ValueError("FFmpeg runtime package set is incomplete")
    # 从实际 RPM 安装载荷跑一次二进制，而不是把"编过了"当成"解码器在"。
    payload = output / "payload"
    payload.mkdir()
    run("rpm", "--root", payload, "--dbpath", "/var/lib/rpm", "--initdb")
    run("rpm", "--root", payload, "--dbpath", "/var/lib/rpm", "--nodeps", "--noscripts",
        "-i", *sorted(rpms.glob("*.rpm")))
    decoders = decoder_proof(payload)
    packages = query("rpm", "-qa", "--root", payload, "--dbpath", "/var/lib/rpm",
                     "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n")
    (output / "installed-packages.tsv").write_text("\n".join(sorted(packages.splitlines())) + "\n")
    result = {"status": "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED", "releasever": "44",
              "arch": "aarch64", "source_url": SOURCE_URL, "source_rpm_sha256": SOURCE_SHA256,
              "source_tarball_sha256": TARBALL_SHA256, "original_spec_sha256": SPEC_SHA256,
              "effective_spec_sha256": sha256(spec),
              "enable_decoders_sha256": sha256(enable_list),
              "bsp_upstream_version": BSP_VERSION, "bsp_local_revision": BSP_REVISION,
              "patches": {name: {"sha256": row["sha256"], "bsp_sha256": row["bsp_sha256"]}
                          for name, row in sorted(ported.items())},
              "decoders": decoders, "packages": selected,
              "rpms": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                       for p in sorted(rpms.glob("*.rpm"))},
              "installed_packages_sha256": sha256(output / "installed-packages.tsv"),
              "compiler": query("gcc", "--version").splitlines()[0], "device_tested": False}
    (output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sunuefi", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.sunuefi.resolve(), args.output.resolve())
