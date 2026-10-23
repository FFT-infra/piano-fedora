#!/usr/bin/env python3
"""解析一次 Release 的桌面绑定：每档一对 build-bundle 与 prepare-install run。

发布默认不填任何 run id：`--desktop all` 把每一档都放进去，run 按 tag 指向的
commit 解析。`desktop_runs` 是要手填时的入口：

    gnome=<bundle_run>:<prepare_run>,kde=...,niri

带 `=` 的条目固定那一档的 run，不带的按 commit 解析。单档发布用
`--desktop <档位>` 配 `--bundle-run` / `--prepare-run`。

每档的 run 都必须通过 `resolve-artifact-run.py` 的同一套校验：workflow 身份、
成功状态、artifact 未过期、提交与 tag 一致。commit 为空时直接报错，不允许
按最近 50 个成功 run 猜——那样可能把别的提交的产物发出去。
"""

import argparse
import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import piano_desktop  # noqa: E402

BUNDLE_WORKFLOW = "build-bundle.yml"
PREPARE_WORKFLOW = "prepare-install.yml"

ALL_DESKTOPS = "all"


class ReleaseError(Exception):
    """桌面绑定写错、指向未知桌面，或对应的 run 不存在。"""


def load_resolver():
    """按路径加载 `resolve-artifact-run.py`（文件名不是合法模块名）。"""
    path = ROOT / "scripts" / "resolve-artifact-run.py"
    loader = importlib.machinery.SourceFileLoader("resolve_artifact_run", str(path))
    spec = importlib.util.spec_from_loader("resolve_artifact_run", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def known_desktops():
    """发布只接受有桌面会话、且可构建的档位。"""
    return piano_desktop.matrix(desktops_only=True)


def bundle_artifacts(desktop):
    """每档的 bundle artifact 带档位后缀；四档共用一个 run 已不再成立。"""
    return [f"deploy-bundle-{desktop}"]


def prepare_artifacts(desktop):
    """root-install 按档命名；boot-tools 与 host-boot-tool 四档同源。"""
    return [f"root-install-{desktop}", "boot-tools", "host-boot-tool"]


def parse_spec(spec, known=None):
    """把 `desktop_runs` 拆成有序条目，拒绝重复、占位与未知桌面。"""
    known = list(known or known_desktops())
    entries = []
    seen = set()
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        desktop, separator, runs = item.partition("=")
        desktop = desktop.strip()
        bundle_run = prepare_run = ""
        if separator:
            bundle_run, colon, prepare_run = runs.partition(":")
            bundle_run, prepare_run = bundle_run.strip(), prepare_run.strip()
            if not colon or (bundle_run and not prepare_run) or (prepare_run and not bundle_run):
                raise ReleaseError(
                    f"desktop {desktop!r} needs both a build-bundle and a prepare-install "
                    "run, or neither (leave them empty to resolve by commit)")
        if desktop not in known:
            raise ReleaseError(
                f"desktop_runs names unknown or unbuildable desktop {desktop!r}; "
                f"known: {known}")
        if desktop in seen:
            raise ReleaseError(f"desktop_runs repeats desktop {desktop!r}")
        seen.add(desktop)
        entries.append({"desktop": desktop,
                        "bundle_run": bundle_run,
                        "prepare_run": prepare_run})
    if not entries:
        raise ReleaseError("desktop_runs is empty")
    return entries


def resolve_entries(entries, repository, commit, resolve_fn):
    """给每档的 run 重新跑一次 artifact 校验，返回带解析结果的条目。

    显式给了 run id 时仍传 commit：`resolve()` 会核对 run 的 head_sha，防止
    把别的提交的 bundle 混进这个 tag 的发布。
    """
    for entry in entries:
        desktop = entry["desktop"]
        try:
            entry["bundle_run"] = resolve_fn(
                repository, BUNDLE_WORKFLOW, bundle_artifacts(desktop),
                run_id=entry.get("bundle_run") or None, commit=commit)
            entry["prepare_run"] = resolve_fn(
                repository, PREPARE_WORKFLOW, prepare_artifacts(desktop),
                run_id=entry.get("prepare_run") or None, commit=commit)
        except ValueError as error:
            raise ReleaseError(f"{desktop}: {error}") from error
    return entries


def plan(spec=None, desktop="gnome", bundle_run=None, prepare_run=None,
         known=None, commit=None, repository=None, resolve_fn=None):
    """入口逻辑，测试用参数注入代替真实的 GitHub API。"""
    known = list(known or known_desktops())
    if not commit:
        # 不给 commit 时 resolve() 会扫最近 50 个成功 run，取第一个 artifact
        # 齐的。发布必须绑定 tag 指向的那次提交，这条捷径不能用在发布上。
        raise ReleaseError("a release needs the tag commit; refusing to resolve runs "
                           "without one")
    if spec:
        entries = parse_spec(spec, known)
    elif desktop == ALL_DESKTOPS:
        entries = [{"desktop": name, "bundle_run": "", "prepare_run": ""}
                   for name in known]
    else:
        if desktop not in known:
            raise ReleaseError(
                f"desktop {desktop!r} is unknown or unbuildable; known: {known}")
        entries = [{"desktop": desktop,
                    "bundle_run": bundle_run or "",
                    "prepare_run": prepare_run or ""}]
    resolve_fn = resolve_fn or load_resolver().resolve
    return resolve_entries(entries, repository, commit, resolve_fn)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--desktop-runs", default="",
                        help="gnome=<bundle_run>:<prepare_run>,kde=...; 不带 = 表示按 commit 解析")
    parser.add_argument("--desktop", default="gnome",
                        help=f"单档发布时的桌面档；{ALL_DESKTOPS} 表示全部档位")
    parser.add_argument("--bundle-run", default="")
    parser.add_argument("--prepare-run", default="")
    parser.add_argument("--commit", required=True,
                        help="tag 指向的提交；每档的 run 都必须在这个提交上")
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        rows = plan(spec=args.desktop_runs, desktop=args.desktop,
                    bundle_run=args.bundle_run, prepare_run=args.prepare_run,
                    commit=args.commit, repository=args.repository)
    except ReleaseError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    for row in rows:
        print(f"{row['desktop']}: bundle {row['bundle_run']} "
              f"prepare {row['prepare_run']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
