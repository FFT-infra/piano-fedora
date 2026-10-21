#!/usr/bin/env python3
"""在设备上验证浏览器是否真的把 H.264 交给 Iris 解码。

判定只看两样东西，都不依赖浏览器自报：

  1. iris 中断计数在播放窗口内增长；
  2. 某个浏览器进程打开了 /dev/video0。

两者都不成立就是软件解码，无论 about:gpu 之类页面怎么写。

为什么需要这个脚本：Fedora 的 Chromium 链接系统 libavcodec，而 Fedora 的
ffmpeg-free 在构建时用 --disable-decoder='h264,hevc,vc1,vvc' 去掉了软件
H.264 解码器。结果是 Chromium 连 MP4 都打不开（FFmpegDemuxer: open context
failed），播放窗口里没有任何解码，iris 计数当然不动。装上 RPM Fusion 的
libavcodec-freeworld 之后能播了，但仍然走软件解码。Chrome 自带 FFmpeg 和
V4L2 解码路径，Firefox 有自己的 VA-API 路径，两者默认就硬解。

所以本脚本把"能播"和"硬解"分开报：前者看 media error，后者看中断和 fd。
"""

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


PLAYER = """<!doctype html><meta charset="utf-8"><title>probe</title>
<video id=v autoplay muted loop playsinline></video>
<script>
const v = document.getElementById('v');
v.src = %(source)s;
let frames = 0;
function tick() { frames += 1; v.requestVideoFrameCallback(tick); }
v.requestVideoFrameCallback(tick);
setInterval(() => {
  document.title = 'frames=' + frames + ' t=' + v.currentTime.toFixed(2) +
    ' ready=' + v.readyState + ' error=' + (v.error ? v.error.code : 'none');
}, 250);
</script>
"""

BROWSERS = {
    'chromium': ['chromium-browser'],
    'chrome': ['google-chrome-stable'],
    'firefox': ['firefox'],
}


def iris_interrupts():
    total = 0
    for line in Path('/proc/interrupts').read_text().splitlines():
        if 'iris' in line.lower():
            for word in line.split(':', 1)[1].split():
                if not word.isdigit():
                    break
                total += int(word)
    return total


def video_holders():
    """返回持有 /dev/video0 的 (pid, comm)，不含检查脚本自己。"""
    holders, mine = [], {str(os.getpid())}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit() or entry.name in mine:
            continue
        try:
            for fd in (entry / 'fd').iterdir():
                try:
                    if os.readlink(fd) == '/dev/video0':
                        holders.append((entry.name,
                                        (entry / 'comm').read_text().strip()))
                        break
                except OSError:
                    continue
        except OSError:
            continue
    return holders


def hardware_decoded(iris_interrupts, holders):
    """硬解要同时满足：中断有增长，且确实有进程打开了 video 节点。

    只满足一个都不算。软件解码同样会拉高 GPU 中断，光看中断会把软解判成
    硬解；反过来，进程碰过 /dev/video0 也可能只是探测能力。
    """
    return iris_interrupts > 0 and bool(holders)


def which(name):
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        candidate = Path(directory) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def read_title(port):
    """从 DevTools 端点读页面标题。标题里带着播放进度和 media error。"""
    import urllib.request
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=2) as reply:
            for tab in json.load(reply):
                if tab.get('type') == 'page' and tab.get('url', '').endswith('probe.html'):
                    return tab.get('title', '')
    except Exception:
        return None
    return None


def probe(command, source, seconds, user_data):
    """启动一个浏览器，播放 source，返回观测结果。"""
    user_data.mkdir(parents=True, exist_ok=True)
    if command[0].endswith('firefox'):
        # Firefox 不认 --user-data-dir，也不接受 --ozone-platform；
        # 它需要独立 profile 才能和已经在跑的实例并存。
        argv = [*command, '--new-instance', f'--profile={user_data}',
                '--no-remote', str(source)]
    else:
        argv = [*command, '--no-sandbox', f'--user-data-dir={user_data}',
                '--ozone-platform=wayland',
                '--autoplay-policy=no-user-gesture-required',
                '--remote-debugging-port=0', '--no-first-run', str(source)]
    log = user_data / 'browser.log'
    before = iris_interrupts()
    with log.open('w') as handle:
        process = subprocess.Popen(argv, stdout=handle, stderr=subprocess.STDOUT,
                                   start_new_session=True)
    deadline = time.time() + seconds
    holders, title = [], None
    while time.time() < deadline:
        holders = holders or video_holders()
        if not command[0].endswith('firefox'):
            port_file = user_data / 'DevToolsActivePort'
            if port_file.is_file():
                # 标题是页面每 250ms 刷一次的进度行。取到非空就留最后一次，
                # 因为刚打开时标题还是 HTML 里那个 "probe"。
                current = read_title(port_file.read_text().split('\n')[0])
                if current and current != 'probe':
                    title = current
        time.sleep(0.5)
    after = iris_interrupts()
    holders = video_holders()
    # 按进程组终止。用 pkill -f <profile 路径> 会连检查脚本自己一起匹配上，
    # 因为脚本的命令行里也有同一个路径。
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
    return {
        'command': argv,
        'iris_interrupts': after - before,
        'video_holders': holders,
        'page_title': title,
        'log': str(log),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True, help='新的证据目录')
    parser.add_argument('--video', type=Path, default=Path('/tmp/browser-h264.mp4'))
    parser.add_argument('--seconds', type=int, default=14)
    parser.add_argument('--browsers', default='chromium,chrome,firefox')
    args = parser.parse_args()

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    if not args.video.is_file():
        print(f'missing video: {args.video}', file=sys.stderr)
        return 2

    page = root / 'probe.html'
    page.write_text(PLAYER % {'source': json.dumps(args.video.as_uri())})
    report = {'video': str(args.video), 'seconds': args.seconds, 'browsers': {}}

    for name in [item.strip() for item in args.browsers.split(',') if item.strip()]:
        command = BROWSERS.get(name)
        if command is None:
            report['browsers'][name] = {'skipped': 'unknown browser'}
            continue
        binary = which(command[0])
        if binary is None:
            report['browsers'][name] = {'skipped': f'{command[0]} is not installed'}
            continue
        result = probe([binary], page, args.seconds, root / name)
        result['hardware_decoded'] = hardware_decoded(result['iris_interrupts'],
                                                      result['video_holders'])
        report['browsers'][name] = result

    (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
