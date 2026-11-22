#!/usr/bin/env python3
"""验证交付集合的文件、内核/运行时/引导身份与 F2FS 完整检查记录。"""

import argparse
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

from piano_artifacts import sha256, verify_uefi_source
import piano_sparse

PROJECT = Path(__file__).resolve().parents[1]

REQUIRED = {"sunuefi_esp.img", "PianoUEFI-product.img", "pianoroot.f2fs.img.zst",
            "pianoroot.f2fs.sparse.img", "initramfs.tar.zst",
            "root-image-manifest.json", "kernel-manifest.json", "initramfs-manifest.json",
            "esp-manifest.json", "runtime-manifest.json", "mesa-manifest.json",
            "ffmpeg-manifest.json", "rootfs-manifest.json", "source-runs.json", "uefi-source.json",
            "sunuefi-module.zip", "module-manifest.json"}
ASSETS = {"sunuefi_esp.img": "esp.img",
          "PianoUEFI-product.img": "PianoUEFI-product.img"}


def verify_module(root):
    """模块 ZIP 与 bundle 里的产品镜像必须同源。

    ESP 刷写装不进 Android 侧模块，已装设备换固件只能装这个 ZIP。它带着
    fd/app/shim，装的必须是 ESP 里那份产品；来源清单里没有绑定的话，两个
    run 的产品可以混在一起，设备上 Android 与 Linux 两侧的固件就不一致了。
    """
    record = json.loads((root / "module-manifest.json").read_text())
    if record.get("artifact_kind") != "android-product-module":
        raise ValueError("module manifest is not an Android product module")
    package = root / "sunuefi-module.zip"
    if sha256(package) != record.get("zip_sha256"):
        raise ValueError("module ZIP differs from its manifest")
    if record.get("same_product", {}).get("product_sha256") != sha256(root / "PianoUEFI-product.img"):
        raise ValueError("module was built from another product image")
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise ValueError("module ZIP is corrupt")
        names = set(archive.namelist())
    for name in ("payload/selector.bin", "payload/fd.bin", "payload/app.bin",
                 "payload/shim.bin", "module.prop"):
        if name not in names:
            raise ValueError("module ZIP lacks " + name)
    return record


