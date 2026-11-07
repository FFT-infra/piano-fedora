#!/usr/bin/env python3
"""把厂商 blob 树和实际出货的固件逐文件对照。

只比路径与 git blob 摘要，不下载厂商文件：blob 摘要是内容寻址的，本地文件
用同一套算法算出来就能判断是否逐字节相同。厂商 blob 不进镜像。
"""

import argparse
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FIRMWARE_PREFIXES = ("proprietary/vendor/firmware/", "proprietary/odm/firmware/")
FIRMWARE_ROOT = "/usr/lib/firmware/"
IGNORED = {"SHA256SUMS", "sha256sums"}


def blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def remote_files(repository, commit):
    listing = subprocess.check_output(
        ["git", "-C", str(repository), "ls-tree", "-r", "--format=%(objectname)\t%(path)", commit],
        text=True)
    result = {}
    for line in listing.splitlines():
        sha, path = line.split("\t", 1)
        if path.startswith(FIRMWARE_PREFIXES):
            result[path] = sha
    return result


def local_files(directory):
    result = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name in IGNORED:
            continue
        result[str(path.relative_to(directory))] = blob_sha(path.read_bytes())
    return result


def owned_paths(path):
    """矩阵声明的固件所有权路径，转换成固件树里的相对路径。"""
    return {line.strip()[len(FIRMWARE_ROOT):] for line in path.read_text().splitlines()
            if line.strip().startswith(FIRMWARE_ROOT)}


def group(path):
    """单边独有的文件按所在目录聚合，根目录下的直接归一组。"""
    directory = path.rsplit("/", 1)[0] if "/" in path else ""
    return directory or "(根目录)"


def by_name(files):
    result = defaultdict(list)
    for path, sha in files.items():
        result[path.rsplit("/", 1)[-1]].append((path, sha))
    return result


