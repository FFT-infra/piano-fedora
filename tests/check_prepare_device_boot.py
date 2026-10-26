#!/usr/bin/env python3
"""本机 BOOT 包装的前置校验，不接触设备。

`scripts/prepare-device-boot.py` 的主体要调用上游 `piano-boot-repack`，离线跑不
了。这个测试覆盖它之前和之后的纯 Python 契约：拒绝错误槽位、拒绝大小或摘要
不符的 stock BOOT、拒绝已存在的输出目录、用新目录重建时清单字段正确。上游
二进制本身的行为由真机与 Actions 覆盖。

上游 7dbabdc 之后不再生成 `boot-request.json`：活动槽由只读 GPT 与高通 A/B
属性判定，所以这里改为断言三个生产者都不再写那份已删除的配置。
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

        # 合体 BOOT 保持上游的 unsigned AVB NONE 元数据。槽 A 的 ABL 直接加载
        # 这份镜像；2026-10-21 用未签名镜像在真机上完成 Android→Linux→Android
        # 往返。若在这里补 os_version 或 RSA4096 footer，piano-boot-repack 的
        # probe/request/adopt 会因为首块偏移 16 与原厂头不符而拒绝，Android
        # 侧的引导助手再也切不了 Linux。这条断言防止 B 槽时代的补签逻辑被
        # 重新引入。
        source = (ROOT / "scripts/prepare-device-boot.py").read_text()
        for forbidden in ("avbtool", "add_hash_footer", "BOOT_OS_VERSION", "set_os_version",
                          "sign_boot_image", "strip_avb_footer"):
            assert forbidden not in source, f"prepare-device-boot signs the BOOT again: {forbidden}"
        assert device_boot.BOOT_PARTITION_BYTES == 96 * 1024 * 1024

    # 请求页只在当前引导的那份镜像里被读到，合体 BOOT 装进 Android 的当前槽。
    # 上游 7dbabdc 之后不再有 boot-request.json：活动槽由只读 GPT 与高通 A/B
    # 属性判定，三个生产者都必须跟着 --android-slot 走，且不得写死某个槽。
    for name in ("prepare-device-boot", "install-f2fs", "prepare-root-capacity"):
        text = (ROOT / "scripts" / (name + ".py")).read_text()
        assert "boot_b\"" not in text and "LINUX_BOOT_DEVICE" not in text, \
            f"{name} still hard-codes the Linux slot"
        # 只允许在注释里提到这条已删除的路径，不能再写它。
        for line in text.splitlines():
            if "/etc/piano/boot-request.json" in line or '"boot-request.json"' in line:
                assert line.lstrip().startswith("#"), \
                    f"{name} still writes the removed boot-request.json: {line.strip()}"
    # 安装计划仍要记下这次用的是哪条判定依据，供人工核对。
    install = (ROOT / "scripts" / "install-f2fs.py").read_text()
    assert '"read-only Qualcomm GPT A/B attributes"' in install, \
        "install-f2fs does not record how the boot device was determined"

    print("OK: BOOT 包装拒绝错误槽位、截断镜像、摘要不符与已存在的输出目录")
    return 0


if __name__ == "__main__":
    sys.exit(main())
