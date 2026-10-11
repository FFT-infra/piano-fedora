#!/usr/bin/env python3
"""离线复跑 native BOOT 重打包工具的文件级 guard，输出单独 JSON 证据。

只使用交付的真实二进制与真实 stock BOOT，不接触任何设备、块设备或分区。
7 项文件级 guard 有真实结论；3 项 device-only guard 恒为 false 并标注原因。
篡改输入只在本脚本自建的 --work-dir 临时文件上发生。
"""

import argparse
import hashlib
import json
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

BOOT_BYTES = 96 * 1024**2
EM_X86_64 = 62
EM_AARCH64 = 183
QEMU = {EM_X86_64: "qemu-x86_64", EM_AARCH64: "qemu-aarch64"}

# 这三项必须依赖真实 Android 属性或块设备读写，离线不可证。
DEVICE_ONLY = ("current_source_guard", "active_slot_guard", "write_readback")

FILE_GUARDS = (
    "lossless_roundtrip",
    "request_persistent_reselection_crc",
    "nested_wrapper_rejected",
    "ota_restore_refused",
    "payload_tamper_rejected",
    "request_owned_wrapper_only",
    "missing_request_stock_passthrough",
)


class VerifyError(Exception):
    pass


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def elf_machine(path):
    header = Path(path).read_bytes()[:64]
    if len(header) < 64 or header[:4] != b"\x7fELF":
        raise VerifyError("tool is not an ELF binary: " + str(path))
    if header[4] != 2 or header[5] != 1:
        raise VerifyError("tool must be little-endian ELF64: " + str(path))
    return struct.unpack_from("<H", header, 18)[0]


def host_machine():
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return EM_X86_64
    if machine in ("aarch64", "arm64"):
        return EM_AARCH64
    return None


def refuse_unsafe_input(path, label):
    if path.is_symlink():
        raise VerifyError(f"{label} must not be a symlink: {path}")
    if path.is_block_device():
        raise VerifyError(f"{label} must not be a real block device: {path}")
    if not path.is_file():
        raise VerifyError(f"{label} is not a regular file: {path}")


class Runner:
    """按 ELF 机器调用工具；机器不匹配时用现成 qemu，不安装任何东西。"""

    def __init__(self, tool, work_dir, timeout):
        self.timeout = timeout
        self.machine = elf_machine(tool)
        self.prefix = []
        if self.machine != host_machine():
            emulator = QEMU.get(self.machine)
            if not emulator:
                raise VerifyError(f"no known emulator for ELF machine {self.machine}")
            found = shutil.which(emulator)
            if not found:
                raise VerifyError(f"host/ELF machine mismatch and {emulator} is unavailable")
            self.prefix = [found]
            self.emulator = found
        else:
            self.emulator = None
        # 在 work-dir 内放一份可执行的副本，避免改动交付文件或要求源文件带执行位。
        self.runnable = Path(work_dir) / "piano-boot-repack"
        shutil.copyfile(tool, self.runnable)
        self.runnable.chmod(0o755)

    def run(self, *args):
        command = [*self.prefix, str(self.runnable), *map(str, args)]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise VerifyError("timeout after %ss: %s" % (self.timeout, " ".join(map(str, args))))
        payload = None
        if result.returncode == 0 and result.stdout.strip():
            try:
                payload = json.loads(result.stdout.strip().splitlines()[-1])
            except json.JSONDecodeError:
                payload = None
        return {
            "command": " ".join(map(str, args)),
            "returncode": result.returncode,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "json": payload,
        }

    def expect_ok(self, *args):
        outcome = self.run(*args)
        if outcome["returncode"] != 0:
            raise VerifyError(f"{outcome['command']} failed: {outcome['stderr'] or outcome['stdout']}")
        if outcome["json"] is None:
            raise VerifyError(f"{outcome['command']} produced no JSON")
        return outcome["json"]

    def expect_refused(self, expected_message, *args):
        outcome = self.run(*args)
        # 只接受"正常退出且非零"；被信号杀死（负返回码）或零返回都不算拒绝。
        if outcome["returncode"] <= 0:
            raise VerifyError(f"{outcome['command']} was not refused by a normal nonzero exit "
                              f"(returncode={outcome['returncode']}): {outcome['stderr'] or outcome['stdout']}")
        combined = (outcome["stderr"] + "\n" + outcome["stdout"]).strip()
        if expected_message not in combined:
            raise VerifyError(f"{outcome['command']} refusal message differs; expected {expected_message!r}, got {combined!r}")
        return combined


