# Iris 补丁消费入口

本系列修改固定 SunUEFI 的 `tools/prepare_release_kernel.py`，把解码顺序与
编码关键帧补丁加入摘要检查、稳定内核应用顺序和最终 Git tree 校验。
`prepare-runtime-kernel.py` 重放同一入口，因此外部模块与 Image 使用同一派生树。

`patches/kernel/` 中的 0035、0037 保留可读原文；只修改原文不会改变构建。
必须同时更新本系列内的嵌套补丁、SHA256 与派生 tree，并运行
`tests/check_iris_source.py`。嵌套补丁使用 Git binary patch 保存原始字节，
避免外层 diff 把内层上下文中的“空格 + tab”误判为新增源码空白错误。
全局补丁应用的严格空白检查保持启用。

0036 SC8541 是历史参考：固定内核已含对应充电驱动，不在本系列重复应用。
驱动和参数存在不能证明本机达到某一充电功率。

设备回归入口是 `make test-device-media MEDIA_OUTPUT=/新的证据目录`，
需在平板上执行。它检查 1080p、300 帧、多个 GOP 的 H.264/HEVC 编码，
以及软件生成的 B 帧视频解码；逐帧与软件解码对照，并保存 Iris 中断差值。
`vainfo` 成功或短视频成功均不能替代此项验收。
