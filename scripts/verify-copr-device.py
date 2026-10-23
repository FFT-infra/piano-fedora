#!/usr/bin/env python3
"""在设备上核对 COPR 仓库可启用、可签名、且内核在其中。

在平板上执行，不是 CI 的一部分：

    python3 scripts/verify-copr-device.py --report copr-device-check.json

脚本只读仓库元数据和包集合，不安装、不升级。只有显式 `--install-test`
才会真的启用仓库并试跑一次 `dnf upgrade --assumeno`。

内核升级由 kernel-piano 自己的 %posttrans 收尾：它重建 initramfs 并重打
ESP 上固件读取的 boot.img，所以这里断言 kernel-piano 可见、且 dry-run 的
升级计划里带着它，而不是像以前那样断言它被排除。

默认值对应 packages/piano-fedora-configs 里随包发布的 .repo。
"""

import argparse
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPO_ID = "copr:copr.fedorainfracloud.org:xinyang26:piano-fedora"
DEFAULT_BASEURL = "https://download.copr.fedorainfracloud.org/results/xinyang26/piano-fedora/"
KERNEL_PACKAGE = "kernel-piano"


def run(args, *, check=True, sudo=False, binary=False, timeout=None):
    command = ["sudo", "-n", *args] if sudo else list(args)
    # binary=True 时保留原始字节：repodata 的 primary 是 gzip，先解码会在 0x8b 上炸。
    try:
        result = subprocess.run(command, capture_output=True, text=not binary,
                                timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{' '.join(command)} timed out after {timeout}s")
    if check and result.returncode:
        stderr = result.stderr if binary else result.stderr.strip()
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"{' '.join(command)} failed: {stderr}")
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
    """取文本元数据。

    repodata 里的 primary 是 `.xml.gz` 文件本身，不是 HTTP 的 Content-Encoding，
    `curl --compressed` 不会解它；直接按文本解码会在 0x8b 上抛 UnicodeDecodeError。
    按 URL 后缀显式解压。
    """
    result = run(["curl", "-fsSL", "--max-time", "30", url], binary=True)
    data = result.stdout
    if url.endswith(".gz"):
        return gzip.decompress(data).decode("utf-8")
    return data.decode("utf-8")


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
    """公钥在仓库根目录，不在 fedora-<rel>-<arch>/ 子目录里。

    随包的 .repo 里 gpgkey 写的就是根路径；这里跟它保持一致，别用 baseurl 拼。
    """
    result = run(["curl", "-fsSL", "--max-time", "30", "-o", "/dev/null",
                  "-w", "%{http_code}", baseurl + "../pubkey.gpg"])
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
        "excludes_kernel": f"exclude={KERNEL_PACKAGE}" in text,
    }


def repoquery_kernel(baseurl):
    """确认 kernel-piano 在仓库里，且不会被排除规则挡掉。

    只查这一个仓库：设备上的 rpmfusion 之类经常超时，一旦它们参与解析，
    `dnf repoquery` 会先等完全部 enabled 仓库才报 "Empty mirrorlist"。
    `--repofrompath` 只给出 repo id 与 baseurl 的一半，dnf5 还需要显式的
    `baseurl=`，否则这个临时仓库自己就是坏的。
    """
    result = run([
        "dnf", "repoquery",
        "--disablerepo=*", f"--repofrompath=coprcheck,{baseurl}",
        f"--setopt=coprcheck.baseurl={baseurl}",
        "--setopt=coprcheck.gpgcheck=1", "--setopt=coprcheck.repo_gpgcheck=0",
        "--setopt=coprcheck.skip_if_unavailable=True",
        "--qf", "%{name}", KERNEL_PACKAGE,
    ], check=False, timeout=180)
    return {"listed": KERNEL_PACKAGE in result.stdout.split(),
            "stderr": result.stderr.strip()[:2000]}


def upgrade_dry_run():
    # --disablerepo='*' 会连本地仓库一起关掉，升级计划就永远为空；这里只
    # 关掉网络上的第三方仓库不够精确，所以保留默认启用集合，但给一个上限：
    # 一个卡住的镜像不应该让整个验收挂十分钟。
    result = run(["dnf", "-q", "--assumeno", "upgrade", "--refresh"],
                 check=False, timeout=300)
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
        for key in ("gpgcheck", "repo_gpgcheck_off"):
            if not record["repo_file"].get(key):
                problems.append(f"installed .repo fails {key}")
        if record["repo_file"]["excludes_kernel"]:
            problems.append("installed .repo still excludes kernel-piano")

    try:
        record["kernel_visibility"] = repoquery_kernel(baseurl)
        if not record["kernel_visibility"]["listed"]:
            problems.append("kernel-piano is not visible in the repository")
    except (RuntimeError, OSError) as error:
        problems.append(f"repoquery failed: {error}")

    if args.install_test:
        try:
            record["upgrade_dry_run"] = upgrade_dry_run()
            if not record["upgrade_dry_run"]["mentions_kernel"]:
                problems.append("upgrade plan does not carry kernel-piano")
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
