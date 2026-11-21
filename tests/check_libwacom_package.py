#!/usr/bin/env python3
"""libwacom 重编包的离线契约。

补丁来自 SunUEFI `patches/libwacom/2.20/`，摘要必须和那边的 series.json
一致。这里锁住：SPI 笔描述与内核 0230 报出来的 ID 对齐、四个替换包的
Provides/Obsoletes、构建矩阵与发布链的接入，以及 %check 真的在解析新
tablet。设备侧验证（桌面设置里能看到这支笔）不在离线测试里。
"""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "packages" / "piano-libwacom"
SPEC = PKG / "piano-libwacom.spec"
PATCH = PKG / "0001-spi-digitizers-and-xiaomi-pad.patch"
BUILD = ROOT / "scripts" / "build-rpms.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "build-piano-rpms.yml"

PATCH_SHA256 = "63017df69dc0454782b13c67d9b7c2ca02aabd912b02485321c64807e441e54b"
TARBALL_SHA256 = "370b45b5e05a91960df0aeb9c9481ae05846aab92ab2d4ec66945a0da4216888"

# 内核 0230-LOCAL-input-nt36532e-pen.patch 报出来的身份。描述文件用同一组
# 值匹配，两个数字对不上桌面就认不到笔。
PEN_VENDOR = "0603"
PEN_PRODUCT = "6532"


def main():
    patch = PATCH.read_text()
    assert hashlib.sha256(PATCH.read_bytes()).hexdigest() == PATCH_SHA256, \
        "补丁内容与 SunUEFI series.json 的摘要不一致"
    assert "WBUSTYPE_SPI" in patch, "补丁没有加 SPI 总线类型"
    assert 'g_str_equal(str, "spi")' in patch, "补丁没有解析 spi 的 DeviceMatch"
    assert "Novatek Focus Pen" in patch and "Novatek Focus Pen Pro" in patch, \
        "补丁没有带上内核报的两支笔名"
    assert f"DeviceMatch=spi|{PEN_VENDOR}|{PEN_PRODUCT}|Novatek Focus Pen;" \
           f"spi|{PEN_VENDOR}|{PEN_PRODUCT}|Novatek Focus Pen Pro;" in patch, \
        "tablet 的 DeviceMatch 与内核 0230 的 vendor/product 不一致"
    assert "xiaomi-pad-8-pro.tablet" in patch and "Name=Xiaomi Pad 8 Pro" in patch, \
        "补丁没有新增 Xiaomi Pad 8 Pro 的 tablet 描述"
    assert 'bustypes = {' in patch and '"spi": "001C",' in patch, \
        "hwdb 生成没有把 spi 映射到 001C"

    spec = SPEC.read_text()
    assert re.search(r"^Version:\s+2\.20\.0$", spec, re.M), "版本没有对上 SPI 补丁的 2.20.0"
    assert re.search(r"^Release:\s+[1-9]\d*", spec, re.M), "Release 不能为零"
    assert TARBALL_SHA256 in spec, "spec 没有记录源码 tarball 的 sha256"
    assert PATCH_SHA256 in spec, "spec 没有记录补丁的 sha256"
    assert "Source0:        %{url}/releases/download/libwacom-%{version}/" in spec, \
        "Source0 不是上游 release tarball"
    assert "Patch0:         0001-spi-digitizers-and-xiaomi-pad.patch" in spec, \
        "spec 没有套 SPI 补丁"
    assert "ExclusiveArch:  aarch64" in spec, "内核补丁只在这台设备上出 aarch64"
    assert "BuildRequires:  libevdev-devel" in spec, "缺 libevdev 开发头"
    assert "BuildRequires:  libgudev-devel" in spec, "缺 gudev 开发头"

    # 四个二进制包各自淘汰发行版同名包，边界写成相对自身 EVR，升版本后
    # 不会悄悄变成自匹配。
    for name in ("libwacom", "libwacom-data", "libwacom-utils", "libwacom-devel"):
        assert f"Provides:       {name} = %{{version}}-%{{release}}" in spec, \
            f"没有声明替换 {name}"
        assert f"Obsoletes:      {name} < %{{version}}-%{{release}}" in spec, \
            f"没有淘汰发行版 {name}"
    assert "BuildArch:      noarch" in spec, "tablet 数据包应当 noarch"
    assert "-Dtests=disabled" in spec and "-Ddocumentation=disabled" in spec, \
        "meson 参数没有关掉测试与文档"

    # %check 用未安装的 list-devices 解析源码树里的 data/。它既是补丁编进去
    # 的证据，也是新 tablet 能被匹配的证据。
    assert "%{_vpath_builddir}/list-devices" in spec, \
        "%check 没有跑构建目录里的 list-devices"
    assert "bus: 'spi',.*name: 'Xiaomi Pad 8 Pro'" in spec, \
        "%check 没有断言 SPI 笔能被数据库匹配"
    for path in ("%{_datadir}/libwacom/*.tablet",
                 "%{_udevrulesdir}/65-libwacom.rules",
                 "%{_udevhwdbdir}/65-libwacom.hwdb"):
        assert path in spec, f"%files 缺少 {path}"

    build = BUILD.read_text()
    packages = re.findall(r'"([a-z0-9-]+)"', build.split("PACKAGES_TO_BUILD+=(")[1].split(")")[0])
    assert "piano-libwacom" in packages, "构建矩阵没有包含 piano-libwacom"

    workflow = WORKFLOW.read_text()
    assert '"piano-libwacom"' in workflow, "workflow 的产物清单没有 piano-libwacom"
    assert "libevdev-devel" in workflow, "workflow 没有安装 libevdev-devel"

    copr = (ROOT / "scripts" / "publish-copr.py").read_text()
    assert '"piano-libwacom"' in copr, "COPR 的用户态包清单没有 piano-libwacom"

    rootfs = (ROOT / "scripts" / "build-clean-rootfs.py").read_text()
    assert '"piano-libwacom"' in rootfs, "clean rootfs 清单没有 piano-libwacom"
    finalize = (ROOT / "scripts" / "finalize-fedora-rootfs.py").read_text()
    assert '"piano-libwacom"' in finalize, "finalize 的必需包集合没有 piano-libwacom"
    verifier = (ROOT / "scripts" / "verify-copr-device.py").read_text()
    assert '"piano-libwacom"' in verifier, "设备端 COPR 校验没有 piano-libwacom"

    print("OK: libwacom SPI 笔补丁、四个替换包、构建与发布链接入齐全")


if __name__ == "__main__":
    main()
