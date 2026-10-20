#!/usr/bin/env python3
"""校验 boot-tools 适配层把上游 selector 输出当作一次性目录。

上游 `package_product_trampoline.selector_only()` 要求目标目录尚不存在；
适配层曾用 `TemporaryDirectory` 先创建目录再传进去，prepare-install 在
2026-10-20 的链上因此失败。这里用三个最小的上游模块桩锁住这个契约。
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

STUBS = {
    "build_android_module.py": """
def product_payload(product):
    return {"app.bin": b"SUNUEFI-APPv1\\0" + b"\\0" * 64}, {"product": "fake"}


def check_selector_layout(selector, memory, offset):
    assert selector and memory and offset
""",
    "build_boot_repack.py": """
from pathlib import Path


def build(native_dir, arch, cc, sysroot):
    native_dir = Path(native_dir)
    native_dir.mkdir(parents=True)
    binary = native_dir / "piano-boot-repack"
    binary.write_bytes(b"BIN")
    return binary, {"executable": str(binary)}


def prototype_policy(binary, native, selector_dir, product, app, native_dir):
    selector_dir = Path(selector_dir)
    assert (selector_dir / "selector.json").is_file(), "selector work dir was removed too early"
    assert Path(app).is_file(), "APPv1 payload was not written"
    (Path(native_dir) / "selector-module.json").write_text('{"ok": true}')
""",
    "package_product_trampoline.py": """
from pathlib import Path


def selector_only(output):
    output = Path(output)
    if output.exists():
        raise ValueError("Output must be a fresh directory")
    output.mkdir(parents=True)
    (output / "selector.bin").write_bytes(b"SEL" * 16)
    (output / "selector.json").write_text("{}")
    return {"selector_memory_bytes": 8192, "metadata_offset": 16}
""",
}

FAILING = """
from pathlib import Path


def selector_only(output):
    output = Path(output)
    if output.exists():
        raise ValueError("Output must be a fresh directory")
    raise ValueError("selector build failed")
"""


def fake_sun(root, selector=STUBS["package_product_trampoline.py"]):
    tools = root / "tools"
    tools.mkdir(parents=True)
    for name, body in STUBS.items():
        (tools / name).write_text(body)
    (tools / "package_product_trampoline.py").write_text(selector)
    return root


def main():
    spec = importlib.util.spec_from_file_location("boot_tools", ROOT / "scripts/build-boot-tools.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix="boot-tools-") as directory:
        base = Path(directory)
        product = base / "product"
        product.mkdir()
        output = base / "boot-tools"
        result = module.build(fake_sun(base / "sunuefi"), product, output)
        assert (output / "selector.bin").is_file()
        assert (output / "native/piano-boot-repack").is_file()
        assert (output / "native/selector-module.json").is_file()
        assert not (output / ".selector").exists(), "selector work directory leaked into the artifact"
        assert not (output / ".incomplete").exists()
        assert result["selector"] == {"ok": True}
        # 断言失败路径也清理标记与工作目录；重新导入以换用失败桩。
        for name in ("build_android_module", "build_boot_repack", "package_product_trampoline"):
            sys.modules.pop(name, None)
        bad_output = base / "boot-tools-bad"
        try:
            module.build(fake_sun(base / "sunuefi-bad", FAILING), product, bad_output)
        except ValueError as error:
            assert "selector build failed" in str(error)
        else:
            raise AssertionError("selector build failure was ignored")
        assert not (bad_output / ".selector").exists()
        assert not (bad_output / ".incomplete").exists()
    print("OK: boot-tools adapter keeps selector output fresh and cleans it up on success and failure")


if __name__ == "__main__":
    main()
