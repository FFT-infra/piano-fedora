#!/usr/bin/env python3
"""离线回归：Release 资产的两条分类规则保持一致。

四档镜像共用同一个 ESP、内核与 initramfs，差的是 root 文件系统。发布时：

- 平台资产只上传一份，名字不带后缀，各档必须逐字节相同；
- 桌面资产每档各一份，在第一处 `.` 前插入 `-<desktop>`。

规则只在 `scripts/release-assets.py` 与 `scripts/unpack-release.sh` 各写一次。
这里逐项比对两条实现，并检查发布工作流真的按分类走。
"""

import importlib.machinery
import importlib.util
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "scripts/release-assets.py"
UNPACK = ROOT / "scripts/unpack-release.sh"
WORKFLOW = ROOT / ".github/workflows/release.yml"


def load():
    loader = importlib.machinery.SourceFileLoader("release_assets", str(ASSETS))
    spec = importlib.util.spec_from_loader("release_assets", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


release_assets = load()


def shell_published_name(name, desktop):
    """直接跑 unpack-release.sh 里的 published_name()。"""
    function = subprocess.run(
        ["sed", "-n", "/^published_name() {/,/^}/p", str(UNPACK)],
        check=True, stdout=subprocess.PIPE, text=True).stdout
    assert function.strip(), "unpack-release.sh has no published_name()"
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "probe.sh"
        script.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + function
                          + f'\npublished_name "{name}" "{desktop}"\n')
        return subprocess.run(["bash", str(script)], check=True,
                              stdout=subprocess.PIPE, text=True).stdout


def test_published_name_inserts_before_the_first_dot():
    cases = {
        ("pianoroot.f2fs.img.zst", "gnome"): "pianoroot-gnome.f2fs.img.zst",
        ("pianoroot.f2fs.sparse.img.zst", "kde"): "pianoroot-kde.f2fs.sparse.img.zst",
        ("root-install-pianoroot.f2fs.sparse.img.zst", "niri"):
            "root-install-pianoroot-niri.f2fs.sparse.img.zst",
        ("root-install-manifest.json", "hyprland"):
            "root-install-manifest-hyprland.json",
        # 没有扩展名时整段当 stem，后缀接在末尾。
        ("root-install-SHA256SUMS", "gnome"): "root-install-SHA256SUMS-gnome",
    }
    for (name, desktop), expected in cases.items():
        assert release_assets.published_name(name, desktop) == expected, (name, desktop)
        assert shell_published_name(name, desktop).strip() == expected, (name, desktop)
    try:
        release_assets.published_name("root-install-SHA256SUMS", "")
    except ValueError as error:
        assert "desktop is required" in str(error), error
    else:
        raise AssertionError("an empty desktop name produced a published asset name")


def test_the_two_implementations_agree_on_every_bundle_file():
    """覆盖 bundle 真实会出现的每个名字，桌面档取四档。"""
    names = sorted(set(release_assets.PLATFORM_ASSETS)
                   | set(release_assets.DESKTOP_INSTALL_ASSETS)
                   | {"pianoroot.f2fs.img.zst", "pianoroot.f2fs.sparse.img",
                      "bundle-manifest.json", "root-image-manifest.json",
                      "rootfs-manifest.json", "source-runs.json", "esp-manifest.json",
                      "SHA256SUMS"})
    for name in names:
        for desktop in ("gnome", "kde", "niri", "hyprland"):
            assert release_assets.published_name(name, desktop) == \
                shell_published_name(name, desktop).strip(), (name, desktop)


def test_platform_assets_are_identical_across_desktops():
    """平台集合里的每份资产都必须是"桌面无关"的：不能随桌面变化的 manifest。

    `esp-manifest.json` 的 `files` 一节记录输出目录里的 `root.tar.zst`，
    四档大小与摘要不同，所以它不能进平台集合。
    """
    platform = set(release_assets.PLATFORM_ASSETS)
    for name in ("bundle-manifest.json", "root-image-manifest.json", "rootfs-manifest.json",
                 "source-runs.json", "SHA256SUMS", "esp-manifest.json",
                 "pianoroot.f2fs.img.zst", "pianoroot.f2fs.sparse.img"):
        assert name not in platform, f"{name} differs per desktop but is published once"
    # 桌面维度真正差的资产都在桌面集合里。
    for name in ("root-install-manifest.json", "root-install-SHA256SUMS",
                 "root-install-pianoroot.f2fs.sparse.img.zst"):
        assert name in release_assets.DESKTOP_INSTALL_ASSETS, name
        assert name not in platform, name


def test_classify_defaults_to_desktop():
    """未知文件名按桌面资产处理：新资产不会被悄悄当成平台共用件。"""
    assert release_assets.classify("PianoUEFI-product.img") == "platform"
    assert release_assets.classify("kernel-manifest.json") == "platform"
    assert release_assets.classify("brand-new-file.bin") == "desktop"
    assert release_assets.classify("root-install-manifest.json") == "desktop"


def test_workflow_uses_the_classification():
    text = WORKFLOW.read_text()
    # 平台资产由 release-assets.py platform 列出，不手写第二份名单。
    assert "scripts/release-assets.py platform" in text
    assert "scripts/release-assets.py classify" in text
    assert "scripts/release-assets.py name" in text
    # 平台资产逐字节比对。
    assert "cmp -s" in text
    # 桌面资产的发布名不能是手写的字面量。
    for literal in ('"pianoroot-gnome.f2fs.img.zst"', '"root-install-manifest-gnome.json"'):
        assert literal not in text, f"release.yml hard-codes a desktop asset name: {literal}"


def test_cli_surface():
    for args, expected in (
            (["classify", "pianoroot.f2fs.sparse.img"], "desktop"),
            (["classify", "sunuefi_esp.img"], "platform"),
            (["name", "root-install-manifest.json", "kde"], "root-install-manifest-kde.json")):
        result = subprocess.run(["python3", str(ASSETS), *args], check=True,
                                stdout=subprocess.PIPE, text=True)
        assert result.stdout.strip() == expected, (args, result.stdout)
    result = subprocess.run(["python3", str(ASSETS), "platform"], check=True,
                            stdout=subprocess.PIPE, text=True)
    assert result.stdout.split() == list(release_assets.PLATFORM_ASSETS)


def main():
    test_published_name_inserts_before_the_first_dot()
    test_the_two_implementations_agree_on_every_bundle_file()
    test_platform_assets_are_identical_across_desktops()
    test_classify_defaults_to_desktop()
    test_workflow_uses_the_classification()
    test_cli_surface()
    print("OK: 平台资产只发一份、桌面资产按 -<desktop> 命名，两条实现逐项一致")


if __name__ == "__main__":
    main()
