#!/usr/bin/env python3
"""离线回归：Release 每档一个归档，命名与成员表只有一处定义。

发布布局：

    piano-<tag>-<desktop>.tar.zst[.part-NN]   这一档的自包含归档
    piano-<tag>-<desktop>.sha256              这一档已上传分片的摘要
    RELEASE-SHA256SUMS                        全部资产的摘要，attestation 的输入

名字由 `scripts/release-assets.py` 算，`unpack-release.sh` 从 sidecar 名反推
归档名（去掉 `.sha256` 换 `.tar.zst`），不写第二份规则。这里比对两边，并
检查发布工作流真的按成员表装配归档。
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


def run(*args):
    return subprocess.run(["python3", str(ASSETS), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.split()


def test_names_follow_one_rule():
    for desktop in ("gnome", "kde", "niri", "hyprland"):
        archive = release_assets.archive_name("v44.5", desktop)
        assert archive == f"piano-v44.5-{desktop}.tar.zst", archive
        assert release_assets.sidecar_name("v44.5", desktop) == \
            f"piano-v44.5-{desktop}.sha256"
        # 归档名去掉 .tar.zst 就是 sidecar 的 stem：unpack-release.sh 靠这一条
        # 从 sidecar 反推归档名，不需要第二份命名规则。
        assert archive.removesuffix(".tar.zst") == \
            release_assets.sidecar_name("v44.5", desktop).removesuffix(".sha256")
    for bad in ("", "v 44", "-v44", "v44/../x"):
        try:
            release_assets.archive_name(bad, "gnome")
        except ValueError:
            continue
        raise AssertionError(f"accepted an invalid tag: {bad!r}")


def test_unpack_derives_the_archive_name_from_the_sidecar():
    text = UNPACK.read_text()
    assert 'archive=${sidecar%.sha256}.tar.zst' in text, (
        "unpack-release.sh must take the archive name from the sidecar name")
    # 归档里的清单只列真实存在的文件，脚本直接整份校验。
    assert "sha256sum -c SHA256SUMS" in text
    assert "(cd root-install && sha256sum -c SHA256SUMS)" in text


def test_bundle_members_and_exclusions_are_disjoint():
    bundled = set(release_assets.BUNDLE_MEMBERS)
    excluded = set(release_assets.EXCLUDED_BUNDLE_FILES)
    assert not bundled & excluded, sorted(bundled & excluded)
    # 三份镜像必须进归档：装机要刷其中两份，首装要校验第三份。
    for name in ("sunuefi_esp.img", "boot_sunuefi.img", "PianoUEFI-product.img"):
        assert name in bundled, name
    # 两份 root 与两个构建中间件不进归档。
    for name in ("pianoroot.f2fs.img.zst", "pianoroot.f2fs.sparse.img",
                 "initramfs.tar.zst", "diagnostic-abl-storage.tar.zst"):
        assert name in excluded, name
    # bundle 自己的 SHA256SUMS 也不进：归档里的那份按目录内容重新生成。
    assert "SHA256SUMS" in excluded


def test_archive_members_cover_the_staging_tree():
    members = set(release_assets.members())
    for name in release_assets.BUNDLE_MEMBERS:
        assert name in members, name
    for name in ("README-INSTALL.txt", "SHA256SUMS", "flash-linux.sh",
                 "flash-linux.bat", "make-device-boot.sh"):
        assert name in members, name
    for name in ("boot-tools", "host-boot-tool", "root-install", "scripts"):
        assert name in members, name
    assert release_assets.verify_name("root-install/SHA256SUMS") == "archive"
    assert release_assets.verify_name("pianoroot.f2fs.sparse.img") == "excluded"


def test_check_bundle_rejects_unknown_files():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name in release_assets.BUNDLE_MEMBERS + release_assets.EXCLUDED_BUNDLE_FILES:
            (root / name).write_text("x\n")
        assert release_assets.check_bundle(root) == ([], [])
        (root / "surprise.bin").write_text("x\n")
        missing, unknown = release_assets.check_bundle(root)
        assert not missing and unknown == ["surprise.bin"], (missing, unknown)
        (root / "surprise.bin").unlink()
        (root / "uefi-source.json").unlink()
        missing, unknown = release_assets.check_bundle(root)
        assert missing == ["uefi-source.json"] and not unknown, (missing, unknown)


def test_write_sums_lists_only_present_files():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "root-install").mkdir()
        (root / "one.img").write_bytes(b"one\n")
        (root / "root-install" / "two.json").write_text("{}\n")
        assert release_assets.write_sums(root) == 2
        rows = (root / "SHA256SUMS").read_text().splitlines()
        assert sorted(row.split("  ", 1)[1] for row in rows) == \
            ["one.img", "root-install/two.json"], rows
        # 清单不列自己，且按路径排序。
        assert not any(row.endswith("  SHA256SUMS") for row in rows)
        # 生成的清单必须能直接喂给 sha256sum -c。
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=root, check=True,
                       stdout=subprocess.PIPE)


def test_workflow_uses_the_member_table():
    text = WORKFLOW.read_text()
    assert "scripts/release-assets.py bundled" in text, (
        "release.yml must take the copied bundle files from release-assets.py")
    assert "scripts/release-assets.py check-bundle" in text, (
        "release.yml must refuse a deploy-bundle with unclassified files")
    assert "scripts/release-assets.py write-sums" in text, (
        "release.yml must regenerate the archive's SHA256SUMS from what it wrote")
    assert "scripts/release-assets.py archive-name" in text
    assert "scripts/release-assets.py sidecar-name" in text
    # 归档必须可复现：固定 mtime、owner 与排序。
    for flag in ("--sort=name", "--mtime=@0", "--owner=0", "--group=0", "--numeric-owner"):
        assert flag in text, f"release.yml archive tar lacks {flag}"
    # 旧的扁平布局不能留任何残余。`unpack-release.sh` 本身仍然存在，只是
    # 不再是一个独立资产：release notes 教用户跑它，宿主机上从仓库取。
    for gone in ("manifests.tar.zst", "piano-installer-", "unpack-release.sh \"$STAGE",
                 "PLATFORM_COMPRESSED", "release_upload_platform", "tags: ["):
        assert gone not in text, f"release.yml still carries the flat layout: {gone}"


def test_cli_surface():
    assert run("archive-name", "v44.5", "kde") == ["piano-v44.5-kde.tar.zst"]
    assert run("sidecar-name", "v44.5", "kde") == ["piano-v44.5-kde.sha256"]
    assert run("bundled") == list(release_assets.BUNDLE_MEMBERS)
    assert run("excluded") == list(release_assets.EXCLUDED_BUNDLE_FILES)
    assert run("members") == release_assets.members()
    assert run("verify-name", "root-install/SHA256SUMS") == ["archive"]
    assert run("verify-name", "pianoroot.f2fs.img.zst") == ["excluded"]


def main():
    test_names_follow_one_rule()
    test_unpack_derives_the_archive_name_from_the_sidecar()
    test_bundle_members_and_exclusions_are_disjoint()
    test_archive_members_cover_the_staging_tree()
    test_check_bundle_rejects_unknown_files()
    test_write_sums_lists_only_present_files()
    test_workflow_uses_the_member_table()
    test_cli_surface()
    print("OK: 每档一个自包含归档，命名与成员表只有一处定义")


if __name__ == "__main__":
    main()
