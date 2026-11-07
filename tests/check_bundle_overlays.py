#!/usr/bin/env python3
"""用完整上游覆盖层链验证触屏、笔座和既有 phandle 引用。"""
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


def main():
    if not (SUN / 'tools/package_release.py').is_file():
        assert 'PIANO_TEST_SUNUEFI' not in os.environ, 'explicit upstream test source is missing'
        print('SKIP: bundle overlay integration needs PIANO_TEST_SUNUEFI')
        return
    assert all(shutil.which(name) for name in ('cpp', 'dtc', 'fdtoverlay')), 'missing overlay test tools'
    # workflow 手写的 overlay 列表必须和上游 release.json 一致。少传一个不会报错，
    # 只会在设备上少一个功能；这条断言是唯一能提前发现它的地方。
    kernel = json.loads((SUN / 'config/release.json').read_text())['kernel']
    workflow = (ROOT / '.github/workflows/build-bundle.yml').read_text()
    passed = re.findall(r'--dtb-overlay\s+(\S+)', workflow)
    expected = ['build/sources/sunuefi/' + name for name in kernel['additional_dtb_overlays']]
    assert passed == expected, f'workflow overlays differ from release.json: {passed}'
    cpu = re.findall(r'--cpu-model-overlay\s+(\S+)', workflow)
    assert cpu == ['build/sources/sunuefi/' + kernel['cpu_model_overlay']], cpu
    sys.path.insert(0, str(SUN / 'tools'))
    import package_release as package
    from compose_piano_dtb import read_fdt
    with tempfile.TemporaryDirectory(prefix='bundle-overlays-') as directory:
        work = Path(directory)
        board = work / 'board.dtb'
        shutil.copyfile(SUN / 'vendor/piano-linux/board.dtb', board)
        before = read_fdt(board.read_bytes())
        overlays = ('cpu-model', 'cma', 'pcie-interrupt-map', 'usb-typec', 'ufs-mcq',
                    'dsp-resources', 'cdsp-fastrpc', 'fingerprint', 'board-identity',
                    'regdma-resource', 'audio-dmic-clock', 'camera-flash',
                    'camera-privacy-led', 'touch-panel', 'pen-dock', 'pen-charger')
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
        # 补齐的七个 overlay 在最终 DT 上各自留下的痕迹。少传一个不会让构建失败，
        # 只会让对应的硬件在设备上不工作，所以只能在这里固定住。
        assert tree['/reserved-memory/linux,cma']['size'] == struct.pack('>II', 0, 0x10000000)
        interrupts = cells(tree['/soc/pcie@1c00000']['interrupt-map'])
        assert [interrupts[index] for index in (7, 16, 25, 34)] == [149, 150, 151, 152]
        assert tree['/soc/qcom,pmic_glink']['compatible'].startswith(b'qcom,sm8750-pmic-glink\0')
        assert tree['/soc/ufshc@1d84000']['reg-names'] == b'std\0mcq\0'
        assert tree['/soc/remoteproc-cdsp@32300000']['power-domains']
        fastrpc = tree['/soc/remoteproc-cdsp@32300000/glink-edge/qcom,fastrpc']
        assert 'qcom,non-secure-domain' in fastrpc
        assert tree['/soc/xiaomi_fingerprint']['compatible'] == b'xiaomi,piano-goodix-control\0'
        assert tree['/soc/rsc@16500000/regulators-4/ldo9']['regulator-name'] == b'piano_fp_vdd\0'
        print('OK: sixteen overlays match release.json; CMA, PCIe IRQs, Type-C, UFS MCQ, '
              'CDSP, fingerprint, touch link, pen GPIOs and inherited phandles all hold')


if __name__ == '__main__':
    main()
