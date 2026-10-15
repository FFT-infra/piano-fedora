#!/usr/bin/env python3
"""单独重建正式运行时的 VA-API、crosvm 与 librevm RPM，供原生设备验证。"""
import argparse
import importlib.util
from pathlib import Path

from piano_runtime_extras import build


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sources", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()
    if args.output.exists():
        ap.error("output already exists; preserve old build evidence and select a new directory")
    spec = importlib.util.spec_from_file_location("runtime_builder", Path(__file__).with_name("build-device-runtime.py"))
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    build(args.sources.resolve(), args.output.resolve(), runtime.rpm_package,
          runtime.put, runtime.sha256, runtime.run)


if __name__ == "__main__":
    main()
