#!/usr/bin/env python3
"""kernel-piano 随包带的板级 DTB：合成器的契约，与 bundle 原样取用它的方式。

板级 DTB 只在 RPM 矩阵 job 里合成一次，build-bundle 把包里那一份原样写进 ESP。
这里锁住：上游 release.json 的 overlay 一个不少；SoC 温区来自与内核同一个提交的
源码；核对步骤不放过裸的 vendor DTB 和没带温区的旧产物。逐字节相等由
build-bundle 的门在真实产物上比对。
"""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUN = Path(os.environ.get('PIANO_TEST_SUNUEFI', ROOT / 'build/test-sources/sunuefi'))
sys.path.insert(0, str(ROOT / 'tests'))
from board_dtb_fixture import cpu_zones, make_kernel_source  # noqa: E402


def load_composer():
    spec = importlib.util.spec_from_file_location('compose_board_dtb',
                                                  ROOT / 'scripts/compose-board-dtb.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rejected(action, *args):
    try:
        action(*args)
    except ValueError:
        return True
    return False


def check_bundle_uses_the_packaged_dtb():
    workflow = (ROOT / '.github/workflows/build-bundle.yml').read_text()
    assert '--dtb-overlay' not in workflow and '--cpu-model-overlay' not in workflow, (
        'build-bundle composes a second board DTB instead of using the packaged one')
    assert 'compose-board-dtb.py --verify build/board-dtb' in workflow, (
        'build-bundle does not check the packaged board DTB against its manifest')
    assert '--dtb build/board-dtb/board.dtb --dtb-sha256 "$dtb_sha256"' in workflow, (
        'build-bundle does not hand the verified board DTB to the packager')
    assert '--panel-vendor auto' in workflow, 'build-bundle no longer leaves the panel to the firmware'


def main():
    composer = load_composer()
    check_bundle_uses_the_packaged_dtb()
    if not (SUN / 'tools/package_release.py').is_file():
        assert 'PIANO_TEST_SUNUEFI' not in os.environ, 'explicit upstream test source is missing'
        print('SKIP: composing the board DTB needs PIANO_TEST_SUNUEFI')
        return
    assert all(shutil.which(name) for name in ('cpp', 'dtc', 'fdtoverlay', 'git')), 'missing overlay test tools'
    cpu_model, overlays = composer.release_overlays(SUN)
    assert 'linux/dts/piano-tsens.dtso' in overlays, 'upstream no longer lists the SoC thermal overlay'

    with tempfile.TemporaryDirectory(prefix='board-dtb-') as directory:
        directory = Path(directory)
        kernel, source = make_kernel_source(directory / 'kernel-fixture', SUN)
        output = directory / 'out'
        manifest = composer.compose(SUN, kernel, source, output)
        assert sorted(path.name for path in output.iterdir()) == ['board-dtb.json', 'board.dtb']
        assert composer.verify(output) == manifest
        assert [row['path'] for row in manifest['overlays']] == [cpu_model, *overlays]
        vendor = SUN / 'vendor/piano-linux/board.dtb'
        assert manifest['base']['sha256'] == composer.digest(vendor) != manifest['sha256']
        recorded = json.loads((kernel / 'manifest.json').read_text())['source_commit']
        assert manifest['kernel_source']['kernel_commit'] == recorded
        assert manifest['thermal']['cooling_maps'] == len(cpu_zones(SUN)) + 1, manifest['thermal']

        sys.path.insert(0, str(SUN / 'tools'))
        from compose_piano_dtb import read_fdt
        tree = read_fdt((output / 'board.dtb').read_bytes())['tree']
        # 这两处是裸 vendor DTB 起不来的原因，合成产物必须带上。
        assert tree['/reserved-memory/linux,cma']['size'] == struct.pack('>II', 0, 0x10000000)
        assert tree['/soc/ufshc@1d84000']['reg-names'] == b'std\0mcq\0'

        # 源码树不在内核产物记录的提交上：温区会来自另一个内核，不合成。
        other, other_source = make_kernel_source(directory / 'other-commit', SUN, commit='0' * 40)
        assert rejected(composer.compose, SUN, other, other_source, directory / 'other-out'), (
            'compose accepts a kernel source that is not at the kernel artifact commit')
        # 内核换了温区名字：overlay 会建出没有传感器的空温区，合成必须失败。
        renamed, renamed_source = make_kernel_source(directory / 'renamed-zone', SUN,
                                                     zones=cpu_zones(SUN)[1:])
        assert rejected(composer.compose, SUN, renamed, renamed_source, directory / 'renamed-out'), (
            'compose accepts a thermal zone without a sensor')

        raw = directory / 'raw'
        shutil.copytree(output, raw)
        shutil.copyfile(vendor, raw / 'board.dtb')
        assert rejected(composer.verify, raw), 'verify accepts the raw vendor DTB under a composed manifest'
        # 摘要与长度都对得上、但不是合成器写的 manifest，同样不认。
        vendor_bytes = vendor.read_bytes()
        (raw / 'board-dtb.json').write_text(json.dumps(
            {'sha256': composer.digest(vendor), 'bytes': len(vendor_bytes)}))
        assert rejected(composer.verify, raw), 'verify accepts a manifest the composer did not write'
        # 没记内核源码的 manifest 是没带 SoC 温区的旧合成产物。
        old = directory / 'no-kernel-source'
        shutil.copytree(output, old)
        (old / 'board-dtb.json').write_text(json.dumps(
            {key: value for key, value in manifest.items() if key != 'kernel_source'}))
        assert rejected(composer.verify, old), 'verify accepts a tree composed without the kernel source'
        truncated = directory / 'truncated'
        shutil.copytree(output, truncated)
        data = (output / 'board.dtb').read_bytes()[:-8]
        (truncated / 'board.dtb').write_bytes(data)
        (truncated / 'board-dtb.json').write_text(json.dumps(
            {**manifest, 'sha256': composer.digest(truncated / 'board.dtb'), 'bytes': len(data)}))
        assert rejected(composer.verify, truncated), 'verify accepts a truncated device tree'
    print('OK: the packaged board DTB carries every release.json overlay and thermal zones from the '
          "kernel's own source; raw, zone-less and truncated trees are rejected")


if __name__ == '__main__':
    main()
