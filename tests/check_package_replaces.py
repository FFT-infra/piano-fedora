#!/usr/bin/env python3
"""回归测试：替换类元数据与 merged-usr 路径都不得让 DNF 事务自杀。

2026-10-17 的 installroot 装配连续失败，报错是

    file /usr/local/sbin/piano-debug-bootstrap conflicts between attempted
    installs of piano-fedora-configs-0.1.0-4.fc44.noarch and
    piano-fedora-configs-0.1.0-4.fc44.noarch

第一轮把它当成 Obsoletes 自匹配（`Obsoletes: piano-runtime < 0.3.0` 对
`Provides: piano-runtime = 0.1.0-4`）并改写了界限，但事务仍然失败。真正的
原因是 Fedora merged-usr：`filesystem` 包把 `/usr/local/sbin` 做成指向
`bin` 的符号链接，而 `%files` 同时列了 `/usr/local/bin/X` 与
`/usr/local/sbin/X`。两者解析到同一个 inode，rpm 6 判成“包与自己冲突”，
于是整个事务回滚。用只含这两个路径的最小 spec 可以在 Fedora 44 上稳定复现。

Obsoletes 的界限仍然要求写成 `< %{version}-%{release}`：硬编码数字会在下次
升版本后悄悄变成自匹配，属于同一个雷区。本测试只读 spec 与已构建 RPM：

* `Provides: X = <版本>` 与 `Obsoletes: X < <界限>` 同时出现时，界限必须严格
  低于该包自身的 EVR；
* `%files` 不得同时列出 merged-usr 别名路径；
* 对 `out/rpms/` 里已构建的 RPM 复算一遍，捕获 spec 之外的漂移。
"""

from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
RPMS = ROOT / "out" / "rpms"


def parse_evr(text):
    """Parse an EVR string into (epoch, version, release) for comparison."""
    text = text.strip().replace("%{?dist}", "").replace("%{dist}", "")
    epoch = 0
    m = re.match(r"(\d+):(.*)$", text)
    if m:
        epoch, text = int(m.group(1)), m.group(2)
    if "-" in text:
        version, release = text.rsplit("-", 1)
    else:
        version, release = text, ""
    return epoch, version, release


def segment_cmp(a, b):
    """RPM-ish comparison of one version or release segment."""
    def parts(s):
        return [int(p) if p.isdigit() else p for p in re.split(r"[._]", s)]
    pa, pb = parts(a), parts(b)
    for x, y in zip(pa, pb):
        if x == y:
            continue
        if isinstance(x, int) and isinstance(y, int):
            return -1 if x < y else 1
        return -1 if str(x) < str(y) else 1
    if len(pa) == len(pb):
        return 0
    return -1 if len(pa) < len(pb) else 1


def evr_cmp(left, right):
    a, b = parse_evr(left), parse_evr(right)
    if a[0] != b[0]:
        return -1 if a[0] < b[0] else 1
    c = segment_cmp(a[1], b[1])
    if c:
        return c
    # An empty release sorts lower than a non-empty one.
    if a[2] == b[2]:
        return 0
    if not a[2]:
        return -1
    if not b[2]:
        return 1
    return segment_cmp(a[2], b[2])


def expand(value, version, release):
    return (value.replace("%{version}", version)
                 .replace("%{release}", release)
                 .replace("%{?dist}", "")
                 .replace("%{dist}", ""))


def spec_evr(text):
    v = re.search(r"^Version:\s*(\S+)", text, re.MULTILINE)
    r = re.search(r"^Release:\s*(\S+)", text, re.MULTILINE)
    if not v or not r:
        return None, None, None
    version = v.group(1)
    release = r.group(1).replace("%{?dist}", "").replace("%{dist}", "")
    return version, release, f"{version}-{release}"


def check_spec(path, failures):
    text = path.read_text(encoding="utf-8")
    version, release, own_evr = spec_evr(text)
    if not own_evr:
        return
    provides = {}
    for m in re.finditer(r"^Provides:\s*([A-Za-z0-9_.+-]+)\s*=\s*(\S+)", text, re.MULTILINE):
        provides[m.group(1)] = expand(m.group(2), version, release)
    for m in re.finditer(r"^Obsoletes:\s*([A-Za-z0-9_.+-]+)\s*<\s*(\S+)", text, re.MULTILINE):
        name, bound = m.group(1), expand(m.group(2), version, release)
        if name not in provides:
            continue
        # `<` is strict: a bound equal to the provide is safe, lower is not.
        if evr_cmp(provides[name], bound) < 0:
            failures.append(
                f"{path.parent.name}: Obsoletes {name} < {bound} also matches its own "
                f"Provides {name} = {provides[name]}; DNF will schedule the package twice")
    check_merged_usr_duplicates(path, text, failures)


