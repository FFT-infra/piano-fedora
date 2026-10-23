#!/usr/bin/env python3
"""离线回归：release.yml 只发布绑定到 tag commit 的已验证产物，单 Release
可以带多档桌面，并且 unpack-release.sh 能把扁平资产还原成安装器与文档要求
的目录布局。

命名规则只有一处实现：`scripts/release-assets.py`。这里的测试直接调它，
再从 unpack-release.sh 里取同样的输入算一遍，两边不一致就失败。
"""

import hashlib
import importlib.machinery
import importlib.util
import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"
UNPACK = ROOT / "scripts/unpack-release.sh"
UPLOAD = ROOT / "scripts/release-upload.sh"
ASSETS = ROOT / "scripts/release-assets.py"

BUNDLE_FILES = (
    "PianoUEFI-product.img", "SHA256SUMS", "boot_sunuefi.img", "bundle-manifest.json",
    "diagnostic-abl-storage.tar.zst", "esp-manifest.json", "ffmpeg-manifest.json",
    "initramfs-manifest.json", "initramfs.tar.zst", "kernel-manifest.json",
    "mesa-manifest.json", "root-image-manifest.json", "rootfs-manifest.json",
    "runtime-manifest.json", "source-runs.json", "sunuefi_esp.img", "uefi-source.json",
)

DESKTOPS = ("gnome", "kde", "niri", "hyprland")

# bundle 里不随 Release 分发的文件：完整镜像与封存镜像，以及两个只给
# 构建链用的中间产物。它们和 root-install 里的 root 内容相同或无人读取，
# 也不直接刷。bundle 自己的 SHA256SUMS 仍然列着它们，解包脚本要能在缺这
# 几行的情况下完成校验，并把清单改写成真有的子集。
UNPUBLISHED_BUNDLE_FILES = (
    "pianoroot.f2fs.img.zst", "pianoroot.f2fs.sparse.img",
    "initramfs.tar.zst", "diagnostic-abl-storage.tar.zst",
)

# 每档打成一个 manifests-<档位>.tar.zst 的成员，与
# scripts/release-assets.py 的 MANIFEST_MEMBERS 一致。
MANIFEST_MEMBERS = (
    "bundle-manifest.json", "esp-manifest.json", "root-image-manifest.json",
    "rootfs-manifest.json", "source-runs.json", "SHA256SUMS",
)

# 归档里还带这一档 root-install 的两份小文件，对应 ROOT_INSTALL_MEMBERS。
ARCHIVE_MEMBERS = MANIFEST_MEMBERS + ("root-install/root-install-manifest.json",
                                      "root-install/SHA256SUMS")


