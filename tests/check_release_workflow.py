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
    "initramfs-manifest.json", "kernel-manifest.json", "mesa-manifest.json",
    "root-image-manifest.json", "rootfs-manifest.json", "runtime-manifest.json",
    "source-runs.json", "sunuefi_esp.img", "uefi-source.json",
)

DESKTOPS = ("gnome", "kde", "niri", "hyprland")


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
        sealed = f"sealed root image bytes {desktop}\n".encode() * 64
        sparse = f"bundle sparse bytes {desktop}\n".encode() * 64
        prepared = f"prepared root bytes {desktop}\n".encode() * 64
        (directory / f"pianoroot{suffix}.f2fs.img.zst").write_bytes(sealed)
        (directory / f"pianoroot{suffix}.f2fs.sparse.img.zst").write_bytes(zstd(sparse))
        (directory / f"root-install-pianoroot{suffix}.f2fs.sparse.img.zst").write_bytes(zstd(prepared))
        split(directory / f"pianoroot{suffix}.f2fs.img.zst", 2)
        split(directory / f"pianoroot{suffix}.f2fs.sparse.img.zst", 2)
        split(directory / f"root-install-pianoroot{suffix}.f2fs.sparse.img.zst", 2)
        payloads[desktop] = (sealed, sparse, prepared)

    def install(suffix, desktop):
        manifest = b'{"android_slot": "_a"}\n'
        (directory / f"root-install-manifest{suffix}.json").write_bytes(manifest)
        # root-install/SHA256SUMS 记录的是还原后的规范名，与真实 prepare-install
        # 产物一致；桌面后缀只出现在 Release 资产名上。
        write_checksums(directory / f"root-install-SHA256SUMS{suffix}",
                        [("pianoroot.f2fs.sparse.img", sha(payloads[desktop][2])),
                         ("root-install-manifest.json", sha(manifest))])

    if desktops == (None,):
        install("", None)
    else:
        for name in desktops:
            install(f"-{name}", name)

    # 平台资产只发一份，名字不带桌面后缀。
    contents = {}
    for name in BUNDLE_FILES:
        if name == "SHA256SUMS":
            continue
        contents[name] = f"bundle file {name}\n".encode()
        (directory / name).write_bytes(contents[name])
    if desktops == (None,):
        contents["pianoroot.f2fs.img.zst"] = payloads[None][0]
        contents["pianoroot.f2fs.sparse.img"] = payloads[None][1]
        write_checksums(directory / "SHA256SUMS",
                        [(name, sha(data)) for name, data in sorted(contents.items())])
    else:
        # 每档的 bundle SHA256SUMS 用还原后的规范名记录本档内容；它本身
        # 也带桌面后缀发布。
        for name in desktops:
            desktop_contents = dict(contents)
            desktop_contents["pianoroot.f2fs.img.zst"] = payloads[name][0]
            desktop_contents["pianoroot.f2fs.sparse.img"] = payloads[name][1]
            write_checksums(directory / f"SHA256SUMS-{name}",
                            [(item, sha(data)) for item, data in sorted(desktop_contents.items())])
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
        assert (work / "pianoroot.f2fs.sparse.img").read_bytes() == payloads[None][1]
        assert (work / "pianoroot.f2fs.img.zst").read_bytes() == payloads[None][0]
        assert (root / "pianoroot.f2fs.sparse.img").read_bytes() == payloads[None][2]
        assert (root / "root-install-manifest.json").is_file()
        assert (root / "SHA256SUMS").is_file()
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
        assert (work / "root-install" / "pianoroot.f2fs.sparse.img").read_bytes() == payloads["kde"][2]


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
            assert (target / "pianoroot.f2fs.sparse.img").read_bytes() == payloads[desktop][1]
            assert (root / "pianoroot.f2fs.sparse.img").read_bytes() == payloads[desktop][2]
            assert (root / "root-install-manifest.json").is_file()


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
    for name in BUNDLE_FILES:
        if name in ("SHA256SUMS", "bundle-manifest.json", "root-image-manifest.json",
                    "rootfs-manifest.json", "source-runs.json", "esp-manifest.json"):
            # 每档各写各的：既有摘要绑定，也有桌面相关的 root 记录。
            assert name not in platform, f"{name} differs per desktop and cannot be shared"
            continue
        if name in ("pianoroot.f2fs.img.zst", "pianoroot.f2fs.sparse.img"):
            continue
        assert name in platform, f"{name} is a shared platform asset but not classified as one"
    for name in release_assets.DESKTOP_INSTALL_ASSETS:
        assert name not in platform
    # esp-manifest.json 的 files 一节记录 root.tar.zst，四档字节不同。
    assert "esp-manifest.json" not in platform


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
    ]
    missing = [item for item in required if item not in text]
    assert not missing, f"release.yml lost: {missing}"
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
    # 之后的档只能 cmp，不能重传。
    reference = text.index("build/reference/platform")
    compare = text.index("cmp -s")
    upload_first = text.index('release_upload "build/in/bundle/$name"')
    assert reference < upload_first < compare, (
        "release.yml must upload the platform assets once, then compare later desktops")
    # 每档的 root-install 绑定到自己的 bundle，而不是只绑一档。
    assert 'record.get("desktop") == desktop' in text

    unpack = UNPACK.read_text()
    for name in ("pianoroot.f2fs.img.zst", "pianoroot.f2fs.sparse.img.zst",
                 "root-install-pianoroot.f2fs.sparse.img.zst", "root-install-SHA256SUMS"):
        assert name in text and name in unpack, f"release and unpack disagree on {name}"
    for name in BUNDLE_FILES:
        assert name in unpack, f"unpack-release.sh does not restore {name}"
    assert "--desktop" in unpack


def main():
    test_workflow_contract()
    test_asset_classification_covers_the_bundle()
    test_naming_rule_has_one_implementation()
    test_single_unsuffixed_release_still_works()
    test_single_suffixed_release_needs_no_flag()
    test_multi_desktop_release_requires_a_choice()
    test_multi_desktop_release_restores_the_chosen_desktop()
    test_unknown_desktop_is_rejected_with_the_available_choices()
    print("OK: release workflow 绑定 tag commit，多桌面资产按后缀还原为规范名")


if __name__ == "__main__":
    main()
