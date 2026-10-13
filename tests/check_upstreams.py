#!/usr/bin/env python3
"""研究清单去重；实际消费的源码必须能追溯到构建锁。"""
import importlib.util
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
rows = json.loads((ROOT / "docs/upstreams.json").read_text())["repositories"]
lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
repos = [row["repo"] for row in rows]
assert len(repos) == len(set(repos)), "duplicate repository"
assert all(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) for repo in repos)
for row in rows:
    assert row["decision"] in ("reference", "consume")
    assert bool(row.get("lock_key")) == (row["decision"] == "consume"), "reference source entered the build lock"
    if row["decision"] == "consume":
        assert lock[row["lock_key"]]["url"] == "https://github.com/" + row["repo"] + ".git"
for name, entry in lock.items():
    assert any(row.get("lock_key") == name for row in rows), "lock source missing from inventory: " + name

spec = importlib.util.spec_from_file_location("audit", ROOT / "scripts/audit-upstreams.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
fixture = {"repo": "example/linux", "ref": "piano/v1", "decision": "reference"}
metadata = {"full_name": "example/linux", "default_branch": "main", "archived": False, "fork": False}
commit = {"sha": "a" * 40, "commit": {"committer": {"date": "2026-10-13T00:00:00Z"}, "message": "fixture"}}
with patch.object(audit, "github", side_effect=[metadata, commit]) as fetch:
    result = audit.inspect_repository(fixture, {})
    assert result["commit"] == "a" * 40
    assert fetch.call_args.args[0].endswith("/commits/piano%2Fv1")
with patch.object(audit, "github", side_effect=ValueError("HTTP 404")):
    assert audit.inspect_repository(fixture, {})["status"] == "unverified"
with patch.object(audit, "github", side_effect=FileNotFoundError("gh unavailable")):
    assert audit.inspect_repository(fixture, {})["status"] == "unverified"
with patch.object(audit, "github", side_effect=[metadata, commit]) as fetch:
    consumer = {**fixture, "lock_key": "kernel", "decision": "consume"}
    result = audit.inspect_repository(consumer, {"kernel": {"ref": "piano/v2", "commit": "b" * 40}})
    assert fetch.call_args.args[0].endswith("/commits/piano%2Fv2"), "catalog must not override the build lock"
    assert result["matches_pin"] is False
# 没有基线的行只拍快照；有基线才谈得上发现上游更新。
with patch.object(audit, "github", side_effect=[metadata, commit]):
    assert "updated" not in audit.inspect_repository(fixture, {}), "unbaselined row must not claim drift"
with patch.object(audit, "github", side_effect=[metadata, commit, metadata, commit]):
    assert audit.inspect_repository({**fixture, "reference_commit": "a" * 40}, {})["updated"] is False
    drifted = audit.inspect_repository({**fixture, "reference_commit": "b" * 40}, {})
    assert drifted["updated"] is True and drifted["reference_commit"] == "b" * 40
# 基线回写只碰已核验的行，且写进 reference_commit/reference_date。
registry = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
json.dump({"schema_version": 1, "checked_date": "2026-10-13",
           "repositories": [{"repo": "example/linux", "decision": "reference"},
                            {"repo": "example/gone", "decision": "reference"}]}, registry)
registry.close()
written = audit.record_baseline(Path(registry.name), [
    {"repo": "example/linux", "status": "verified", "commit": "a" * 40,
     "commit_date": "2026-10-13T00:00:00Z"},
    {"repo": "example/gone", "status": "unverified", "error": "HTTP 404"}])
after = json.loads(Path(registry.name).read_text())
rows_after = {row["repo"]: row for row in after["repositories"]}
assert written == 1, written
assert rows_after["example/linux"]["reference_commit"] == "a" * 40
assert rows_after["example/linux"]["reference_date"] == "2026-10-13"
assert "reference_commit" not in rows_after["example/gone"], "unverified row gained a baseline"
Path(registry.name).unlink()
print(f"OK: {len(rows)} unique upstreams, locked consumers, selected-ref and failure evidence")
