#!/usr/bin/env python3
"""离线回归：release.yml 只发布绑定到 tag commit 的已验证产物，每档一个
自包含归档，unpack-release.sh 能把归档还原成安装指南要求的暂存目录。

命名规则只有一处实现：`scripts/release-assets.py`。这里合成真实的归档与
sidecar，再跑 unpack-release.sh，两条路径（手动与脚本）都要得到同一个目录。
"""

import hashlib
import importlib.machinery
import importlib.util
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"
UNPACK = ROOT / "scripts/unpack-release.sh"
UPLOAD = ROOT / "scripts/release-upload.sh"
ASSETS = ROOT / "scripts/release-assets.py"
TAG = "v44.5"
DESKTOPS = ("gnome", "kde", "niri", "hyprland")


def load_release_assets():
    loader = importlib.machinery.SourceFileLoader("release_assets", str(ASSETS))
    spec = importlib.util.spec_from_loader("release_assets", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


release_assets = load_release_assets()

# 归档里每份文件的内容。manifest 与镜像只需要可区分字节，测试关心的是
# 目录形状与清单覆盖，不是镜像语义。
ARCHIVE_FILES = {
    "PianoUEFI-product.img": b"uefi product\n",
    "boot_sunuefi.img": b"esp payload\n",
    "sunuefi_esp.img": b"esp image\n",
    "bundle-manifest.json": b'{"status": "fixture"}\n',
    "esp-manifest.json": b'{"esp_files": []}\n',
    "ffmpeg-manifest.json": b'{"ffmpeg": true}\n',
    "initramfs-manifest.json": b'{"initramfs": true}\n',
    "kernel-manifest.json": b'{"kernel_release": "7.2.9"}\n',
    "mesa-manifest.json": b'{"mesa": true}\n',
    "root-image-manifest.json": b'{"desktop": "gnome"}\n',
    "rootfs-manifest.json": b'{"rootfs": true}\n',
    "runtime-manifest.json": b'{"runtime": true}\n',
    "source-runs.json": b'{"uefi_run": "1"}\n',
    "uefi-source.json": b'{"uefi": true}\n',
    "README-INSTALL.txt": b"install guide\n",
    "flash-linux.sh": b"#!/usr/bin/env bash\n",
    "flash-linux.bat": b"@echo off\n",
    "make-device-boot.sh": b"#!/usr/bin/env bash\n",
    "root-install/root-install-manifest.json": b'{"android_slot": "_a"}\n',
    "scripts/prepare-device-boot.py": b"# helper\n",
    "scripts/piano_artifacts.py": b"# helper\n",
    "scripts/piano_sparse.py": b"# helper\n",
    "boot-tools/config.json": b'{"target": "android"}\n',
    "host-boot-tool/manifest.json": b'{"arch": "host"}\n',
}


def root_payload(desktop):
    """每档的 root 内容不同，装机只该看到自己那一档。"""
    return f"prepared root for {desktop}\n".encode() * 64


def build_archive(directory, desktop, parts=1):
    """在 directory 里写这一档的归档与 sidecar，返回 root 的字节。"""
    stage = Path(tempfile.mkdtemp(dir=directory, prefix=".stage-"))
    try:
        for name, data in ARCHIVE_FILES.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        root = root_payload(desktop)
        (stage / "root-install" / "pianoroot.f2fs.sparse.img").write_bytes(root)
        # root-install 自己的清单只覆盖它的两份文件。
        (stage / "root-install" / "SHA256SUMS").write_text(
            f"{hashlib.sha256(root).hexdigest()}  pianoroot.f2fs.sparse.img\n"
            f"{hashlib.sha256(ARCHIVE_FILES['root-install/root-install-manifest.json']).hexdigest()}"
            "  root-install-manifest.json\n")
        (stage / "flash-linux.sh").chmod(0o755)
        (stage / "make-device-boot.sh").chmod(0o755)
        release_assets.write_sums(stage)

        name = release_assets.archive_name(TAG, desktop)
        archive = directory / name
        packed = subprocess.run(
            ["tar", "-C", str(stage), "--sort=name", "--mtime=@0", "--owner=0",
             "--group=0", "--numeric-owner", "-cf", "-", "."],
            check=True, stdout=subprocess.PIPE).stdout
        archive.write_bytes(subprocess.run(["zstd", "-q", "-c"], input=packed,
                                           check=True, stdout=subprocess.PIPE).stdout)
        pieces = [archive]
        if parts > 1:
            split(archive, parts)
            pieces = sorted(directory.glob(f"{name}.part-*"))
        sidecar = directory / release_assets.sidecar_name(TAG, desktop)
        sidecar.write_text("".join(
            f"{hashlib.sha256(piece.read_bytes()).hexdigest()}  {piece.name}\n"
            for piece in pieces))
        return root
    finally:
        for item in sorted(stage.rglob("*"), reverse=True):
            item.unlink() if item.is_file() else item.rmdir()
        stage.rmdir()


def split(path, parts):
    data = path.read_bytes()
    size = max(1, len(data) // parts)
    for index in range(parts):
        chunk = data[index * size:(index + 1) * size] if index < parts - 1 \
            else data[index * size:]
        (path.parent / f"{path.name}.part-{index:02d}").write_bytes(chunk)
    path.unlink()


def write_release_sums(directory):
    rows = []
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.name != "RELEASE-SHA256SUMS":
            rows.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}")
    (directory / "RELEASE-SHA256SUMS").write_text("\n".join(rows) + "\n")


def build_release(directory, desktops=DESKTOPS, parts=None):
    parts = parts or {name: 1 for name in desktops}
    payloads = {}
    for desktop in desktops:
        payloads[desktop] = build_archive(directory, desktop, parts[desktop])
    write_release_sums(directory)
    return payloads


def run_unpack(work, *args, env=None):
    return subprocess.run(["bash", str(UNPACK), *args], cwd=work,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, env=env)


def assert_staging_layout(directory):
    """解出来的目录就是安装指南画的暂存目录。"""
    for name in ARCHIVE_FILES:
        assert (directory / name).is_file(), name
    for name in ("root-install/pianoroot.f2fs.sparse.img",
                 "root-install/root-install-manifest.json",
                 "root-install/SHA256SUMS", "boot-tools", "host-boot-tool",
                 "scripts/prepare-device-boot.py", "make-device-boot.sh"):
        assert (directory / name).exists(), name
    # device-boot/ 由 make-device-boot.sh 现场生成，archive 里不该有。
    assert not (directory / "device-boot").exists()
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=directory, check=True,
                   stdout=subprocess.PIPE)
    subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=directory / "root-install",
                   check=True, stdout=subprocess.PIPE)


