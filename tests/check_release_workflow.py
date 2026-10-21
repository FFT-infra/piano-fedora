#!/usr/bin/env python3
"""离线回归：release.yml 只发布绑定到 tag commit 的已验证产物，并核对
unpack-release.sh 能把扁平资产还原成安装器和文档要求的目录布局。"""

import hashlib
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"
UNPACK = ROOT / "scripts/unpack-release.sh"

BUNDLE_FILES = (
    "PianoUEFI-product.img", "SHA256SUMS", "boot_sunuefi.img", "bundle-manifest.json",
    "diagnostic-abl-storage.tar.zst", "esp-manifest.json", "ffmpeg-manifest.json",
    "initramfs-manifest.json", "kernel-manifest.json", "mesa-manifest.json",
    "root-image-manifest.json", "rootfs-manifest.json", "runtime-manifest.json",
    "source-runs.json", "sunuefi_esp.img", "uefi-source.json",
)


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


def build_assets(directory):
    sealed = b"sealed root image bytes\n" * 64
    sparse = b"bundle sparse bytes\n" * 64
    prepared = b"prepared root bytes\n" * 64
    (directory / "pianoroot.f2fs.img.zst").write_bytes(sealed)
    (directory / "pianoroot.f2fs.sparse.img.zst").write_bytes(zstd(sparse))
    (directory / "root-install-pianoroot.f2fs.sparse.img.zst").write_bytes(zstd(prepared))
    # 两份大镜像都走分片路径，证明 cat *.part-* 的顺序与完整性。
    split(directory / "pianoroot.f2fs.img.zst", 2)
    split(directory / "pianoroot.f2fs.sparse.img.zst", 2)
    split(directory / "root-install-pianoroot.f2fs.sparse.img.zst", 2)

    contents = {}
    for name in BUNDLE_FILES:
        if name == "SHA256SUMS":
            continue
        contents[name] = f"bundle file {name}\n".encode()
        (directory / name).write_bytes(contents[name])
    contents["pianoroot.f2fs.img.zst"] = sealed
    contents["pianoroot.f2fs.sparse.img"] = sparse
    write_checksums(directory / "SHA256SUMS",
                    [(name, sha(data)) for name, data in sorted(contents.items())])

    manifest = b'{"android_slot": "_a"}\n'
    (directory / "root-install-manifest.json").write_bytes(manifest)
    write_checksums(directory / "root-install-SHA256SUMS",
                    [("pianoroot.f2fs.sparse.img", sha(prepared)),
                     ("root-install-manifest.json", sha(manifest))])

    (directory / "unpack-release.sh").write_bytes(UNPACK.read_bytes())
    assets = sorted(p.name for p in directory.iterdir() if p.is_file())
    write_checksums(directory / "RELEASE-SHA256SUMS",
                    [(name, sha((directory / name).read_bytes())) for name in assets])


def test_workflow_contract():
    text = WORKFLOW.read_text()
    required = [
        'tags: ["v*"]',
        "contents: write",
        "id-token: write",
        "attestations: write",
        "--workflow build-bundle.yml",
        "--workflow prepare-install.yml",
        '--commit "$sha"',
        "verify-bundle.py --bundle build/in/bundle",
        "source_bundle_manifest_sha256",
        'record["android_slot"] == "_a"',
        'gh release create "$TAG" --draft',
        "subject-checksums: build/stage/RELEASE-SHA256SUMS",
        'gh release edit "$TAG" --draft=false',
        "max_asset=2147000000",
        "split -b 1900M",
        "run-id: ${{ steps.src.outputs.bundle_run }}",
        "run-id: ${{ steps.src.outputs.prepare_run }}",
    ]
    missing = [item for item in required if item not in text]
    assert not missing, f"release.yml lost: {missing}"
    # GitHub 的单文件上限是 2 GiB；切片阈值必须严格低于它。
    assert 2147000000 < 2 * 1024**3
    # 先上传 bundle 再下载 root-install，标准 runner 才能放下两份大镜像。
    assert text.index("Upload the deploy bundle") < text.index("Download the prepared root and boot tools")
    # 发布不能重新编译任何东西。
    for forbidden in ("build-bundle.yml --ref", "prepare-install.yml --ref", "make ", "podman "):
        assert forbidden not in text, f"release.yml rebuilds instead of releasing: {forbidden}"

    unpack = UNPACK.read_text()
    for name in ("pianoroot.f2fs.img.zst", "pianoroot.f2fs.sparse.img.zst",
                 "root-install-pianoroot.f2fs.sparse.img.zst", "root-install-SHA256SUMS"):
        assert name in text and name in unpack, f"release and unpack disagree on {name}"
    for name in BUNDLE_FILES:
        assert name in unpack, f"unpack-release.sh does not restore {name}"


def test_unpack_restores_the_documented_layout():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        build_assets(work)
        subprocess.run(["bash", "unpack-release.sh"], cwd=work, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        root = work / "root-install"
        assert (work / "pianoroot.f2fs.sparse.img").read_bytes() == b"bundle sparse bytes\n" * 64
        assert (work / "pianoroot.f2fs.img.zst").read_bytes() == b"sealed root image bytes\n" * 64
        assert (root / "pianoroot.f2fs.sparse.img").read_bytes() == b"prepared root bytes\n" * 64
        assert (root / "root-install-manifest.json").is_file()
        assert (root / "SHA256SUMS").is_file()
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=work, check=True,
                       stdout=subprocess.PIPE)
        subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=root, check=True,
                       stdout=subprocess.PIPE)
        # 重新解包必须拒绝覆盖已经验证过的目录。
        result = subprocess.run(["bash", "unpack-release.sh"], cwd=work,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        assert result.returncode != 0 and "already exists" in result.stdout, result.stdout


def main():
    test_workflow_contract()
    test_unpack_restores_the_documented_layout()
    print("OK: release workflow 绑定 tag commit，扁平资产可还原为暂存目录与 root-install/")


if __name__ == "__main__":
    main()
