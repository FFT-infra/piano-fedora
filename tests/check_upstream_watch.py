#!/usr/bin/env python3
"""上游看板：只把白名单里已漂移的 pin 写回锁文件，未核验不参与。"""
import importlib.util
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("watch", ROOT / "scripts/upstream-watch.py")
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


def digest(seed):
    return (seed * 40)[:40]


AUDIT = {"schema_version": 1, "repositories": [
    # 白名单内的用户态上游：已漂移，报告与 bump 都要处理。
    {"repo": "google/crosvm", "ref": "main", "status": "verified",
     "commit": digest("2"), "subject": "devices: fix | pipe"},
    # 内核/UEFI 一类：已漂移，只能报告，不能自动改 pin。
    {"repo": "slwyts/Project-SunUEFI", "ref": "main", "status": "verified",
     "commit": digest("6"), "subject": "fix(display)"},
    # 白名单内但没有漂移。
    {"repo": "code002-2/librevm", "ref": "main", "status": "verified",
     "commit": digest("4"), "subject": "cpus"},
    # 核验失败：不能当成漂移，也不能写 pin。
    {"repo": "blu-sharky/uperf", "ref": "main", "status": "unverified",
     "error": "HTTP 503"},
    # 只作参照的研究上游：没有 lock_key，不参与报告。
    {"repo": "linux-msm/audioreach-topology", "status": "verified",
     "commit": digest("9"), "subject": "topology"},
]}

UPSTREAMS = {"repositories": [
    {"repo": "google/crosvm", "lock_key": "crosvm", "decision": "consume"},
    {"repo": "slwyts/Project-SunUEFI", "lock_key": "sunuefi", "decision": "consume"},
    {"repo": "code002-2/librevm", "lock_key": "librevm", "decision": "consume"},
    {"repo": "blu-sharky/uperf", "lock_key": "uperf", "decision": "consume"},
    {"repo": "linux-msm/audioreach-topology", "decision": "reference"},
]}

LOCK = {"schema_version": 1, "sources": {
    "crosvm": {"url": "https://github.com/google/crosvm.git", "ref": "main", "commit": digest("1")},
    "sunuefi": {"url": "https://github.com/slwyts/Project-SunUEFI.git", "ref": "main", "commit": digest("3")},
    "librevm": {"url": "https://github.com/code002-2/librevm.git", "ref": "main", "commit": digest("4")},
    "uperf": {"url": "https://github.com/blu-sharky/uperf.git", "ref": "main", "commit": digest("5")},
}}


def write(root, name, document):
    path = root / name
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return path


def load_rows(root):
    return watch.load(write(root, "audit.json", AUDIT),
                      write(root, "upstreams.json", UPSTREAMS),
                      write(root, "sources.lock.json", LOCK))


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    _, rows = load_rows(root)
    result = watch.summary(rows)
    assert result == {"drift": ["crosvm", "sunuefi"], "auto": ["crosvm"],
                      "unverified": ["uperf"]}, result

    text = watch.render(None, rows, "2026-10-20T00:00:00+00:00")
    assert "`crosvm`" in text and "自动升级 PR" in text
    assert "`sunuefi`" in text and "人工评审" in text
    # markdown 表格里的竖线必须转义，否则一列提交主题会把表格拆散。
    assert "devices: fix \\| pipe" in text
    assert "未能核验" in text and "HTTP 503" in text
    assert "audioreach" not in text
    # 未核验的 uperf 与未漂移的 librevm 都不进表。
    assert "`uperf`" not in text.split("### 未能核验")[0]

    # bump 只动白名单里已漂移的那一项，其余字节不变。
    lock = write(root, "lock.json", LOCK)
    pr_body = root / "pr.md"
    changed = watch.bump(lock, rows)
    assert changed == ["crosvm"], changed
    written = json.loads(lock.read_text())
    assert written["sources"]["crosvm"]["commit"] == digest("2")
    assert written["sources"]["sunuefi"]["commit"] == digest("3"), "manual upstream moved"
    assert written["sources"]["uperf"]["commit"] == digest("5"), "unverified upstream moved"
    assert written["sources"]["crosvm"]["ref"] == "main", "bump must not change the ref"
    assert lock.read_text() == json.dumps(written, ensure_ascii=False, indent=2) + "\n"
    body = watch.render_pull_request(rows, changed, "2026-10-20")
    assert "`crosvm`" in body and "`sunuefi`" not in body

    # 再 bump 一次没有变化，说明这一步是幂等的。
    assert watch.bump(lock, rows) == []

    # 全部一致时不报漂移。
    clean = watch.load(write(root, "audit-clean.json", {"repositories": [
        {"repo": "google/crosvm", "status": "verified", "commit": digest("1")},
    ]}), write(root, "upstreams-clean.json", UPSTREAMS),
        write(root, "lock-clean.json", LOCK))[1]
    assert watch.summary(clean) == {"drift": [], "auto": [], "unverified": []}
    assert "所有锁定 ref 都还指向 pin" in watch.render(None, clean)

print("OK: upstream watch reports drift and bumps only whitelisted pins")
