#!/usr/bin/env python3
"""声卡模块链：内核没有 snd-seq，发行版的 modprobe 规则就不能去加载它。"""

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
    # /usr/lib/modprobe.d/dist-alsa.conf 给 snd-pcm 挂了 `modprobe snd-seq`，
    # 而这个内核没编 CONFIG_SND_SEQUENCER。kmod 按文件名全局排序取先命中的
    # 那条，换个名字压不住，所以必须用同名文件遮住发行版那份。
    override = MODPROBE / "dist-alsa.conf"
    assert override.is_file(), "dist-alsa.conf override is missing"
    lines = list(directives(override))
    assert any(line.startswith("install snd-pcm ") for line in lines), \
        "the override must keep loading snd-pcm"
    assert not any("snd-seq" in line for line in lines), \
        "the override still chains snd-seq"

    # 包内任何一份 modprobe 配置都不能再把 snd-seq 带回来。
    for path in MODPROBE.glob("*.conf"):
        for line in directives(path):
            assert "snd-seq" not in line, path

    print("OK: modprobe config no longer asks for snd-seq")


if __name__ == "__main__":
    main()
