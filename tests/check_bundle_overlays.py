#!/usr/bin/env python3
"""合成出来的板级 DTB 上，每个上游 overlay 都留下了它该留的东西。

    check_bundle_overlays.py                   # 用内核替身合成一份再检查
    check_bundle_overlays.py --dtb board.dtb   # 检查已合成的产物（RPM 矩阵 job 用）

少叠一个 overlay 不会让构建失败，只会让对应的硬件在设备上不工作，所以只能在
这里固定住。
"""
import argparse
import importlib.util
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


def composed_with_fixture(directory):
    spec = importlib.util.spec_from_file_location('compose_board_dtb',
                                                  ROOT / 'scripts/compose-board-dtb.py')
    composer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(composer)
    kernel, source = make_kernel_source(Path(directory) / 'kernel-fixture', SUN)
    composer.compose(SUN, kernel, source, Path(directory) / 'out')
    return Path(directory) / 'out/board.dtb'


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dtb', type=Path, help='已合成的 board.dtb；省略时用内核替身合成')
    args = parser.parse_args()
    if not (SUN / 'tools/package_release.py').is_file():
        assert 'PIANO_TEST_SUNUEFI' not in os.environ, 'explicit upstream test source is missing'
        print('SKIP: bundle overlay integration needs PIANO_TEST_SUNUEFI')
        return
    sys.path.insert(0, str(SUN / 'tools'))
    from compose_piano_dtb import read_fdt
    with tempfile.TemporaryDirectory(prefix='bundle-overlays-') as directory:
        if args.dtb is None:
            assert all(shutil.which(name) for name in ('cpp', 'dtc', 'fdtoverlay', 'git')), (
                'missing overlay test tools')
        board = args.dtb or composed_with_fixture(directory)
        before = read_fdt((SUN / 'vendor/piano-linux/board.dtb').read_bytes())
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
        # 上游把笔的霍尔与充电器合并进 SC96231 节点，单独的 pen-dock 节点没有了。
        charger = tree['/soc/qcom,qupv3_2_geni_se@8c0000/i2c@894000/charger@38']
        assert charger['compatible'] == b'southchip,sc96231\0'
        tlmm = tree['/__symbols__']['piano_tlmm_ml'].rstrip(b'\0').decode()
        gpio = cells(charger['hall-gpios'])
        assert handles[gpio[0]] == handles[gpio[3]] == tlmm
        assert (gpio[1:3], gpio[4:6]) == ((200, 1), (196, 1))
        assert handles[cells(charger['pinctrl-0'])[0]] == tlmm + '/pen-charger-irqs-state'
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
        # 随 SunUEFI 61b6c592 一起进来的四个。
        gcc = cells(tree['/soc/clock-controller@100000']['clocks'])
        assert len(gcc) == 8 and handles.get(gcc[3]), 'GCC clocks[3] must reach the PCIe PHY'
        assert tree['/battery']['compatible'] == b'xiaomi,piano-battery\0'
        assert [handles[handle] for handle in cells(tree['/battery']['power-supplies'])] == [
            '/soc/qcom,qupv3_1_geni_se@ac0000/i2c@a94000/bq27z561_master@55',
            '/soc/qcom,qupv3_2_geni_se@8c0000/i2c@88c000/bq27z561_slave@55']
        camera = '/soc/cci-ml@ac7b000/i2c-bus@1/camera@10'
        assert cells(tree[camera]['assigned-clock-rates']) == (19200000,)
        assert struct.unpack('>Q', tree[camera + '/port/endpoint']['link-frequencies'])[0] == 857600000
        assert tree['/soc/interconnect@1780000']['compatible'] == b'qcom,sm8750-mmss-noc\0'
        # 两条路径各三段（提供者 + 两个单元），提供者按 MDSS 的父子关系解析。
        interconnects = cells(tree['/soc/display-subsystem@ae00000']['interconnects'])
        assert [handles[interconnects[index]] for index in (0, 3, 6, 9)] == [
            '/soc/interconnect@1780000', '/soc/interconnect@1',
            '/soc/interconnect@24100000', '/soc/interconnect@1600000']
        # SoC 温区：GPU 能当降频设备，overlay 点名的每个 CPU 温区都有被动触发点，
        # 并且绑到了一个能降频的 CPU 上。
        assert cells(tree['/soc/gpu@3d00000']['#cooling-cells']) == (2,)
        for zone in cpu_zones(SUN):
            node = '/thermal-zones/' + zone
            maps = [props for path, props in tree.items() if path.startswith(node + '/cooling-maps/')]
            assert len(maps) == 1, (zone, len(maps))
            trip = handles[cells(maps[0]['trip'])[0]]
            assert trip.startswith(node + '/trips/') and tree[trip]['type'] == b'passive\0', (zone, trip)
            cpu = handles[cells(maps[0]['cooling-device'])[0]]
            assert cpu.startswith('/cpus/cpu@') and '#cooling-cells' in tree[cpu], (zone, cpu)
        print('OK: every overlay in release.json left its mark: CMA, PCIe IRQs and clocks, Type-C, '
              'UFS MCQ, CDSP, fingerprint, touch link, pen GPIOs, battery, camera clock, '
              'display interconnects, CPU and GPU thermal cooling, and inherited phandles all hold')


if __name__ == '__main__':
    main()