def load_release_assets():
    loader = importlib.machinery.SourceFileLoader("release_assets", str(ASSETS))
    spec = importlib.util.spec_from_loader("release_assets", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


release_assets = load_release_assets()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def zstd(data):
    return subprocess.run(["zstd", "-q", "-c"], input=data, check=True,
                          stdout=subprocess.PIPE).stdout


def write_checksums(path, rows):
    path.write_text("".join(f"{digest}  {name}\n" for name, digest in rows))


def split(path, parts):
    data = path.read_bytes()
    size = max(1, len(data) // parts)
    for index in range(parts):
        chunk = data[index * size:(index + 1) * size] if index < parts - 1 else data[index * size:]
        (path.parent / f"{path.name}.part-{index:02d}").write_bytes(chunk)
    path.unlink()


def published(name, desktop):
    """unpack-release.sh 的 published_name()，用它来对照 Python 边。"""
    stem, dot, rest = name.partition(".")
    return f"{stem}-{desktop}.{rest}" if dot else f"{name}-{desktop}"


def build_assets(directory, desktops=(None,)):
    """写一套 Release 资产。desktops=None 表示旧版单档无后缀。"""
    payloads = {}
    if desktops == (None,):
        layouts = [("", None)]
    else:
        layouts = [(f"-{name}", name) for name in desktops]
    for suffix, desktop in layouts:
        prepared = f"prepared root bytes {desktop}\n".encode() * 64
        (directory / f"root-install-pianoroot{suffix}.f2fs.sparse.img.zst").write_bytes(zstd(prepared))
        split(directory / f"root-install-pianoroot{suffix}.f2fs.sparse.img.zst", 2)
        payloads[desktop] = prepared

    def install(suffix, desktop):
        manifest = b'{"android_slot": "_a"}\n'
        (directory / f"root-install-manifest{suffix}.json").write_bytes(manifest)
        # root-install/SHA256SUMS 记录的是还原后的规范名，与真实 prepare-install
        # 产物一致；桌面后缀只出现在 Release 资产名上。它和清单一起打进
        # manifests tar，所以只留在归档成员里，不落成独立资产。
        write_checksums(directory / f"root-install-SHA256SUMS{suffix}",
                        [("pianoroot.f2fs.sparse.img", sha(payloads[desktop])),
                         ("root-install-manifest.json", sha(manifest))])

    if desktops == (None,):
        install("", None)
    else:
        for name in desktops:
            install(f"-{name}", name)

    # bundle 的 SHA256SUMS 列的是整个 deploy-bundle，包括不随 Release 发布的
    # 那几份；目录里只落真正会发布成单个资产的文件，还原解包时"清单里有、
    # 目录里没有"的真实处境。三份平台镜像按 Release 的样子压缩发布。
    contents = {}
    for name in BUNDLE_FILES:
        contents[name] = f"bundle file {name}\n".encode()
        if name in MANIFEST_MEMBERS or name in UNPUBLISHED_BUNDLE_FILES:
            continue
        if name in release_assets.PLATFORM_COMPRESSED:
            (directory / f"{name}.zst").write_bytes(zstd(contents[name]))
            continue
        (directory / name).write_bytes(contents[name])
    contents["pianoroot.f2fs.img.zst"] = b"sealed root image\n"
    contents["pianoroot.f2fs.sparse.img"] = b"bundle sparse root\n"

    # 每档的 manifest 与 root-install 小文件打成一个 tar，与 release.yml 的
    # 做法一致：固定 mtime 与 owner，成员按名字排序。bundle 的 SHA256SUMS 也
    # 在里面，它用还原后的规范名记录本档内容；root-install 的清单与摘要带
    # `root-install/` 前缀，解包后直接落到装机脚本要求的位置。
    if desktops == (None,):
        names = [None]
    else:
        names = list(desktops)
    for desktop in names:
        suffix = "" if desktop is None else f"-{desktop}"
        stage = directory / f".stage{suffix}"
        stage.mkdir()
        (stage / "root-install").mkdir()
        for name in MANIFEST_MEMBERS:
            if name == "SHA256SUMS":
                # 清单不列自己，与真实 deploy-bundle 一致。
                write_checksums(stage / name,
                                [(item, sha(data)) for item, data in sorted(contents.items())
                                 if item != "SHA256SUMS"])
            else:
                # 与 bundle 里那份逐字节相同，SHA256SUMS 才校验得上。
                (stage / name).write_bytes(contents[name])
        (stage / "root-install" / "root-install-manifest.json").write_bytes(
            (directory / f"root-install-manifest{suffix}.json").read_bytes())
        (stage / "root-install" / "SHA256SUMS").write_bytes(
            (directory / f"root-install-SHA256SUMS{suffix}").read_bytes())
        # 归档成员是本档的最终资产；它们不再单独发布，从目录里删掉。
        (directory / f"root-install-manifest{suffix}.json").unlink()
        (directory / f"root-install-SHA256SUMS{suffix}").unlink()
        archive = directory / f"manifests{suffix}.tar"
        subprocess.run(["tar", "-C", str(stage), "--sort=name", "--mtime=@0",
                        "--owner=0", "--group=0", "--numeric-owner",
                        "-cf", str(archive), *ARCHIVE_MEMBERS], check=True)
        (directory / f"manifests{suffix}.tar.zst").write_bytes(zstd(archive.read_bytes()))
        archive.unlink()
        for item in sorted(stage.rglob("*"), reverse=True):
            item.unlink() if item.is_file() else item.rmdir()
        stage.rmdir()
    (directory / "unpack-release.sh").write_bytes(UNPACK.read_bytes())
    assets = sorted(p.name for p in directory.iterdir() if p.is_file())
    write_checksums(directory / "RELEASE-SHA256SUMS",
                    [(name, sha((directory / name).read_bytes())) for name in assets])
    return payloads


def run_unpack(work, *args):
    return subprocess.run(["bash", "unpack-release.sh", *args], cwd=work,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def test_single_unsuffixed_release_still_works():
    """旧版 Release（无 --desktop、无后缀）保持原来的行为。"""
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        payloads = build_assets(work, desktops=(None,))
        result = run_unpack(work)
        assert result.returncode == 0, result.stdout
        root = work / "root-install"
        assert (root / "pianoroot.f2fs.sparse.img").read_bytes() == payloads[None]
        # 完整镜像与封存镜像都不进 Release，装机只用 root-install 那一份。
        assert not (work / "pianoroot.f2fs.sparse.img").exists()
        assert not (work / "pianoroot.f2fs.img.zst").exists()
        assert (root / "root-install-manifest.json").is_file()
        assert (root / "SHA256SUMS").is_file()
        # 解包后暂存目录的清单只列真有的文件，手动刷机的
        # `sha256sum -c SHA256SUMS` 才能直接跑通。
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=work, check=True,
                       stdout=subprocess.PIPE)
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=root, check=True,
                       stdout=subprocess.PIPE)
        # 重新解包必须拒绝覆盖已经验证过的目录。
        again = run_unpack(work)
        assert again.returncode != 0 and "already exists" in again.stdout, again.stdout


def test_single_suffixed_release_needs_no_flag():
    """一档带后缀的 Release：省略 --desktop 也能自动识别。"""
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        payloads = build_assets(work, desktops=("kde",))
        result = run_unpack(work)
        assert result.returncode == 0, result.stdout
        assert "Desktop: kde" in result.stdout, result.stdout
        assert (work / "root-install" / "pianoroot.f2fs.sparse.img").read_bytes() == payloads["kde"]


def test_multi_desktop_release_requires_a_choice():
    """多档 Release 不给 --desktop 必须失败，并列出可选项。"""
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        build_assets(work, desktops=DESKTOPS)
        result = run_unpack(work)
        assert result.returncode != 0, "a multi-desktop release unpacked without a choice"
        for name in DESKTOPS:
            assert name in result.stdout, result.stdout
        assert "--desktop" in result.stdout, result.stdout


def test_multi_desktop_release_restores_the_chosen_desktop():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        payloads = build_assets(work, desktops=DESKTOPS)
        for desktop in DESKTOPS:
            target = work / desktop
            target.mkdir()
            for asset in work.iterdir():
                if asset.is_file():
                    (target / asset.name).write_bytes(asset.read_bytes())
            result = run_unpack(target, "--desktop", desktop)
            assert result.returncode == 0, (desktop, result.stdout)
            root = target / "root-install"
            assert (root / "pianoroot.f2fs.sparse.img").read_bytes() == payloads[desktop]
            assert (root / "root-install-manifest.json").is_file()
            # 6 个 manifest 从 tar 里解出来，落在暂存目录。
            for name in MANIFEST_MEMBERS:
                if name == "SHA256SUMS":
                    continue
                assert (target / name).is_file(), (desktop, name)


def test_partial_download_installs_the_downloaded_desktop():
    """只下平台资产加一档 root-install 也要能装：README 就是这么教用户下的。

    另外三档的 root 各 1–3 GiB，装机时不该被要求下完。所以第 1 步只能校验
    目录里真有的资产，第 4 步的 bundle 清单也只能校验它的子集。
    """
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        full = work / "full"
        full.mkdir()
        payloads = build_assets(full, desktops=DESKTOPS)
        # 模拟用户：下平台资产 + niri 那一档，跳过其他三档的 root、manifest
        # 包与 root-install 清单。后缀既可能接 `.`（manifests-gnome.tar.zst），
        # 也可能接在末尾（root-install-SHA256SUMS-gnome）。
        partial = work / "partial"
        partial.mkdir()
        skipped = tuple(name for name in DESKTOPS if name != "niri")
        for asset in full.iterdir():
            if not asset.is_file():
                continue
            if any(re.search(rf"-{name}(\.|$)", asset.name) for name in skipped):
                continue
            (partial / asset.name).write_bytes(asset.read_bytes())
        result = run_unpack(partial, "--desktop", "niri")
        assert result.returncode == 0, result.stdout
        assert (partial / "root-install" / "pianoroot.f2fs.sparse.img").read_bytes() == payloads["niri"]
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=partial, check=True,
                       stdout=subprocess.PIPE)


