#!/usr/bin/env python3
"""把一次精确的 piano-rpms 构建发布到 Fedora COPR。

COPR 公开实例没有打开二进制上传（`/build/create/rpm-upload` 不存在），
`copr-cli` 2.4 也没有 `uploadrpm` 子命令。所以这里走 SRPM 重建：用矩阵
产出的 `.src.rpm` 让 COPR 在干净的 chroot 里重新构建用户态包，结果进
COPR 的仓库。

kernel-piano 也发布，但生成的 `.repo` 用 `exclude=kernel-piano*` 排除它：
用户态 `dnf upgrade` 无法替换 ESP 实际引导的 Image，装上 COPR 的内核会
让 ESP 和 rootfs 的内核版本脱钩。

默认只做校验（dry-run）。真正上传必须显式给 `--execute`。
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# 用户态包：COPR 仓库对它们提供升级。
USERSPACE = (
    "piano-fedora-configs", "alsa-xiaomi-piano", "xiaomi-piano-firmware",
    "piano-camera", "xiaomi-piano-thp", "sm8750-video-accel",
    "uperf-linux", "libssc", "iio-sensor-proxy-ssc",
)
# 内核包：发布进仓库供审计，但 .repo 默认排除。
KERNEL_PACKAGES = ("kernel-piano",)
REQUIRED = USERSPACE + KERNEL_PACKAGES

# 这些包互相依赖，COPR 按顺序构建，后者需要前者的 BuildRequires 已经在
# 项目里。iio-sensor-proxy-ssc 的 BuildRequires 里有 libssc。
BUILD_ORDER = (
    "libssc",
    "iio-sensor-proxy-ssc",
    "piano-fedora-configs",
    "alsa-xiaomi-piano",
    "xiaomi-piano-firmware",
    "piano-camera",
    "xiaomi-piano-thp",
    "sm8750-video-accel",
    "uperf-linux",
    "kernel-piano",
)

CHROOT = "fedora-44-aarch64"
SUCCESS_STATES = {"succeeded", "skipped"}
FAILURE_STATES = {"failed", "canceled", "cancelled"}


class PublishError(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_manifest(rpms_dir):
    manifest = Path(rpms_dir) / "piano-rpms-manifest.json"
    if not manifest.is_file():
        raise PublishError(f"missing {manifest}")
    data = json.loads(manifest.read_text())
    if data.get("status") != "PIANO_RPMS_BUILT":
        raise PublishError(f"unexpected manifest status: {data.get('status')!r}")
    return data


def github_api(path):
    """gh api，失败时给出可读原因而不是裸 traceback。"""
    try:
        return json.loads(subprocess.check_output(["gh", "api", path], text=True,
                                                  stderr=subprocess.PIPE))
    except subprocess.CalledProcessError as error:
        detail = (error.stderr or b"").decode("utf-8", "replace").strip()
        raise PublishError(f"gh api {path} failed: {detail or error.returncode}") from error
    except (OSError, ValueError) as error:
        raise PublishError(f"gh api {path} failed: {error}") from error


def verify_run(run_id):
    """只接受 workflow_id 是 host-check 的成功 run。

    build-piano-rpms.yml 是 workflow_call 复用件，没有自己的 run；实际
    run 属于已注册的 host-check。按 workflow_id 比对，display name 改名
    或撞名都不影响判断。
    """
    if not re.fullmatch(r"[1-9][0-9]*", str(run_id)):
        raise PublishError(f"invalid run id: {run_id!r}")
    repository = os.environ.get("GITHUB_REPOSITORY")
    if not repository:
        raise PublishError("GITHUB_REPOSITORY is required")
    run = github_api(f"repos/{repository}/actions/runs/{run_id}")
    workflow = github_api(f"repos/{repository}/actions/workflows/host-check.yml")
    if run.get("workflow_id") != workflow.get("id"):
        raise PublishError(f"run {run_id} is not a host-check run")
    if (run.get("status"), run.get("conclusion")) != ("completed", "success"):
        raise PublishError(f"run {run_id} is {run.get('status')}/{run.get('conclusion')}")
    return run


def verify_artifacts(rpms_dir, manifest, run):
    """每个 RPM/SRPM 的大小和 SHA256 都必须和 manifest 一致。

    二进制 RPM 在 artifact 根目录，SRPM 在 srpms/ 子目录。
    """
    problems = []
    for kind, key, subdir, pattern in (
            ("RPM", "files", "", "*.rpm"),
            ("SRPM", "srpms", "srpms", "*.src.rpm")):
        recorded = manifest.get(key)
        if not isinstance(recorded, dict) or not recorded:
            problems.append(f"manifest has no {key}")
            continue
        directory = Path(rpms_dir) / subdir
        present = {p.name: p for p in directory.glob(pattern)}
        if subdir == "":
            # Binary RPMs only; the SRPMs live in srpms/.
            present = {name: path for name, path in present.items()
                       if not name.endswith(".src.rpm") and not name.endswith(".nosrc.rpm")}
        for name, row in sorted(recorded.items()):
            path = present.get(name)
            if path is None:
                problems.append(f"missing {kind} {name}")
                continue
            if path.stat().st_size != row.get("bytes"):
                problems.append(f"{kind} {name} size changed")
            elif sha256(path) != row.get("sha256"):
                problems.append(f"{kind} {name} sha256 changed")
        for name in sorted(set(present) - set(recorded)):
            problems.append(f"unrecorded {kind} {name}")
    names = {name.rsplit("-", 2)[0] for name in manifest.get("files", {})}
    missing = sorted(set(REQUIRED) - names)
    if missing:
        problems.append("missing packages: " + ", ".join(missing))
    # kernel run 绑定：manifest 里的内核 run 必须和这次 host-check 的
    # kernel_run_id 输入一致，防止把两个 run 的产物拼在一起。
    kernel_run = manifest.get("kernel_run")
    if not kernel_run or not re.fullmatch(r"[1-9][0-9]*", str(kernel_run)):
        problems.append("manifest has no kernel run binding")
    if not manifest.get("kernel_release"):
        problems.append("manifest has no kernel release")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("sunuefi_commit", ""))):
        problems.append("manifest has no SunUEFI commit")
    if problems:
        raise PublishError("; ".join(problems))
    return names, str(kernel_run)


def copr_cli():
    binary = os.environ.get("COPR_CLI") or "copr-cli"
    try:
        subprocess.run([binary, "--version"], capture_output=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PublishError(f"{binary} is not usable: {exc}") from exc
    return binary


def write_copr_config(path, username):
    """从 secrets 写一份临时 copr 配置，权限 0600，退出时删除。

    `login` 是 API 登录名（哈希），`username` 是 FAS 用户名。copr-cli
    不带参数执行 `list` 时按 `username` 查项目，两者混用会让已经建好的
    项目在下一次发布时看起来不存在。
    """
    login = os.environ.get("COPR_LOGIN")
    token = os.environ.get("COPR_TOKEN")
    if not login or not token:
        raise PublishError("COPR_LOGIN and COPR_TOKEN are required for --execute")
    path = Path(path)
    path.write_text(
        "[copr-cli]\n"
        f"login = {login}\n"
        f"username = {username}\n"
        f"token = {token}\n"
        "copr_url = https://copr.fedorainfracloud.org\n"
    )
    path.chmod(0o600)
    return path


def run_cli(binary, config, args, *, check=True):
    env = dict(os.environ, COPR_CONFIG=str(config))
    result = subprocess.run([binary, "--config", str(config), *args],
                            capture_output=True, text=True, env=env)
    if check and result.returncode:
        raise PublishError(f"copr-cli {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def ensure_project(binary, config, project):
    """幂等创建项目；已存在就只确认 chroot 打开着。"""
    listing = run_cli(binary, config, ["list"], check=False)
    if listing.returncode == 0 and re.search(
            rf"^\s*{re.escape(project.split('/')[-1])}\s*$", listing.stdout, re.M):
        return "exists"
    created = run_cli(binary, config, [
        "create", project, "--chroot", CHROOT,
        "--description", "Fedora 44 aarch64 packages for Xiaomi Pad 8 Pro (piano)",
        "--instructions",
        "kernel-piano is excluded on purpose: user-space DNF cannot update the "
        "ESP that SunUEFI boots. Use the release bundle for kernel updates.",
    ], check=False)
    if created.returncode:
        # 并发创建或权限问题：再看一次列表，真的没有才算失败。
        again = run_cli(binary, config, ["list"], check=False)
        if again.returncode == 0 and re.search(
                rf"^\s*{re.escape(project.split('/')[-1])}\s*$", again.stdout, re.M):
            return "raced"
        raise PublishError(f"cannot create {project}: {created.stderr.strip()}")
    return "created"


def submit(binary, config, project, srpm_dir, package):
    matches = sorted(Path(srpm_dir).glob(package + "-*.src.rpm"))
    if len(matches) != 1:
        raise PublishError(f"expected one SRPM for {package}, found {len(matches)}")
    result = run_cli(binary, config, [
        "build", project, str(matches[0]), "--chroot", CHROOT,
    ])
    # `copr-cli build` 在构建失败时返回非零；成功时 stdout 里有 build id。
    match = re.search(r"Build was created:.*?(\d+)|Created builds:\s*(.+)", result.stdout)
    return result.stdout.strip(), match


def wait_for_builds(binary, config, project, build_ids, timeout=7200, interval=30):
    """轮询 build 状态。任何 failed/canceled 都让整体失败，绝不误报成功。"""
    pending = set(build_ids)
    states = {}
    deadline = time.time() + timeout
    while pending and time.time() < deadline:
        for build_id in sorted(pending):
            result = run_cli(binary, config, ["status", str(build_id)], check=False)
            if result.returncode:
                continue
            state = result.stdout.strip().splitlines()[-1].strip().lower() if result.stdout.strip() else ""
            states[build_id] = state
            if state in SUCCESS_STATES:
                pending.discard(build_id)
            elif state in FAILURE_STATES:
                raise PublishError(f"COPR build {build_id} ended {state}")
        if pending:
            time.sleep(interval)
    if pending:
        raise PublishError("COPR builds did not finish: " + ", ".join(sorted(pending)))
    return states


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True,
                        help="成功的 host-check run，必须带 piano-rpms artifact")
    parser.add_argument("--rpms", default="build/in/piano-rpms",
                        help="下载后的 piano-rpms artifact 目录")
    parser.add_argument("--project", default="xinyang26/piano-fedora")
    parser.add_argument("--kernel-run-id", default="",
                        help="可选：要求 manifest 的内核 run 与此值一致")
    parser.add_argument("--record", default="build/copr-publication.json")
    parser.add_argument("--execute", action="store_true",
                        help="真正上传并等待构建；默认只校验")
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()
    owner, separator, name = args.project.partition("/")
    if not separator or not owner or not name:
        raise PublishError("--project must be owner/name")

    run = verify_run(args.run_id)
    manifest = load_manifest(args.rpms)
    packages, kernel_run = verify_artifacts(args.rpms, manifest, run)
    # 同一套 RPM 只能来自一次内核 run。manifest 的内核 run 必须和调用者
    # 声明的一致，否则说明 artifact 被换过或跨 run 混装。
    if args.kernel_run_id and str(args.kernel_run_id) != kernel_run:
        raise PublishError(
            f"manifest kernel run {kernel_run} != --kernel-run-id {args.kernel_run_id}")
    record = {
        "status": "VALIDATED",
        "run_id": str(args.run_id),
        "run_url": run.get("html_url"),
        "project": args.project,
        "chroot": CHROOT,
        "kernel_run": manifest["kernel_run"],
        "kernel_release": manifest["kernel_release"],
        "sunuefi_commit": manifest["sunuefi_commit"],
        "packages": sorted(packages),
        "excluded": list(KERNEL_PACKAGES),
        "manifest_sha256": sha256(Path(args.rpms) / "piano-rpms-manifest.json"),
    }
    if not args.execute:
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        Path(args.record).write_text(json.dumps(record, indent=2) + "\n")
        print(json.dumps({"status": "VALIDATED", "record": args.record}, indent=2))
        return 0

    binary = copr_cli()
    config = write_copr_config(Path(args.record).with_suffix(".copr.conf"), owner)
    try:
        record["project_state"] = ensure_project(binary, config, args.project)
        outputs, ids = {}, []
        for package in BUILD_ORDER:
            if package not in packages:
                raise PublishError(f"{package} was not built by this run")
            output, match = submit(binary, config, args.project, Path(args.rpms) / "srpms", package)
            outputs[package] = output
            if match and match.group(1):
                ids.append(match.group(1))
        states = wait_for_builds(binary, config, args.project, ids, timeout=args.timeout)
        record.update(status="PUBLISHED", build_ids=ids, build_states=states,
                      outputs=outputs)
    finally:
        config.unlink(missing_ok=True)
    Path(args.record).parent.mkdir(parents=True, exist_ok=True)
    Path(args.record).write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"status": "PUBLISHED", "builds": record["build_ids"],
                      "record": args.record}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PublishError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
