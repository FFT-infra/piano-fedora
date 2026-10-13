#!/usr/bin/env python3
"""实际编译与安装 adsprpcd；拒绝来源漂移，保留 Fedora listener 库。"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path)
    ap.add_argument("--tool", type=Path, default=ROOT / "scripts/build-device-runtime.py")
    args = ap.parse_args()
    spec = importlib.util.spec_from_file_location("runtime", args.tool)
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    pin = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["fastrpc"]["commit"]
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source, payload, build = root / "source", root / "payload", root / "build"
        (source / "src").mkdir(parents=True)
        (source / "inc").mkdir()
        build.mkdir()
        (source / "src/dsprpcd.c").write_text(
            '#include <stdio.h>\n#ifndef USE_ADSP\n#error missing ADSP\n#endif\n'
            '#ifndef USE_SYSLOG\n#error missing syslog\n#endif\n'
            'int main(void) { puts("ADSP listener fixture"); return DEFAULT_DOMAIN_ID; }\n')
        (source / "LICENSE.txt").write_text("BSD-3-Clause fixture\n")
        lib = payload / "usr/lib64/libadsp_default_listener.so.1"
        lib.parent.mkdir(parents=True)
        lib.write_bytes(b"Fedora owns the listener")
        real_check = subprocess.check_output

        def source_check(command, **kwargs):
            if command[0] == "git":
                return pin + "\n" if "rev-parse" in command else ""
            return real_check(command, **kwargs)

        with patch.object(runtime.subprocess, "check_output", side_effect=source_check):
            proof = runtime.build_fastrpc_daemon(source, payload, build)
        assert proof["commit"] == pin
        exe = payload / "usr/bin/adsprpcd"
        assert os.access(exe, os.X_OK)
        assert subprocess.check_output([exe, "--help"], text=True).strip() == "ADSP listener fixture"
        assert lib.read_bytes() == b"Fedora owns the listener"
        assert (payload / "usr/share/licenses/piano-runtime/fastrpc-LICENSE.txt").is_file()
        for bad in ("0" * 40 + "\n", pin + "\n"):
            replies = [bad, "" if bad.startswith("0") else " M src/dsprpcd.c\n"]
            with patch.object(runtime.subprocess, "check_output", side_effect=replies):
                try:
                    runtime.build_fastrpc_daemon(source, payload, build)
                except ValueError:
                    pass
                else:
                    raise AssertionError("changed FastRPC source accepted")
        if args.source:
            # 可选本机实读源码测试：不 mock pin、编译器或上游 C 源码。
            actual = root / "actual-build"
            actual.mkdir()
            runtime.build_fastrpc_daemon(args.source.resolve(), root / "actual-payload", actual)
    print("OK: native daemon compilation, canonical path, preserved listener, source rejection")
    return 0


if __name__ == "__main__":
    sys.exit(main())
