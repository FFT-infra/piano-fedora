#!/usr/bin/env python3
"""重建固定 Fedora FFmpeg 源 RPM，启用 v4l2m2m H.264/HEVC 解码器并加入 BSP 补丁。

Fedora 的 ffmpeg-free 用 `--disable-decoder="h264,hevc,vc1,vvc"` 关掉原生解码器，
且 enable_decoders 里没有 h264_v4l2m2m/hevc_v4l2m2m，所以官方包只有编码器没有
硬件解码器。本脚本在原配方上做两件事：把两个 v4l2m2m 解码器加回解码器清单，
并套上 SunUEFI BSP 的 stateful decoder 补丁（Iris 解码器需要它才能正常收尾）。

补丁取自 BSP 自己的 `series-9.json`：上游已把 7.1.5 那套按 FFmpeg 9 重写，
不再需要本仓库做 ff_refstruct 到 av_refstruct 的文本移植。
"""

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

from piano_artifacts import sha256

ROOT = Path(__file__).resolve().parents[1]
SOURCE_URL = "https://kojipkgs.fedoraproject.org/packages/ffmpeg/9.0.2/3.fc45/src/ffmpeg-9.0.2-3.fc45.src.rpm"
SOURCE_SHA256 = "0fabe97324ebeda50b02b4e6303f3a09187a6a24907e9b679d045de2021507b3"
SPEC_SHA256 = "ad2066c09c916e2942182770bdeb2c4f9dd7a235969ab827c10b45499691f687"
TARBALL_SHA256 = "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"
ENABLE_DECODERS_SHA256 = "84ee7d0501f5c9d501f9518f5f9d2458f529ae519b51df0bd9751c66dfaa03b6"
SPEC_RELEASE_OLD = "Release:        3%{?dist}\n"
SPEC_RELEASE_NEW = "Release:        3.piano1%{?dist}\n"
SPEC_PATCH_ANCHOR = "Patch1002:      ffmpeg-chromium.patch\n"
RELEASE = "3.piano1.fc45"
# 官方版本号一旦走到 9.0.3，release 后缀的 piano1 就压不住了。抬 epoch 能压住，
# 但 epoch 卸不掉：补丁进了上游之后回不到 Fedora 的包。这里改用 RPM Fusion
# 对同一个包的做法：换包名，声明它提供原名并与原名冲突。
# Fedora 的配方只让变体走 `pkg_suffix`，`Name:` 仍是 ffmpeg，源包名因此还是
# ffmpeg（RPM Fusion 的完整版也是这个名字）。改的是展开后的二进制包：主包是
# `%{name}%{?pkg_suffix}` = ffmpeg-piano，另外七个 libav* 也带 -piano。
# 设备上装的是这些二进制包，源包名不参与 dnf 的解析。
SOURCE_PACKAGE = "ffmpeg"
VARIANT_PACKAGE = "ffmpeg-piano"
# Fedora 的 spec 用一个开关区分 free 与完整编解码器两版：`pkg_suffix` 默认
# `-free`，所有 17 个子包名和 75 条依赖都跟着它走。把它改成 `-piano`，整份
# 配方就变成改名版，不用逐行改依赖。
SPEC_SUFFIX_OLD = "%global pkg_suffix -free\n"
SPEC_SUFFIX_NEW = "%global pkg_suffix -piano\n"
# 每个出货子包的锚点 -> 它替代的 Fedora 包名。锚点必须唯一，改名后的包名由
# 锚点里的宏展开得到。
REPLACES = {
    "%package -n     %{pkg_name}\n": "ffmpeg-free",
    "%package -n libavcodec%{?pkg_suffix}\n": "libavcodec-free",
    "%package -n libavdevice%{?pkg_suffix}\n": "libavdevice-free",
    "%package -n libavfilter%{?pkg_suffix}\n": "libavfilter-free",
    "%package -n libavformat%{?pkg_suffix}\n": "libavformat-free",
    "%package -n libavutil%{?pkg_suffix}\n": "libavutil-free",
    "%package -n libswresample%{?pkg_suffix}\n": "libswresample-free",
    "%package -n libswscale%{?pkg_suffix}\n": "libswscale-free",
}
# 只有运行时库和 CLI，-devel 与 freeworld 不装进设备镜像。
PACKAGES = {"ffmpeg-piano", "libavcodec-piano", "libavdevice-piano", "libavfilter-piano",
            "libavformat-piano", "libavutil-piano", "libswresample-piano", "libswscale-piano"}
# 解码器名 -> 在 enable_decoders 里插到哪一行之后（该文件按字母序排列）。
DECODERS = {"h264_v4l2m2m": "h263p", "hevc_v4l2m2m": "hdr"}

