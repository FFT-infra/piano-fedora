#!/usr/bin/env python3
"""回归测试：Obsoletes/Provides 不得自匹配。

实机教训（2026-10-17）：`piano-fedora-configs.spec` 写着

    Obsoletes: piano-runtime < 0.3.0
    Provides:  piano-runtime = %{version}-%{release}     # = 0.1.0-4

0.1.0-4 低于 0.3.0，于是这个包把自己提供的 `piano-runtime` 也“淘汰”了。
DNF 在解析时把同一个 NEVRA 排进事务两次，整个 installroot 装配直接失败：

    file /usr/local/sbin/piano-debug-bootstrap conflicts between attempted
    installs of piano-fedora-configs-0.1.0-4.fc44.noarch and
    piano-fedora-configs-0.1.0-4.fc44.noarch

结果是从没产出过任何镜像（`build/clean-distros/` 始终为空），而 release
打包脚本静默回退到历史镜像。同一模式在 `sm8750-video-accel` 也出现过。

本测试只读 spec 与已构建 RPM，不接触设备：

* 若 spec 同时 `Provides: X = <版本>` 与 `Obsoletes: X < <界限>`，
  界限必须严格小于该包自身的 EVR，否则报错；
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
    print("OK: 所有 Obsoletes 界限都严格低于自身 Provides，不会再自匹配导致事务失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())
