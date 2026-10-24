#!/usr/bin/env python3
"""一个路径只能有一个所有者。

rpm 只在内容与元数据完全一致时才允许两个包声明同一路径；字节不同就整单回滚
（run 39294072981 因此在 DNF 阶段失败，153 个重复路径里 23 个内容不同）。这里
把两层契约钉住：

1. 矩阵内部：packages/* 的 %files 不能有两个人认领同一路径；
2. 矩阵与运行时之间：runtime/matrix-owned-paths.txt 里的每个路径都必须真的
   属于某个矩阵包。运行时按这份清单从载荷里删文件，清单写错（例如删掉一个
   只有运行时才产出的文件）会让镜像少文件而审计照样全绿。
"""
import fnmatch
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
PRUNE_LIST = ROOT / "runtime" / "matrix-owned-paths.txt"

MACROS = {
    "%{_bindir}": "/usr/bin",
    "%{_sbindir}": "/usr/sbin",
    "%{_libexecdir}": "/usr/libexec",
    "%{_unitdir}": "/usr/lib/systemd/system",
    "%{_userunitdir}": "/usr/lib/systemd/user",
    "%{_datadir}": "/usr/share",
    "%{_sysconfdir}": "/etc",
    "%{_prefix}": "/usr",
    "%{_libdir}": "/usr/lib64",
    "%{_lib}": "/usr/lib64",
    "%{_localstatedir}": "/var",
    "%{_sharedstatedir}": "/var/lib",
}
DIRECTIVES = re.compile(
    r"^(%config(\(noreplace\))?|%dir|%attr\([^)]*\)|%license|%doc|%ghost|%verify\([^)]*\)|%exclude)\s*")
# %files 里的条目本身也以 % 开头（%{_prefix}、%config、%dir），所以 section
# 结束只能靠"这一行不是条目"来判断。
ENTRY = re.compile(r"""^(["']?(/|%\{|%(config|dir|attr|license|doc|ghost|verify|exclude)\b))""")


def spec_macros(text):
    """spec 自己用 %global/%define 声明的宏（例如 _firmwaredir）。"""
    local = {}
    for line in text.splitlines():
        match = re.match(r"%\s*(?:global|define)\s+(\w+)\s+(.+)$", line)
        if match:
            local["%{" + match.group(1) + "}"] = match.group(2).strip()
    return local


def spec_paths(spec):
    """返回 spec 的 %files 里声明的绝对路径集合。"""
    text = spec.read_text(encoding="utf-8", errors="replace")
    macros = dict(MACROS)
    # %global 可以引用别的宏，按顺序反复展开直到稳定。
    for name, value in spec_macros(text).items():
        macros[name] = value
    for _ in range(4):
        expanded = {name: _expand(value, macros) for name, value in macros.items()}
        if expanded == macros:
            break
        macros = expanded
    body = []
    inside = False
    for line in text.splitlines():
        if line.startswith("%files"):
            inside = True
            continue
        if inside:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and not ENTRY.match(stripped):
                break
            body.append(line)
    claimed, excluded = set(), set()
    for line in body:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        is_exclude = entry.startswith("%exclude")
        license_entry = entry.startswith("%license")
        while True:
            cleaned = DIRECTIVES.sub("", entry).strip()
            if cleaned == entry:
                break
            entry = cleaned
        entry = entry.strip('"').strip("'")
        entry = _expand(entry, macros)
        if license_entry and not entry.startswith("/"):
            path = f"/usr/share/licenses/{spec.parent.name}/{entry}".rstrip("/")
        elif entry.startswith("/") and "%" not in entry:
            path = entry.rstrip("/") or "/"
        else:
            continue
        (excluded if is_exclude else claimed).add(path)
    return claimed - excluded


def _expand(entry, macros):
    for _ in range(4):
        before = entry
        for macro, value in macros.items():
            entry = entry.replace(macro, value)
        if entry == before:
            break
    return entry


def main():
    failures = []
    owners = {}
    for spec in sorted(PACKAGES.glob("*/*.spec")):
        package = spec.parent.name
        for path in spec_paths(spec):
            owners.setdefault(path, set()).add(package)

    for path, packages in sorted(owners.items()):
        if len(packages) > 1:
            failures.append(
                f"{path} is claimed by {sorted(packages)}; one path, one owner")

    def claimed(path):
        found = set()
        for claim, packages in owners.items():
            if claim == path:
                found |= packages
            elif "*" in claim or "?" in claim or "[" in claim:
                # rpm 的目录条目是递归的：%{_firmwaredir}/* 认领整棵子树。
                if (fnmatch.fnmatchcase(path, claim)
                        or fnmatch.fnmatchcase(path, claim.rstrip("/") + "/*")):
                    found |= packages
            elif path.startswith(claim + "/"):
                found |= packages
        return found

    # v4l2loopback 由 kernel-piano 单独拥有：它按内核 ABI 编译，模块树的名字
    # 就是内核 release。piano-runtime 再产一份（历史上确实产过）会让 DNF 因为
    # 双所有者中止整个 rootfs 事务。这里的路径是合成的——release 字符串只有
    # 内核 manifest 知道，测试关心的是谁认领这棵树。
    loopback = "/usr/lib/modules/7.3.0-piano/updates/v4l2loopback.ko"
    loop_owners = claimed(loopback)
    if loop_owners != {"kernel-piano"}:
        failures.append(
            f"{loopback} is claimed by {sorted(loop_owners) or 'nobody'}; "
            "kernel-piano must be its only owner")
    stray = sorted(p for p in owners
                   if "v4l2loopback" in p and owners[p] != {"kernel-piano"})
    if stray:
        failures.append(f"v4l2loopback is also shipped by {stray}")

    # 运行时侧同样不能再产一份：它不在 packages/ 里，上面的 %files 检查看不见。
    runtime = (ROOT / "scripts" / "build-device-runtime.py").read_text(encoding="utf-8")
    if "usr/lib/modules" in runtime or "--loopback" in runtime:
        failures.append(
            "build-device-runtime.py still stages a kernel module; "
            "v4l2loopback belongs to kernel-piano")

    prune = []
    for number, line in enumerate(PRUNE_LIST.read_text().splitlines(), 1):
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if not entry.startswith("/"):
            failures.append(f"matrix-owned-paths.txt:{number} is not an absolute path: {entry}")
            continue
        prune.append((number, entry))
    seen = {}
    for number, entry in prune:
        if entry in seen:
            failures.append(
                f"matrix-owned-paths.txt:{number} repeats {entry} (first at line {seen[entry]})")
            continue
        seen[entry] = number
        packages = claimed(entry)
        if not packages:
            failures.append(
                f"matrix-owned-paths.txt:{number} prunes {entry} but no packages/* spec "
                "declares it; the runtime would drop a file nobody else ships")
        elif len(packages) > 1:
            failures.append(
                f"matrix-owned-paths.txt:{number} {entry} is ambiguous: {sorted(packages)}")

    if failures:
        for message in failures:
            print("FAIL: " + message, file=sys.stderr)
        return 1
    print(f"OK: {len(owners)} matrix paths have a single owner; "
          f"{len(prune)} runtime-pruned paths resolve to one package each")
    return 0


if __name__ == "__main__":
    sys.exit(main())
