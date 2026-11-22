#!/usr/bin/env python3
"""声卡模块链：不再遮住发行版的 modprobe 配置。

内核已把 CONFIG_SND_SEQUENCER 编成模块，alsa-lib 的 dist-alsa.conf 能正常
拉起 snd-seq。同名的历史覆盖文件必须不存在，否则 /dev/snd/seq 永远不出现。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODPROBE = ROOT / "packages/piano-fedora-configs/etc/modprobe.d"


def directives(path):
    """只看生效的行，注释里提到 snd-seq 是说明，不是指令。"""
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            yield line


def main():
    override = MODPROBE / "dist-alsa.conf"
    assert not override.exists(), (
        "dist-alsa.conf override shadows alsa-lib and hides /dev/snd/seq")

    # 包内任何一份 modprobe 配置都不能再把 snd-seq 拦下来。
    for path in MODPROBE.glob("*.conf"):
        for line in directives(path):
            assert "snd-seq" not in line, path

    print("OK: modprobe config no longer shadows dist-alsa.conf or snd-seq")


if __name__ == "__main__":
    main()
