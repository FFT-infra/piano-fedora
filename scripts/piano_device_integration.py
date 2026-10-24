"""固定来源的手指滤波校准与触控载荷，供标准构建和增量 RPM 复用。"""
import importlib.util
import json
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
    # panel.cpp carries readPanelVendor(), which the bridge uses to pick the
    # processor-to-panel mapping; without it the link fails.
    for name in ("nvt_touch_core", "nvt_finger_filter", "frame", "processor",
                 "calibration", "panel", "live_pipeline", "input", "piano-finger-bridge"):
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


def stage_payload(source, pin, payload, touch_proof, camera_proof, put, sha256, finger_configs):
    extractor = source / "tools/extract-thp-calibration.py"
    spec = importlib.util.spec_from_file_location("piano_calibration", extractor)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # piano-camerad belongs to packages/piano-camera now; the touch helper stays
    # here because it is built from the pinned SunUEFI derived source.
    proven = ["usr/bin/piano-touch-view"]
    calibration = {}
    for lcd in ("csot", "boe"):
        ini = finger_configs / f"piano_nova_{lcd}_thp_config.ini"
        if not ini.is_file():
            raise ValueError("complete private finger calibration is required: " + str(ini))
        # Electrode calibration is shipped by packages/xiaomi-piano-thp. The
        # runtime does not write a second copy; it re-extracts the private
        # factory INI and demands the matrix file match byte for byte, so a
        # regenerated INI can never leave a stale calibration on the device.
        shipped = ROOT / "packages/xiaomi-piano-thp/usr/share/piano/touch" / f"piano-{lcd}.json"
        generated = json.dumps(module.extract(ini), indent=2) + "\n"
        if not shipped.is_file():
            raise ValueError("matrix finger calibration is missing: " + str(shipped))
        if shipped.read_text() != generated:
            raise ValueError("matrix finger calibration differs from the factory INI: " + shipped.name)
        calibration[shipped.relative_to(ROOT / "packages/xiaomi-piano-thp").as_posix()] = sha256(shipped)
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
    proven.append(wrapper.relative_to(payload).as_posix())
    docs = payload / "usr/share/licenses/piano-runtime/piano-integration"
    docs.mkdir(parents=True, exist_ok=True)
    for name in ("NOTICE", "LICENSE"):
        put(source / name, docs / name)
    shutil.copytree(source / "LICENSES", docs / "LICENSES", dirs_exist_ok=True)
    return {"commit": pin["commit"], "touch": touch_proof, "camera": camera_proof,
            "extractor_sha256": sha256(extractor),
            "files": {**{name: sha256(payload / name) for name in proven}, **calibration},
            "finger_calibration_owner": "xiaomi-piano-thp", "device_tested": False}
