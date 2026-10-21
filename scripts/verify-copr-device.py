#!/usr/bin/env python3
"""在设备上核对 COPR 仓库可启用、可签名、且不会升级内核。

在平板上执行，不是 CI 的一部分：

    python3 scripts/verify-copr-device.py --report copr-device-check.json

脚本只读仓库元数据和包集合，不安装、不升级。只有显式 `--install-test`
才会真的启用仓库并试跑一次 `dnf upgrade --assumeno`。

默认值对应 packages/piano-fedora-configs 里随包发布的 .repo。
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_REPO_ID = "copr:copr.fedorainfracloud.org:xinyang26:piano-fedora"
DEFAULT_BASEURL = "https://download.copr.fedorainfracloud.org/results/xinyang26/piano-fedora/"
EXCLUDED = "kernel-piano"


def run(args, *, check=True, sudo=False):
    command = ["sudo", "-n", *args] if sudo else list(args)
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"{' '.join(command)} failed: {result.stderr.strip()}")
    return result


def detect_releasever():
    text = Path("/etc/os-release").read_text()
    match = re.search(r"^VERSION_ID=(\d+)$", text, re.M)
    if not match:
        raise RuntimeError("cannot determine VERSION_ID from /etc/os-release")
    return match.group(1)


def repo_url(releasever, arch="aarch64"):
    return f"{DEFAULT_BASEURL}fedora-{releasever}-{arch}/"


def fetch(url):
    result = run(["curl", "-fsSL", "--max-time", "30", url])
    return result.stdout


def parse_repomd(xml):
    """返回 repodata 里每种数据的 location，用来确认包索引存在。"""
    rows = re.findall(r'<data type="([^"]+)">.*?href="([^"]+)"', xml, re.S)
    if not rows:
        raise RuntimeError("repomd.xml has no data sections")
    return dict(rows)


def list_packages(baseurl):
    xml = fetch(baseurl + "repodata/repomd.xml")
    data = parse_repomd(xml)
    if "primary" not in data:
        raise RuntimeError("repodata has no primary index")
    primary = fetch(baseurl + data["primary"])
    names = sorted(set(re.findall(r"<name>([^<]+)</name>", primary)))
    return names, sorted(data)


def key_available(baseurl):
    result = run(["curl", "-fsSL", "--max-time", "30", "-o", "/dev/null",
                  "-w", "%{http_code}", baseurl + "pubkey.gpg"])
    return result.stdout.strip() == "200"


def repo_file_state():
    path = Path("/etc/yum.repos.d/piano-fedora-copr.repo")
    if not path.is_file():
        return {"installed": False, "path": str(path)}
    text = path.read_text()
    return {
        "installed": True,
        "path": str(path),
        "gpgcheck": "gpgcheck=1" in text,
        "repo_gpgcheck_off": "repo_gpgcheck=0" in text,
        "excludes_kernel": f"exclude={EXCLUDED}*" in text or f"exclude={EXCLUDED}" in text,
    }


def repoquery_kernel(baseurl):
    """确认排除规则对 kernel-piano 生效。"""
    with tempfile.TemporaryDirectory() as tmp:
        options = " ".join([
            f"--repofrompath=coprcheck,{baseurl}",
            "--setopt=coprcheck.gpgcheck=1",
            "--setopt=coprcheck.repo_gpgcheck=0",
            f"--setopt=coprcheck.exclude={EXCLUDED}*",
            "--setopt=coprcheck.skip_if_unavailable=True",
        ])
        visible = run(["dnf", "repoquery", options, "--qf", "%{name}", EXCLUDED], check=False)
        without_exclude = run([
            "dnf", "repoquery", f"--repofrompath=coprcheck,{baseurl}",
            "--setopt=coprcheck.skip_if_unavailable=True",
            "--qf", "%{name}", EXCLUDED,
        ], check=False)
    return {
        "listed_with_exclude": EXCLUDED in visible.stdout.split(),
        "listed_without_exclude": EXCLUDED in without_exclude.stdout.split(),
    }


def upgrade_dry_run():
    result = run(["dnf", "-q", "--assumeno", "upgrade", "--refresh"], check=False)
    output = result.stdout + result.stderr
    return {
        "exit_code": result.returncode,
        "mentions_kernel": bool(re.search(r"\bkernel-piano\b", output)),
        "accepted_without_confirm": result.returncode == 0,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", type=Path, default=Path("copr-device-check.json"))
    ap.add_argument("--repo-id", default=DEFAULT_REPO_ID)
    ap.add_argument("--install-test", action="store_true",
                    help="额外执行一次 dnf upgrade --assumeno（不实际升级）")
    args = ap.parse_args()

    releasever = detect_releasever()
    baseurl = repo_url(releasever)
    record = {"status": "UNKNOWN", "releasever": releasever, "baseurl": baseurl,
              "repo_id": args.repo_id}
    problems = []

    try:
        names, indexes = list_packages(baseurl)
        record["package_count"] = len(names)
        record["packages"] = names
        record["indexes"] = indexes
        expected = {
            "piano-fedora-configs", "alsa-xiaomi-piano", "xiaomi-piano-firmware",
            "piano-camera", "xiaomi-piano-thp", "sm8750-video-accel",
            "uperf-linux", "libssc", "iio-sensor-proxy-ssc", "kernel-piano",
        }
        missing = sorted(expected - set(names))
        if missing:
            problems.append("repository is missing: " + ", ".join(missing))
        record["missing_packages"] = missing
    except (RuntimeError, OSError) as error:
        problems.append(f"cannot read repodata: {error}")

    record["gpg_key_reachable"] = False
    try:
        record["gpg_key_reachable"] = key_available(baseurl)
        if not record["gpg_key_reachable"]:
            problems.append("pubkey.gpg is not reachable")
    except (RuntimeError, OSError) as error:
        problems.append(f"cannot reach pubkey: {error}")

    record["repo_file"] = repo_file_state()
    if record["repo_file"]["installed"]:
        for key in ("gpgcheck", "repo_gpgcheck_off", "excludes_kernel"):
            if not record["repo_file"].get(key):
                problems.append(f"installed .repo fails {key}")

    try:
        record["kernel_exclusion"] = repoquery_kernel(baseurl)
        if record["kernel_exclusion"]["listed_with_exclude"]:
            problems.append("kernel-piano is visible despite the exclude rule")
        if not record["kernel_exclusion"]["listed_without_exclude"]:
            problems.append("kernel-piano is not in the repository at all")
    except (RuntimeError, OSError) as error:
        problems.append(f"repoquery failed: {error}")

    if args.install_test:
        try:
            record["upgrade_dry_run"] = upgrade_dry_run()
            if record["upgrade_dry_run"]["mentions_kernel"]:
                problems.append("upgrade would touch kernel-piano")
            if record["upgrade_dry_run"]["accepted_without_confirm"]:
                problems.append("dnf upgrade did not require confirmation")
        except (RuntimeError, OSError) as error:
            problems.append(f"upgrade dry-run failed: {error}")

    record["problems"] = problems
    record["status"] = "PASS" if not problems else "FAIL"
    args.report.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": record["status"], "problems": problems,
                      "report": str(args.report)}, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
