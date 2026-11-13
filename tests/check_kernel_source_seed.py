#!/usr/bin/env python3
"""三条依赖上游内核源码的准备链都要先把基线对象喂给 prepare_release_kernel。

prepare_release_kernel 只在对象缺失时才联网，而它固定的地址是
git.kernel.org；GitHub Actions 连它经常超时。这里核对 seed 步骤确实排在
准备内核源码之前，并且它只接受封存序列声明的那一个提交与树。
"""

import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load():
    spec = importlib.util.spec_from_file_location(
        "seed_kernel_source", ROOT / "scripts/seed-kernel-source.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repository, *args):
    return subprocess.run(["git", "-C", str(repository), *args], check=True,
                          stdout=subprocess.PIPE, text=True).stdout.strip()


def make_mirror(path):
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "test")
    (path / "Makefile").write_text("all:\n")
    git(path, "add", "Makefile")
    git(path, "commit", "-q", "-m", "base")
    git(path, "tag", "v7.3.3")
    return git(path, "rev-parse", "HEAD"), git(path, "rev-parse", "HEAD^{tree}")


def series(sun, commit, tree):
    policy = sun / "patches/linux/7.3"
    policy.mkdir(parents=True)
    (policy / "series.json").write_text(json.dumps({
        "schema_version": 1, "target": "release", "kernel_version": "7.3.3",
        "base_commit": commit, "base_tree": tree,
        "target_tree": "e" * 40, "original_target_commit": "f" * 40,
        "source_url": "https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git",
        "patches": [],
    }))


def main():
    seed = load()
    workflows = {
        ".github/workflows/build-kernel.yml": "python3 scripts/prepare-production-kernel.py",
        ".github/workflows/build-device-runtime.yml": "python3 scripts/prepare-runtime-kernel.py",
        ".github/workflows/build-piano-rpms.yml": "python3 scripts/prepare-runtime-kernel.py",
    }
    for name, prepare in workflows.items():
        text = (ROOT / name).read_text()
        assert "python3 scripts/seed-kernel-source.py" in text, name + " 没有预置内核源码"
        assert text.index("python3 scripts/seed-kernel-source.py") < text.index(prepare), \
            name + " 的预置步骤排在准备内核源码之后"

    with tempfile.TemporaryDirectory(prefix="seed-kernel-") as folder:
        root = Path(folder)
        mirror = root / "mirror"
        commit, tree = make_mirror(mirror)
        sun = root / "sunuefi"
        series(sun, commit, tree)
        repository = seed.seed(sun, mirror)
        assert git(repository, "rev-parse", commit + "^{tree}") == tree
        # 对象已在仓库里时不再联网：镜像被移走也能走完。
        shutil.rmtree(mirror)
        assert seed.seed(sun, mirror) == repository

        # 镜像的 tag 指到别的提交时必须停下，不能把错的基线塞进仓库。
        other = root / "other"
        other_commit, other_tree = make_mirror(other)
        wrong = root / "wrong"
        series(wrong, other_commit, other_tree)
        (wrong / "patches/linux/7.3/series.json").write_text(json.dumps({
            **json.loads((wrong / "patches/linux/7.3/series.json").read_text()),
            "base_commit": "0" * 40, "source_url": str(other),
        }))
        try:
            seed.seed(wrong, other)
        except ValueError:
            pass
        else:
            raise AssertionError("a commit the mirror does not contain was accepted")

        # 提交对了但树对不上同样拒绝：摘要漂移说明镜像给的不是封存序列的基线。
        drifted = root / "drifted"
        series(drifted, other_commit, "1" * 40)
        try:
            seed.seed(drifted, other)
        except ValueError:
            pass
        else:
            raise AssertionError("a base tree that differs from the series was accepted")

    print("OK: kernel source seeding is wired before every preparation step")


if __name__ == "__main__":
    main()
