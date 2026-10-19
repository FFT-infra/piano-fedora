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
    paths = {"crosvm": ["usr/bin/crosvm"],
             "librevm": ["usr/bin/" + n for n in ("librevm", "librevm-daemon", "librevm-gui")]}
    for items in paths.values():
        for name in items:
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("fixture " + name)
    extras = {name: {"commit": lock[name]["commit"]} for name in paths}
    adapter = (ROOT / "runtime/librevm/fedora.patch").read_text()
    assert "+    if vm.hypervisor.disable_sandbox || resolved.hypervisor == Hypervisor::Gunyah {" in adapter
    assert "+        assert!(joined.contains(\"--disable-sandbox\"));" in adapter
    assert "+    fn kvm_keeps_the_sandbox_for_default_config() {" in adapter
    assert "-            disable_sandbox: true," in adapter
    assert "+            disable_sandbox: false," in adapter
    extras["crosvm"].update(binary_sha256=m.sha256(root / paths["crosvm"][0]), features=["gunyah", "net", "balloon"])
    extras["librevm"].update(adapter_sha256=m.sha256(ROOT / "runtime/librevm/fedora.patch"),
        cargo_lock_sha256=m.sha256(ROOT / "runtime/librevm/Cargo.lock"),
        binaries={Path(n).name: m.sha256(root / n) for n in paths["librevm"]})
    relative = "usr/lib/tmpfiles.d/piano-crosvm.conf"
    (root / relative).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "runtime/crosvm/piano-crosvm.conf", root / relative)
    # sm8750-video-accel ships the driver and its msm alias; the runtime no
    # longer builds a second VA-API backend at the same path.
    dri = root / "usr/lib64/dri"
    dri.mkdir(parents=True)
    (dri / "v4l2_drv_video.so").write_text("fixture driver")
    alias = dri / "msm_drv_video.so"
    alias.symlink_to("v4l2_drv_video.so")
    proof = {"extras": extras}
    m.verify_runtime_extras(root, proof)
    # The clean matrix owns these files through separate specs. A manifest from
    # that path has no legacy extras proof and is accepted only as an explicit
    # absence, not as a partially populated payload.
    m.verify_runtime_extras(root, {})

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
    # An extras proof from the old layout still carried iris-vaapi and session
    # fixes. Those payloads have no owner now, so such a manifest is rejected
    # instead of being silently half-verified.
    bad = copy.deepcopy(proof)
    bad["extras"]["iris-vaapi"] = {"commit": lock["iris-vaapi"]["commit"]}
    reject(bad)
    for name in [n for items in paths.values() for n in items] + [relative]:
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
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    names = ["usr/bin/piano-touch-view", "usr/lib/piano/touch-input",
             "usr/share/piano/touch/piano-csot.json", "usr/share/piano/touch/piano-boe.json"]
    for name in names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture")
    proof = {"integration": {"commit": lock["piano-integration"]["commit"],
             "files": {n: m.sha256(root / n) for n in names},
             "touch": {"single_thp_reader": True, "finger_adapter_sha256": {
                 p.name: m.sha256(p) for p in (ROOT / "runtime/touch").iterdir() if p.is_file()}}}}
    m.verify_device_integration(root, proof)
    bad = copy.deepcopy(proof)
    bad["integration"]["files"] = {}
    try:
        m.verify_device_integration(root, bad)
    except ValueError:
        pass
    else:
        raise AssertionError("missing integration payload accepted")
    for name in names:
        target = root / name
        data = target.read_bytes()
        target.write_bytes(data + b"tamper")
        try:
            m.verify_device_integration(root, proof)
        except ValueError:
            pass
        else:
            raise AssertionError("tampered integration payload accepted: " + name)
        target.write_bytes(data)
print("runtime integration: pinned sources, required payload, adapters and installed bytes checked")