def test_single_desktop_needs_no_flag():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        payloads = build_release(work, desktops=("kde",))
        # 只下这一档的分片与 sidecar，与 README 教的下载方式一致。
        result = run_unpack(work)
        assert result.returncode == 0, result.stdout
        assert "Desktop: kde" in result.stdout, result.stdout
        assert (work / "root-install" / "pianoroot.f2fs.sparse.img").read_bytes() \
            == payloads["kde"]
        assert_staging_layout(work)


def test_multi_desktop_requires_a_choice():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        build_release(work)
        result = run_unpack(work)
        assert result.returncode != 0, "a multi-desktop directory unpacked without a choice"
        assert "--desktop" in result.stdout, result.stdout


def test_each_desktop_installs_its_own_root():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        payloads = build_release(work, parts={name: 2 for name in DESKTOPS})
        for desktop in DESKTOPS:
            target = work / desktop
            target.mkdir()
            # 用户只下自己那一档：分片与 sidecar，别的档一概不碰。
            for piece in work.glob(f"piano-{TAG}-{desktop}.*"):
                (target / piece.name).write_bytes(piece.read_bytes())
            result = run_unpack(target, "--desktop", desktop)
            assert result.returncode == 0, (desktop, result.stdout)
            assert (target / "root-install" / "pianoroot.f2fs.sparse.img").read_bytes() \
                == payloads[desktop], desktop
            assert_staging_layout(target)


def test_unknown_desktop_is_rejected():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        build_release(work, desktops=("gnome", "kde"))
        result = run_unpack(work, "--desktop", "niri")
        assert result.returncode != 0, "an absent desktop was accepted"
        assert "niri" in result.stdout, result.stdout


def test_partial_download_fails_the_sidecar_check():
    """分片缺一片就必须在解开之前停下，不能解出半个归档。"""
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        build_release(work, desktops=("gnome",), parts={"gnome": 2})
        (work / f"piano-{TAG}-gnome.tar.zst.part-01").unlink()
        result = run_unpack(work)
        assert result.returncode != 0, result.stdout
        assert not (work / "root-install").exists(), "a partial archive was unpacked"