def load_inputs(boot_tools, stock_boot):
    boot_tools = Path(boot_tools)
    refuse_unsafe_input(boot_tools / "selector-module.json", "selector-module.json")
    selector_meta = json.loads((boot_tools / "selector-module.json").read_text())
    selector = boot_tools / "selector.bin"
    refuse_unsafe_input(selector, "selector.bin")
    if sha256(selector) != selector_meta["selector_sha256"] or selector.stat().st_size != selector_meta["selector_bytes"]:
        raise VerifyError("selector.bin differs from selector-module.json")
    product = boot_tools / "product"
    image = product / "PianoUEFI-product.img"
    fd = product / "PianoUEFI-product.fd"
    shim = product / "BootShim.bin"
    for path, label in ((image, "PianoUEFI-product.img"), (fd, "PianoUEFI-product.fd"), (shim, "BootShim.bin")):
        refuse_unsafe_input(path, label)
    if sha256(fd) != selector_meta["product_fd_sha256"]:
        raise VerifyError("product FD differs from the selector manifest")
    # 可选绑定：产品 manifest/build-ok.json 记录同一 FD、BootShim 与压缩内核摘要。
    product_manifest = product / "manifest.json"
    if product_manifest.is_file():
        files = json.loads(product_manifest.read_text()).get("files", {})
        for name, path in (("PianoUEFI-product.fd", fd), ("BootShim.bin", shim),
                           ("PianoUEFI-product.img", image)):
            record = files.get(name)
            if record is not None and (record.get("sha256") != sha256(path)
                                       or record.get("bytes") != path.stat().st_size):
                raise VerifyError(f"product manifest disagrees with {name}")
    build_ok = product / "build-ok.json"
    if build_ok.is_file():
        outputs = json.loads(build_ok.read_text()).get("outputs", {})
        for name, path in (("PianoUEFI-product.fd", fd), ("BootShim.bin", shim)):
            if name in outputs and outputs[name] != sha256(path):
                raise VerifyError(f"build-ok.json disagrees with {name}")
    shim_bytes, fd_bytes = shim.read_bytes(), fd.read_bytes()
    data = image.read_bytes()
    if len(data) < 4096 or data[:8] != b"ANDROID!":
        raise VerifyError("product image is not an Android boot image")
    kernel_bytes, ramdisk_bytes = struct.unpack_from("<II", data, 8)
    start = 4096 + (kernel_bytes + 4095) // 4096 * 4096
    if ramdisk_bytes < 64 or start + ramdisk_bytes > len(data):
        raise VerifyError("product image APPv1 bounds differ")
    app = data[start:start + ramdisk_bytes]
    if hashlib.sha256(app).hexdigest() != selector_meta["app_payload_sha256"]:
        raise VerifyError("product APP payload differs from the selector manifest")
    # 产品内核区是共享核心的 gzip 流（压缩长度即 header kernel_bytes，不等于 raw 长度）；
    # 解压后必须正好等于 BootShim + FD，这与上游 product_payload 同构。
    stream = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = stream.decompress(data[4096:4096 + kernel_bytes], len(shim_bytes) + len(fd_bytes) + 1)
    if not stream.eof or raw != shim_bytes + fd_bytes:
        raise VerifyError("product image kernel does not contain the shared BootShim + FD core")
    if product_manifest.is_file():
        record = json.loads(product_manifest.read_text()).get("files", {}).get("product-kernel.bin")
        if record is not None and record.get("sha256") != hashlib.sha256(data[4096:4096 + kernel_bytes]).hexdigest():
            raise VerifyError("product image kernel region differs from the manifest product-kernel.bin")
    return {
        "selector": selector,
        "selector_meta": selector_meta,
        "product_image": image,
        "product_fd": fd,
        "boot_shim": shim,
        "app_payload_bytes": app,
        "stock_boot": Path(stock_boot).resolve(),
    }