# BSP 补丁固定在 9.0.2/sunuefi3，这是上游为 FFmpeg 9 重写过的系列。
# 补丁自带 From:，但 Fedora 的 %autosetup -S git_am 需要完整邮件头，
# 新补丁漏了 From: 时 git am 会以空作者失败，所以缺了就补一条。
BSP_SERIES = "patches/ffmpeg/9.0.2"
BSP_SERIES_FILE = "series.json"
BSP_VERSION = "9.0.2"
BSP_REVISION = "sunuefi4"
PORT_MAIL_HEADER = "From: slwyts <slwyts@foxmail.com>\n"
# 每一条的 sha256 与 series.json 记录的一致。单独钉一份是为了在 upstream
# 原地改写同一版本号时仍然失败，而不是安静地跟着换补丁。
PORT_SHA256 = {
    "0004-v4l2m2m-ffmpeg9-stateful.patch":
        "ad7b86872378f38fbbdf8a24e31726f62a765869b6f910322be6a8d8695b1a3c",
    "0005-v4l2m2m-orphan-capture-ffmpeg9.patch":
        "b35c619d8467a15144db5ba58b4015884de71167f32b0c31fdb6f3b834e29bb3",
    "0006-v4l2m2m-p010-capture.patch":
        "2eeca4bcb88a54819c8211703eb3b6461e00a4fd122d5c405ff2764c08381044",
    "0001-v4l2m2m-join-sequence-header.patch":
        "fa547bc67853afc574a49653c5f4e57170f11a916c967d0220e2a0f7b83c6263",
    "0002-v4l2m2m-copy-visible-nv12-rows.patch":
        "83360b9c808c765a2f40ede0978e8984c6e1fec76ee1a1d7ebbadb32e32ca7ae",
    "0003-v4l2m2m-stop-unstarted-encoder.patch":
        "b8069106dc3c73279444607d7b3aab8726c0cf5a9bfb59d95fd7fed4c942d1f4",
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
    """从锁定的 BSP checkout 取 FFmpeg 9 系列，逐条核对来源摘要。"""
    folder = sun / BSP_SERIES
    bsp = json.loads((folder / BSP_SERIES_FILE).read_text())
    if (bsp.get("upstream_version"), bsp.get("local_revision")) != (BSP_VERSION, BSP_REVISION):
        raise ValueError("BSP FFmpeg series changed version; re-check the patches before porting")
    # 系列是为哪个源码包写的，必须就是 Fedora SRPM 里那个 tar.xz。
    source = bsp.get("source") or {}
    if source.get("sha256") != TARBALL_SHA256:
        raise ValueError("BSP FFmpeg series targets another source tarball than the Fedora SRPM")
    ported = {}
    for row in bsp["series"]:
        patch_path = (folder / row["path"]).resolve()
        raw = patch_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("BSP patch differs from its recorded digest: " + row["path"])
        # Fedora 的 %autosetup -S git_am 需要合法邮件作者头（From: Name <email>）。
        # 漏了 email 或整个 From: 的补丁补一条标准邮件头，保证 git am 能作为有效提交解析。
        text = raw.decode()
        if not re.match(r"^From:\s+[^\n<]+<[^\n>]+>", text):
            if text.startswith("From:"):
                text = PORT_MAIL_HEADER + text.split("\n", 1)[1]
            else:
                text = PORT_MAIL_HEADER + text
        digest = hashlib.sha256(text.encode()).hexdigest()
        name = patch_path.name
        if digest != PORT_SHA256.get(name):
            raise ValueError("BSP patch is not the reviewed FFmpeg 9 port: " + name)
        ported["sunuefi-" + name] = {"text": text, "bsp_sha256": row["sha256"],
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
    if text.count(SPEC_SUFFIX_OLD) != 1:
        raise ValueError("unexpected Fedora FFmpeg spec suffix switch")
    declared = "".join(f"Patch{2001 + index}:      {name}\n" for index, name in enumerate(names))
    text = text.replace(SPEC_RELEASE_OLD, SPEC_RELEASE_NEW).replace(
        SPEC_PATCH_ANCHOR, SPEC_PATCH_ANCHOR + declared, 1)
    text = text.replace(SPEC_SUFFIX_OLD, SPEC_SUFFIX_NEW)
    # 每个子包声明它替代的 Fedora 包名：Provides 让依赖原名的东西继续解析；
    # Obsoletes 让按原名提的安装请求落到这里——dnf 解析明确的包名时只认名字，
    # Provides 压不过同名的官方包；
    # Conflicts 保证两个版本不会同时装进设备，也是 Fedora 版本号超过本包时
    # 唯一的报错出口（那时 Obsoletes 的区间盖不住，构建直接红，需要跟着新
    # SRPM 重做）。
    for anchor, fedora in REPLACES.items():
        if text.count(anchor) != 1:
            raise ValueError("unexpected Fedora FFmpeg spec layout: " + anchor.strip())
        text = text.replace(anchor, anchor + (
            f"Provides:       {fedora} = %{{version}}-%{{release}}\n"
            f"Provides:       {fedora}%{{?_isa}} = %{{version}}-%{{release}}\n"
            f"Obsoletes:      {fedora} < %{{version}}-%{{release}}\n"
            f"Conflicts:      {fedora}%{{?_isa}}\n"), 1)
    return text


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
    if sha256(top / "SOURCES/ffmpeg-9.0.2.tar.xz") != TARBALL_SHA256:
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
    # -ba 同时产出二进制与源包：COPR 走 SRPM 重建，没有源包就发不出去。
    run("rpmbuild", "-ba", "--define", "_topdir " + str(top),
        "--define", "_smp_build_ncpus 4", "--define", "_smp_mflags -j4",
        "--define", "debug_package %{nil}", spec, env=environment)
    srpms = output / "srpms"
    srpms.mkdir()
    built = sorted((top / "SRPMS").glob("*.src.rpm"))
    if len(built) != 1:
        raise ValueError(f"expected one FFmpeg SRPM, found {[p.name for p in built]}")
    # 源包名与 Fedora 相同，靠 release 证明这是本配方打的包，而不是官方源包。
    srpm_name, srpm_release = query("rpm", "-qp", "--qf", "%{NAME} %{RELEASE}",
                                    built[0]).split()
    if (srpm_name, srpm_release) != (SOURCE_PACKAGE, RELEASE):
        raise ValueError("the FFmpeg SRPM is not this recipe's piano variant")
    shutil.copyfile(built[0], srpms / built[0].name)
    rpms = output / "rpms"
    rpms.mkdir()
    selected = {}
    for path in (top / "RPMS").rglob("*.rpm"):
        name, version, release, arch = query(
            "rpm", "-qp", "--qf", "%{NAME} %{VERSION} %{RELEASE} %{ARCH}", path).split()
        if name not in PACKAGES:
            continue
        if name in selected or (version, release, arch) != ("9.0.2", RELEASE, "aarch64"):
            raise ValueError("unexpected FFmpeg RPM identity: " + path.name)
        shutil.copyfile(path, rpms / path.name)
        selected[name] = {"file": path.name, "nevra": f"{name}-{version}-{release}.{arch}"}
    if selected.keys() != PACKAGES:
        raise ValueError("FFmpeg runtime package set is incomplete")
    # 从实际 RPM 安装载荷跑一次二进制，而不是把"编过了"当成"解码器在"。
    payload = output / "payload"
    payload.mkdir()
    run("rpm", "--root", payload, "--dbpath", "/var/lib/rpm", "--initdb")
    # Fedora 45 的 rpm 6 默认 %_pkgverify_level=all，本地编出来的包没有签名，
    # 安装会以每个包都失败告终。这里装的是刚编出的载荷，来源已经核对过摘要。
    run("rpm", "--root", payload, "--dbpath", "/var/lib/rpm",
        "--define", "_pkgverify_level none", "--nodeps", "--noscripts",
        "-i", *sorted(rpms.glob("*.rpm")))
    decoders = decoder_proof(payload)
    packages = query("rpm", "-qa", "--root", payload, "--dbpath", "/var/lib/rpm",
                     "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\n")
    (output / "installed-packages.tsv").write_text("\n".join(sorted(packages.splitlines())) + "\n")
    result = {"status": "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED", "releasever": "45",
              "arch": "aarch64", "source_package": SOURCE_PACKAGE,
              "variant_package": VARIANT_PACKAGE,
              "replaces": list(REPLACES.values()), "source_url": SOURCE_URL, "source_rpm_sha256": SOURCE_SHA256,
              "source_tarball_sha256": TARBALL_SHA256, "original_spec_sha256": SPEC_SHA256,
              "effective_spec_sha256": sha256(spec),
              "enable_decoders_sha256": sha256(enable_list),
              "bsp_upstream_version": BSP_VERSION, "bsp_local_revision": BSP_REVISION,
              "patches": {name: {"sha256": row["sha256"], "bsp_sha256": row["bsp_sha256"]}
                          for name, row in sorted(ported.items())},
              "decoders": decoders, "packages": selected,
              "rpms": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                       for p in sorted(rpms.glob("*.rpm"))},
              "srpms": {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                        for p in sorted(srpms.glob("*.src.rpm"))},
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
