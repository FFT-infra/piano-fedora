#!/usr/bin/env python3
"""只读巡检 Fedora 稳定库里重编包对应的官方版本，与台账基准比对。

`make test` 不联网，这一步由 .github/workflows/upstream-watch.yml 每周调用。
它只报告，不改锁文件、不关 issue、不触发构建。输出 JSON 给 watch 脚本并进
漂移报告：台账基准版本与 Fedora 稳定库不一致时，提示「先重基补丁集，再看
drop_when 是否满足」，避免 Conflicts 把官方子包挡在外面。

数据源是 Fedora Packages 的 JSON（packages.fedoraproject.org）。取不到时记
unverified，不把「查不到」当成「没更新」。
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path
import datetime

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "scripts" / "fedora-overlays.json"
RELEASEVER = json.loads((ROOT / "product.json").read_text())["target"]["releasever"]
def pkgs_url(name, slug):
    return ("https://packages.fedoraproject.org/pkgs/{name}/{slug}/index.html"
            .format(name=name, slug=slug or name))
TIMEOUT = 30


def stable_version(name, slug):
    """从 Fedora Packages 的 releases 概览里取该分支的 stable 版本。

    概览表 Stable 列在 Testing 列之前，第一个 .fcNN 匹配就是稳定版；测试版
    排在后面，不会被误当成 stable。拿不到就抛异常，交给调用方记 unverified。
    """
    import re
    with urllib.request.urlopen(pkgs_url(name, slug), timeout=TIMEOUT) as response:
        page = response.read().decode("utf-8", "replace")
    versions = re.findall(
        r">\s*([0-9][0-9A-Za-z.^~+_-]*?)\.fc" + str(RELEASEVER) + r"\s*<", page)
    if not versions:
        raise ValueError(f"no stable version for {name} in Fedora {RELEASEVER}")
    # 去掉 -1 这种 release 号，只留上游版本号。
    return versions[0].split("-")[0]


def render_report(result):
    """把巡检结果渲染成可追加到漂移 issue 的 markdown。"""
    when = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    lines = [f"## Fedora 重编包巡检（{when}）", ""]
    moved = [r for r in result["packages"] if r.get("moved")]
    unverified = [r for r in result["packages"] if r["status"] != "verified"]
    if not moved and not unverified:
        lines.append("Fedora 稳定库版本与全部重编基准一致，没有需要评估的重基。")
        return "\n".join(lines) + "\n"
    if moved:
        lines += ["| 包 | 重编基准 | Fedora 稳定版 | 需要做的事 |",
                  "| --- | --- | --- | --- |"]
        for r in moved:
            note = r["rebase_note"].replace("|", "\\|")
            lines.append(
                f"| `{r['name']}` | `{r['base_version']}` | `{r['stable_version']}` | "
                f"{note} |")
        lines += ["", "是否达到 `drop_when` 要人工判断。", ""]
    if unverified:
        lines += ["### 未能核验", ""]
        for r in unverified:
            lines.append(f"- `{r['name']}`：{r.get('error') or '未知错误'}")
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path)
    ap.add_argument("--report", type=Path, help="额外写一段可追加到漂移 issue 的 markdown")
    args = ap.parse_args(argv)

    data = json.loads(LEDGER.read_text())
    rows = []
    for overlay in data["overlays"]:
        for pkg in overlay["fedora_packages"]:
            # base_version 是我们基于哪个 SRPM 重编，不是 Fedora 当前稳定版。
            # 两者不同只说明「该评估重基或删除」，不代表台账写错。
            row = {"overlay": overlay["key"], "name": pkg["name"],
                   "base_version": pkg["base_version"], "status": "verified",
                   "drop_when": overlay["drop_when"],
                   "rebase_note": overlay["rebase_note"]}
            try:
                row["stable_version"] = stable_version(pkg["name"],
                                                       pkg.get("pkgs_slug"))
                row["moved"] = row["stable_version"] != pkg["base_version"]
            except (OSError, ValueError, KeyError) as exc:
                row.update(status="unverified", error=str(exc), moved=False)
            rows.append(row)

    result = {"schema_version": 1, "releasever": str(RELEASEVER),
              "note": "moved 表示稳定库版本与重编基准不同，按 rebase_note 评估重基。",
              "packages": rows,
              "moved": [r["name"] for r in rows if r.get("moved")],
              "unverified": [r["name"] for r in rows if r["status"] != "verified"]}
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(render_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