def guard_lossless_roundtrip(runner, inputs, work):
    wrapped = work / "wrapped-lossless.img"
    restored = work / "restored.img"
    runner.expect_ok("repack", "--input", inputs["stock_boot"], "--output", wrapped,
                     "--selector", inputs["selector"],
                     "--selector-memory-bytes", inputs["selector_meta"]["selector_memory_bytes"],
                     "--selector-metadata-offset", inputs["selector_meta"]["metadata_offset"],
                     "--shim", inputs["boot_shim"], "--fd", inputs["product_fd"],
                     "--app", work / "app-payload.bin")
    runner.expect_ok("restore", "--input", wrapped, "--output", restored)
    if sha256(restored) != sha256(inputs["stock_boot"]):
        raise VerifyError("restored BOOT is not byte-equal to the stock source")
    return {"detail": "repack then restore reproduces the stock BOOT byte-for-byte",
            "wrapped_sha256": sha256(wrapped)}


def guard_request_reselection(runner, inputs, work):
    wrapped = work / "wrapped.img"
    linux_img = work / "req-linux.img"
    android_img = work / "req-android.img"
    first = runner.expect_ok("request", "--input", wrapped, "--output", linux_img, "--target", "linux")
    preview_before = sha256(linux_img)
    preview1 = runner.expect_ok("request", "--input", linux_img, "--target", "linux", "--preview")
    second = runner.expect_ok("request", "--input", linux_img, "--output", android_img, "--target", "android")
    preview_before2 = sha256(android_img)
    preview2 = runner.expect_ok("request", "--input", android_img, "--target", "android", "--preview")
    observed = (first["target"], first["sequence"], second["target"], second["sequence"],
                preview2["target"], preview2["sequence"], preview1["target"], preview1["sequence"])
    if observed != (2, 1, 0, 2, 0, 2, 2, 1):
        raise VerifyError(f"persistent reselection differed: {observed}")
    # preview 必须真的只读：比较前后独立计算的 sha256，而不是工具自报字段。
    if sha256(linux_img) != preview_before or sha256(android_img) != preview_before2:
        raise VerifyError("read-only preview changed the wrapper bytes on disk")
    if preview1["file_sha256"] != preview_before or preview2["file_sha256"] != preview_before2:
        raise VerifyError("preview reported a file hash that differs from the on-disk bytes")
    restored = work / "restored-after-request.img"
    runner.expect_ok("restore", "--input", android_img, "--output", restored)
    if sha256(restored) != sha256(inputs["stock_boot"]):
        raise VerifyError("restore after reselection is not byte-equal to the stock source")
    return {"detail": "linux(2,1) then android(0,2); previews byte-stable on disk; restore byte-equal",
            "observed": observed}


def guard_nested_wrapper_rejected(runner, inputs, work):
    wrapped = work / "wrapped.img"
    message = runner.expect_refused("input is already wrapped",
                                    "repack", "--input", wrapped, "--output", work / "nested.img",
                                    "--selector", inputs["selector"],
                                    "--selector-memory-bytes", inputs["selector_meta"]["selector_memory_bytes"],
                                    "--selector-metadata-offset", inputs["selector_meta"]["metadata_offset"],
                                    "--shim", inputs["boot_shim"], "--fd", inputs["product_fd"],
                                    "--app", work / "app-payload.bin")
    return {"detail": "re-wrapping a wrapped BOOT is refused", "message": message}


