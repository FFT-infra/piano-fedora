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

# 平台设备名取自 reg 的第一段。SunUEFI v1.0.0 的绑定把 top 排到最前，名字从
# ad27000.isp-ml 变成 ac04000.isp-ml，写死任何一个都会在另一份设备树上找不到
# 设备，服务在真机上 failed。节点名 isp-ml 两边相同。
for fixed in ("ad27000.isp-ml", "ac04000.isp-ml"):
    assert f"/sys/bus/platform/devices/{fixed}" not in text, f"camera-start 写死了 {fixed}"
assert "for candidate in /sys/bus/platform/devices/*.isp-ml; do" in text, \
    "camera-start 不再按节点名找 CAMSS 的平台设备"
assert text.index("CAMSS=$(camss_device)") < text.index('"$CAMSS/iommu_group"'), \
    "CAMSS 必须先解析再使用"
print("OK: camera-start 按节点名找 CAMSS，不依赖 reg 第一段的地址")
