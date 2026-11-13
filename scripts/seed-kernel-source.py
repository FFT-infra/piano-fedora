#!/usr/bin/env python3
"""把封存序列的基线对象预置进上游工具会用的 bare 仓库。

prepare_release_kernel.py 只在对象缺失时才 fetch，而它固定的地址是
git.kernel.org。GitHub Actions 的出口连它经常 135 秒超时，整条构建就停在
准备内核源码这一步。这里先从 stable 维护者的 GitHub 镜像取同一个 tag，
核对它就是封存序列声明的基线提交与基线树；对象已经在仓库里时什么都不做。
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

SERIES = "patches/linux/7.3/series.json"
MIRROR = "https://github.com/gregkh/linux.git"


def git(repository, *args, check=True):
    result = subprocess.run(
        ["git", "-C", str(repository), *map(str, args)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and result.returncode:
        raise ValueError("git " + " ".join(map(str, args)) + ": " + result.stderr.strip()[:2000])
    return result.stdout.strip()


def has_commit(repository, commit):
    return subprocess.run(
        ["git", "-C", str(repository), "cat-file", "-e", commit + "^{commit}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def load_policy(sun, series=SERIES):
    data = json.loads((sun / series).read_text())
    base = data.get("base_commit")
    version = data.get("kernel_version")
    tree = data.get("base_tree")
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("series base_commit is not a full object id")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise ValueError("series kernel_version is not a stable version")
    if not isinstance(tree, str) or not re.fullmatch(r"[0-9a-f]{40}", tree):
        raise ValueError("series base_tree is not a full object id")
    return {"base_commit": base, "kernel_version": version, "base_tree": tree,
            "source_url": data.get("source_url")}


def verify(repository, policy):
    commit = git(repository, "rev-parse", policy["base_commit"] + "^{commit}")
    if commit != policy["base_commit"]:
        raise ValueError("the fetched tag is not the sealed base commit")
    tree = git(repository, "rev-parse", policy["base_commit"] + "^{tree}")
    if tree != policy["base_tree"]:
        raise ValueError("the fetched commit tree is " + tree + ", not the sealed base tree")


def seed(sun, mirror=MIRROR, series=SERIES):
    """把基线提交放进上游工具会用的 bare 仓库；对象已存在时不联网。"""
    sun = Path(sun).resolve()
    policy = load_policy(sun, series)
    repository = sun / "build/kernel-sources/linux-piano.git"
    if not repository.is_dir():
        repository.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "--bare", str(repository)], check=True)
    if has_commit(repository, policy["base_commit"]):
        verify(repository, policy)
        print("kernel source already has", policy["base_commit"])
        return repository
    tag = "v" + policy["kernel_version"]
    try:
        git(repository, "fetch", "--no-tags", "--depth=1", mirror,
            "refs/tags/" + tag + ":refs/tags/" + tag)
    except ValueError as error:
        # 镜像改名或删 tag 时退回封存序列自己声明的地址，行为不比没有这一步差。
        print("mirror fetch failed: " + str(error), file=sys.stderr)
        print("falling back to " + str(policy["source_url"]), file=sys.stderr)
        git(repository, "fetch", "--no-tags", "--depth=1",
            policy["source_url"], policy["base_commit"])
    verify(repository, policy)
    print("kernel source seeded with", tag, "at", policy["base_commit"])
    return repository


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sunuefi", type=Path, required=True)
    ap.add_argument("--mirror", default=MIRROR)
    ap.add_argument("--series", default=SERIES)
    args = ap.parse_args()
    seed(args.sunuefi, args.mirror, args.series)


if __name__ == "__main__":
    main()
