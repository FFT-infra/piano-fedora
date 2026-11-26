#!/usr/bin/env python3
"""kernel-piano 随包带的板级 DTB 必须与 bundle 写进 ESP 的那一份同源。

装机用 ESP 镜像里的 DTB，之后每次内核升级改用包里的那一份。两份由两个 job
各自合成，这里锁住：overlay 列表是同一份；合成产物确实叠上了 overlay；核对
步骤不放过裸的 vendor DTB。逐字节相等由 build-bundle 的门在真实产物上比对。
"""
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUN = Path(os.environ.get('PIANO_TEST_SUNUEFI', ROOT / 'build/test-sources/sunuefi'))


def load_composer():
    spec = importlib.util.spec_from_file_location('compose_board_dtb',
                                                  ROOT / 'scripts/compose-board-dtb.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rejected(composer, directory):
    try:
        composer.verify(directory)
    except ValueError:
        return True
    return False


def main():
    composer = load_composer()
    if not (SUN / 'tools/package_release.py').is_file():
        assert 'PIANO_TEST_SUNUEFI' not in os.environ, 'explicit upstream test source is missing'
        print('SKIP: composing the board DTB needs PIANO_TEST_SUNUEFI')
        return
    assert all(shutil.which(name) for name in ('cpp', 'dtc', 'fdtoverlay')), 'missing overlay test tools'
    cpu_model, overlays = composer.shipped_overlays(SUN)
    workflow = (ROOT / '.github/workflows/build-bundle.yml').read_text()
    prefix = 'build/sources/sunuefi/'
    assert re.findall(r'--cpu-model-overlay\s+(\S+)', workflow) == [prefix + cpu_model]
    assert re.findall(r'--dtb-overlay\s+(\S+)', workflow) == [prefix + name for name in overlays], (
        'the composer and build-bundle apply different overlay lists')
    assert '--panel-vendor auto' in workflow, 'build-bundle no longer leaves the panel to the firmware'

    with tempfile.TemporaryDirectory(prefix='board-dtb-') as directory:
        output = Path(directory) / 'out'
        manifest = composer.compose(SUN, output)
        assert sorted(path.name for path in output.iterdir()) == ['board-dtb.json', 'board.dtb']
        assert composer.verify(output) == manifest
        assert [row['path'] for row in manifest['overlays']] == [cpu_model, *overlays]
        vendor = SUN / 'vendor/piano-linux/board.dtb'
        assert manifest['base']['sha256'] == composer.digest(vendor) != manifest['sha256']

        sys.path.insert(0, str(SUN / 'tools'))
        from compose_piano_dtb import read_fdt
        tree = read_fdt((output / 'board.dtb').read_bytes())['tree']
        # 这两处是裸 vendor DTB 起不来的原因，合成产物必须带上。
        assert tree['/reserved-memory/linux,cma']['size'] == struct.pack('>II', 0, 0x10000000)
        assert tree['/soc/ufshc@1d84000']['reg-names'] == b'std\0mcq\0'

        raw = Path(directory) / 'raw'
        shutil.copytree(output, raw)
        shutil.copyfile(vendor, raw / 'board.dtb')
        assert rejected(composer, raw), 'verify accepts the raw vendor DTB under a composed manifest'
        # 摘要与长度都对得上、但不是合成器写的 manifest，同样不认。
        vendor_bytes = vendor.read_bytes()
        (raw / 'board-dtb.json').write_text(json.dumps(
            {'sha256': composer.digest(vendor), 'bytes': len(vendor_bytes)}))
        assert rejected(composer, raw), 'verify accepts a manifest the composer did not write'
        truncated = Path(directory) / 'truncated'
        shutil.copytree(output, truncated)
        data = (output / 'board.dtb').read_bytes()[:-8]
        (truncated / 'board.dtb').write_bytes(data)
        (truncated / 'board-dtb.json').write_text(json.dumps(
            {**manifest, 'sha256': composer.digest(truncated / 'board.dtb'), 'bytes': len(data)}))
        assert rejected(composer, truncated), 'verify accepts a truncated device tree'
    print('OK: the packaged board DTB is composed from the same overlay list as the ESP image, '
          'carries CMA and UFS MCQ, and the raw vendor DTB is rejected')


if __name__ == '__main__':
    main()
