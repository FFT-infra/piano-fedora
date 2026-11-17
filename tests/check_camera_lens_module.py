#!/usr/bin/env python3
"""离线检查：camera-start 载入的对焦执行器模块要和 DT 里的 compatible 一致。

SunUEFI 18274377 把后摄执行器从 DW9768 换成双向的 GT9764BA，设备树写的是
`giantec,gt9764ba`，内核模块名是 `gt9764`（`CONFIG_VIDEO_GT9764=m`）。脚本里
还留 `modprobe dw9768` 时，modprobe 只是报一行 not found，相机照常启动，
只是对焦永远不工作——这种失败不会让任何构建变红。

这里只读源码，不编译、不碰设备。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packages/piano-camera/usr/lib/piano/camera-start"
# SunUEFI 18274377 的 piano-camera-sensors.dtso 与 piano-platform.config
DT_COMPATIBLE = "giantec,gt9764ba"
MODULE = "gt9764"


def main():
    lines = [line.strip() for line in SCRIPT.read_text(encoding="utf-8").splitlines()]
    modprobes = [line.split()[1] for line in lines
                 if line.startswith("modprobe ") and len(line.split()) == 2]
    assert MODULE in modprobes, \
        f"camera-start 没有载入对焦执行器模块 {MODULE}（DT 声明 {DT_COMPATIBLE}）"
    assert "dw9768" not in modprobes, \
        "camera-start 仍在载入 dw9768；18274377 起后摄换成 GT9764BA，模块名是 gt9764"
    print(f"OK: camera-start 载入 {MODULE}，与 DT 的 {DT_COMPATIBLE} 一致")


if __name__ == "__main__":
    sys.exit(main())
