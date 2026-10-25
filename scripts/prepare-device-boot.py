#!/usr/bin/env python3
"""用 Actions 原生工具包装本机 BOOT，验证无损恢复和持久改选；不接触设备。"""

import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys

# 调用方用 `python3 -I` 启动（见 make-device-boot.sh）。`-I` 隐含 `-P`，
# 会把脚本目录移出 sys.path，同级的 piano_artifacts 与 piano_sparse 就导入
# 不到。这里只补回脚本自己的目录，`-I` 对用户 site-packages 与 PYTHON*
# 环境变量的隔离仍然生效。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from piano_artifacts import sha256  # noqa: E402
from piano_sparse import require  # noqa: E402

BOOT_PARTITION_BYTES = 96 * 1024**2
# 合体 BOOT 保持上游的 unsigned AVB NONE 元数据。槽 A 的 ABL 直接加载这份
# 镜像，2026-10-21 用同一份未签名镜像在真机上完成 Android→Linux→Android
# 往返。不要在这里补 os_version 或 RSA4096 footer：piano-boot-repack 的
# probe/request/adopt 会把首块与包装器里保存的原厂头逐字节比较，改偏移 16
# 会让 Android 侧的引导助手认不出当前 BOOT，切 Linux 直接失败。
#
# ABL 只加载当前槽的 BOOT，选择器在 BootSelect.c 里按
# Base + align(original_image_size) 在“正在引导的那份镜像”末尾取请求页。
# 合体 BOOT 装进 Android 的当前槽（首装是 A），运行 Linux 时被读到的就是
# 这张分区的页；不再切活动槽，包装因此跟着 --slot 走。
#
# 上游 7dbabdc 之后，Linux 侧不再有 boot-request.json：piano-next-boot-helper
# 直接读主备 GPT 与高通 A/B 属性定位活动槽，再读那份 BOOT 的实际 generation。
# 所以这里只产出镜像与清单，不生成配置文件。


def checked_file(path, record):
    require(path.is_file() and not path.is_symlink(), "missing or symlink file: " + str(path))
    require(path.stat().st_size == record["bytes"] and sha256(path) == record["sha256"], "sealed file differs: " + str(path))


def prepare(stock, tools, host_tool, bundle, slot, source_sha, output):
    require(slot in ("_a", "_b"), "explicit Android slot required")
    require(stock.is_file() and not stock.is_symlink() and stock.stat().st_size == BOOT_PARTITION_BYTES,
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

    # 上一步的 request 已经把 linux 请求页写进 boot_linux.img；镜像保持
    # piano-boot-repack 的 unsigned NONE 元数据，不再补签名或 os_version。
    #
    # 上游 7dbabdc 删掉了 /etc/piano/boot-request.json：Linux 侧的
    # piano-next-boot-helper 改为直接读主备 GPT 与高通 A/B 属性定位活动槽，
    # 不再依赖 ESP 或 root 里的槽位记录。这里因此不再生成配置文件，只记录
    # 这次包装针对哪个槽，供安装计划交叉核对。
    record = {"schema_version": 1, "status": "DEVICE_BOOT_FILE_VERIFIED_NOT_DEVICE_BOOTED",
              "active_slot": slot, "app_generation": app_sha[:32],
              "source_boot_sha256": source_sha,
              "uefi_product_sha256": sha256(product / "PianoUEFI-product.img"),
              "host_tool_sha256": sha256(native), "native_tool_sha256": sha256(tools / "native/piano-boot-repack"),
              "source_probe": source_probe, "repack": repack, "restore": restore,
              "requests": requests, "restore_after_request": restore_after_request,
              "byte_equal_restoration": True,
              "boot_device_detection": "read-only Qualcomm GPT A/B attributes",
              "device_passthrough_verified": False, "standard_recovery_verified": False,
              "device_operation_performed": False,
              "files": {name: {"bytes": (output / name).stat().st_size, "sha256": sha256(output / name)}
                        for name in ("boot_android.img", "boot_linux.img")}}
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
    print(json.dumps({"status": result["status"], "active_slot": result["active_slot"],
                      "boot_device_detection": result["boot_device_detection"]}))


if __name__ == "__main__":
    main()