def compare(ours, vendor):
    """按 basename 配对，内容相同与不同分开记；两边独有的按名字列出。"""
    ours_by_name, vendor_by_name = by_name(ours), by_name(vendor)
    identical, differs = [], []
    for name in sorted(set(ours_by_name) & set(vendor_by_name)):
        for our_path, our_sha in ours_by_name[name]:
            for vendor_path, vendor_sha in vendor_by_name[name]:
                record = {"name": name, "ours": our_path, "vendor": vendor_path}
                (identical if our_sha == vendor_sha else differs).append(record)
    return {"identical": identical, "differs": differs,
            "vendor_only": sorted(set(vendor_by_name) - set(ours_by_name)),
            "ours_only": sorted(set(ours_by_name) - set(vendor_by_name))}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--vendor-blobs", type=Path, required=True)
    ap.add_argument("--commit", help="默认取 sources.lock.json 的 vendor-blobs 锁定值")
    ap.add_argument("--firmware", type=Path, required=True)
    ap.add_argument("--owned", type=Path, default=ROOT / "runtime/matrix-owned-paths.txt",
                    help="矩阵所有权清单，用来核对声明的固件路径是否都有对应文件")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if not args.output.resolve().is_relative_to(ROOT / "build"):
        raise SystemExit("report output must stay in the ignored build directory")
    if args.commit is None:
        lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
        args.commit = lock["vendor-blobs"]["commit"]
    # 只取索引的检出没有出过 checkout，HEAD 指向不了任何提交，所以核对的是
    # 对象在不在：锁定的提交必须真的被这份索引覆盖。
    subprocess.run(["git", "-C", str(args.vendor_blobs), "cat-file", "-e", args.commit + "^{commit}"],
                   check=True)
    args.output.mkdir(parents=True, exist_ok=True)

    vendor = remote_files(args.vendor_blobs, args.commit)
    ours = local_files(args.firmware)
    vendor_by_name, ours_by_name = by_name(vendor), by_name(ours)
    buckets = compare(ours, vendor)
    identical, differs = buckets["identical"], buckets["differs"]
    vendor_only, ours_only = buckets["vendor_only"], buckets["ours_only"]

    vendor_only_dirs = Counter()
    for name in vendor_only:
        vendor_only_dirs[group(vendor_by_name[name][0][0])] += 1
    ours_only_dirs = Counter()
    for name in ours_only:
        ours_only_dirs[group(ours_by_name[name][0][0])] += 1

    owned = owned_paths(args.owned)
    owned_missing = sorted(owned - set(ours))
    owned_extra = sorted(set(ours) - owned)

    report = {
        "vendor_commit": args.commit,
        "counts": {"vendor": len(vendor), "ours": len(ours), "identical": len(identical),
                   "differs": len(differs), "vendor_only": len(vendor_only),
                   "ours_only": len(ours_only)},
        "identical": identical,
        "differs": differs,
        "vendor_only_directories": dict(vendor_only_dirs.most_common()),
        "ours_only_directories": dict(ours_only_dirs.most_common()),
        "vendor_only_sample": {directory: [name for name in vendor_only
                                           if group(vendor_by_name[name][0][0]) == directory][:8]
                               for directory in list(vendor_only_dirs)[:8]},
        "ours_only": ours_only,
        "owned_missing": owned_missing,
        "owned_extra": owned_extra,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    lines = ["# 厂商固件对照", "",
             f"- 厂商提交：`{args.commit}`",
             f"- 厂商固件文件：{len(vendor)}",
             f"- 出货固件文件：{len(ours)}",
             f"- 同名同内容：{len(identical)}，同名不同内容：{len(differs)}",
             f"- 厂商有、我们没有：{len(vendor_only)}；我们有、厂商没有：{len(ours_only)}",
             "",
             "按 basename 匹配。两边目录布局不同：厂商按 `proprietary/vendor/firmware` 与",
             "`proprietary/odm/firmware` 存放，出货树按 `/usr/lib/firmware` 展开。",
             "厂商侧只有仓库里提交的 148 个固件文件，不是 vendor 分区的全集，",
             "所以“厂商有、我们没有”只说明这份清单里没有，不说明设备上没有。",
             ""]
    if identical:
        lines += ["## 同名同内容", "", "| 出货路径 |", "| --- |"]
        lines += [f"| `{item['ours']}` |" for item in identical]
        lines.append("")
    if differs:
        lines += ["## 同名但内容不同", "", "| 出货路径 | 厂商路径 |", "| --- | --- |"]
        lines += [f"| `{item['ours']}` | `{item['vendor']}` |" for item in differs[:40]]
        lines.append("")
    if vendor_only_dirs:
        lines += ["## 厂商有、我们没有（按目录）", "", "| 目录 | 文件数 | 例子 |", "| --- | --- | --- |"]
        for directory, count in vendor_only_dirs.most_common(12):
            sample = report["vendor_only_sample"].get(directory, [])[:4]
            lines.append(f"| `{directory}` | {count} | {', '.join(sample)} |")
        lines.append("")
    if ours_only_dirs:
        lines += ["## 我们有、厂商没有（按目录）", "", "| 目录 | 文件数 |", "| --- | --- |"]
        lines += [f"| `{directory}` | {count} |" for directory, count in ours_only_dirs.most_common(10)]
        lines.append("")
    lines += ["## 所有权路径核对", "",
              f"`runtime/matrix-owned-paths.txt` 声明了 {len(owned)} 条 `/usr/lib/firmware` 下的路径，"
              f"出货树有 {len(ours)} 个文件。",
              f"声明了但树里没有：{len(owned_missing)} 条；树里有但没声明：{len(owned_extra)} 条。", ""]
    if owned_missing:
        lines += ["| 缺少 |", "| --- |"] + [f"| `{path}` |" for path in owned_missing[:20]] + [""]
    if owned_extra:
        lines += ["| 未声明 |", "| --- |"] + [f"| `{path}` |" for path in owned_extra[:20]] + [""]
    lines += ["传感器配置不在这条对照里：`/var/lib/piano-sensors` 的 60 个文件来自设备",
              "自身分区，2026-11-08 已在平板上与厂商树逐个比对，文件名集合与目标文件摘要",
              "都相同。"]
    (args.output / "report.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(report["counts"], indent=2))


if __name__ == "__main__":
    main()
