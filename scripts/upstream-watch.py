#!/usr/bin/env python3
"""把上游审计结果整理成跟踪 issue 和白名单升级分支。

审计本身由 scripts/audit-upstreams.py --execute 完成（只读 GitHub API），
这里只消费它的 JSON：

- report：生成人看的 markdown + workflow 用的摘要（哪些漂移、哪些可自动升级）；
- bump：只改白名单上游的 commit，其余字段逐字节保留。

判断"漂移"用 build_pin 与上游当前 ref 指向的 commit 比较。审计里的 updated
依赖 docs/upstreams.json 的人工基线，基线过期就会误报，不能拿来做决定。
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 纯用户态、构建链短、CI 能兜住的上游：可以自动改 pin 开 PR，等人合并。
# 内核、UEFI、相机/触控、固件和 debian-piano 的改动无法用离线 CI 替代真机
# 验收，只报告不动 pin。
AUTO_BUMP = {
    "crosvm",
    "librevm",
    "uperf",
    "piano-mesa",
    "piano-sensors",
    "piano-mipps-auth",
}


def load(audit_path, upstreams_path, lock_path):
    audit = json.loads(Path(audit_path).read_text())
    upstreams = json.loads(Path(upstreams_path).read_text())
    lock = json.loads(Path(lock_path).read_text())
    key_of = {row["repo"]: row.get("lock_key")
              for row in upstreams["repositories"]
              if row.get("lock_key")}
    rows = []
    for record in audit["repositories"]:
        key = key_of.get(record["repo"])
        if not key or key not in lock["sources"]:
            continue
        source = lock["sources"][key]
        rows.append({
            "key": key,
            "repo": record["repo"],
            "ref": record.get("ref") or source.get("ref"),
            "pin": source["commit"],
            "head": record.get("commit"),
            "status": record["status"],
            "error": record.get("error"),
            "subject": record.get("subject"),
            "drift": record["status"] == "verified"
                     and record.get("commit") not in (None, source["commit"]),
        })
    rows.sort(key=lambda row: row["key"])
    return audit, rows


def disposition(row):
    if row["key"] in AUTO_BUMP:
        return "自动升级 PR"
    return "人工评审"


def render(audit, rows, today=None):
    moved = [row for row in rows if row["drift"]]
    broken = [row for row in rows if row["status"] != "verified"]
    auto = [row for row in moved if row["key"] in AUTO_BUMP]
    when = today or datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    lines = [
        "<!-- 由 .github/workflows/upstream-watch.yml 生成，不要手改 -->",
        f"## 上游漂移（{when}）",
        "",
        f"登记 {len(rows)} 个消费上游：{len(rows) - len(broken)} 个核验通过，"
        f"{len(moved)} 个 ref 已移动，{len(broken)} 个未能核验。",
        "",
    ]
    if moved:
        lines += ["| 上游 | ref | 锁定 | 上游当前 | 处置 | 最近提交 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for row in moved:
            compare = (f"https://github.com/{row['repo']}/compare/"
                       f"{row['pin']}...{row['head']}")
            lines.append(
                f"| `{row['key']}` | `{row['ref']}` | `{row['pin'][:8]}` | "
                f"[`{row['head'][:8]}`]({compare}) | {disposition(row)} | "
                f"{(row['subject'] or '').replace('|', '\\|')} |")
        lines.append("")
    if auto:
        lines += [f"白名单（可自动开 PR）：{', '.join('`' + row['key'] + '`' for row in auto)}", ""]
    if broken:
        lines += ["### 未能核验", ""]
        for row in broken:
            lines.append(f"- `{row['key']}`（{row['repo']}）：{row['error'] or '未知错误'}")
        lines.append("")
    if not moved and not broken:
        lines.append("所有锁定 ref 都还指向 pin。")
    return "\n".join(lines) + "\n"


def summary(rows):
    return {
        "drift": [row["key"] for row in rows if row["drift"]],
        "auto": [row["key"] for row in rows if row["drift"] and row["key"] in AUTO_BUMP],
        "unverified": [row["key"] for row in rows if row["status"] != "verified"],
    }


def bump(lock_path, rows):
    """把白名单里已漂移的 commit 写回锁文件，其余内容原样保留。"""
    changed = []
    document = json.loads(Path(lock_path).read_text())
    for row in rows:
        if not row["drift"] or row["key"] not in AUTO_BUMP:
            continue
        entry = document["sources"][row["key"]]
        if entry["commit"] == row["head"]:
            continue
        entry["commit"] = row["head"]
        changed.append(row["key"])
    if changed:
        Path(lock_path).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return changed


def render_pull_request(rows, changed, today=None):
    when = today or datetime.date.today().isoformat()
    lines = [
        f"自动跟进白名单上游的 pin（{when}）。",
        "",
        "只改 `sources.lock.json` 的 commit，ref 不动。合并前请确认：",
        "",
        "- 这个 PR 由 `GITHUB_TOKEN` 创建，GitHub 不会为它自动跑 workflow；需要手动 `gh workflow run host-check.yml` 或关掉重开。",
        "- `make test` 里的补丁、清单与源码 pin 校验会拒绝任何不一致。",
        "",
        "| 上游 | 旧 pin | 新 pin | 最近提交 |",
        "| --- | --- | --- | --- |",
    ]
    by_key = {row["key"]: row for row in rows}
    for key in changed:
        row = by_key[key]
        compare = f"https://github.com/{row['repo']}/compare/{row['pin']}...{row['head']}"
        lines.append(f"| `{key}` | `{row['pin'][:8]}` | [`{row['head'][:8]}`]({compare}) | "
                     f"{(row['subject'] or '').replace('|', '\\|')} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--audit", type=Path, default=ROOT / "build/upstream-audit.json")
    ap.add_argument("--upstreams", type=Path, default=ROOT / "docs/upstreams.json")
    ap.add_argument("--lock", type=Path, default=ROOT / "sources.lock.json")
    sub = ap.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="生成 markdown 报告与摘要")
    report.add_argument("--output", type=Path)
    report.add_argument("--summary", type=Path)
    report.add_argument("--today")
    upgrade = sub.add_parser("bump", help="把白名单里漂移的 pin 写回锁文件")
    upgrade.add_argument("--pr-body", type=Path)
    args = ap.parse_args()

    audit, rows = load(args.audit, args.upstreams, args.lock)
    if args.command == "report":
        text = render(audit, rows, args.today)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text)
        else:
            sys.stdout.write(text)
        if args.summary:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(json.dumps(summary(rows), indent=2) + "\n")
        return 0

    changed = bump(args.lock, rows)
    print(json.dumps(changed))
    if args.pr_body:
        args.pr_body.parent.mkdir(parents=True, exist_ok=True)
        args.pr_body.write_text(render_pull_request(rows, changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
