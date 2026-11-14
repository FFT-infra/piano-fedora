#!/usr/bin/env python3
"""引导参数的合同只在 patches/kernel/0001 的 + 行里定义一次。

workflow 与文档只引用不变量，不复制具体数值。这里离线核对构建器
build_piano_full_kernel.py 会拒绝的写法，避免长构建跑到一半才发现。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches" / "kernel" / "0001-linux-73-series.patch"
WORKFLOW = ROOT / ".github" / "workflows" / "build-kernel.yml"
DOCS = ROOT / "docs" / "known-issues.md"


def command_line():
    added = [line[1:] for line in PATCH.read_text(encoding="utf-8").splitlines()
             if line.startswith("+") and not line.startswith("+++")
             and '"command_line"' in line]
    assert len(added) == 1, f"command_line 的 + 行应恰好一条: {added}"
    match = re.search(r'"command_line"\s*:\s*"([^"]*)"', added[0])
    assert match, "command_line 不是字符串"
    return match.group(1)


def main():
    line = command_line()
    # build_piano_full_kernel.py 的 command_line(): 单空格、ASCII、短于 2048 字节。
    tokens = line.split()
    assert line == " ".join(tokens), "命令行有多余空白"
    assert line.isascii(), "命令行必须是 ASCII"
    assert len(line.encode()) < 2048, "命令行超过 2048 字节"
    assert tokens.count("piano.root=LABEL=PIANOROOT") == 1, "根选择必须恰好一条"
    assert "rdinit=/pianoinit" in tokens, "initramfs 入口丢失"
    assert "rootfstype=f2fs" in tokens, "F2FS 根丢失"
    # 0 会在 initcalls 结束时放弃依赖，负值永不收尾；只接受正数。
    deferred = [token for token in tokens if token.startswith("deferred_probe_timeout=")]
    assert len(deferred) == 1, f"延迟探测超时必须恰好一条: {deferred}"
    value = deferred[0].split("=", 1)[1]
    assert re.fullmatch(r"[0-9]+", value) and int(value) > 0, \
        f"延迟探测超时必须是正整数: {value}"

    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert workflow.count("startswith('deferred_probe_timeout=')") == 2, \
        "编译前与产物核对两处都必须断言延迟探测超时"
    docs = DOCS.read_text(encoding="utf-8")
    literal = "deferred_probe_timeout=" + value
    assert literal not in workflow and literal not in docs, \
        "具体数值只能在补丁里出现一次，别处引用补丁"
    print(f"OK: 引导参数合同成立，deferred_probe_timeout 为正整数，值只在补丁里定义")


if __name__ == "__main__":
    main()
