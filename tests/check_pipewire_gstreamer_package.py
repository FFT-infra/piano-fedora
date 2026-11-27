#!/usr/bin/env python3
"""piano-pipewire-gstreamer 的离线契约。

Fedora 45 的 pipewire-1.6.12-1 已带 1.6 分支收的 GStreamer 修复（首选 caps、
buffer_recycle 的加锁顺序、首帧空指针、32 位 modifier 截断），pipewiresrc 还缺
三处：caps 带列表时每次 reconfigure 都断开重连（上游 97699d70，1.6.12 没有
带）、MemPtr chunk 的映射在重协商时被移除、不兼容的采集格式让
wait_negotiated 一直等。

这份包替换 Fedora 的 pipewire-gstreamer 子包，在同一版源码上补这三处。这里
锁住：补丁序列完整且不重复带基准源码已有的修复、替换声明成立、只下发那一个
插件、构建矩阵、镜像清单与 COPR 发布链都带上它。设备侧的协商与取帧行为不在
离线测试里。

Fedora 带上这三处之后，本测试与整份包一起删掉。
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import package_sets
PKG = ROOT / "packages" / "piano-pipewire-gstreamer"
SPEC = PKG / "piano-pipewire-gstreamer.spec"


def main():
    patches = sorted(p.name for p in PKG.glob("*.patch"))
    assert len(patches) == 3, f"补丁序列应为 3 个，实际 {patches}"
    body = "\n".join((PKG / name).read_text() for name in patches)
    # 三条里只有不重连的判定是上游提交，补丁头的 From 就是它的提交号；另外
    # 两条是下游补丁，没有上游提交号。多出别的提交号说明带了不该带的东西。
    expected = {"97699d707df374773fe5d6e32ea7362ef8dcf51b"}
    got = set(re.findall(r"^From ([0-9a-f]{40}) ", body, re.M))
    assert got == expected, f"补丁序列里的上游提交不对：多 {got - expected} 少 {expected - got}"
    # 不重连的判定要改成"现有 caps 仍是下游子集就不重连"，否则每秒重连
    # 十几次、池子拆掉后 sink 还在拷贝。
    assert "gst_caps_is_subset (current_caps, possible_caps)" in body, \
        "缺少不重连的判定，reconfigure 会继续反复重连"
    assert "97699d70" in body, "不重连的判定没有记上游提交号"
    # caps 交集为空时 fixate 仍留空 caps，断言会让 wait_negotiated 一直等；
    # 只有这条流该失败。
    assert "No compatible capture format" in body, \
        "缺少不兼容采集格式的错误路径，wait_negotiated 会一直等"
    # MemPtr 指向的映射在重协商时会被移除，必须拷贝后再交出；fd 背书的内存
    # 仍走 share。少了这条，sink 手里的 buffer 会在池子拆掉后读到已解映射的
    # 地址。
    assert "SPA_DATA_MemPtr" in body and "has_shared_memory" in body, \
        "缺少 MemPtr 拷贝路径，下游 buffer 仍会引用已移除的映射"
    # 1.6.12 的源码已带首选 caps 与 per-plane stride。SunUEFI 的 1.6.11 序列里
    # 还有这两条，照着序列整份搬回来会在 %prep 阶段套不上。
    assert "set_pref_int_range" not in body, "首选 caps 已在 1.6.12 的源码里，不该再带"
    assert "extrapolate_stride" not in body, "per-plane stride 已在 1.6.12 的源码里，不该再带"

    spec = SPEC.read_text()
    assert re.search(r"^Version:\s+1\.6\.12$", spec, re.M), "版本没有对上 Fedora 的 1.6.12"
    assert re.search(r"^Release:\s+[2-9]", spec, re.M), \
        "Release 必须高于 Fedora 的 1.6.12-1，否则 Obsoletes 不生效、两份包会文件冲突"
    declared = re.findall(r"^Patch\d+:\s+(\S+)$", spec, re.M)
    assert declared == patches, f"spec 的 Patch 行与实际补丁不一致：{declared} vs {patches}"
    # 同一个 .so 只能由一份包提供，两条声明缺一不可。
    assert "Provides:       pipewire-gstreamer = %{version}-%{release}" in spec, \
        "没有声明替换 pipewire-gstreamer"
    assert "Obsoletes:      pipewire-gstreamer < %{version}-%{release}" in spec, \
        "没有淘汰 Fedora 的 pipewire-gstreamer"
    # Obsoletes 只压得住更旧的版本；更高版本会被 dnf 当成升级把我们换掉。
    assert "Conflicts:      pipewire-gstreamer > %{version}-%{release}" in spec, \
        "没有拦住 Fedora 更高版本的静默替换"
    # 只下发插件；其余子包由 Fedora 的 pipewire 提供，多带一个就会冲突。
    files = spec.split("%files", 1)[1].split("%changelog", 1)[0]
    owned = [line.strip() for line in files.splitlines() if line.strip().startswith("/") or line.strip().startswith("%{")]
    assert owned == ["%{_libdir}/gstreamer-1.0/libgstpipewire.so"], \
        f"%files 只应有那个插件，实际是 {owned}"
    # meson 选项写错一个就会在配置阶段停，构建矩阵里没有逐项校验。
    assert "-Dgstreamer=enabled" in spec and "-Dgstreamer-device-provider=enabled" in spec, \
        "没有启用 GStreamer 插件与设备枚举"

    assert "piano-pipewire-gstreamer" in package_sets.matrix(), \
        "构建矩阵没有包含 piano-pipewire-gstreamer"

    workflow = (ROOT / ".github" / "workflows" / "build-piano-rpms.yml").read_text()
    assert "package_sets.matrix()" in workflow, "workflow 的产物清单没有从 package_sets 派生"

    for script in ("build-clean-rootfs.py", "finalize-fedora-rootfs.py",
                   "verify-copr-device.py"):
        text = (ROOT / "scripts" / script).read_text()
        assert "package_sets" in text, f"{script} 没有从 package_sets 取包清单"

    # 没有进 COPR 就没有任何一台设备会拿到它。publish-copr.py 对 REQUIRED 之外
    # 的产物静默跳过，少写一行等于这个包只存在于 CI 的 artifact 里。
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "publish_copr", ROOT / "scripts" / "publish-copr.py")
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)
    assert "piano-pipewire-gstreamer" in publish.REQUIRED, \
        "publish-copr.py 的必需清单没有 piano-pipewire-gstreamer"
    assert "piano-pipewire-gstreamer" in publish.BUILD_ORDER, \
        "publish-copr.py 的构建顺序没有 piano-pipewire-gstreamer"

    copr = (ROOT / "tests" / "check_copr_release.py").read_text()
    assert '"piano-pipewire-gstreamer"' in copr, \
        "check_copr_release.py 的包清单没有 piano-pipewire-gstreamer"

    print("OK: pipewire-gstreamer 补丁、替换声明、单一插件下发与镜像清单齐全")


if __name__ == "__main__":
    main()
