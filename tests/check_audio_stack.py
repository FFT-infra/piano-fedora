#!/usr/bin/env python3
"""声卡模块链：历史兼容覆盖检查。

旧版内核未编 CONFIG_SND_SEQUENCER 时用同名 dist-alsa.conf 屏蔽 snd-seq 报错。
当前内核已开启 CONFIG_SND_SEQUENCER=m，本测试暂时保持断言，待包升级与断言统一清理。
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
    # /usr/lib/modprobe.d/dist-alsa.conf 给 snd-pcm 挂了 `modprobe snd-seq`。
    # 历史原因：旧内核未编 CONFIG_SND_SEQUENCER 时，用同名文件遮住发行版配置。
    # 当前内核已编入 CONFIG_SND_SEQUENCER=m，此处保留断言直到包与配置完成统一清理。
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
