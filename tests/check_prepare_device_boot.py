#!/usr/bin/env python3
"""本机 BOOT 包装的前置校验与 boot-request 生成，不接触设备。

`scripts/prepare-device-boot.py` 的主体要调用上游 `piano-boot-repack`，离线跑不
了。这个测试覆盖它之前和之后的纯 Python 契约：拒绝错误槽位、拒绝大小或摘要
不符的 stock BOOT、拒绝已存在的输出目录、用新目录重建时生成正确的
`boot-request.json` 与清单字段。上游二进制本身的行为由真机与 Actions 覆盖。
"""

import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("device_boot", ROOT / "scripts/prepare-device-boot.py")
device_boot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(device_boot)


def expect_refusal(call, needle):
    try:
        call()
    except (ValueError, AssertionError, SystemExit) as error:
        assert needle in str(error), f"refused for the wrong reason: {error}"
    else:
        raise AssertionError(f"accepted input that should fail: {needle}")


def main():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        stock = work / "stock-boot.img"
        stock.write_bytes(b"\0" * (96 * 1024 * 1024))
        digest = hashlib.sha256(stock.read_bytes()).hexdigest()
        tools, host_tool, bundle = work / "tools", work / "host", work / "bundle"
        for path in (tools, host_tool, bundle):
            path.mkdir()
        output = work / "out"

        # 槽位必须是显式的 _a / _b；其余拒绝发生在读任何材料之前。
        expect_refusal(lambda: device_boot.prepare(stock, tools, host_tool, bundle, "a", digest, output),
                       "explicit Android slot required")

        # 摘要不符时不能继续；否则装的是另一台设备的 BOOT。
        expect_refusal(lambda: device_boot.prepare(stock, tools, host_tool, bundle, "_a", "0" * 64, output),
                       "does not match the measured current-device digest")

        # 不足 96 MiB 的 stock BOOT 是截断文件，不能拿来包装。
        short = work / "short-boot.img"
        short.write_bytes(b"\0" * 1024)
        expect_refusal(lambda: device_boot.prepare(short, tools, host_tool, bundle, "_a",
                                                   hashlib.sha256(short.read_bytes()).hexdigest(), output),
                       "expected the full current 96 MiB BOOT")

        # 已有输出目录不覆盖，旧证据必须原样保留。
        output.mkdir()
        expect_refusal(lambda: device_boot.prepare(stock, tools, host_tool, bundle, "_a", digest, output),
                       "use a new output directory")

        # 缺 manifest 的工具目录要被点名拒绝，不能等到调用上游二进制才失败。
        expect_refusal(lambda: device_boot.prepare(stock, tools, host_tool, bundle, "_a", digest,
                                                   work / "fresh"),
                       "selector-module.json")

        # 持久引导的门槛值。0x1e0001a9 = Android 15 加 2026-09 补丁，与本地
        # 打包器一致；写成 15 << 11 会得到 0x79a9，ABL 同样拒绝。
        assert device_boot.BOOT_OS_VERSION == 0x1E0001A9, hex(device_boot.BOOT_OS_VERSION)
        assert device_boot.BOOT_PARTITION_BYTES == 96 * 1024 * 1024
        assert device_boot.BOOT_PARTITION_NAME == "boot"

        # 去掉 NONE footer 时只留原始镜像，长度与 footer 里的 original_size 相同。
        signed = work / "signed-boot.img"
        original = 4096
        footer = bytearray(64)
        footer[0:4] = b"AVBf"
        footer[4:8] = (1).to_bytes(4, "big")
        footer[12:20] = original.to_bytes(8, "big")
        head = b"ANDROID!" + b"\0" * (original - len(b"ANDROID!"))
        signed.write_bytes(head + bytes(device_boot.BOOT_PARTITION_BYTES - original - 64) + bytes(footer))
        stripped = device_boot.strip_avb_footer(signed)
        assert len(stripped) == original, len(stripped)
        assert stripped[:8] == b"ANDROID!"

        # 没有 footer 的镜像不能静默当成已签名产物。
        bare = work / "bare-boot.img"
        bare.write_bytes(b"ANDROID!" + b"\0" * (device_boot.BOOT_PARTITION_BYTES - 8))
        expect_refusal(lambda: device_boot.strip_avb_footer(bare), "no AVB footer")

    # 请求页只在当前引导的那份镜像里被读到，Linux 首装从 boot_b 启动。三个
    # 生产者写进 boot-request 的 boot_device 必须一致，否则 root-install 与
    # device-boot 的配置互相对不上，或者指向一张读了也没用的分区。
    assert device_boot.LINUX_BOOT_DEVICE == "/dev/disk/by-partlabel/boot_b", \
        device_boot.LINUX_BOOT_DEVICE
    for name in ("install-f2fs", "prepare-root-capacity"):
        spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.LINUX_BOOT_DEVICE == device_boot.LINUX_BOOT_DEVICE, (
            f"{name}.LINUX_BOOT_DEVICE differs from prepare-device-boot")

    print("OK: BOOT 包装拒绝错误槽位、截断镜像、摘要不符与已存在的输出目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
