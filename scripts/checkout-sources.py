#!/usr/bin/env python3
"""获取锁定源码和实际需要的传递依赖，输出供 Actions 消费的目录。"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def case_collisions(destination):
    """列出因文件系统大小写不敏感而检不出的路径。

    virtiofs 与 APFS 这类挂载点上，`xt_CONNMARK.h` 与 `xt_connmark.h` 只能存在
    一个，git 会把另一个报成删除。只接受这一类差异：其余任何不干净都照旧失败。
    """
    # 不能走 git()：它的 strip() 会吃掉首行开头的状态位空格。
    status = subprocess.run(["git", "-C", str(destination), "status", "--porcelain"],
                            check=True, capture_output=True, text=True).stdout
    if not status.strip():
        return []
    deleted = []
    for line in status.splitlines():
        if not line.startswith(" D "):
            raise ValueError(f"source identity/cleanliness mismatch: {destination}")
        deleted.append(line[3:])
    index = git(destination, "ls-files").splitlines()
    twins = {}
    for path in index:
        twins.setdefault(path.lower(), []).append(path)
    for path in deleted:
        other = [candidate for candidate in twins.get(path.lower(), []) if candidate != path]
        if not other or not (destination / other[0]).exists():
            raise ValueError(f"source identity/cleanliness mismatch: {destination}")
    return deleted


def index_only(url, commit, destination):
    """只取文件清单与 blob 摘要，不下载文件内容。

    `--filter=blob:none` 下 `ls-tree -r` 只需要树对象；厂商 blob 树 1.28 GiB，
    对照任务要的只是路径与内容摘要。
    """
    if destination.exists():
        raise ValueError(f"source output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "remote", "add", "origin", url], check=True)
    subprocess.run(["git", "-C", str(destination), "fetch", "--depth=1", "--filter=blob:none",
                    "origin", commit], check=True)


def checkout(url, commit, destination, full_history=False, sparse_paths=(), allow_case_collisions=False):
    if destination.exists():
        # Git checkout 会为未初始化的 gitlink 留一个空目录。
        if destination.is_dir() and not any(destination.iterdir()):
            destination.rmdir()
        else:
            raise ValueError(f"source output already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ["git", "clone", "--filter=blob:none", "--no-checkout"]
    if not full_history:
        subprocess.run(["git", "init", "-q", str(destination)], check=True)
        subprocess.run(["git", "-C", str(destination), "remote", "add", "origin", url], check=True)
        subprocess.run(["git", "-C", str(destination), "fetch", "--depth=1", "--filter=blob:none",
                        "origin", commit], check=True)
    else:
        subprocess.run([*command, url, str(destination)], check=True)
    if sparse_paths:
        subprocess.run(["git", "-C", str(destination), "sparse-checkout", "set", "--no-cone",
                        *sparse_paths], check=True)
    subprocess.run(["git", "-C", str(destination), "checkout", "--detach", commit], check=True)
    if git(destination, "rev-parse", "HEAD") != commit:
        raise ValueError(f"source identity mismatch: {destination}")
    collisions = case_collisions(destination) if allow_case_collisions else []
    if not allow_case_collisions and git(destination, "status", "--porcelain"):
        raise ValueError(f"source identity/cleanliness mismatch: {destination}")
    return collisions


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--mode", choices=("check", "boot", "device", "graphics", "installer",
                                       "gunyah", "loopback", "vendor-dt", "vendor-blobs"),
                    default="check")
    ap.add_argument("--kernel", action="store_true")
    args = ap.parse_args()
    if args.mode in ("gunyah", "loopback") and not args.kernel:
        ap.error(f"the {args.mode} source mode requires --kernel")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "build"):
        raise ValueError("source outputs must stay in the ignored build directory")
    lock = json.loads((ROOT / "sources.lock.json").read_text())["sources"]
    sun = output / "sunuefi"
    checkout(lock["sunuefi"]["url"], lock["sunuefi"]["commit"], sun)
    upstream = json.loads((sun / "sources.lock.json").read_text())
    names = [] if args.mode in ("check", "gunyah", "vendor-dt") else ["debian-piano-current"]
    if args.mode == "vendor-blobs":
        # 对照的本地一侧就是实际出货的固件树，没有它比不了。
        names = ["piano-firmware-current"]
    if args.mode == "graphics":
        names = ["piano-mesa-current"]
    if args.mode == "installer":
        names = ["Mu-Silicium", "Mu-Silicium/Mu_Basecore", "simple-init"]
    if args.mode == "device":
        names += ["piano-firmware-current", "piano-sensors-current", "audioreach-topology", "v4l2loopback"]
    if args.mode == "loopback":
        # kernel-piano 拥有 v4l2loopback，所以矩阵也要这份锁定的源码；外部
        # 模块还需要内核源码树与 Gunyah 派生输入，那两样由 --kernel 带来。
        names = ["v4l2loopback"]
    for name in names:
        path = "upstream/" + name
        owner = sun if "/" not in name else sun / "upstream" / name.rsplit("/", 1)[0]
        link = path if owner == sun else name.rsplit("/", 1)[1]
        registered = git(owner, "ls-files", "--stage", "--", link).split()
        if len(registered) < 2 or registered[0] != "160000" or registered[1] != upstream[name]["commit"]:
            raise ValueError(f"submodule registration differs from the upstream lock: {name}")
        subprocess.run(["git", "-C", str(owner), "submodule", "update", "--init", "--depth=1", link], check=True)
        if git(sun / path, "rev-parse", "HEAD") != upstream[name]["commit"]:
            raise ValueError(f"submodule pin differs: {name}")
    if args.kernel:
        kernel = lock["linux-piano"]
        checkout(kernel["url"], kernel["commit"], sun / "upstream/linux-piano", True)
    elif args.mode == "boot":
        kernel = lock["linux-piano"]
        checkout(kernel["url"], kernel["commit"], sun / "upstream/linux-piano",
                 sparse_paths=("/include/dt-bindings/",))
    if args.mode == "vendor-dt":
        # 厂商 DTS 只作 overlay 的审阅基准，不进产物。稀疏检出按目录取，
        # 整棵树 3 GiB，其中绝大多数是内核驱动。
        vendor = lock["vendor-kernel"]
        # 整棵 include/ 都要：dt-bindings 里有指向 ../linux/*.h 的符号链接，
        # 只取 dt-bindings 会留下断链。
        collisions = checkout(vendor["url"], vendor["commit"], output / "vendor-kernel", sparse_paths=(
            "/arch/arm64/boot/dts/vendor/qcom/",
            "/arch/arm64/boot/dts/techpack/",
            "/techpack/camera-kernel/dt-bindings/",
            "/include/"), allow_case_collisions=True)
        if collisions:
            print(f"case-insensitive filesystem: {len(collisions)} paths not checked out", file=sys.stderr)
    if args.mode == "vendor-blobs":
        vendor = lock["vendor-blobs"]
        index_only(vendor["url"], vendor["commit"], output / "vendor-blobs")
    if args.mode in ("boot", "vendor-dt"):
        dtc = lock["dtc"]
        checkout(dtc["url"], dtc["commit"], output / "dtc")
    if args.mode == "device":
        mipps = lock["piano-mipps-auth"]
        checkout(mipps["url"], mipps["commit"], output / "piano-mipps-auth")
        fastrpc = lock["fastrpc"]
        checkout(fastrpc["url"], fastrpc["commit"], output / "fastrpc")
        for name in ("librevm", "crosvm", "iris-vaapi", "uperf"):
            entry = lock[name]
            checkout(entry["url"], entry["commit"], output / name)
            if name == "crosvm":
                subprocess.run(["git", "-C", str(output / name), "submodule", "update", "--init",
                                "--depth=1", "third_party/minijail"], check=True)
        entry = lock["piano-integration"]
        checkout(entry["url"], entry["commit"], output / "piano-integration", sparse_paths=(
            "/device/piano-thp/", "/device/camera/", "/tools/extract-thp-calibration.py",
            "/LICENSES/", "/LICENSE", "/NOTICE"))
    subprocess.run(["python3", str(ROOT / "scripts/apply-patches.py"), "--sunuefi", str(sun)], check=True)
    print(json.dumps({"sunuefi": str(sun), "dependencies": names, "pins": {
        name: upstream[name]["commit"] for name in names}}, indent=2))


if __name__ == "__main__":
    main()
