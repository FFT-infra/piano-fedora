#!/usr/bin/env python3
"""用完整上游覆盖层链验证触屏、笔座和既有 phandle 引用。"""
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SUN = Path(os.environ.get('PIANO_TEST_SUNUEFI', ROOT / 'build/test-sources/sunuefi'))


def main():
    if not (SUN / 'tools/package_release.py').is_file():
        assert 'PIANO_TEST_SUNUEFI' not in os.environ, 'explicit upstream test source is missing'
        print('SKIP: bundle overlay integration needs PIANO_TEST_SUNUEFI')
        return
    assert all(shutil.which(name) for name in ('cpp', 'dtc', 'fdtoverlay')), 'missing overlay test tools'
    sys.path.insert(0, str(SUN / 'tools'))
    import package_release as package
    from compose_piano_dtb import read_fdt
    with tempfile.TemporaryDirectory(prefix='bundle-overlays-') as directory:
        work = Path(directory)
        board = work / 'board.dtb'
        shutil.copyfile(SUN / 'vendor/piano-linux/board.dtb', board)
        before = read_fdt(board.read_bytes())
        overlays = ('cpu-model', 'board-identity', 'regdma-resource', 'audio-dmic-clock',
                    'camera-flash', 'camera-privacy-led', 'touch-panel', 'pen-dock',
                    'pen-charger')
        for name in overlays:
            print('APPLY:', name, flush=True)
            package.apply_cpu_model_overlay(board, SUN / f'linux/dts/piano-{name}.dtso',
                                            work, cpu_only=name == 'cpu-model')
        after = read_fdt(board.read_bytes())
        tree, handles = after['tree'], after['phandles']
        assert after['reservations'] == before['reservations']
        assert after['boot_cpu'] == before['boot_cpu']
        for handle, path in before['phandles'].items():
            assert handles.get(handle) == path, f'existing phandle changed: {path}'
        cells = lambda value: struct.unpack('>' + 'I' * (len(value) // 4), value)
        panel = '/soc/display-subsystem@ae00000/dsi@ae94000/panel@0'
        touch = '/soc/qcom,qupv3_1_geni_se@ac0000/spi@a88000/touchscreen@0'
        assert handles[cells(tree[touch]['panel'])[0]] == panel
        dock = tree['/soc/pen-dock']
        assert dock['compatible'] == b'xiaomi,piano-pen-dock\0'
        tlmm = tree['/__symbols__']['piano_tlmm_ml'].rstrip(b'\0').decode()
        gpio = cells(dock['hall-gpios'])
        assert handles[gpio[0]] == handles[gpio[3]] == tlmm
        assert (gpio[1:3], gpio[4:6]) == ((200, 1), (196, 1))
        assert handles[cells(dock['pinctrl-0'])[0]] == tlmm + '/pen-dock-default-state'
        print('OK: nine overlays, touch link, pen GPIOs/pinctrl and all inherited phandles')


if __name__ == '__main__':
    main()
