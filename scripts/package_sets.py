#!/usr/bin/env python3
"""读取 `package-sets.json`：仓库里包集合的唯一真源。

构建矩阵、COPR 发布顺序、rootfs 装配、设备验收与文档包数都从这里派生。
`tests/check_package_sets.py` 断言各处不再另写清单。

命令行模式给 shell 与测试用：

    python3 scripts/package_sets.py --set matrix           # 每行一个包名
    python3 scripts/package_sets.py --set matrix --arch aarch64
    python3 scripts/package_sets.py --set runtime
    python3 scripts/package_sets.py --count all            # COPR 发布的包总数
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "package-sets.json"


def load(path=SOURCE):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def matrix(data=None, arch=None):
    """构建矩阵里的包名，保持 JSON 里的顺序（即 COPR 构建顺序）。

    带 `arches` 的包只在列出的架构上构建；不给 arch 时全部返回。
    """
    data = data or load()
    rows = data["matrix"]
    if arch is not None:
        rows = [row for row in rows
                if "arches" not in row or arch in row["arches"]]
    return [row["name"] for row in rows]


def kernel(data=None):
    data = data or load()
    names = [row["name"] for row in data["matrix"] if row.get("kernel")]
    if len(names) != 1:
        raise ValueError(f"package-sets.json must name exactly one kernel package, got {names}")
    return names[0]


def runtime(data=None):
    return list((data or load())["runtime"])


def graphics_binaries(data=None):
    return list((data or load())["graphics"]["binaries"])


def published(data=None):
    """COPR 主项目发布的全部包名：矩阵 + 运行时 + 图形栈二进制。"""
    data = data or load()
    return matrix(data) + runtime(data) + graphics_binaries(data)


def extras(data=None, key="order"):
    return list((data or load())["extras"][key])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--set", dest="which", default="matrix",
                        choices=("matrix", "runtime", "graphics", "published", "extras"))
    parser.add_argument("--arch", help="只列在该架构上构建的矩阵包")
    parser.add_argument("--count", action="store_true",
                        help="只打印数量，供文档与测试核对")
    parser.add_argument("--count-all", action="store_true",
                        help="打印 COPR 主项目发布的包数")
    args = parser.parse_args(argv)

    data = load()
    if args.count_all:
        print(len(published(data)))
        return 0
    if args.which == "matrix":
        names = matrix(data, args.arch)
    elif args.which == "runtime":
        names = runtime(data)
    elif args.which == "graphics":
        names = graphics_binaries(data)
    elif args.which == "published":
        names = published(data)
    else:
        names = extras(data)
    if args.count:
        print(len(names))
    else:
        print("\n".join(names))
    return 0


if __name__ == "__main__":
    sys.exit(main())
