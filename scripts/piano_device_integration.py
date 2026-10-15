"""固定来源的手指滤波、相机转换与性能调度，供标准构建和增量 RPM 复用。"""
import importlib.util
import json
import os
import shutil
from pathlib import Path

from piano_runtime_extras import checked_copy

ROOT = Path(__file__).resolve().parents[1]


def integration_source(sources, output, run):
    _, source, pin = checked_copy(sources, "piano-integration", output)
    run(["patch", "--batch", "--fuzz=0", "-p1", "-i",
         ROOT / "runtime/touch/uinput-physical.patch"], cwd=source)
    return source, pin


def build_touch(runtime, output, flags, uapi, touch, pen, source, run, sha256):
    patch = ROOT / "runtime/touch/fedora-finger.patch"
    run(["patch", "--batch", "--fuzz=0", "-p1", "-i", patch], cwd=touch.parents[2])
    folder = output / "finger-build"
    folder.mkdir()
    core = source / "device/piano-thp"
    # The pinned offline tests exercise calibration, framing and contact expiry.
    run(["make", "-C", core, "OUT=" + str(folder / "tests"), "test"])
    cxx = [*pen["cxx_command"], "-std=c++20", "-I" + str(core / "src"),
           "-I" + str(ROOT / "runtime/touch")]
    objects = []
    for name in ("nvt_touch_core", "nvt_finger_filter", "frame", "processor",
                 "calibration", "live_pipeline", "input", "piano-finger-bridge"):
        src = (ROOT / "runtime/touch" if name == "piano-finger-bridge" else core / "src") / (name + ".cpp")
        obj = folder / (name + ".o")
        run([*cxx, "-c", src, "-o", obj])
        objects.append(obj)
    entry = output / "piano-touch-view-entry.c"
    entry.write_text(runtime.entry_source("piano-touch-view"))
    obj, entry_obj = output / "piano-touch-view.o", output / "piano-touch-view-entry.o"
    run([*flags, "-isystem", uapi / "include", "-I", output / "pen-source",
         "-I", ROOT / "runtime/touch", "-Dmain=PianoOriginalMain", "-c", touch, "-o", obj])
    run([*flags, "-c", entry, "-o", entry_obj])
    binary = output / "piano-touch-view"
    run([*pen["cxx_command"], "-static", entry_obj, obj,
         output / pen["archive"]["file"], *objects, "-lm", "-o", binary])
    run([binary, "--help"], timeout=10)
    return {**runtime.verify_elf(binary), "finger_source_sha256": sha256(touch),
            "finger_adapter_sha256": {p.name: sha256(p) for p in (ROOT / "runtime/touch").iterdir() if p.is_file()},
            "single_thp_reader": True, "pen_archive_sha256": pen["archive"]["sha256"]}


def adapt_camera(camera, source, proof, run, sha256):
    patch = ROOT / "patches/runtime/camerad-bgrx-neon.patch"
    run(["patch", "--batch", "--fuzz=0", "-p1", "-i", patch], cwd=camera.parent.parent)
    header = source / "device/camera/nv12-bgrx.h"
    shutil.copyfile(header, camera.parent / header.name)
    test = camera.parent / "test-nv12-bgrx"
    run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", camera.parent,
         source / "device/camera/test-nv12-bgrx.c", "-lm", "-o", test])
    run([test])
    proof.update(fedora_effective_source_sha256=sha256(camera),
                 neon_header_sha256=sha256(header), bgrx_adapter_sha256=sha256(patch))


