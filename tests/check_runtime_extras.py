#!/usr/bin/env python3
"""实际 rootfs 载荷、源码 pin 与证明必须一致；篡改和缺失均拒绝。"""
import copy
import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("finalizer", ROOT / "scripts/finalize-fedora-rootfs.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    paths = {"iris-vaapi": ["usr/lib64/dri/v4l2_drv_video.so"], "crosvm": ["usr/bin/crosvm"],
             "librevm": ["usr/bin/" + n for n in ("librevm", "librevm-daemon", "librevm-gui")]}
    for items in paths.values():
        for name in items:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("fixture " + name)
    extras = {name: {"commit": lock[name]["commit"]} for name in paths}
    extras["iris-vaapi"]["driver_sha256"] = m.sha256(root / paths["iris-vaapi"][0])
    extras["crosvm"].update(binary_sha256=m.sha256(root / paths["crosvm"][0]), features=["gunyah", "net", "balloon"])
    extras["librevm"].update(adapter_sha256=m.sha256(ROOT / "runtime/librevm/fedora.patch"),
        cargo_lock_sha256=m.sha256(ROOT / "runtime/librevm/Cargo.lock"),
        binaries={Path(n).name: m.sha256(root / n) for n in paths["librevm"]})
    session = {}
    for p in (ROOT / "runtime/usr").rglob("*"):
        if p.is_file():
            relative = p.relative_to(ROOT / "runtime")
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, target)
            session[str(relative)] = m.sha256(p)
    relative = "usr/lib/tmpfiles.d/piano-crosvm.conf"
    shutil.copyfile(ROOT / "runtime/crosvm/piano-crosvm.conf", root / relative)
    alias = root / "usr/lib64/dri/msm_drv_video.so"
    alias.symlink_to("v4l2_drv_video.so")
    proof = {"extras": extras, "session_fixes": session}
    m.verify_runtime_extras(root, proof)
    def reject(bad):
        try:
            m.verify_runtime_extras(root, bad)
        except ValueError:
            return
        raise AssertionError("invalid installed runtime was accepted")
    for section, key, value in (("crosvm", "commit", "0" * 40), ("crosvm", "features", ["kvm"]),
                                 ("librevm", "adapter_sha256", "0" * 64)):
        bad = copy.deepcopy(proof)
        bad["extras"][section][key] = value
        reject(bad)
    bad = copy.deepcopy(proof)
    bad["session_fixes"] = {}
    reject(bad)
    for name in [n for items in paths.values() for n in items] + list(session) + [relative]:
        p = root / name
        data = p.read_bytes()
        p.write_bytes(data + b"tampered")
        reject(proof)
        p.unlink()
        reject(proof)
        p.write_bytes(data)
    alias.unlink()
    reject(proof)
    alias.symlink_to("v4l2_drv_video.so")
    m.verify_runtime_extras(root, proof)
print("runtime extras: pinned sources, installed bytes, missing files and alias checked")
