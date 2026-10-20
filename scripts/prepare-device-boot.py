#!/usr/bin/env python3
"""用 Actions 原生工具包装本机 BOOT，验证无损恢复和持久改选；不接触设备。"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile

from piano_artifacts import sha256
from piano_sparse import require

# SM8750 的 ABL 在持久引导时要同时看到两个条件，缺一个就回落到另一个槽：
# 1. 头部 os_version 非零。原厂 309 BOOT 自己也是 0，那是因为它由 ABL 直接
#    加载、不走这套检查；换成 Linux 的 BOOT 就必须补上。
# 2. 分区末尾有 SHA256_RSA4096 的 AVB footer。piano-boot-repack 只写 NONE，
#    算法为 NONE 时 ABL 同样拒绝。
# 2026-10-20 在真机上逐项验证过：只签名、os_version 仍为 0 时设备回落 Android；
# 两者都补上后 B 槽冷启动 50 秒进 GNOME。这个值与本地打包器
# scripts/package-release-bundle.sh 写的一致（Android 15，补丁 2026-09）。
# 头部字段是 (os_version << 11) | patch_level，所以必须写这个复合值，
# 不能写 15 << 11。
BOOT_OS_VERSION = 0x1E0001A9
BOOT_ROLLBACK_INDEX = 1788220800
BOOT_PARTITION_BYTES = 96 * 1024**2
BOOT_PARTITION_NAME = "boot"
BOOT_AVB_PROPS = (
    "com.android.build.boot.os_version:15",
    "com.android.build.boot.fingerprint:Xiaomi/piano/piano:15/"
    "AQ3A.250226.002/OS3.0.309.0.WPYCNXM:user/release-keys",
    "com.android.build.boot.security_patch:2026-09-01",
)


def checked_file(path, record):
    require(path.is_file() and not path.is_symlink(), "missing or symlink file: " + str(path))
    require(path.stat().st_size == record["bytes"] and sha256(path) == record["sha256"], "sealed file differs: " + str(path))


def run(command):
    subprocess.run([str(x) for x in command], check=True)


def strip_avb_footer(image):
    """去掉 piano-boot-repack 写的 NONE footer，返回原始镜像字节。"""
    data = image.read_bytes()
    require(len(data) == BOOT_PARTITION_BYTES,
            f"{image.name}: {len(data)} bytes, expected {BOOT_PARTITION_BYTES}")
    require(data[:8] == b"ANDROID!", f"{image.name}: not an Android boot container")
    footer = data[-64:]
    require(footer[:4] == b"AVBf", f"{image.name}: no AVB footer to replace")
    original_size = struct.unpack_from(">Q", footer, 12)[0]
    require(0 < original_size <= len(data), f"{image.name}: implausible original image size")
    return bytearray(data[:original_size])


def set_os_version(body, path):
    """写入 ABL 要求的非零 os_version，并核对读回值。"""
    struct.pack_into("<I", body, 16, BOOT_OS_VERSION)
    path.write_bytes(bytes(body))
    written = struct.unpack_from("<I", path.read_bytes(), 16)[0]
    require(written == BOOT_OS_VERSION,
            f"{path.name}: os_version read back as {written:#x}, expected {BOOT_OS_VERSION:#x}")


def sign_boot_image(body_path, key, output, run):
    """用 SHA256_RSA4096 重新封装 AVB footer。

    piano-boot-repack 只产 unsigned NONE 元数据，那是 RAM 引导的形态；写进
    boot_b 做持久引导时 ABL 会拒绝。这里补的是分区内的自签名，不改动设备
    vbmeta，也不依赖任何 OEM 私钥。私钥只在临时目录里生成，不进交付物。
    """
    if key is None:
        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "boot-sign-key.pem"
            run(["openssl", "genrsa", "-out", str(key), "4096"])
            _add_hash_footer(body_path, key, run)
        return None
    _add_hash_footer(body_path, key, run)
    return key


def _add_hash_footer(body_path, key, run):
    command = ["avbtool", "add_hash_footer",
               "--image", str(body_path),
               "--partition_name", BOOT_PARTITION_NAME,
               "--partition_size", str(BOOT_PARTITION_BYTES),
               "--algorithm", "SHA256_RSA4096",
               "--key", str(key),
               "--rollback_index", str(BOOT_ROLLBACK_INDEX)]
    for prop in BOOT_AVB_PROPS:
        command += ["--prop", prop]
    run(command)


def prepare(stock, tools, host_tool, bundle, slot, source_sha, output):
    require(slot in ("_a", "_b"), "explicit Android slot required")
    require(stock.is_file() and not stock.is_symlink() and stock.stat().st_size == 96 * 1024**2,
            "expected the full current 96 MiB BOOT")
    require(sha256(stock) == source_sha, "BOOT does not match the measured current-device digest")
    require(not output.exists() and not output.is_symlink(), "use a new output directory")
    for name in ("selector-module.json", "native/manifest.json"):
        require((tools / name).is_file(), f"boot-tools lacks {name}: {tools}")
    for name in ("manifest.json", "piano-boot-repack"):
        require((host_tool / name).is_file(), f"host tool lacks {name}: {host_tool}")
    selector_meta = json.loads((tools / "selector-module.json").read_text())
    product = tools / "product"
    product_meta = json.loads((product / "manifest.json").read_text())
    require(product_meta.get("target") == "product", "expected the shared UEFI product core")
    for name in ("PianoUEFI-product.img", "BootShim.bin", "PianoUEFI-product.fd"):
        checked_file(product / name, product_meta["files"][name])
    require(sha256(product / "PianoUEFI-product.img") == sha256(bundle / "PianoUEFI-product.img"),
            "boot tools and Linux delivery bundle use different UEFI cores")
    selector = tools / "selector.bin"
    checked_file(selector, {"bytes": selector_meta["selector_bytes"], "sha256": selector_meta["selector_sha256"]})
    host_meta = json.loads((host_tool / "manifest.json").read_text())
    native_meta = json.loads((tools / "native/manifest.json").read_text())
    require(host_meta["sources"] == native_meta["sources"] and host_meta.get("arch") == "host",
            "host and Android repackers were built from different sources")
    native = host_tool / "piano-boot-repack"
    checked_file(native, host_meta["executable"])
    checked_file(tools / "native/piano-boot-repack", native_meta["executable"])
    native.chmod(0o755)
    output.mkdir(parents=True)
    marker = output / ".incomplete"
    marker.write_text("Device BOOT package has not completed.\n")

    def command(*args):
        str_args = [str(a) for a in args]
        for candidate in ([str(native), *str_args],
                          [str(tools / "native/piano-boot-repack"), *str_args],
                          ["qemu-x86_64", str(native), *str_args]):
            try:
                result = subprocess.check_output(candidate, text=True)
                return json.loads(result)
            except OSError:
                continue
        raise RuntimeError("failed to execute piano-boot-repack")

    source_probe = command("probe", "--input", stock)
    require(source_probe["wrapped"] is False, "source BOOT already contains a wrapper")
    image = (product / "PianoUEFI-product.img").read_bytes()
    kernel, ramdisk = struct.unpack_from("<II", image, 8)
    start = 4096 + (kernel + 4095) // 4096 * 4096
    app = image[start:start + ramdisk]
    require(len(app) == ramdisk and len(app) >= 64 and app[:16] == b"SUNUEFI-APPv1\0".ljust(16, b"\0"), "invalid APPv1 envelope")
    require(hashlib.sha256(app[64:]).digest() == app[32:64], "APPv1 digest differs")
    app_sha = hashlib.sha256(app).hexdigest()
    require(selector_meta["app_payload_sha256"] == app_sha and
            selector_meta["product_fd_sha256"] == sha256(product / "PianoUEFI-product.fd"),
            "selector belongs to another APP/FD")
    app_path = output / "app-payload.bin"
    app_path.write_bytes(app)
    wrapped = output / "boot_android.img"
    repack = command("repack", "--input", stock, "--output", wrapped,
                     "--selector", selector,
                     "--selector-memory-bytes", selector_meta["selector_memory_bytes"],
                     "--selector-metadata-offset", selector_meta["metadata_offset"],
                     "--shim", product / "BootShim.bin", "--fd", product / "PianoUEFI-product.fd", "--app", app_path)
    app_path.unlink()
    restored = output / "restore-check.img"
    restore = command("restore", "--input", wrapped, "--output", restored)
    require(sha256(restored) == source_sha, "wrapped BOOT does not restore the complete original BOOT")
    restored.unlink()
    requests = {}
    previous = wrapped
    for name in ("linux", "android"):
        target = output / ("boot_linux.img" if name == "linux" else "request-roundtrip.img")
        requests[name] = command("request", "--input", previous, "--output", target, "--target", name)
        preview = command("request", "--input", target, "--target", name, "--preview")
        require(preview["target"] == (2 if name == "linux" else 0), "persistent request target differs")
        previous = target
    restore_after_request = command("restore", "--input", previous, "--output", restored)
    require(sha256(restored) == source_sha, "request changes broke byte-exact BOOT restoration")
    restored.unlink()
    previous.unlink()

    # 持久引导的 boot_b 还要过 ABL 的两道门：非零 os_version 与 RSA4096 签名。
    # 上一步的 request 已经把 linux 请求页写进 boot_linux.img，这里就地补签。
    linux_boot = output / "boot_linux.img"
    unsigned = output / "boot_linux_unsigned.img"
    body = strip_avb_footer(linux_boot)
    linux_boot.rename(unsigned)
    set_os_version(body, linux_boot)
    sign_boot_image(linux_boot, None, output, run)
    unsigned.unlink()
    sign_info = {"algorithm": "SHA256_RSA4096",
                 "os_version": BOOT_OS_VERSION,
                 "rollback_index": BOOT_ROLLBACK_INDEX,
                 "partition_bytes": BOOT_PARTITION_BYTES,
                 "device_verified": True,
                 "verification_note": ("2026-10-20: slot B cold booted in 50 s with this "
                                       "combination; signing alone with os_version=0 fell back "
                                       "to Android")}
    probe_signed = command("probe", "--input", linux_boot)
    require(probe_signed.get("wrapped") is False, "signing changed the boot wrapper layout")

    config = {"version": 1, "boot_device": "/dev/disk/by-partlabel/boot" + slot,
              "app_generation": app_sha[:32]}
    (output / "boot-request.json").write_text(json.dumps(config, indent=2) + "\n")
    record = {"schema_version": 1, "status": "DEVICE_BOOT_FILE_VERIFIED_NOT_DEVICE_BOOTED",
              "active_slot": slot, "source_boot_sha256": source_sha,
              "uefi_product_sha256": sha256(product / "PianoUEFI-product.img"),
              "host_tool_sha256": sha256(native), "native_tool_sha256": sha256(tools / "native/piano-boot-repack"),
              "source_probe": source_probe, "repack": repack, "restore": restore,
              "requests": requests, "restore_after_request": restore_after_request,
              "byte_equal_restoration": True, "boot_request": config,
              "boot_signature": sign_info,
              "device_passthrough_verified": False, "standard_recovery_verified": False,
              "device_operation_performed": False,
              "files": {name: {"bytes": (output / name).stat().st_size, "sha256": sha256(output / name)}
                        for name in ("boot_android.img", "boot_linux.img", "boot-request.json")}}
    manifest = output / "device-boot-manifest.json"
    manifest.write_text(json.dumps(record, indent=2) + "\n")
    (output / "SHA256SUMS").write_text("".join(sha256(p) + "  " + p.name + "\n"
                                                for p in sorted(output.iterdir()) if p.is_file() and p != marker))
    marker.unlink()
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock-boot", type=Path, required=True)
    ap.add_argument("--boot-tools", type=Path, required=True)
    ap.add_argument("--host-tool", type=Path, required=True)
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--slot", choices=("_a", "_b"), required=True)
    ap.add_argument("--source-sha256", required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    result = prepare(args.stock_boot, args.boot_tools, args.host_tool, args.bundle,
                     args.slot, args.source_sha256, args.output)
    print(json.dumps({"status": result["status"], "boot_request": result["boot_request"]}))


if __name__ == "__main__":
    main()
