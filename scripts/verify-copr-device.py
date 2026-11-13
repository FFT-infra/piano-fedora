#!/usr/bin/env python3
"""在设备上核对 COPR 仓库可启用、可签名、且内核在其中。

在平板上执行，不是 CI 的一部分：

    python3 scripts/verify-copr-device.py --report copr-device-check.json

脚本只读仓库元数据和包集合，不安装、不升级。只有显式 `--install-test`
才会真的启用仓库并试跑一次 `dnf upgrade --assumeno`。

内核升级由 kernel-piano 自己的 %posttrans 收尾：它重建 initramfs 并重打
ESP 上固件读取的 UKI，所以这里断言 kernel-piano 可见、且 dry-run 的
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
EXTRAS_REPO_ID = "copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras"
EXTRAS_BASEURL = "https://download.copr.fedorainfracloud.org/results/xinyang26/piano-fedora-extras/"
KERNEL_PACKAGE = "kernel-piano"
# 图形栈的补丁版改用改名包，这里列的是仓库里必须出现的新名字，以及它们
# 替代的 Fedora 原名。
GRAPHICS_RENAMES = {"mesa-piano-dri-drivers": "mesa-dri-drivers",
                    "ffmpeg-piano": "ffmpeg-free"}
EXTRAS_PACKAGES = {"gnome-settings-daemon-piano", "kwin", "gtk3",
                   "piano-gnome-extras", "piano-kde-extras"}
EXTRAS_DESKTOP = ("gnome-settings-daemon-piano", "kwin", "gtk3")
EXTRAS_META = ("piano-gnome-extras", "piano-kde-extras")


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


def repo_url(releasever, arch="aarch64", base=DEFAULT_BASEURL):
    return f"{base}fedora-{releasever}-{arch}/"


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
    return names, sorted(data), primary


def package_releases(primary, names):
    """从 primary 里取每个包发布的 release 与 arch。

    extras 的 KWin/GTK3 与 Fedora 同名，只有 `.piano1` release 能说明设备
    装的是补丁构建；meta 包则必须是 noarch。
    """
    records = {}
    for block in re.findall(r'<package type="rpm">.*?</package>', primary, re.S):
        name = re.search(r"<name>([^<]+)</name>", block)
        arch = re.search(r"<arch>([^<]+)</arch>", block)
        release = re.search(r'<version[^>]*\brel="([^"]+)"', block)
        if not name or name.group(1) not in names:
            continue
        records.setdefault(name.group(1), []).append({
            "arch": arch.group(1) if arch else "",
            "release": release.group(1) if release else "",
        })
    return records


def key_available(baseurl):
    """公钥在仓库根目录，不在 fedora-<rel>-<arch>/ 子目录里。

    随包的 .repo 里 gpgkey 写的就是根路径；这里跟它保持一致，别用 baseurl 拼。
    """
    result = run(["curl", "-fsSL", "--max-time", "30", "-o", "/dev/null",
                  "-w", "%{http_code}", baseurl + "../pubkey.gpg"])
    return result.stdout.strip() == "200"


def repo_file_state(path="/etc/yum.repos.d/piano-fedora-copr.repo"):
    path = Path(path)
    if not path.is_file():
        return {"installed": False, "path": str(path)}
    text = path.read_text()
    return {
        "installed": True,
        "path": str(path),
        "enabled": "enabled=1" in text,
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


def graphics_renames(names):
    """确认仓库里是改名后的补丁版，且没有同名的 Fedora 包一起出现。

    mesa 与 ffmpeg 的补丁版换成 mesa-piano-* 与 ffmpeg-piano / libavcodec-piano，
    再声明提供 Fedora 的原名、与原名冲突。两个名字都进仓库时，全新安装
    会优先选名字完全匹配的那个，补丁版就被绕过去了。
    """
    names = set(names)
    return {ours: {"present": ours in names, "fedora_name_present": fedora in names}
            for ours, fedora in GRAPHICS_RENAMES.items()}


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
    ap.add_argument("--report", type=Path)
    ap.add_argument("--repo-id")
    ap.add_argument("--extras", action="store_true",
                    help="核对默认关闭的 extras 仓库与 .piano1 桌面构建")
    ap.add_argument("--install-test", action="store_true",
                    help="额外执行一次 dnf upgrade --assumeno（不实际升级）")
    args = ap.parse_args()

    releasever = detect_releasever()
    baseurl = repo_url(releasever, base=EXTRAS_BASEURL if args.extras else DEFAULT_BASEURL)
    repo_id = args.repo_id or (EXTRAS_REPO_ID if args.extras else DEFAULT_REPO_ID)
    report = args.report or Path("copr-extras-device-check.json" if args.extras
                                 else "copr-device-check.json")
    record = {"status": "UNKNOWN", "releasever": releasever, "baseurl": baseurl,
              "repo_id": repo_id, "mode": "extras" if args.extras else "default"}
    problems = []

    try:
        names, indexes, primary = list_packages(baseurl)
        record["package_count"] = len(names)
        record["packages"] = names
        record["indexes"] = indexes
        if args.extras:
            expected = EXTRAS_PACKAGES
            missing = sorted(expected - set(names))
            if missing:
                problems.append("repository is missing: " + ", ".join(missing))
            record["missing_packages"] = missing
            releases = package_releases(primary, expected)
            record["releases"] = releases
            for name in EXTRAS_DESKTOP:
                rows = releases.get(name, [])
                if not any(".piano1" in row["release"] for row in rows):
                    problems.append(f"{name} has no .piano1 release")
            for name in EXTRAS_META:
                rows = releases.get(name, [])
                if not any(row["arch"] == "noarch" for row in rows):
                    problems.append(f"{name} is not built as noarch")
            if "gnome-settings-daemon" in names:
                problems.append("extras repository still ships gnome-settings-daemon")
        else:
            expected = {
                "piano-fedora-configs", "alsa-xiaomi-piano", "xiaomi-piano-firmware",
                "piano-camera", "piano-fingerprint", "piano-libfprint",
                "piano-pipewire-gstreamer", "xiaomi-piano-thp", "sm8750-video-accel",
                "uperf-linux", "libssc", "iio-sensor-proxy-ssc", "kernel-piano",
                # 图形栈：这两个是本地重建的包，靠改名后声明 Provides/Conflicts
                # 压过官方版本。仓库里没有它们，设备 dnf upgrade 就会装回官方版。
                "mesa-piano-dri-drivers", "ffmpeg-piano",
            }
            missing = sorted(expected - set(names))
            if missing:
                problems.append("repository is missing: " + ", ".join(missing))
            record["missing_packages"] = missing
            # 改名是这两个包压过官方版的依据，名字错了就等于没发。
            record["graphics_renames"] = graphics_renames(names)
    except (RuntimeError, OSError) as error:
        problems.append(f"cannot read repodata: {error}")

    record["gpg_key_reachable"] = False
    try:
        record["gpg_key_reachable"] = key_available(baseurl)
        if not record["gpg_key_reachable"]:
            problems.append("pubkey.gpg is not reachable")
    except (RuntimeError, OSError) as error:
        problems.append(f"cannot reach pubkey: {error}")

    repo_path = ("/etc/yum.repos.d/"
                 "_copr:copr.fedorainfracloud.org:xinyang26:piano-fedora-extras.repo"
                 if args.extras else "/etc/yum.repos.d/piano-fedora-copr.repo")
    record["repo_file"] = repo_file_state(repo_path)
    if record["repo_file"]["installed"] and args.extras:
        if not record["repo_file"].get("gpgcheck"):
            problems.append("installed extras .repo fails gpgcheck")
    elif record["repo_file"]["installed"]:
        for key in ("gpgcheck", "repo_gpgcheck_off"):
            if not record["repo_file"].get(key):
                problems.append(f"installed .repo fails {key}")
        if record["repo_file"]["excludes_kernel"]:
            problems.append("installed .repo still excludes kernel-piano")

    if not args.extras:
        try:
            record["kernel_visibility"] = repoquery_kernel(baseurl)
            if not record["kernel_visibility"]["listed"]:
                problems.append("kernel-piano is not visible in the repository")
        except (RuntimeError, OSError) as error:
            problems.append(f"repoquery failed: {error}")

        # 仓库里只能有改名后的包。两个名字都在时，全新安装会优先选名字完全
        # 匹配的 Fedora 包，补丁版就被绕过去了。发布记录里写了改名不等于仓库
        # 里真的改了：唯一可信的是仓库自身。
        for ours, row in record.get("graphics_renames", {}).items():
            if not row["present"]:
                continue  # 缺失已经在 missing_packages 里报过
            if row["fedora_name_present"]:
                fedora = GRAPHICS_RENAMES[ours]
                problems.append(f"repository still ships {fedora} next to {ours}; "
                                "a fresh install would pick the official build")

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
    report.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": record["status"], "problems": problems,
                      "report": str(report)}, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