def test_legacy_release_ships_the_platform_images_raw():
    """旧 Release 直接发解压后的平台镜像，新脚本不能强行找 .zst。"""
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        payloads = build_assets(work, desktops=("gnome",))
        # 还原成 v44.3 那种形状：平台镜像是原名文件，没有 .zst。
        for name in release_assets.PLATFORM_COMPRESSED:
            archive = work / f"{name}.zst"
            (work / name).write_bytes(subprocess.run(
                ["zstd", "-dc", str(archive)], check=True, stdout=subprocess.PIPE).stdout)
            archive.unlink()
        assets = sorted(p.name for p in work.iterdir()
                        if p.is_file() and p.name != "RELEASE-SHA256SUMS")
        write_checksums(work / "RELEASE-SHA256SUMS",
                        [(name, sha((work / name).read_bytes())) for name in assets])
        result = run_unpack(work, "--desktop", "gnome")
        assert result.returncode == 0, result.stdout
        for name in release_assets.PLATFORM_COMPRESSED:
            assert (work / name).is_file(), name
        assert (work / "root-install" / "pianoroot.f2fs.sparse.img").read_bytes() == payloads["gnome"]


def test_unknown_desktop_is_rejected_with_the_available_choices():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        build_assets(work, desktops=("gnome", "kde"))
        result = run_unpack(work, "--desktop", "niri")
        assert result.returncode != 0, "an absent desktop was accepted"
        assert "gnome" in result.stdout and "kde" in result.stdout, result.stdout


