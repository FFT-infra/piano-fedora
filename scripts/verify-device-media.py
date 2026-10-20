#!/usr/bin/env python3
"""在设备上验证 Iris 多 GOP 编码与 B 帧解码，保留逐帧软件对照和日志。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


def interrupts():
    rows = [line for line in Path('/proc/interrupts').read_text().splitlines()
            if 'iris' in line.lower()]
    if not rows:
        raise RuntimeError('Iris interrupt entry unavailable')
    total = 0
    for row in rows:
        for word in row.split(':', 1)[1].split():
            if not word.isdigit():
                break
            total += int(word)
    return total


def checksums(path):
    return [line.split(',')[-1].strip() for line in path.read_text().splitlines()
            if line.strip() and not line.startswith('#')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='新的证据目录，不覆盖旧结果')
    parser.add_argument('--device', default='/dev/dri/renderD128')
    parser.add_argument('--fixture-dir', type=Path,
                        help='预生成的 B 帧素材目录；设备缺少 libx264/libx265 时使用')
    parser.add_argument('--fixture-types', type=Path,
                        help='fixture 的帧类型清单（设备 ffprobe 无法识别 pict_type 时使用）')
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, 'LIBVA_DRIVER_NAME': 'v4l2'}
    report = {'kernel': platform.release(), 'driver': 'v4l2', 'tests': [], 'passed': False}
    ffmpeg = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'info', '-xerror', '-y']

    def run(name, command):
        report['current_stage'] = name
        (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        with (root / (name + '.log')).open('w') as log:
            log.write('command: ' + json.dumps(command) + '\n')
            log.flush()
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                           check=True, timeout=180, env=env)

    def inspect(name, encoded):
        run(name + '-probe', ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                             '-show_frames', '-show_entries', 'frame=key_frame,pict_type',
                             '-of', 'json', str(encoded)])
        text = (root / (name + '-probe.log')).read_text().split('\n', 1)[1]
        return json.loads(text)['frames']

    def decode(name, encoded, decoder):
        software, hardware = root / (name + '-sw.md5'), root / (name + '-hw.md5')
        run(name + '-software', [*ffmpeg, '-i', str(encoded), '-pix_fmt', 'nv12',
                                '-f', 'framemd5', str(software)])
        before = interrupts()
        # Fedora 的 VAAPI 解码由 v4l2m2m 包装器落到 Iris；它输出系统内存 NV12，
        # 不存在 hwdownload 滤镜链，必须显式指定解码器。
        run(name + '-hardware', [*ffmpeg, '-c:v', decoder,
                                '-i', str(encoded), '-pix_fmt', 'nv12',
                                '-f', 'framemd5', str(hardware)])
        after = interrupts()
        reference, actual = checksums(software), checksums(hardware)
        if len(reference) != 300 or len(actual) != 300:
            raise RuntimeError(f'{name}: frame count {len(reference)}/{len(actual)}, expected 300')
        if reference != actual:
            raise RuntimeError(f'{name}: decoded pixels differ from software reference')
        if after <= before:
            raise RuntimeError(f'{name}: no Iris interrupts during decoding')
        return {'frames': 300, 'all_frame_checksums_match': True,
                'iris_interrupts': after - before,
                'encoded_sha256': hashlib.sha256(encoded.read_bytes()).hexdigest()}

    try:
        # vainfo 只打印驱动能力清单，不是编解码链路的一环。目标镜像没有装
        # libva-utils，硬把这一步当门槛会让整份验收在 `vainfo: No such file`
        # 上直接失败，而下面的 h264_vaapi/hevc_vaapi 编码本身是好的。
        # 有 vainfo 就留一份能力记录，没有就跳过并记明原因。
        if shutil.which('vainfo'):
            run('vainfo', ['vainfo', '--display', 'drm', '--device', args.device])
        else:
            report['vainfo'] = 'skipped: libva-utils is not installed in the image'
        for codec, software_encoder in (('h264', 'libx264'), ('hevc', 'libx265')):
            hardware_decoder = codec + '_v4l2m2m'
            name = codec + '-vaapi-gop60'
            encoded = root / (name + '.mp4')
            before = interrupts()
            run(name + '-encode', [*ffmpeg, '-vaapi_device', args.device, '-f', 'lavfi',
                                  '-i', 'testsrc2=size=1920x1080:rate=30',
                                  '-vf', 'format=nv12,hwupload', '-c:v', codec + '_vaapi',
                                  '-bf', '0', '-g', '60', '-frames:v', '300', str(encoded)])
            encode_interrupts = interrupts() - before
            frames = inspect(name, encoded)
            keyframes = [i for i, f in enumerate(frames) if f['key_frame']]
            if len(frames) != 300 or keyframes != [0, 60, 120, 180, 240]:
                raise RuntimeError(f'{name}: frames={len(frames)}, keyframes={keyframes}')
            if encode_interrupts <= 0:
                raise RuntimeError(name + ': no Iris interrupts during encoding')
            report['tests'].append({'name': name, 'keyframes': keyframes,
                                    'encode_iris_interrupts': encode_interrupts,
                                    **decode(name, encoded, hardware_decoder)})

            name = codec + '-bframes3'
            encoded = root / (name + '.mp4')
            if args.fixture_dir:
                source = args.fixture_dir.resolve() / (name + '.mp4')
                if not source.is_file():
                    raise RuntimeError(f'{name}: fixture missing at {source}')
                shutil.copyfile(source, encoded)
                types = None
                if args.fixture_types:
                    types = json.loads((args.fixture_types.resolve() /
                                        (name + '-types.json')).read_text())
            else:
                extra = ['-x265-params', 'pools=2:frame-threads=2'] if codec == 'hevc' else []
                run(name + '-generate', [*ffmpeg, '-f', 'lavfi', '-i',
                                        'testsrc2=size=1920x1080:rate=30', '-c:v', software_encoder,
                                        '-preset', 'fast', '-bf', '3', '-g', '60', *extra,
                                        '-frames:v', '300', str(encoded)])
                types = None
            frames = inspect(name, encoded)
            if types is not None:
                if len(frames) != len(types):
                    raise RuntimeError(f'{name}: probe/type-list frame count differs')
                frames = [{'key_frame': f['key_frame'], 'pict_type': t}
                          for f, t in zip(frames, types)]
            bframes = sum(f['pict_type'] == 'B' for f in frames)
            if not bframes:
                raise RuntimeError(name + ': fixture has no B frames')
            report['tests'].append({'name': name, 'bframes': bframes,
                                    **decode(name, encoded, hardware_decoder)})
        report['passed'] = True
        report.pop('current_stage', None)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        report['error'] = str(exc)
    (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
