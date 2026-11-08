#!/usr/bin/env python3
"""固件对照脚本的契约：blob 摘要、basename 配对、目录聚合与所有权路径解析。"""

import importlib.util
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location("compare_vendor_blobs",
                                                  ROOT / "scripts/compare-vendor-blobs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    module = load()

    with tempfile.TemporaryDirectory(prefix="vendor-blobs-") as directory:
        work = Path(directory)
        # blob 摘要必须与 git 自己算的一致，否则跨仓库比对全是假差异。
        sample = work / "sample.bin"
        sample.write_bytes(b"piano firmware\0\x01\x02")
        expected = subprocess.check_output(["git", "hash-object", str(sample)], text=True).strip()
        assert module.blob_sha(sample.read_bytes()) == expected, module.blob_sha(sample.read_bytes())

        firmware = work / "firmware"
        (firmware / "novatek").mkdir(parents=True)
        (firmware / "novatek/nt36532.bin").write_bytes(b"same")
        (firmware / "adsp.b00").write_bytes(b"only ours")
        (firmware / "SHA256SUMS").write_text("ignored\n")
        ours = module.local_files(firmware)
        assert "SHA256SUMS" not in ours, ours
        assert set(ours) == {"novatek/nt36532.bin", "adsp.b00"}, ours

        repo = work / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        kept = repo / "proprietary/odm/firmware"
        kept.mkdir(parents=True)
        (kept / "nt36532.bin").write_bytes(b"same")
        (kept / "MCU_Upgrade.bin").write_bytes(b"vendor only")
        (repo / "proprietary/vendor/lib64").mkdir(parents=True)
        (repo / "proprietary/vendor/lib64/ignored.so").write_bytes(b"not firmware")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
                        "-c", "user.name=t", "commit", "-qm", "fixture"], check=True)
        commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                         text=True).strip()
        vendor = module.remote_files(repo, commit)
        # 只收 proprietary/{vendor,odm}/firmware 下的文件，其余目录不进对照。
        assert set(vendor) == {"proprietary/odm/firmware/nt36532.bin",
                               "proprietary/odm/firmware/MCU_Upgrade.bin"}, vendor

        buckets = module.compare(ours, vendor)
        assert [item["name"] for item in buckets["identical"]] == ["nt36532.bin"], buckets
        assert buckets["differs"] == [], buckets
        assert buckets["vendor_only"] == ["MCU_Upgrade.bin"], buckets
        assert buckets["ours_only"] == ["adsp.b00"], buckets

        # 同名不同内容必须进 differs，不能因为 basename 相同就算命中。
        (kept / "nt36532.bin").write_bytes(b"different")
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
                        "-c", "user.name=t", "commit", "-qam", "change"], check=True)
        changed = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                          text=True).strip()
        buckets = module.compare(ours, module.remote_files(repo, changed))
        assert [item["name"] for item in buckets["differs"]] == ["nt36532.bin"], buckets
        assert buckets["identical"] == [], buckets

    assert module.group("proprietary/odm/firmware/x.bin") == "proprietary/odm/firmware"
    assert module.group("ath12k/WCN7850/hw2.0/board.bin") == "ath12k/WCN7850/hw2.0"
    assert module.group("adsp.b00") == "(根目录)"

    with tempfile.TemporaryDirectory(prefix="owned-") as directory:
        paths = Path(directory) / "owned.txt"
        paths.write_text("/usr/lib/firmware/novatek/a.bin\n/etc/other\n/usr/lib/firmware/b.bin\n")
        assert module.owned_paths(paths) == {"novatek/a.bin", "b.bin"}, module.owned_paths(paths)

    print("OK: blob digests, basename pairing, directory grouping and ownership paths behave")


if __name__ == "__main__":
    main()