def stage_payload(source, pin, payload, touch_proof, camera_proof, put, sha256, finger_configs):
    extractor = source / "tools/extract-thp-calibration.py"
    spec = importlib.util.spec_from_file_location("piano_calibration", extractor)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files = ["usr/bin/piano-touch-view", "usr/lib/piano/piano-camerad"]
    for lcd in ("csot", "boe"):
        ini = finger_configs / f"piano_nova_{lcd}_thp_config.ini"
        if not ini.is_file():
            raise ValueError("complete private finger calibration is required: " + str(ini))
        dest = payload / f"usr/share/piano/touch/piano-{lcd}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(module.extract(ini), indent=2) + "\n")
        dest.chmod(0o600)
        files.append(dest.relative_to(payload).as_posix())
    wrapper = payload / "usr/lib/piano/touch-input"
    text = wrapper.read_text()
    needle = 'esac\nif [ -n "$calibration" ]'
    if needle not in text:
        raise ValueError("pinned touch-input LCD selection changed")
    text = text.replace(needle, '''esac
case "$lcd_id" in
  0) export PIANO_FINGER_CALIBRATION=/usr/share/piano/touch/piano-csot.json ;;
  1) export PIANO_FINGER_CALIBRATION=/usr/share/piano/touch/piano-boe.json ;;
  *) echo 'piano-touch: unsupported LCD; calibrated finger input refused' >&2; exit 1 ;;
esac
if [ -n "$calibration" ]''', 1)
    wrapper.write_text(text)
    files.append(wrapper.relative_to(payload).as_posix())
    ucm = payload / "usr/share/alsa/ucm2/Qualcomm/sm8750/Xiaomi-Pad-8-Pro/HiFi.conf"
    text = ucm.read_text()
    if text.count("Amp Volume' 235") != 4:
        raise ValueError("pinned Piano amplifier UCM sequence changed")
    text = text.replace("Amp Volume' 235", "Amp Volume' 192")
    text = text.replace("# OEM FSM_Volume is 235 (-7.5 dB) on all four speaker paths.",
                        "# Conservative -23.6 dB default; the Piano kernel caps the controls at 215.")
    ucm.write_text(text)
    files.append(ucm.relative_to(payload).as_posix())
    docs = payload / "usr/share/licenses/piano-runtime/piano-integration"
    docs.mkdir(parents=True, exist_ok=True)
    for name in ("NOTICE", "LICENSE"):
        put(source / name, docs / name)
    shutil.copytree(source / "LICENSES", docs / "LICENSES", dirs_exist_ok=True)
    return {"commit": pin["commit"], "touch": touch_proof, "camera": camera_proof,
            "extractor_sha256": sha256(extractor),
            "files": {name: sha256(payload / name) for name in files}, "device_tested": False}


def build_performance(sources, output, package, put, run, sha256):
    (output / "rpms").mkdir(parents=True, exist_ok=True)
    _, source, pin = checked_copy(sources, "uperf", output)
    adapter = ROOT / "runtime/uperf/fedora.patch"
    run(["patch", "--batch", "--fuzz=0", "-p1", "-i", adapter], cwd=source)
    build = output / "uperf-build"
    # Upstream tests contain literal /tmp paths. Keep test files in the approved
    # scratch area without changing the shipped daemon or test assertions.
    scratch = output / "uperf-test-scratch"
    scratch.mkdir()
    for test in (source / "tests").glob("*.c"):
        test.write_text(test.read_text().replace('"/tmp', '"' + str(scratch)))
    run(["cmake", "-S", source, "-B", build, "-DCMAKE_BUILD_TYPE=Release",
         "-DCMAKE_INSTALL_PREFIX=/usr", "-DCMAKE_INSTALL_LIBDIR=lib", "-DBUILD_GUI=ON"])
    run(["cmake", "--build", build, "-j2"])
    run(["ctest", "--test-dir", build, "--output-on-failure"])
    payload = output / "uperf-payload"
    run(["cmake", "--install", build], env=dict(os.environ, DESTDIR=str(payload)))
    config = payload / "etc/uperf-linux/config.json"
    data = json.loads(config.read_text())
    data["modules"]["input"].update(screen_width=32000, screen_height=21360)
    data["modules"]["thermal"].update(warn_temp=70000, throttle_temp=80000,
                                       critical_temp=95000, recovery_temp=75000)
    config.write_text(json.dumps(data, indent=2) + "\n")
    put(source / "LICENSE", payload / "usr/share/licenses/uperf-linux/LICENSE")
    package("uperf-linux", payload, output, version="0.1.2", requires=("polkit", "systemd"),
            license_expression="GPL-3.0-only")
    return {"commit": pin["commit"], "adapter_sha256": sha256(adapter), "files": {
        p.relative_to(payload).as_posix(): sha256(p) for p in payload.rglob("*") if p.is_file()},
        "device_tested": False}