def test_naming_rule_has_one_implementation():
    """Python 与 unpack-release.sh 的 published_name 必须给出同样的名字。"""
    unpack = UNPACK.read_text()
    assert "published_name()" in unpack, "unpack-release.sh lost its naming helper"
    # 从脚本里把函数体单独抠出来跑，避免只比对字符串长相。
    function = subprocess.run(
        ["sed", "-n", "/^published_name() {/,/^}/p", str(UNPACK)],
        check=True, stdout=subprocess.PIPE, text=True).stdout
    assert function.count("\n") > 3, "could not extract published_name() from unpack-release.sh"
    probe = function + '\n[ "$#" -eq 2 ] || exit 0\npublished_name "$1" "$2"\n'
    script = Path(tempfile.mkdtemp()) / "probe.sh"
    try:
        script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + probe)
        script.chmod(0o755)
        for name in ("pianoroot.f2fs.img.zst", "root-install-manifest.json",
                     "root-install-SHA256SUMS", "root-install-pianoroot.f2fs.sparse.img.zst",
                     "pianoroot.f2fs.sparse.img.zst"):
            for desktop in DESKTOPS:
                got = subprocess.run(["bash", str(script), name, desktop], check=True,
                                     stdout=subprocess.PIPE, text=True).stdout.strip()
                want = release_assets.published_name(name, desktop)
                assert got == want, (name, desktop, got, want)
    finally:
        script.unlink(missing_ok=True)
        script.parent.rmdir()


