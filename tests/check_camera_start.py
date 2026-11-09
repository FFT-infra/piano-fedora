#!/usr/bin/env python3
"""camera-start 切换 IOMMU 组之前必须先读当前值。

7.3 内核在内核把 CAMSS 的组放进 DMA 域之后把 `iommu_group/type` 设成只读，
root 写入同样得到 EPERM。旧脚本无条件写，写失败就 die，服务因此在真机上
failed；组其实已经是 DMA。这里锁住"先读、只在不是 DMA 时才写"这一契约。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "packages/piano-camera/usr/lib/piano/camera-start"

text = SCRIPT.read_text()
lines = text.splitlines()
guard = 'if [ "$(cat "$CAMSS/iommu_group/type")" != DMA ]; then'
assert guard in [line.strip() for line in lines], "camera-start 不再先读当前的 IOMMU 组类型"
index = [line.strip() for line in lines].index(guard)
assert lines[index + 1].startswith("    echo DMA > \"$CAMSS/iommu_group/type\""), \
    "类型切换必须落在读取之后的 if 里"
assert text.count('echo DMA > "$CAMSS/iommu_group/type"') == 1, "出现了第二处无条件的类型写入"
print("OK: camera-start 只在 IOMMU 组不是 DMA 时才写 type，已是 DMA 不再报失败")