def guard_ota_restore_refused(runner, inputs, work):
    message = runner.expect_refused("SPLITv1 metadata not found",
                                    "restore", "--input", inputs["stock_boot"], "--output", work / "unowned-restore.img")
    return {"detail": "restoring a stock (non-owned) BOOT is refused", "message": message}


def guard_payload_tamper_rejected(runner, inputs, work):
    tampered = bytearray(inputs["app_payload_bytes"])
    tampered[len(tampered) // 2] ^= 0xFF
    tampered_path = work / "app-tampered.bin"
    tampered_path.write_bytes(bytes(tampered))
    message = runner.expect_refused("APPv1 payload hash mismatch",
                                    "repack", "--input", inputs["stock_boot"], "--output", work / "tampered.img",
                                    "--selector", inputs["selector"],
                                    "--selector-memory-bytes", inputs["selector_meta"]["selector_memory_bytes"],
                                    "--selector-metadata-offset", inputs["selector_meta"]["metadata_offset"],
                                    "--shim", inputs["boot_shim"], "--fd", inputs["product_fd"],
                                    "--app", tampered_path)
    return {"detail": "a tampered APP payload is refused", "message": message}


def guard_request_owned_only(runner, inputs, work):
    message = runner.expect_refused("SPLITv1 metadata not found",
                                    "request", "--input", inputs["stock_boot"], "--target", "linux", "--preview")
    return {"detail": "request on an unowned stock BOOT is refused", "message": message}


def guard_missing_request_passthrough(runner, inputs, work):
    wrapped = work / "wrapped.img"
    preview = runner.expect_ok("request", "--input", wrapped, "--target", "android", "--preview")
    if (preview["target"], preview["sequence"]) != (0, 0):
        raise VerifyError(f"a request-less wrapper did not pass through to stock: {preview}")
    return {"detail": "a wrapper with no request passes through to stock (target 0, sequence 0)",
            "observed": (preview["target"], preview["sequence"])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tool", type=Path, required=True, help="交付的 native piano-boot-repack 二进制")
    parser.add_argument("--stock-boot", type=Path, required=True, help="真实 stock BOOT 文件（必须是常规文件）")
    parser.add_argument("--boot-tools", type=Path, required=True, help="含 selector.bin 与 product/ 的目录")
    parser.add_argument("--output", type=Path, required=True, help="独占的新 JSON 证据文件")
    parser.add_argument("--work-dir", type=Path, required=True, help="唯一临时目录；不得使用 /tmp")
    parser.add_argument("--timeout", type=int, default=900, help="每条子命令的秒级上限")
    args = parser.parse_args(argv)

    if args.output.exists() or args.output.is_symlink():
        parser.error("--output must be a new, exclusive path")
    work_dir = args.work_dir.resolve()
    if not work_dir.is_dir():
        parser.error("--work-dir must be an existing directory")
    if work_dir == Path("/tmp") or work_dir.is_relative_to("/tmp"):
        parser.error("--work-dir must not be under /tmp")
    try:
        refuse_unsafe_input(args.tool, "tool")
        refuse_unsafe_input(args.stock_boot, "stock-boot")
        stock_boot = args.stock_boot.resolve()
        if stock_boot.stat().st_size != BOOT_BYTES:
            raise VerifyError(f"stock-boot must be the full {BOOT_BYTES} byte BOOT")
        inputs = load_inputs(args.boot_tools, stock_boot)
    except VerifyError as error:
        print("verify-native-boot.py: " + str(error), file=sys.stderr)
        return 2

    guards = {}
    setup_error = None
    with tempfile.TemporaryDirectory(prefix="verify-native-boot-", dir=work_dir) as temporary:
        work = Path(temporary)
        try:
            runner = Runner(args.tool.resolve(), work, args.timeout)
        except (VerifyError, OSError) as error:
            print("verify-native-boot.py: " + str(error), file=sys.stderr)
            return 2
        # repack/restore 依赖的 APP payload 只从交付 product 镜像解出，写进临时目录。
        (work / "app-payload.bin").write_bytes(inputs["app_payload_bytes"])
        # 先建立一次包装镜像，供多个 guard 复用（只在本临时目录内）。
        try:
            runner.expect_ok("repack", "--input", inputs["stock_boot"], "--output", work / "wrapped.img",
                             "--selector", inputs["selector"],
                             "--selector-memory-bytes", inputs["selector_meta"]["selector_memory_bytes"],
                             "--selector-metadata-offset", inputs["selector_meta"]["metadata_offset"],
                             "--shim", inputs["boot_shim"], "--fd", inputs["product_fd"],
                             "--app", work / "app-payload.bin")
        except (VerifyError, OSError, KeyError, ValueError) as error:
            # 建立包装镜像失败（含超时）时，任何 guard 都不能标 passed。
            setup_error = str(error)
        for name, function in (
            ("lossless_roundtrip", guard_lossless_roundtrip),
            ("request_persistent_reselection_crc", guard_request_reselection),
            ("nested_wrapper_rejected", guard_nested_wrapper_rejected),
            ("ota_restore_refused", guard_ota_restore_refused),
            ("payload_tamper_rejected", guard_payload_tamper_rejected),
            ("request_owned_wrapper_only", guard_request_owned_only),
            ("missing_request_stock_passthrough", guard_missing_request_passthrough),
        ):
            if setup_error is not None:
                guards[name] = {"passed": False, "error": "setup repack failed: " + setup_error}
                continue
            try:
                detail = function(runner, inputs, work)
                guards[name] = {"passed": True, **detail}
            except (VerifyError, OSError, KeyError, ValueError) as error:
                guards[name] = {"passed": False, "error": str(error)}

    passed = all(guards[name]["passed"] for name in FILE_GUARDS)
    record = {
        "schema_version": 1,
        "status": "HOST_FILE_LEVEL_GUARDS_VERIFIED_NOT_DEVICE_VERIFIED" if passed
                  else "HOST_FILE_LEVEL_GUARDS_INCOMPLETE_NOT_DEVICE_VERIFIED",
        "device_tested": False,
        "device_operation_performed": False,
        "stock_boot_is_block_device": False,
        "runner": {
            "tool_machine": runner.machine,
            "host_machine": host_machine(),
            "emulator": runner.emulator,
            "timeout_seconds": args.timeout,
        },
        "hashes": {
            "tool_sha256": sha256(args.tool.resolve()),
            "stock_boot_sha256": sha256(stock_boot),
            "selector_sha256": sha256(inputs["selector"]),
            "product_image_sha256": sha256(inputs["product_image"]),
            "product_fd_sha256": sha256(inputs["product_fd"]),
            "boot_shim_sha256": sha256(inputs["boot_shim"]),
            "app_payload_sha256": hashlib.sha256(inputs["app_payload_bytes"]).hexdigest(),
        },
        "selector": {
            "selector_bytes": inputs["selector_meta"]["selector_bytes"],
            "selector_memory_bytes": inputs["selector_meta"]["selector_memory_bytes"],
            "metadata_offset": inputs["selector_meta"]["metadata_offset"],
        },
        "file_guards": guards,
        "device_only_guards": {name: False for name in DEVICE_ONLY},
        "device_only_reason": "requires Android getprop identity, active-slot block device or a real BOOT write/readback",
        "notes": "只读文件级验证；未连接或写入任何设备，未使用真实块设备作为输入。",
    }
    if setup_error is not None:
        record["setup_error"] = setup_error
    # open("x") 保证真正独占：文件已存在则失败，不覆盖。
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": record["status"],
                      "passed": [name for name in FILE_GUARDS if guards[name]["passed"]],
                      "failed": [name for name in FILE_GUARDS if not guards[name]["passed"]],
                      "output": str(args.output)}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