def verify(root):
    missing = REQUIRED - {p.name for p in root.iterdir() if p.is_file()}
    if missing:
        raise ValueError("bundle is incomplete: " + str(sorted(missing)))
    read = lambda name: json.loads((root / name).read_text())
    source_runs = read("source-runs.json")
    verify_uefi_source(PROJECT, root / "PianoUEFI-product.img", read("uefi-source.json"), source_runs.get("uefi_run"))
    verify_module(root)
    kernel = read("kernel-manifest.json")
    kernel_hash = sha256(root / "kernel-manifest.json")
    for name in ("initramfs-manifest.json", "runtime-manifest.json", "rootfs-manifest.json"):
        m = read(name)
        if m.get("kernel_manifest_sha256") != kernel_hash or m.get("kernel_release") != kernel["kernel_release"]:
            raise ValueError("bundle mixes kernel builds: " + name)
    esp = read("esp-manifest.json")
    if esp["kernel_manifest_sha256"] != kernel_hash:
        raise ValueError("ESP and rootfs use different kernel manifests")
    # 固件走标准启动顺序，ESP 的 EFI/BOOT/BOOTAA64.EFI（systemd-boot）是入口。
    # 上游 package_release.py 默认给 BLS 布局，也支持可选的版本化 UKI；两种都
    # 带同一个 systemd-boot，载荷按 boot_layout 字段核对，不猜。
    layout = esp.get("boot_layout")
    release = kernel["kernel_release"]
    systemd_boot = {"/EFI/systemd/systemd-bootaa64.efi", "/EFI/BOOT/BOOTAA64.EFI",
                    "/loader/loader.conf"}
    if layout == "bls":
        expected_paths = systemd_boot | {
            f"/linux/{release}/linux", f"/linux/{release}/initrd",
            f"/linux/{release}/board.dtb", f"/loader/entries/linux-{release}.conf"}
    elif layout == "uki":
        expected_paths = systemd_boot | {f"/EFI/Linux/linux-{release}.efi"}
    else:
        raise ValueError(f"ESP manifest names an unknown boot layout: {layout!r}")
    if set(esp["esp_files"]) != expected_paths:
        raise ValueError("ESP layout differs from the actual firmware loader: "
                         f"got {sorted(esp['esp_files'])}")
    for asset, packaged in ASSETS.items():
        row = esp["files"][packaged]
        if sha256(root / asset) != row["sha256"] or (root / asset).stat().st_size != row["bytes"]:
            raise ValueError("upstream-packaged component changed: " + asset)
    image = read("root-image-manifest.json")
    for component in ("rootfs", "kernel", "runtime", "mesa", "ffmpeg", "initramfs"):
        if image.get(component + "_manifest_sha256") != sha256(root / (component + "-manifest.json")):
            raise ValueError("root image refers to another component: " + component)
    for component in ("runtime", "mesa", "ffmpeg"):
        if read("rootfs-manifest.json").get(component + "_manifest_sha256") != sha256(root / (component + "-manifest.json")):
            raise ValueError("rootfs refers to another installed component: " + component)
    releasever = read("rootfs-manifest.json").get("releasever")
    if not releasever:
        raise ValueError("rootfs manifest does not name the Fedora release")
    mesa = read("mesa-manifest.json")
    if (mesa.get("status") != "FEDORA_MESA_BUILT_NOT_DEVICE_VERIFIED"
            or mesa.get("releasever") != releasever
            or mesa.get("chip_id") != "0xffff44050001"):
        raise ValueError("bundle does not include the verified piano Mesa build")
    ffmpeg = read("ffmpeg-manifest.json")
    if (ffmpeg.get("status"), ffmpeg.get("releasever"), ffmpeg.get("arch")) != (
            "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED", releasever, "aarch64") or set(
                ffmpeg.get("decoders", {})) != {"h264_v4l2m2m", "hevc_v4l2m2m"}:
        raise ValueError("bundle does not include the verified piano FFmpeg build")
    if image["status"] != "IMAGE_VERIFIED" or image.get("format") != "raw-f2fs" or image["label"] != "PIANOROOT":
        raise ValueError("root image is not a verified piano F2FS image")
    proof = image["verify"]
    for key in ("file_content_checked", "numeric_owner_checked", "mode_checked", "symlink_targets_checked",
                "hardlink_groups_checked", "mtime_checked", "xattr_checked", "fsck_read_only", "fsck_ran_unmounted"):
        if proof.get(key) is not True:
            raise ValueError("root image verification is missing: " + key)
    if proof["fsck_exit"] != 0 or proof["fsck_changed_image"] or proof["xattr_read_errors_ignored"]:
        raise ValueError("root image verification did not complete safely")
    if proof.get("selinux_labels", 0) < 1:
        raise ValueError("Fedora SELinux labels were not applied and verified")
    raw_hash, expanded = hashlib.sha256(), 0
    with subprocess.Popen(["zstd", "-dc", str(root / "pianoroot.f2fs.img.zst")], stdout=subprocess.PIPE) as process:
        for block in iter(lambda: process.stdout.read(1024 * 1024), b""):
            raw_hash.update(block)
            expanded += len(block)
        if process.wait() != 0:
            raise ValueError("compressed root image failed to decode")
    if expanded != image["bytes"] or raw_hash.hexdigest() != image["sha256"]:
        raise ValueError("compressed root image differs from the verified raw image")
    # 封存镜像与设备容量无关，传输格式在 bundle 里现成提供，装机不需要 Actions。
    transport = root / "pianoroot.f2fs.sparse.img"
    if not transport.is_file():
        raise ValueError("bundle is missing the flashable root transport")
    decoded = piano_sparse.inspect(transport)
    if (decoded["expanded_bytes"], decoded["expanded_sha256"]) != (image["bytes"], image["sha256"]):
        raise ValueError("root transport does not decode to the sealed image")
    inventory = {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                 for p in sorted(root.iterdir()) if p.is_file()
                 and p.name not in ("bundle-manifest.json", "SHA256SUMS")}
    return {"status": "HOST_VERIFIED_FEDORA_F2FS_BUNDLE_NOT_DEVICE_VERIFIED",
            "kernel_release": kernel["kernel_release"], "kernel_manifest_sha256": kernel_hash,
            "mesa_manifest_sha256": sha256(root / "mesa-manifest.json"),
            "ffmpeg_manifest_sha256": sha256(root / "ffmpeg-manifest.json"),
            "root_image": {"format": "raw-f2fs", "label": "PIANOROOT", "expanded_sha256": image["sha256"],
                           "bytes": image["bytes"], "verification": proof},
            "source_runs": source_runs, "uefi_source_verified": True, "device_tested": False,
            "device_operation_performed": False,
            "files": inventory,
            "deployment_requirements": ["recoverable device backup", "userdata/data-handling choice",
                                        "current stock BOOT and slot/generation provisioning"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bundle", required=True, type=Path)
    ap.add_argument("--write-manifest", action="store_true")
    args = ap.parse_args()
    root = args.bundle.resolve()
    result = verify(root)
    if args.write_manifest:
        (root / "bundle-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
        (root / "SHA256SUMS").write_text("".join(sha256(p) + "  " + p.name + "\n"
                                              for p in sorted(root.iterdir()) if p.is_file() and p.name != "SHA256SUMS"))
    print(json.dumps({"status": result["status"], "kernel_release": result["kernel_release"], "device_tested": False}))


if __name__ == "__main__":
    main()