def test_download_path_fetches_only_this_desktop():
    """`--tag` 只拉 sidecar 列出的文件，已在盘上的跳过。"""
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        release = work / "release"
        release.mkdir()
        payloads = build_release(release, parts={name: 2 for name in DESKTOPS})
        target = work / "target"
        logs = work / "curl.log"
        stub = make_curl_stub(work, release)
        result = run_unpack(work, "--tag", TAG, "--desktop", "niri", "--dir", str(target),
                            env={**os.environ, "PATH": f"{stub.parent}:{os.environ['PATH']}",
                                 "STUB_LOG": str(logs), "STUB_SOURCE": str(release)})
        assert result.returncode == 0, result.stdout
        fetched = sorted(line.split("/")[-1] for line in logs.read_text().split())
        assert fetched == sorted([f"piano-{TAG}-niri.sha256", f"piano-{TAG}-niri.tar.zst.part-00",
                                  f"piano-{TAG}-niri.tar.zst.part-01"]), fetched
        assert (target / "root-install" / "pianoroot.f2fs.sparse.img").read_bytes() \
            == payloads["niri"]
        assert_staging_layout(target)


def make_curl_stub(work, source):
    bindir = work / "bin"
    bindir.mkdir()
    script = bindir / "curl"
    script.write_text(f"""#!/usr/bin/env bash
set -euo pipefail
out=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -o) out=$2; shift 2 ;;
        -*) shift ;;
        *) url=$1; shift ;;
    esac
done
[ -n "$out" ] || exit 2
echo "$url" >> "$STUB_LOG"
cp "$STUB_SOURCE/$out" "$out"
""")
    script.chmod(0o755)
    return script


def test_rerun_refuses_to_overwrite():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        build_release(work, desktops=("gnome",))
        first = run_unpack(work)
        assert first.returncode == 0, first.stdout
        again = run_unpack(work)
        assert again.returncode != 0 and "empty one" in again.stdout, again.stdout


def test_workflow_contract():
    text = WORKFLOW.read_text()
    required = [
        "contents: write",
        "id-token: write",
        "attestations: write",
        "scripts/release-desktops.py",
        "--desktop-runs",
        "scripts/release-assets.py archive-name",
        "scripts/release-assets.py sidecar-name",
        "scripts/release-assets.py bundled",
        "scripts/release-assets.py check-bundle",
        "scripts/release-assets.py write-sums",
        ". scripts/release-upload.sh",
        'gh release create "$TAG" --draft',
        'gh release edit "$TAG" --draft=false',
        "subject-checksums: build/stage/RELEASE-SHA256SUMS",
        "if: inputs.publish == 'true'",
        "if: inputs.publish != 'true'",
        "release_upload_split",
        'gh release upload "$TAG" "$STAGE/RELEASE-SHA256SUMS"',
        '--name "deploy-bundle-${desktop}"',
        '--name "root-install-${desktop}"',
        "make-device-boot.sh",
    ]
    missing = [item for item in required if item not in text]
    assert not missing, f"release.yml lost: {missing}"
    # 打 tag 不能再自动建 Release：四档发布要显式 dispatch。
    assert "push:" not in text, "release.yml still triggers on tag pushes"
    assert "tags: [" not in text, "release.yml still triggers on tag pushes"
    assert "GITHUB_REF_NAME" not in text
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
    # 每档的 root-install 绑定到自己的 bundle，而不是只绑一档。
    assert 'record.get("desktop") == desktop' in text
    # 归档解压回来必须与装配目录逐项相同。
    assert "diff -r \"$archive\" build/verify" in text
    # 旧的扁平布局不再出现。
    for gone in ("release_upload_platform", "manifests.tar.zst", "piano-installer-",
                 "PLATFORM_COMPRESSED"):
        assert gone not in text, f"release.yml still carries {gone}"


def test_release_notes_teach_both_paths():
    """说明里的命令必须与归档真实形状一致：分片合并、两条校验、生成 BOOT。"""
    text = WORKFLOW.read_text()
    notes = text[text.index("cat > build/stage/notes.md"):text.index("gh release create")]
    for token in ("sha256sum -c piano-${TAG}-${example}.sha256",
                  "cat piano-${TAG}-${example}.tar.zst.part-* | zstd -dc | tar -x",
                  "sha256sum -c SHA256SUMS",
                  "(cd root-install && sha256sum -c SHA256SUMS)",
                  "./make-device-boot.sh",
                  "unpack-release.sh --tag ${TAG} --desktop ${example}"):
        assert token in notes, f"release notes omit {token!r}"
    # 原厂 ABL 不实现 fetch，说明里不能再教它。
    assert "fastboot fetch boot_a" not in text


def main():
    test_workflow_contract()
    test_release_notes_teach_both_paths()
    test_single_desktop_needs_no_flag()
    test_multi_desktop_requires_a_choice()
    test_each_desktop_installs_its_own_root()
    test_unknown_desktop_is_rejected()
    test_partial_download_fails_the_sidecar_check()
    test_download_path_fetches_only_this_desktop()
    test_rerun_refuses_to_overwrite()
    print("OK: 每档一个自包含归档，手动与脚本两条路径解出同一个暂存目录")


if __name__ == "__main__":
    main()
