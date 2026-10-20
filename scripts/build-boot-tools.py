#!/usr/bin/env python3
"""用上游固定入口产出安装侧 boot-tools：selector、ARM64 重打包工具与 selector 清单。

上游删掉了 `prepare_android_module.py`，同一份逻辑留在两个入口里：
`package_product_trampoline.py --selector-only` 出 selector.bin，
`build_boot_repack.py` 的 `build()` 出静态 ARM64 piano-boot-repack。
这里只做串联和字段拼装，编译、布局断言和 APPv1 校验都留在上游模块内。
"""

import argparse
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(sun):
    sys.path.insert(0, str(sun / 'tools'))
    import build_android_module as module
    import build_boot_repack as repack
    import package_product_trampoline as trampoline
    return module, repack, trampoline


def build(sun, product, output, cc=None, sysroot=None):
    sun, product, output = sun.resolve(), product.resolve(), output.resolve()
    for name in ('tools/build_boot_repack.py', 'tools/package_product_trampoline.py'):
        if not (sun / name).is_file():
            raise ValueError('SunUEFI checkout lacks ' + name + ': ' + str(sun))
    if output.exists() or output.is_symlink():
        raise ValueError('Use a fresh boot-tools directory: ' + str(output))
    module, repack, trampoline = load(sun)
    # product_payload 同时校验 manifest、Android 头、APPv1 摘要和压缩核。
    payloads, product_record = module.product_payload(product)
    output.mkdir(parents=True)
    marker = output / '.incomplete'
    marker.write_text('boot-tools 尚未完成。\n')
    try:
        with tempfile.TemporaryDirectory(prefix='selector-', dir=output) as directory:
            selector_dir = Path(directory)
            selector_meta = trampoline.selector_only(selector_dir)
            selector = (selector_dir / 'selector.bin').read_bytes()
            module.check_selector_layout(selector, selector_meta['selector_memory_bytes'],
                                         selector_meta['metadata_offset'])
            (output / 'selector.bin').write_bytes(selector)
            native_dir = output / 'native'
            binary, native = repack.build(native_dir, 'aarch64', cc, sysroot)
            # prototype_policy 用同一批 payload 补上 product_fd_sha256 与
            # app_payload_sha256：安装器靠这两个摘要证明 selector 属于这张产品镜像。
            app = output / 'app.bin'
            app.write_bytes(payloads['app.bin'])
            try:
                repack.prototype_policy(binary, native, selector_dir, product, app, native_dir)
            finally:
                app.unlink()
            selector_module = json.loads((native_dir / 'selector-module.json').read_text())
    except Exception:
        marker.unlink(missing_ok=True)
        raise
    marker.unlink()
    return {'product': product_record, 'selector': selector_module,
            'native': native['executable'], 'output': str(output)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--sunuefi', type=Path, required=True)
    ap.add_argument('--product', type=Path, required=True)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--cc')
    ap.add_argument('--sysroot', type=Path)
    args = ap.parse_args()
    print(json.dumps(build(args.sunuefi, args.product, args.output, args.cc, args.sysroot),
                     indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
