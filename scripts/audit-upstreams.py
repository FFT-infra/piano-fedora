#!/usr/bin/env python3
"""只读核验已登记上游的当前提交与 fork 来源，不修改构建 pin。"""
import argparse
import concurrent.futures
import datetime
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]


def github(endpoint):
    result = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True, timeout=45)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "GitHub API failed")
    return json.loads(result.stdout)


def inspect_repository(row, lock):
    record = {"repo": row["repo"], "decision": row["decision"], "status": "unverified"}
    try:
        metadata = github("repos/" + row["repo"])
        source = lock.get(row.get("lock_key"))
        ref = source["ref"] if source else row["ref"] or metadata["default_branch"]
        commit = github("repos/" + row["repo"] + "/commits/" + quote(ref, safe=""))
        sha = commit["sha"]
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("invalid remote commit")
        record.update(status="verified", canonical_repo=metadata["full_name"], ref=ref,
                      commit=sha, pushed_at=metadata.get("pushed_at"),
                      default_branch=metadata["default_branch"],
                      archived=metadata["archived"], fork=metadata["fork"],
                      parent=metadata.get("parent", {}).get("full_name"),
                      commit_date=commit["commit"]["committer"]["date"],
                      subject=commit["commit"]["message"].splitlines()[0])
        # reference_commit 是上次复核时记下的提交。没有它，这份审计只能拍快照，
        # 报不出"谁更新了"；有它才谈得上发现上游变化。
        if row.get("reference_commit"):
            baseline = row["reference_commit"]
            record.update(reference_commit=baseline, updated=sha != baseline)
        if row.get("lock_key"):
            pin = lock[row["lock_key"]]["commit"]
            record.update(build_pin=pin, matches_pin=sha == pin)
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        # 404 可能是删除、改名或无权限；不能推导出不存在公开替代实现。
        record.update(status="unverified", error=str(exc))
    print(record["status"] + " " + row["repo"], file=sys.stderr, flush=True)
    return record


def record_baseline(path, records):
    """把本次核到的提交写回登记表，作为下次比对的上次复核基线。

    这是唯一会写 docs/ 的路径，必须显式给 --record-baseline；未核验的仓库保持原样。
    """
    document = json.loads(path.read_text())
    by_repo = {row["repo"]: row for row in document["repositories"]}
    written = 0
    for record in records:
        if record["status"] != "verified":
            continue
        row = by_repo[record["repo"]]
        if row.get("reference_commit") != record["commit"]:
            row["reference_commit"] = record["commit"]
            row["reference_date"] = record["commit_date"][:10]
            written += 1
    document["checked_date"] = datetime.date.today().isoformat()
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return written


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=ROOT / "build/upstream-audit.json")
    ap.add_argument("--repo", action="append", help="仅核验指定的已登记仓库，可重复")
    ap.add_argument("--execute", action="store_true", help="执行只读 GitHub API 查询")
    ap.add_argument("--record-baseline", action="store_true",
                    help="把本次核到的提交写回 docs/upstreams.json，供下次比对")
    args = ap.parse_args()
    if args.record_baseline and not args.execute:
        ap.error("--record-baseline 需要与 --execute 一起用")
    rows = json.loads((ROOT / "docs/upstreams.json").read_text())["repositories"]
    if args.repo:
        unknown = set(args.repo) - {row["repo"] for row in rows}
        if unknown:
            ap.error("未登记的仓库：" + ", ".join(sorted(unknown)))
        rows = [row for row in rows if row["repo"] in args.repo]
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        ap.error("审计输出只能写入 build/")
    if not args.execute:
        print(json.dumps({"repositories": [row["repo"] for row in rows], "output": str(output)}, indent=2))
        return 0
    lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(lambda row: inspect_repository(row, lock), rows))
    report = {"schema_version": 1, "started_at": started,
              "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "method": "GitHub API repository metadata and exact selected ref commit",
              "repositories": records}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    failures = sum(row["status"] != "verified" for row in records)
    compared = [row for row in records if row.get("updated") is not None]
    updated = [row for row in compared if row["updated"]]
    summary = {"total": len(records), "verified": len(records) - failures,
               "unverified": failures, "baselined": len(compared),
               "updated": len(updated), "output": str(output)}
    if args.record_baseline:
        summary["baseline_written"] = record_baseline(ROOT / "docs/upstreams.json", records)
    print(json.dumps(summary, ensure_ascii=False))
    for row in updated:
        print("UPDATED " + row["repo"] + " " + row["reference_commit"][:12] + " -> " + row["commit"][:12]
              + "  " + row.get("subject", ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
