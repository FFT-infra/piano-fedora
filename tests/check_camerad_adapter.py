#!/usr/bin/env python3
"""实测固定上游相机适配输出、GCC 修复和无设备访问的帮助入口。"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sunuefi", type=Path, default=Path(os.environ.get(
        "PIANO_TEST_SUNUEFI", ROOT / "build/test-sunuefi")))
    ap.add_argument("--public-source", type=Path)
    args = ap.parse_args()
    public = args.public_source or Path(os.environ.get(
        "PIANO_TEST_PUBLIC_SOURCE", args.sunuefi / "upstream/debian-piano-current"))
    if not (args.sunuefi / "tools/build_piano_runtime_helpers.py").is_file() or not (public / "camera/piano-camerad.c").is_file():
        print("SKIP: pinned camera sources unavailable; real adapter/GCC test not run")
        return 0
    expected = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    assert subprocess.check_output(["git", "-C", str(args.sunuefi), "rev-parse", "HEAD"], text=True).strip() == expected
    sys.path.insert(0, str(args.sunuefi / "tools"))
    import build_piano_runtime_helpers as upstream
    upstream.repository(public, upstream.PUBLIC_COMMIT)
    spec = importlib.util.spec_from_file_location("fedora_runtime", ROOT / "scripts/build-device-runtime.py")
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        camera, proof = upstream.derive_camerad_source(public, work)
        before = camera.read_bytes()
        assert any(row["file"].endswith("piano-camerad-local-video-group.patch") for row in proof["patches"])
        # 必须保留不匹配输入的拒绝路径，不能为了让长构建通过而移除摘要检查。
        try:
            runtime.adapt_camerad_for_fedora(camera, {**proof, "effective_source_sha256": "0" * 64})
        except ValueError:
            pass
        else:
            raise AssertionError("foreign camera adapter proof accepted")
        assert camera.read_bytes() == before
        runtime.adapt_camerad_for_fedora(camera, proof)
        assert camera.read_bytes() != before
        assert proof["fedora_effective_source_sha256"] == runtime.sha256(camera)
        entry = work / "entry.c"
        entry.write_text(upstream.entry_source("piano-camerad"))
        obj, binary = work / "camera.o", work / "piano-camerad"
        subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-Dmain=PianoOriginalMain",
                        "-c", str(camera), "-o", str(obj)], check=True, capture_output=True, timeout=30)
        subprocess.run(["gcc", str(entry), str(obj), "-lm", "-o", str(binary)],
                       check=True, capture_output=True, timeout=30)
        result = subprocess.run([binary, "--help"], check=True, capture_output=True, text=True, timeout=5)
        assert "--help performs no device access" in result.stdout
        check_route(camera, work)
    print("OK: pinned camera adapter, GCC compilation, foreign-input rejection and help entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
