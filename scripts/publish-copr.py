#!/usr/bin/env python3
"""把一次精确的 piano-rpms 构建发布到 Fedora COPR。

COPR 公开实例没有打开二进制上传（`/build/create/rpm-upload` 不存在），
`copr-cli` 2.4 也没有 `uploadrpm` 子命令。所以这里走 SRPM 重建：用矩阵
产出的 `.src.rpm` 让 COPR 在干净的 chroot 里重新构建，结果进 COPR 的仓库。

kernel-piano 也在同一批里发布，仓库不再排除它。设备上 `dnf upgrade` 装上
新内核之后，包自己的 `%posttrans` 会 depmod、重建 initramfs，并把新内核
重新打进 ESP 上固件读取的 UKI；写失败时旧文件保持不动，设备继续用上一个
内核启动。

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
    "piano-camera", "piano-fingerprint", "piano-libfprint",
    "piano-pipewire-gstreamer", "xiaomi-piano-thp", "sm8750-video-accel",
    "uperf-linux", "libssc", "iio-sensor-proxy-ssc",
)
# 内核包：与用户态包一起发布。装到设备上之后由包自己的 %posttrans 重建
# initramfs 并重打 ESP 的 UKI，所以不需要在仓库层面排除。
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
    "piano-fingerprint",
    "piano-libfprint",
    "piano-pipewire-gstreamer",
    "xiaomi-piano-thp",
    "sm8750-video-accel",
    "uperf-linux",
    "kernel-piano",
)

RELEASEVER = json.loads(
    (PROJECT_ROOT / "product.json").read_text(encoding="utf-8"))["target"]["releasever"]
CHROOT = f"fedora-{RELEASEVER}-aarch64"
SUCCESS_STATES = {"succeeded", "skipped"}
FAILURE_STATES = {"failed", "canceled", "cancelled"}

# 图形栈的两个本地重建，来自各自的 workflow 与 artifact，不在 piano-rpms
# 矩阵里：它们的源包是 Fedora 官方 SRPM 加补丁，不属于 packages/ 目录。
# 两个的二进制包都改了名（mesa-piano-*、ffmpeg-piano / libavcodec-piano），并
# 声明提供 Fedora 的原名、与原名冲突。设备上装的从来不是 Fedora 的包，所以
# dnf upgrade 没有可覆盖的目标。源包名只有 mesa 改了；ffmpeg 的配方把变体放在
# `pkg_suffix` 里，源包仍叫 ffmpeg，靠 release 区分。发布前按源包名逐个核对，
# 二进制包名另外要求全部带 -piano-。
GRAPHICS = {
    "mesa": {"manifest": "FEDORA_MESA_BUILT_NOT_DEVICE_VERIFIED",
             "source": "mesa-piano"},
    "ffmpeg": {"manifest": "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED",
               "source": "ffmpeg"},
}
# 先发图形栈：设备上 dnf upgrade 要能看见改名后的包，
# 而矩阵里的包名与它们不重叠，顺序只影响失败时先报哪一个。
GRAPHICS_ORDER = ("mesa", "ffmpeg")

# 可选 extras 仓库：默认不启用，只放桌面补丁和两个 meta 包。GSD 改名，
# KWin/GTK3 保持同名、release 带 .piano1。GTK3 先编，两个 meta 最后。
EXTRAS_ORDER = ("gtk3", "kwin", "gnome-settings-daemon-piano",
                "piano-gnome-extras", "piano-kde-extras")
EXTRAS_STATUS = "PIANO_EXTRAS_BUILT_NOT_DEVICE_VERIFIED"
EXTRAS_PROJECT = "xinyang26/piano-fedora-extras"
DESKTOP_PACKAGES = ("gnome-settings-daemon-piano", "kwin", "gtk3")


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


def verify_graphics(directory, name):
    """核对一个图形栈 artifact：状态、源包名、SRPM 与每个 RPM 的摘要。

    这两个包靠改名压过官方版：子包声明 Provides 原名并与原名冲突。
    构件如果还是 Fedora 的源包名，说明构建脚本没走改名路径，发出去设备
    还是会被官方版盖掉。这里把源包名当作发布前提直接断言。
    """
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise PublishError(f"missing {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    expected = GRAPHICS[name]["manifest"]
    problems = []
    if manifest.get("status") != expected:
        problems.append(f"{name}: unexpected manifest status {manifest.get('status')!r}")
    if manifest.get("source_package") != GRAPHICS[name]["source"]:
        problems.append(f"{name}: manifest source package is "
                        f"{manifest.get('source_package')!r}, not "
                        f"{GRAPHICS[name]['source']!r}")
    # 二进制包也必须带改名后的名字。源包名对了而子包还是 Fedora 的原名，
    # 等于没有 Provides/Conflicts，设备照样会被官方版盖掉。
    for filename in manifest.get("rpms", {}):
        if "-piano-" not in filename:
            problems.append(f"{name}: {filename} is not a renamed piano package")
    for kind, key, subdir in (("RPM", "rpms", "rpms"), ("SRPM", "srpms", "srpms")):
        recorded = manifest.get(key)
        if not isinstance(recorded, dict) or not recorded:
            problems.append(f"{name}: manifest has no {key}")
            continue
        directory_ = directory / subdir
        for filename, row in sorted(recorded.items()):
            path = directory_ / filename
            if not path.is_file():
                problems.append(f"{name}: missing {kind} {filename}")
            elif path.stat().st_size != row.get("bytes"):
                problems.append(f"{name}: {kind} {filename} size changed")
            elif sha256(path) != row.get("sha256"):
                problems.append(f"{name}: {kind} {filename} sha256 changed")
    srpm_names = sorted(manifest.get("srpms", {}))
    if len(srpm_names) != 1:
        problems.append(f"{name}: expected exactly one SRPM, got {srpm_names}")
    if problems:
        raise PublishError("; ".join(problems))
    return {
        "status": manifest["status"],
        "source_package": manifest["source_package"],
        "srpm": srpm_names[0],
        "rpm_count": len(manifest.get("rpms", {})),
        "manifest_sha256": sha256(manifest_path),
    }


def verify_extras(directory):
    """核对独立 extras artifact：状态、包集合、文件摘要与 SRPM 唯一性。

    GSD 改名，KWin/GTK3 与 Fedora 同名但 release 必须带 piano1；两个 meta
    包是 noarch，不要求 piano1。这里不依赖 piano-rpms 的 manifest，extras
    可以单独构建、单独发布。
    """
    directory = Path(directory)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise PublishError(f"missing {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    problems = []
    if manifest.get("status") != EXTRAS_STATUS:
        problems.append(f"extras: unexpected status {manifest.get('status')!r}")
    if manifest.get("device_tested") is not False:
        problems.append("extras: device_tested must stay false until verification")
    rows = manifest.get("packages")
    if not isinstance(rows, dict):
        rows = {}
    if set(rows) != set(EXTRAS_ORDER):
        problems.append(f"extras: packages are {sorted(rows)}, "
                        f"expected {sorted(EXTRAS_ORDER)}")
    for name in EXTRAS_ORDER:
        row = rows.get(name)
        if not isinstance(row, dict):
            continue
        if row.get("binary_package") != name:
            problems.append(f"extras: {name} row names "
                            f"{row.get('binary_package')!r}")
        if name in DESKTOP_PACKAGES and "piano1" not in str(row.get("release", "")):
            problems.append(f"extras: {name} release {row.get('release')!r} "
                            "lacks piano1")
        if not row.get("desktops"):
            problems.append(f"extras: {name} has no desktop scope")
    if rows.get("gnome-settings-daemon-piano", {}).get("source_package") \
            != "gnome-settings-daemon":
        problems.append("extras: GSD row does not record the Fedora source package")

    for kind, key, subdir, pattern in (
            ("RPM", "files", "rpms", "*.rpm"),
            ("SRPM", "srpms", "srpms", "*.src.rpm")):
        recorded = manifest.get(key)
        if not isinstance(recorded, dict) or not recorded:
            problems.append(f"extras: no {key}")
            continue
        present = {path.name: path for path in (directory / subdir).glob(pattern)}
        if subdir == "rpms":
            present = {name: path for name, path in present.items()
                       if not name.endswith(".src.rpm")}
        for filename, record in sorted(recorded.items()):
            path = present.get(filename)
            if path is None:
                problems.append(f"extras: missing {kind} {filename}")
            elif path.stat().st_size != record.get("bytes"):
                problems.append(f"extras: {kind} {filename} size changed")
            elif sha256(path) != record.get("sha256"):
                problems.append(f"extras: {kind} {filename} sha256 changed")
        for filename in sorted(set(present) - set(recorded)):
            problems.append(f"extras: unrecorded {kind} {filename}")

    srpms = sorted(manifest.get("srpms", {}))
    if len(srpms) != len(EXTRAS_ORDER):
        problems.append(f"extras: expected {len(EXTRAS_ORDER)} SRPMs, got {srpms}")
    for name in EXTRAS_ORDER:
        matching = [srpm for srpm in srpms if srpm.startswith(name + "-")]
        if len(matching) != 1:
            problems.append(f"extras: expected one SRPM for {name}, got {matching}")
    if problems:
        raise PublishError("; ".join(problems))
    return {
        "status": manifest["status"],
        "packages": {name: rows[name]["nevra"] for name in EXTRAS_ORDER},
        "source_packages": {name: rows[name]["source_package"]
                            for name in EXTRAS_ORDER},
        "srpms": srpms,
        "manifest_sha256": sha256(manifest_path),
    }


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


def project_listed(stdout, name):
    """`copr-cli list` 的输出里是否出现这个项目。

    copr-cli 2.4 的列表是 `print_project_info()` 的逐行输出，项目名在
    `Name: piano-fedora` 这一行。按裸名字匹配永远不命中，于是每次发布
    都先撞一次 "You already have a project named"。
    """
    return re.search(rf"^\s*Name:\s*{re.escape(name)}\s*$", stdout, re.M) is not None


def ensure_chroot(binary, config, project):
    """项目里的 chroot 不随 releasever 自动出现，缺了 build 会被 COPR 拒绝。

    项目建的是一次成型：releasever 从 44 升到 45 之后，旧项目仍然只有
    fedora-44-aarch64，`copr-cli build --chroot fedora-45-aarch64` 直接失败。
    `modify --chroot` 只是往项目里追加，不动已有的那一个。
    """
    probe = run_cli(binary, config, ["get-chroot", f"{project}/{CHROOT}"], check=False)
    if probe.returncode == 0:
        return "present"
    run_cli(binary, config, ["modify", project, "--chroot", CHROOT])
    return "added"


def ensure_project(binary, config, project):
    """幂等创建项目：已存在就补 chroot，不存在才 create。"""
    name = project.split("/")[-1]
    listing = run_cli(binary, config, ["list"], check=False)
    if listing.returncode == 0 and project_listed(listing.stdout, name):
        return "exists+" + ensure_chroot(binary, config, project)
    created = run_cli(binary, config, [
        "create", project, "--chroot", CHROOT,
        "--description",
        f"Fedora {RELEASEVER} aarch64 packages for Xiaomi Pad 8 Pro (piano)",
        "--instructions",
        "kernel-piano upgrades rebuild the UKI on the ESP: the package's "
        "%posttrans rebuilds the initramfs and repacks /EFI/Linux/sunuefi.efi. "
        "Keep a copy of that file if you want a manual rollback.",
    ], check=False)
    if created.returncode:
        # 并发创建或权限问题：再看一次列表，真的没有才算失败。
        again = run_cli(binary, config, ["list"], check=False)
        if again.returncode == 0 and project_listed(again.stdout, name):
            return "raced"
        raise PublishError(f"cannot create {project}: {created.stderr.strip()}")
    return "created"


def parse_build_ids(stdout):
    """从 `copr-cli build` 的 stdout 里取出 build id。

    copr-cli 2.4（CI 钉住的版本）固定打印
    `Created builds: <id> [<id> ...]`，见 main.py 的
    print_build_info_and_wait()。
    """
    ids = []
    for line in stdout.splitlines():
        if "Created builds:" in line:
            ids.extend(re.findall(r"\d+", line.split("Created builds:", 1)[1]))
    return ids


def submit(binary, config, project, srpm_dir, package, srpm_name=None):
    """提交一个 SRPM。

    srpm_name 给包名与源包名不一致的图形栈用：mesa 的源包产出六个二进制
    包，按包名 glob 会找到多个；这里直接锁定 manifest 记录的那一个文件名。
    """
    matches = ([Path(srpm_dir) / srpm_name] if srpm_name
               else sorted(Path(srpm_dir).glob(package + "-*.src.rpm")))
    if len(matches) != 1 or not matches[0].is_file():
        raise PublishError(f"expected one SRPM for {package}, found "
                           f"{[p.name for p in matches if p.is_file()]}")
    result = run_cli(binary, config, [
        "build", project, str(matches[0]), "--chroot", CHROOT,
    ])
    # `copr-cli build` 不带 --nowait 时自己等完构建，失败以非零退出；
    # 但 id 仍要从 stdout 解析，发布记录和事后核对都靠它。
    ids = parse_build_ids(result.stdout)
    if len(ids) != 1:
        raise PublishError(
            f"{package}: expected one build id, got {ids or 'none'}: "
            f"{result.stdout.strip()[:400]}")
    return result.stdout.strip(), ids[0]


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


def write_record(record, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")


def publish_extras(args):
    project = args.project or EXTRAS_PROJECT
    owner, separator, name = project.partition("/")
    if not separator or not owner or not name:
        raise PublishError("--project must be owner/name")
    extras = verify_extras(args.extras)
    record_path = Path(args.record or "build/copr-extras-publication.json")
    record = {
        "status": "VALIDATED",
        "mode": "extras",
        "project": project,
        "chroot": CHROOT,
        "packages": sorted(extras["packages"]),
        "source_packages": extras["source_packages"],
        "srpms": extras["srpms"],
        "manifest_sha256": extras["manifest_sha256"],
    }
    if not args.execute:
        write_record(record, record_path)
        print(json.dumps({"status": "VALIDATED", "record": str(record_path)},
                         indent=2))
        return 0

    binary = copr_cli()
    config = write_copr_config(record_path.with_suffix(".copr.conf"), owner)
    try:
        record["project_state"] = ensure_project(binary, config, project)
        outputs, ids = {}, []
        for package in EXTRAS_ORDER:
            output, build_id = submit(binary, config, project,
                                      Path(args.extras) / "srpms", package)
            outputs[package] = output
            ids.append(build_id)
        states = wait_for_builds(binary, config, project, ids, timeout=args.timeout)
        record.update(status="PUBLISHED", build_ids=ids, build_states=states,
                      outputs=outputs)
    finally:
        config.unlink(missing_ok=True)
    write_record(record, record_path)
    print(json.dumps({"status": "PUBLISHED", "builds": record["build_ids"],
                      "record": str(record_path)}, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("default", "extras"), default="default")
    parser.add_argument("--run-id", default="",
                        help="默认模式：成功的 host-check run，必须带 piano-rpms artifact")
    parser.add_argument("--rpms", default="build/in/piano-rpms",
                        help="下载后的 piano-rpms artifact 目录")
    parser.add_argument("--extras", default="build/in/piano-extras",
                        help="解包后的 piano-extras artifact 目录")
    parser.add_argument("--mesa", default="build/in/mesa",
                        help="解包后的 piano-fedora-mesa artifact 目录")
    parser.add_argument("--ffmpeg", default="build/in/ffmpeg",
                        help="解包后的 piano-fedora-ffmpeg artifact 目录")
    parser.add_argument("--project", default="",
                        help="默认模式用 xinyang26/piano-fedora，extras 模式用 "
                             "xinyang26/piano-fedora-extras")
    parser.add_argument("--kernel-run-id", default="",
                        help="可选：要求 manifest 的内核 run 与此值一致")
    parser.add_argument("--record", default="")
    parser.add_argument("--execute", action="store_true",
                        help="真正上传并等待构建；默认只校验")
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()

    if args.mode == "extras":
        return publish_extras(args)

    if not args.run_id:
        raise PublishError("--run-id is required in default mode")
    project = args.project or "xinyang26/piano-fedora"
    owner, separator, name = project.partition("/")
    if not separator or not owner or not name:
        raise PublishError("--project must be owner/name")
    record_path = Path(args.record or "build/copr-publication.json")

    run = verify_run(args.run_id)
    manifest = load_manifest(args.rpms)
    packages, kernel_run = verify_artifacts(args.rpms, manifest, run)
    graphics = {name: verify_graphics(getattr(args, name), name) for name in GRAPHICS}
    # 同一套 RPM 只能来自一次内核 run。manifest 的内核 run 必须和调用者
    # 声明的一致，否则说明 artifact 被换过或跨 run 混装。
    if args.kernel_run_id and str(args.kernel_run_id) != kernel_run:
        raise PublishError(
            f"manifest kernel run {kernel_run} != --kernel-run-id {args.kernel_run_id}")
    record = {
        "status": "VALIDATED",
        "run_id": str(args.run_id),
        "run_url": run.get("html_url"),
        "project": project,
        "chroot": CHROOT,
        "kernel_run": manifest["kernel_run"],
        "kernel_release": manifest["kernel_release"],
        "sunuefi_commit": manifest["sunuefi_commit"],
        "packages": sorted(packages),
        "graphics": graphics,
        "kernel_packages": list(KERNEL_PACKAGES),
        "manifest_sha256": sha256(Path(args.rpms) / "piano-rpms-manifest.json"),
    }
    if not args.execute:
        write_record(record, record_path)
        print(json.dumps({"status": "VALIDATED", "record": str(record_path)},
                         indent=2))
        return 0

    binary = copr_cli()
    config = write_copr_config(record_path.with_suffix(".copr.conf"), owner)
    try:
        record["project_state"] = ensure_project(binary, config, project)
        outputs, ids = {}, []
        # 先发图形栈：设备 dnf upgrade 要能看见改名后的补丁版，
        # 而矩阵包的包名与它们不重叠，顺序只决定失败时先报哪一个。
        for name in GRAPHICS_ORDER:
            srpm_dir = Path(getattr(args, name)) / "srpms"
            output, build_id = submit(binary, config, project,
                                      srpm_dir, name,
                                      srpm_name=graphics[name]["srpm"])
            outputs[name] = output
            ids.append(build_id)
        for package in BUILD_ORDER:
            if package not in packages:
                raise PublishError(f"{package} was not built by this run")
            output, build_id = submit(binary, config, project,
                                      Path(args.rpms) / "srpms", package)
            outputs[package] = output
            ids.append(build_id)
        states = wait_for_builds(binary, config, project, ids, timeout=args.timeout)
        record.update(status="PUBLISHED", build_ids=ids, build_states=states,
                      outputs=outputs)
    finally:
        config.unlink(missing_ok=True)
    write_record(record, record_path)
    print(json.dumps({"status": "PUBLISHED", "builds": record["build_ids"],
                      "record": str(record_path)}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PublishError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        sys.exit(1)
