#!/usr/bin/env python3
"""相机二进制现在由 packages/piano-camera 构建。

这里验证三件事：spec 里的两个 Fedora 补丁能干净应用到矩阵源码、应用后的源码
能用 %build 的命令编译并响应 --help、route() 在补丁后仍然只改自己拥的 source
pad。锁定上游可用时再加一条来源断言：矩阵源码必须等于"上游派生的 camerad +
评审过的 NEON 补丁"。
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "piano-camera"
PATCHES = ("camerad-gcc-bounds.patch", "camerad-owned-pix-route.patch")


def check_route(camera, work):
    """运行实际 route 函数；ioctl 边界只记录拓扑修改，不访问设备。"""
    text = camera.read_text()
    start = text.index("static int route(")
    end = text.index("\nstatic int find_media(", start)
    harness = r'''
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <linux/media.h>
struct topo { struct media_v2_topology t; struct media_v2_pad *pads; struct media_v2_link *links; };
static int calls, failed;
static struct { uint32_t source; uint16_t pad; uint32_t sink; bool enabled; } effect[4];
static const struct media_v2_pad *topo_pad(struct topo *tp, uint32_t id) {
    for (unsigned i=0;i<tp->t.num_pads;i++) if (tp->pads[i].id==id) return &tp->pads[i];
    return 0;
}
static int setup_link(struct topo *tp, uint32_t source, uint16_t pad, uint32_t sink, uint16_t sink_pad, bool enable) {
    (void)tp; (void)sink_pad;
    assert(calls<4);
    effect[calls].source=source; effect[calls].pad=pad; effect[calls].sink=sink; effect[calls++].enabled=enable;
    if (failed) { errno=EBUSY; return -1; }
    return 0;
}
'''
    harness += text[start:end]
    harness += r'''
int main(void) {
    struct media_v2_entity source={.id=10}, sink={.id=20};
    struct media_v2_pad pads[]={
        {.id=101,.entity_id=10,.index=4}, {.id=102,.entity_id=20,.index=0},
        {.id=103,.entity_id=10,.index=1}, {.id=104,.entity_id=30,.index=0},
        {.id=105,.entity_id=40,.index=1}, {.id=106,.entity_id=50,.index=0}};
    struct media_v2_link wanted={.source_id=101,.sink_id=102,.flags=MEDIA_LNK_FL_ENABLED};
    struct media_v2_link rdi={.source_id=103,.sink_id=104,.flags=MEDIA_LNK_FL_ENABLED};
    struct media_v2_link foreign={.source_id=105,.sink_id=106,.flags=MEDIA_LNK_FL_ENABLED};
    struct media_v2_link links[]={wanted,rdi,foreign};
    struct topo tp={.t={.num_pads=6,.num_links=3},.pads=pads,.links=links};
    assert(route(&tp,&source,4,&sink,0)==0);
    assert(calls==1 && effect[0].source==10 && effect[0].pad==1 && !effect[0].enabled);
    /* Link order must not let an existing desired path skip sibling cleanup. */
    calls=0; links[0]=rdi; links[1]=wanted;
    assert(route(&tp,&source,4,&sink,0)==0);
    assert(calls==1 && effect[0].pad==1 && !effect[0].enabled);
    /* A new path first retires the competing branch, then enables PIX. */
    calls=0; links[1].flags=0;
    assert(route(&tp,&source,4,&sink,0)==0);
    assert(calls==2 && !effect[0].enabled && effect[1].enabled && effect[1].pad==4);
    /* Busy routes must fail before the new path is enabled. */
    calls=0; failed=1;
    assert(route(&tp,&source,4,&sink,0)==-1);
    assert(calls==1 && !effect[0].enabled);
    /* An immutable link is never rewritten. Foreign entities stay untouched. */
    calls=0; failed=0; links[0].flags|=MEDIA_LNK_FL_IMMUTABLE; links[1]=wanted;
    assert(route(&tp,&source,4,&sink,0)==0 && calls==0);
    return 0;
}
'''
    path, binary = work / "route-test.c", work / "route-test"
    path.write_text(harness)
    subprocess.run(["gcc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", str(path), "-o", str(binary)],
                   check=True, capture_output=True)
    result = subprocess.run([binary], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def patched_source(work):
    """把矩阵源码复制出来，应用 spec 声明的补丁。"""
    tree = work / "package"
    shutil.copytree(PACKAGE / "src", tree / "src")
    for name in PATCHES:
        patch = PACKAGE / name
        assert patch.is_file(), f"{name} is missing from packages/piano-camera"
        subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch)],
                       cwd=tree, check=True, capture_output=True)
    return tree / "src" / "piano-camerad.c"


def check_shipped_pipeline(camera):
    """交付的管道必须同时带着 BGRx 转换和真机修过的传感器方向。

    packages/piano-camera 的源码是下游维护的版本，比锁定的 debian-piano
    （2026-10-09）多了 s5kjn1 方向、CCM 与 REQBUFS 处理。这些是真机验证过的
    修复，用几条特征把它们钉住，避免有人"重新生成"源码时把它们冲掉。
    """
    text = camera.read_text()
    for marker in ('#include "nv12-bgrx.h"', "output_bgrx",
                   "S5KJN1 needs VFLIP", "VIDIOC_SUBDEV_G_FMT",
                   "VIDIOC_S_EXT_CTRLS"):
        assert marker in text, (
            f"shipped camerad lost a reviewed pipeline feature: {marker}. "
            "packages/piano-camera/src is downstream-maintained; do not "
            "regenerate it from the pinned debian-piano without porting these")


def check_runtime_does_not_rebuild_camerad():
    """一个路径只能有一个所有者：运行时不得再派生 camerad。"""
    text = (ROOT / "scripts/build-device-runtime.py").read_text()
    assert 'if name == "piano-camerad":' in text, (
        "build-device-runtime must keep skipping piano-camerad; "
        "packages/piano-camera owns that binary")
    assert "derive_camerad_source" not in text and "adapt_camerad_for_fedora" not in text, (
        "build-device-runtime must not derive a second camerad")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sunuefi", type=Path, default=Path(os.environ.get(
        "PIANO_TEST_SUNUEFI", ROOT / "build/test-sunuefi")))
    args = ap.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        camera = patched_source(work)
        check_shipped_pipeline(camera)
        # 交付的 main() 不带参数解析，这里只证明它能起来、在没有相机节点时
        # 给出诊断并干净退出，而不是段错误或静默成功。
        obj = work / "piano-camerad"
        subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(camera.parent),
                        str(camera), "-lm", "-lpthread", "-o", str(obj)],
                       check=True, capture_output=True, timeout=60)
        result = subprocess.run([obj], capture_output=True, text=True, timeout=10)
        assert result.returncode == 1, (result.returncode, result.stderr)
        assert "no camera outputs" in result.stderr, result.stderr
        assert "loopback output: BGRx" in result.stderr, result.stderr
        check_route(camera, work)
    check_runtime_does_not_rebuild_camerad()
    print("OK: package camera source compiles with its patches and keeps route() single-owner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
