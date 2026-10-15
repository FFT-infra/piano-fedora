#!/usr/bin/env python3
"""从明确摘要的既有 RPM 增量更新；完整生产构建仍使用 build-device-runtime.py。"""
import argparse
import importlib.util
import json
import subprocess
from pathlib import Path

from piano_artifacts import sha256
from piano_device_integration import integration_source, stage_payload

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime_builder", ROOT / "scripts/build-device-runtime.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("base-rpm", "helpers", "additional", "sources", "output", "finger-config-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--base-sha256", required=True)
    args = parser.parse_args()
    if sha256(args.base_rpm) != args.base_sha256:
        raise ValueError("base runtime RPM digest differs")
    args.output.mkdir()
    (args.output / "rpms").mkdir()
    payload = args.output / "payload"
    payload.mkdir()
    archive = subprocess.Popen(["rpm2cpio", str(args.base_rpm)], stdout=subprocess.PIPE)
    try:
        subprocess.run(["cpio", "-idm", "--no-absolute-filenames"], stdin=archive.stdout,
                       cwd=payload, check=True)
    finally:
        archive.stdout.close()
    if archive.wait() != 0:
        raise ValueError("base runtime extraction failed")
    proof = json.loads((args.helpers / "helpers-proof.json").read_text())
    for binary, destination in (("piano-touch-view", "usr/bin/piano-touch-view"),
                                ("piano-camerad", "usr/lib/piano/piano-camerad")):
        builder.put(args.helpers / "helpers" / binary, payload / destination, True)
    if sha256(payload / "usr/bin/piano-touch-view") != proof["touch"]["sha256"]:
        raise ValueError("built finger binary differs from proof")
    if sha256(payload / "usr/lib/piano/piano-camerad") != proof["camera"].get("binary_sha256"):
        raise ValueError("built camera binary differs from proof")
    source, pin = integration_source(args.sources, args.output, builder.run)
    integration = stage_payload(source, pin, payload, proof["touch"], proof["camera"],
                                builder.put, sha256, args.finger_config_dir)
    additional = json.loads((args.additional / "additional-proof.json").read_text())
    meta = payload / "usr/share/piano-provenance/fedora-runtime.json"
    record = json.loads(meta.read_text())
    record.update(status="INCREMENTAL_RUNTIME_BUILT_NOT_DEVICE_VERIFIED", integration=integration,
                  touch=proof["touch"], camera=proof["camera"], **additional,
                  incremental_base_rpm_sha256=args.base_sha256, device_tested=False)
    meta.write_text(json.dumps(record, indent=2) + "\n")
    # Preserve the base package's dependency contract, including soname requirements.
    requirements = subprocess.check_output(["rpm", "-qp", "--requires", str(args.base_rpm)], text=True).splitlines()
    builder.rpm_package("piano-runtime", payload, args.output, version="0.2.0",
                        requires=tuple(r for r in requirements if not r.startswith("rpmlib(")))
    record["rpms"] = {p.name: sha256(p) for p in (args.output / "rpms").glob("*.rpm")}
    (args.output / "manifest.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