def test_asset_classification_covers_the_bundle():
    """bundle 里已知的每份资产都必须有明确归类，平台集合里不能混进桌面资产。"""
    platform = set(release_assets.PLATFORM_ASSETS)
    members = set(release_assets.MANIFEST_MEMBERS)
    for name in BUNDLE_FILES:
        if name in members:
            # 每档各写各的：既有摘要绑定，也有桌面相关的 root 记录。它们
            # 不逐个发，打成一个 tar。
            assert name not in platform, f"{name} differs per desktop and cannot be shared"
            assert release_assets.classify(name) == "manifest-archive", name
            continue
        if name in ("pianoroot.f2fs.img.zst", "pianoroot.f2fs.sparse.img"):
            continue
        if name in ("initramfs.tar.zst", "diagnostic-abl-storage.tar.zst"):
            # 不再随 Release 发布：前者是 build-bundle 解包用的中间产物，
            # 内容与 boot_sunuefi.img 里那份相同；后者是 ABL 调试件。
            assert name not in platform, f"{name} is no longer published"
            continue
        assert name in platform, f"{name} is a shared platform asset but not classified as one"
    for name in release_assets.DESKTOP_INSTALL_ASSETS:
        assert name not in platform
    # esp-manifest.json 的 files 一节记录 root.tar.zst，四档字节不同。
    assert "esp-manifest.json" not in platform


def test_manifest_archive_members_are_one_definition():
    """release.yml 的 tar 命令与 unpack-release.sh 必须用同一份成员表。"""
    members = tuple(release_assets.MANIFEST_MEMBERS)
    assert members == MANIFEST_MEMBERS, "tests/check_release_workflow.py drifted"
    workflow = WORKFLOW.read_text()
    assert "release-assets.py manifest-members" in workflow, (
        "release.yml must take the archive members from scripts/release-assets.py")
    for flag in ("--sort=name", "--mtime=@0", "--owner=0", "--group=0", "--numeric-owner"):
        assert flag in workflow, f"release.yml manifest tar lacks {flag}"
    unpack = UNPACK.read_text()
    for name in ("bundle-manifest.json", "root-image-manifest.json", "source-runs.json",
                 "SHA256SUMS"):
        assert name in unpack, f"unpack-release.sh never checks for {name} after unpacking"
    assert "manifests.tar.zst" in unpack