# Directories that are symlinks to a sibling under merged-usr. Listing the same
# basename under both spellings makes rpm resolve them to one path and abort the
# transaction with a "conflicts between attempted installs of <pkg> and <pkg>".
MERGED_USR_ALIASES = {
    "/usr/local/sbin": "/usr/local/bin",
    "/sbin": "/usr/bin",
    "/bin": "/usr/bin",
    "/usr/sbin": "/usr/bin",
}


def merged_usr_files(text):
    """Return the absolute paths listed in the spec's %files section."""
    listed = []
    in_files = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("%files"):
            in_files = True
            continue
        if in_files and stripped.startswith("%") and not stripped.startswith("%{") \
                and not stripped.startswith("%attr") and not stripped.startswith("%config") \
                and not stripped.startswith("%doc") and not stripped.startswith("%dir") \
                and not stripped.startswith("%license") and not stripped.startswith("%ghost"):
            in_files = False
            continue
        if not in_files or not stripped.startswith("/"):
            continue
        listed.append(stripped.split()[0])
    return listed


def check_merged_usr_duplicates(path, text, failures):
    resolved = {}
    for entry in merged_usr_files(text):
        for alias, canonical in MERGED_USR_ALIASES.items():
            if entry.startswith(alias + "/"):
                resolved.setdefault(canonical + entry[len(alias):], []).append(entry)
                break
        else:
            resolved.setdefault(entry, []).append(entry)
    for target, entries in sorted(resolved.items()):
        if len(entries) > 1:
            failures.append(
                f"{path.parent.name}: %files lists merged-usr aliases {entries} which all "
                f"resolve to {target}; rpm aborts the transaction with a self-conflict")


def check_built_rpms(failures):
    if not RPMS.is_dir():
        return
    rpms = sorted(RPMS.glob("*.rpm"))
    if not rpms:
        return
    for rpm in rpms:
        qf = subprocess.run(["rpm", "-qp", "--qf",
                             "%{VERSION}-%{RELEASE}\n", str(rpm)],
                            capture_output=True, text=True)
        if qf.returncode != 0:
            continue
        own = qf.stdout.strip().replace(".fc44", "").replace(".fc43", "")
        provides = subprocess.run(["rpm", "-qp", "--provides", str(rpm)],
                                  capture_output=True, text=True).stdout
        obsoletes = subprocess.run(["rpm", "-qp", "--obsoletes", str(rpm)],
                                   capture_output=True, text=True).stdout
        prov_map = {}
        for line in provides.splitlines():
            m = re.match(r"^([A-Za-z0-9_.+-]+)\s*=\s*(\S+)$", line.strip())
            if m:
                prov_map[m.group(1)] = m.group(2)
        for line in obsoletes.splitlines():
            m = re.match(r"^([A-Za-z0-9_.+-]+)\s*<\s*(\S+)$", line.strip())
            if not m:
                continue
            name, bound = m.group(1), m.group(2)
            if name in prov_map and evr_cmp(prov_map[name], bound) < 0:
                failures.append(
                    f"{rpm.name}: Obsoletes {name} < {bound} matches its own "
                    f"Provides {name} = {prov_map[name]}")
        listing = subprocess.run(["rpm", "-qp", "--qf", "[%{FILENAMES}\n]", str(rpm)],
                                 capture_output=True, text=True).stdout
        resolved = {}
        for entry in listing.splitlines():
            for alias, canonical in MERGED_USR_ALIASES.items():
                if entry.startswith(alias + "/"):
                    resolved.setdefault(canonical + entry[len(alias):], []).append(entry)
                    break
            else:
                resolved.setdefault(entry, []).append(entry)
        for target, entries in sorted(resolved.items()):
            if len(entries) > 1:
                failures.append(
                    f"{rpm.name}: ships merged-usr aliases {entries} for one path {target}; "
                    f"rpm 6 aborts the transaction with a self-conflict")


def main():
    failures = []
    specs = sorted(PACKAGES.glob("*/*.spec"))
    if not specs:
        print("FAIL: no spec files found", file=sys.stderr)
        return 1
    for spec in specs:
        check_spec(spec, failures)
    check_built_rpms(failures)
    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: Obsoletes 界限严格低于自身 Provides，且没有任何包重复声明 merged-usr 别名路径")
    return 0


if __name__ == "__main__":
    sys.exit(main())
