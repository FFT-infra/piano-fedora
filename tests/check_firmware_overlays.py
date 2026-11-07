#!/usr/bin/env python3
"""固件叠层安装脚本的契约：清单来源、摘要核对、失败时不落盘。"""

import hashlib
import importlib.util
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages/xiaomi-piano-firmware"


def load():
    spec = importlib.util.spec_from_file_location("firmware_overlays",
                                                  PACKAGE / "firmware-overlays.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_overlay(sunuefi, name, files, component="piano-adsp", rom="OS4.0.4.0.XPYCNXM"):
    directory = sunuefi / "vendor/firmware" / name
    directory.mkdir(parents=True)
    manifest = {"schema_version": 1, "component": component, "rom": rom, "files": {}}
    for filename, content in files.items():
        (directory / filename).write_bytes(content)
        manifest["files"][filename] = {"bytes": len(content),
                                       "sha256": hashlib.sha256(content).hexdigest()}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return "vendor/firmware/" + name + "/manifest.json"


def write_config(sunuefi, overlays):
    (sunuefi / "config").mkdir(parents=True)
    (sunuefi / "config/release.json").write_text(
        json.dumps({"firmware": {"overlays": overlays}}))


def main():
    module = load()

    with tempfile.TemporaryDirectory(prefix="overlays-") as directory:
        work = Path(directory)
        sunuefi, firmware = work / "sunuefi", work / "firmware"
        firmware.mkdir()
        (firmware / "adsp.b00").write_bytes(b"old rom")
        first = write_overlay(sunuefi, "hyperos4-adsp", {"adsp.b00": b"new rom", "adsp.mdt": b"mdt"})
        second = write_overlay(sunuefi, "hyperos4-cdsp", {"cdsp.mdt": b"cdsp"},
                               component="piano-cdsp")
        write_config(sunuefi, [first, second])

        applied = module.apply(sunuefi, firmware)
        # 清单来自 release.json，叠层覆盖同名文件，也新增文件。
        assert [row["component"] for row in applied] == ["piano-adsp", "piano-cdsp"], applied
        assert [row["files"] for row in applied] == [2, 1], applied
        assert (firmware / "adsp.b00").read_bytes() == b"new rom"
        assert (firmware / "adsp.mdt").read_bytes() == b"mdt"
        assert (firmware / "cdsp.mdt").read_bytes() == b"cdsp"

        # 摘要不对时整份都不写：先核对再复制，不留下半套固件。
        broken = work / "broken"
        (broken / "firmware").mkdir(parents=True)
        target = write_overlay(broken / "sunuefi", "hyperos4-cdsp",
                               {"cdsp.mdt": b"cdsp", "cdsp.b00": b"b00"})
        (broken / "sunuefi/vendor/firmware/hyperos4-cdsp/cdsp.b00").write_bytes(b"tampered")
        write_config(broken / "sunuefi", [target])
        try:
            module.apply(broken / "sunuefi", broken / "firmware")
        except ValueError as error:
            assert "checksum mismatch" in str(error), error
        else:
            raise AssertionError("a tampered overlay must fail")
        assert not any((broken / "firmware").iterdir()), sorted((broken / "firmware").iterdir())

        # 带目录的文件名与符号链接都不接受，否则能把文件写到固件目录外面。
        for index, name in enumerate(("nested/cdsp.mdt", "..")):
            escape = work / f"escape{index}"
            (escape / "firmware").mkdir(parents=True)
            entry = write_overlay(escape / "sunuefi", "hyperos4-cdsp", {"cdsp.mdt": b"x"})
            manifest = escape / "sunuefi/vendor/firmware/hyperos4-cdsp/manifest.json"
            data = json.loads(manifest.read_text())
            data["files"][name] = data["files"].pop("cdsp.mdt")
            manifest.write_text(json.dumps(data))
            write_config(escape / "sunuefi", [entry])
            try:
                module.apply(escape / "sunuefi", escape / "firmware")
            except ValueError as error:
                assert "filename" in str(error), error
            else:
                raise AssertionError("unsafe firmware filename must fail: " + name)

    # 叠层的提交号必须与 sources.lock.json 的 sunuefi pin 一致：spec 里写的是
    # 一份副本，这里守住它不漂移。
    spec = (PACKAGE / "xiaomi-piano-firmware.spec").read_text()
    commit = re.search(r"^%global\s+sunuefi_commit\s+(\w+)$", spec, re.M).group(1)
    sources = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
    assert commit == sources["sunuefi"]["commit"], (commit, sources["sunuefi"]["commit"])
    assert ("Project-SunUEFI-%{sunuefi_commit}.tar.gz" in spec
            and "firmware-overlays.py" in spec), "spec must ship the overlay archive and script"
    assert (PACKAGE / "firmware-overlays.py").is_file()

    print("OK: overlay manifest, checksums, unsafe names and the pinned commit behave")


if __name__ == "__main__":
    main()