def test_workflow_contract():
    text = WORKFLOW.read_text()
    required = [
        'tags: ["v*"]',
        "contents: write",
        "id-token: write",
        "attestations: write",
        "scripts/release-desktops.py",
        "--desktop-runs",
        "scripts/release-assets.py platform",
        "scripts/release-assets.py classify",
        "scripts/release-assets.py name",
        ". scripts/release-upload.sh",
        'gh release create "$TAG" --draft',
        'gh release edit "$TAG" --draft=false',
        "subject-checksums: build/stage/RELEASE-SHA256SUMS",
        "if: inputs.publish == 'true'",
        "if: inputs.publish != 'true'",
    ]
    missing = [item for item in required if item not in text]
    assert not missing, f"release.yml lost: {missing}"
    # 转公开必须显式传参，默认停在 draft。
    assert 'default: "false"' in text, "publish/prerelease 输入必须有 false 默认值"
    publish_block = text[text.index("name: Publish the release"):]
    publish_block = publish_block[:publish_block.index("name: Leave the release as a draft")]
    assert "if: inputs.publish == 'true'" in publish_block, (
        "Publish the release 必须在 publish 输入为 true 时才跑")
    assert '--draft=false' in publish_block
    # GitHub 的单文件上限是 2 GiB；切片阈值必须严格低于它。
    upload = UPLOAD.read_text()
    assert "release_max_asset=2147000000" in upload
    assert 2147000000 < 2 * 1024**3
    assert "split -b 1900M" in upload
    # 上传 helper 只在一处实现，workflow 里不再重复定义。
    for duplicated in ("record() {", "upload() {"):
        assert duplicated not in text, f"release.yml redefines {duplicated}"
    # 发布不能重新编译任何东西。
    for forbidden in ("build-bundle.yml --ref", "prepare-install.yml --ref", "make ", "podman "):
        assert forbidden not in text, f"release.yml rebuilds instead of releasing: {forbidden}"
    # draft 必须在任何上传之前建好，否则 gh release upload 没有目标。
    assert text.index("Create the draft release") < text.index("release_upload")
    # 平台资产先逐字节比对，再上传一次：参考副本第一次写入后才允许上传，
    # 之后的档只能 cmp，不能重传。上传走压缩 helper，比对仍用 bundle 原件。
    reference = text.index("build/reference/platform")
    compare = text.index("cmp -s")
    upload_first = text.index('release_upload_platform "build/in/bundle/$name"')
    assert reference < upload_first < compare, (
        "release.yml must upload the platform assets once, then compare later desktops")
    # 平台镜像压缩发布；解包脚本负责还原。
    assert "release_upload_platform" in text
    # 解包脚本的压缩名单是 PLATFORM_COMPRESSED 的第二份实现，必须逐项一致；
    # 名单里的每个名字都按 `<name>.zst` 还原。
    block = UNPACK.read_text()
    block = block[block.index("compressed_platform=(") + len("compressed_platform=("):]
    block = block[:block.index(")")]
    assert block.split() == list(release_assets.PLATFORM_COMPRESSED), (
        "unpack-release.sh compresses a different set than scripts/release-assets.py")
    assert '"$name.zst"' in UNPACK.read_text(), (
        "unpack-release.sh must decompress each platform image from <name>.zst")
    # 每档的 root-install 绑定到自己的 bundle，而不是只绑一档。
    assert 'record.get("desktop") == desktop' in text

    # 每档的循环把压缩副本写到同一个 $STAGE 路径。zstd 没有 -f 时第二档会
    # 反问要不要覆盖，非交互环境直接以 1 退出——第一次四档发布就停在这里。
    commands, buffer = [], ""
    for line in text.splitlines():
        stripped = line.rstrip()
        buffer += stripped.rstrip("\\").strip() + " "
        if not stripped.endswith("\\"):
            commands.append(buffer)
            buffer = ""
    staged = [c for c in commands if "zstd" in c and '-o "$STAGE/' in c]
    assert staged, "release.yml no longer compresses into $STAGE"
    for command in staged:
        assert re.search(r"\bzstd\b[^|]*\s-f\b", command), (
            "a per-desktop stage compression runs without -f; the second "
            "desktop would stop at zstd's overwrite prompt: " + command)

    unpack = UNPACK.read_text()
    assert "root-install-pianoroot.f2fs.sparse.img.zst" in text
    assert "root-install-pianoroot.f2fs.sparse.img.zst" in unpack
    # root-install 的清单与摘要进了归档，不再单独发布；两边都按归档成员核对。
    assert "root-install-manifest.json" in text and "root-install-manifest.json" in unpack
    assert "root-install/SHA256SUMS" in text and "root-install/SHA256SUMS" in unpack
    assert "archive-members" in text, (
        "release.yml must take the archive members, root-install files included, "
        "from scripts/release-assets.py")
    # 完整镜像与封存镜像只在"不发"的那条分支里出现，两边都要写明。
    for name in ("pianoroot.f2fs.img.zst", "pianoroot.f2fs.sparse.img"):
        assert name in text, f"release.yml does not mention {name}"
    assert "pianoroot.f2fs.img.zst|pianoroot.f2fs.sparse.img" in text, (
        "release.yml must skip both unshipped root images by name")
    # 每档 6 个 manifest 走 tar；解包脚本要能解出来并核对关键成员。
    assert "manifests.tar.zst" in text and "manifest-members" in text
    assert "manifests.tar.zst" in unpack
    for name in MANIFEST_MEMBERS:
        assert name in unpack or name in " ".join(MANIFEST_MEMBERS), name
    assert "--desktop" in unpack


def main():
    test_workflow_contract()
    test_asset_classification_covers_the_bundle()
    test_naming_rule_has_one_implementation()
    test_single_unsuffixed_release_still_works()
    test_single_suffixed_release_needs_no_flag()
    test_multi_desktop_release_requires_a_choice()
    test_multi_desktop_release_restores_the_chosen_desktop()
    test_partial_download_installs_the_downloaded_desktop()
    test_legacy_release_ships_the_platform_images_raw()
    test_unknown_desktop_is_rejected_with_the_available_choices()
    print("OK: release workflow 绑定 tag commit，多桌面资产按后缀还原为规范名")


if __name__ == "__main__":
    main()
