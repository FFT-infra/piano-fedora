#!/usr/bin/env python3
"""build-kernel 必须真的把 ccache 打开，并在两次 run 之间保留它。

上游 tools/build_piano_full_kernel.py 判断 ccache 是否可用只看一个条件：
`CCACHE_DIR` 是否存在。它存在才把 CC 换成 "ccache clang" 并固定
KBUILD_BUILD_TIMESTAMP。工作流只安装 ccache 而不设这个变量时，整条构建
仍然走裸 clang 全量重编，而日志里没有任何地方会提示这一点。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/build-kernel.yml"

text = WORKFLOW.read_text()
lines = [line.strip() for line in text.splitlines() if line.strip()]

assert "CCACHE_DIR: ${{ github.workspace }}/.ccache" in lines, \
    "build-kernel 没有设置 CCACHE_DIR：上游不会启用 ccache"
for name in ("CCACHE_MAXSIZE", "CCACHE_COMPILERCHECK", "CCACHE_BASEDIR"):
    assert any(line.startswith(name + ":") for line in lines), f"缺少 {name}"

assert "uses: actions/cache/restore@v4" in lines, "没有恢复编译器缓存"
assert "uses: actions/cache/save@v4" in lines, "没有保存编译器缓存"
assert "path: ${{ env.CCACHE_DIR }}" in lines, "缓存路径必须与 CCACHE_DIR 一致"
assert any(line.startswith("restore-keys:") for line in lines), \
    "没有 restore-keys：换 key 之后一个对象都命中不了"

save_index = lines.index("uses: actions/cache/save@v4")
assert any("cache-hit" in line for line in lines[save_index - 4:save_index]), \
    "保存步骤没有按 cache-hit 短路，命中时会重复上传同一份缓存"

# 缓存 key 只决定恢复哪一份；命中率靠它跟上内核输入。源 pin 与补丁是主要变量。
key_line = next(line for line in lines if line.startswith("key: kernel-ccache-"))
for name in ("sources.lock.json", "patches/**"):
    assert name in key_line, f"缓存 key 没有覆盖 {name}"

print("OK: build-kernel 通过 CCACHE_DIR 启用 ccache，并在 run 之间恢复与保存")
