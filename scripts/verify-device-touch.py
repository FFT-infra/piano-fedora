#!/usr/bin/env python3
"""在设备上做持续触控取证：速率、覆盖范围、多点与中断一致性。

判定要点：

- 速率用 SYN 报文数，不用 ABS 事件数。协议 B 的多点触控会把每个手指的
  坐标各自发一遍，ABS 计数随手指数翻倍，不能当作采样率。
- 中断只看 NVT-ts-spi 这一条。它是触控 IC 的硬件中断，事件速率与它同期
  增长才说明整条链路在出数据，而不只是内核在合成事件。
- 相邻坐标跳变要看分位数而不是最大值。多指交替时 slot 切换会让坐标从
  一根手指跳到另一根，这是正常行为，不是丢帧。

脚本会先等第一次真实触摸再开始计时，避免把等待时间算进统计。
"""

import argparse
import collections
import json
import os
from pathlib import Path
import select
import struct
import sys
import time

FMT = 'llHHI'
SIZE = struct.calcsize(FMT)
EV_SYN, EV_KEY, EV_ABS = 0, 1, 3

ABS = {0x00: 'X', 0x01: 'Y', 0x18: 'PRESSURE', 0x2f: 'SLOT',
       0x30: 'TOUCH_MAJOR', 0x35: 'POSITION_X', 0x36: 'POSITION_Y',
       0x39: 'TRACKING_ID', 0x3a: 'PRESSURE_MT'}
BTN = {0x14a: 'BTN_TOUCH', 0x145: 'BTN_TOOL_FINGER', 0x146: 'BTN_TOOL_PEN'}
IRQ_NAME = 'NVT-ts-spi'


def touch_interrupts():
    for line in Path('/proc/interrupts').read_text().splitlines():
        if IRQ_NAME in line:
            total = 0
            for word in line.split(':', 1)[1].split():
                if not word.isdigit():
                    break
                total += int(word)
            return total
    return None


def wait_for_touch(fd, timeout):
    """等第一个事件。返回是否等到。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if not ready:
            continue
        try:
            if os.read(fd, SIZE * 64):
                return True
        except BlockingIOError:
            continue
    return False


def summarise(xs, ys, presses, slots, syn, per_second, elapsed, irq_delta):
    jumps = sorted([abs(b - a) for a, b in zip(xs, xs[1:])]
                   + [abs(b - a) for a, b in zip(ys, ys[1:])])

    def percentile(q):
        if not jumps:
            return None
        return jumps[min(len(jumps) - 1, int(len(jumps) * q))]

    rates = list(per_second.values())
    return {
        'seconds': round(elapsed, 1),
        'syn': syn,
        'syn_per_second': round(syn / elapsed, 1) if elapsed else None,
        'syn_per_second_min': min(rates) if rates else None,
        'syn_per_second_max': max(rates) if rates else None,
        'seconds_with_events': len(per_second),
        'idle_seconds': int(elapsed) - len(per_second),
        'iris_interrupts': irq_delta,
        'slots': sorted(slots),
        'x': {'count': len(xs), 'min': min(xs), 'max': max(xs)} if xs else None,
        'y': {'count': len(ys), 'min': min(ys), 'max': max(ys)} if ys else None,
        'pressure': ({'count': len(presses), 'min': min(presses),
                      'max': max(presses)} if presses else None),
        'jump_p50': percentile(0.5),
        'jump_p95': percentile(0.95),
        'jump_p99': percentile(0.99),
        'jump_max': jumps[-1] if jumps else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True, help='新的证据目录')
    parser.add_argument('--device', default='/dev/input/event7')
    parser.add_argument('--seconds', type=int, default=45)
    parser.add_argument('--wait', type=int, default=90,
                        help='等第一次触摸的最长秒数')
    args = parser.parse_args()

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    before_irq = touch_interrupts()
    report = {'device': args.device, 'interrupt_source': IRQ_NAME,
              'interrupts_before': before_irq}

    fd = os.open(args.device, os.O_RDONLY | os.O_NONBLOCK)
    try:
        print(f'等待触摸（最多 {args.wait}s）…', flush=True)
        if not wait_for_touch(fd, args.wait):
            report['error'] = f'no touch event within {args.wait}s'
            (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
            print(json.dumps(report, indent=2))
            return 1

        print(f'开始统计 {args.seconds} 秒，请持续划动。', flush=True)
        stats = collections.Counter()
        xs, ys, presses, slots = [], [], [], set()
        syn, per_second = 0, collections.Counter()
        start = time.time()
        end = start + args.seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.2)
            if not ready:
                continue
            try:
                data = os.read(fd, SIZE * 512)
            except BlockingIOError:
                continue
            for offset in range(0, len(data) - SIZE + 1, SIZE):
                _, _, etype, code, value = struct.unpack(FMT, data[offset:offset + SIZE])
                if etype == EV_SYN:
                    syn += 1
                    per_second[int(time.time() - start)] += 1
                elif etype == EV_KEY:
                    if value:
                        stats[BTN.get(code, f'KEY_{code:#x}')] += 1
                elif etype == EV_ABS:
                    stats[ABS.get(code, f'ABS_{code:#x}')] += 1
                    if code == 0x35:
                        xs.append(value)
                    elif code == 0x36:
                        ys.append(value)
                    elif code == 0x3a:
                        presses.append(value)
                    elif code == 0x2f:
                        slots.add(value)
        elapsed = time.time() - start
    finally:
        os.close(fd)

    after_irq = touch_interrupts()
    delta = (None if before_irq is None or after_irq is None
             else after_irq - before_irq)
    report.update(summarise(xs, ys, presses, slots, syn, per_second, elapsed, delta))
    report['interrupts_after'] = after_irq
    report['events'] = dict(stats)
    (root / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
