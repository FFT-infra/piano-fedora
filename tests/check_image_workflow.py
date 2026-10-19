#!/usr/bin/env python3
"""用没有 Python 的模拟 Fedora 容器执行镜像 workflow 的 RPM 安装入口。"""

import hashlib
import base64
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from piano_artifacts import uefi_patch_identities


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def check_mesa(root):
    mesa = module("mesa_builder", ROOT / "scripts/build-fedora-mesa.py")
    spec, srpm, top = root / "mesa.spec", root / "mesa.src.rpm", root / "rpmbuild"
    with patch.object(mesa, "run") as dnf:
        mesa.install_build_dependencies(spec, srpm, top)
        args = tuple(map(str, dnf.call_args.args))
        # spec 提供本机条件依赖；SRPM 继续提供冻结的动态 Rust crate 依赖。
        assert "--spec" in args, "native spec build requirements were omitted"
        assert args[args.index("--spec") + 1] == str(spec), args
        assert args[args.index("--srpm") + 1] == str(srpm), args
        assert "_topdir " + str(top) in args, args
    header = bytearray(64)
    header[:5] = b"\x7fELF\x02"
    struct.pack_into("<H", header, 18, 183)
    data = bytes(header) + struct.pack("<Q", 0xffff44050001) + b"Adreno (TM) 830v1\0"
    library = root / "usr/lib64"
    library.mkdir(parents=True)
    for name in ("libgallium-26.2.3.so", "libvulkan_freedreno.so"):
        (library / name).write_bytes(data)
    assert len(mesa.driver_proof(root)) == 2
    (library / "libgallium-26.2.3.so").write_bytes(data.replace(
        struct.pack("<Q", 0xffff44050001), struct.pack("<Q", 0xffff44050000)))
    try:
        mesa.driver_proof(root)
    except ValueError as error:
        assert "lacks" in str(error)
    else:
        raise AssertionError("a generic A830 device table was accepted for piano")


def check_bundle(root, verifier=ROOT / "scripts/verify-bundle.py"):
    bundle = module("bundle_verifier", verifier)
    root.mkdir()
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    write = lambda name, value: (root / name).write_text(json.dumps(value))
    raw = b"verified fixture raw bytes"
    for name in ("sunuefi_esp.img", "PianoUEFI-product.img", "pianoroot.f2fs.img.zst"):
        (root / name).write_bytes(b"fixture")
    (root / "boot_sunuefi.img").write_bytes(b"ANDROID!fixture")
    write("kernel-manifest.json", {"kernel_release": "fixture"})
    kernel = digest(root / "kernel-manifest.json")
    identity = {"kernel_release": "fixture", "kernel_manifest_sha256": kernel}
    for name in ("runtime", "initramfs"):
        write(name + "-manifest.json", identity)
    write("mesa-manifest.json", {"status": "FEDORA_MESA_BUILT_NOT_DEVICE_VERIFIED", "chip_id": "0xffff44050001"})
    write("ffmpeg-manifest.json", {"status": "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED",
                                   "releasever": "44", "arch": "aarch64",
                                   "decoders": {"h264_v4l2m2m": " V..... h264_v4l2m2m",
                                                "hevc_v4l2m2m": " V..... hevc_v4l2m2m"}})
    write("rootfs-manifest.json", {**identity, **{name + "_manifest_sha256": digest(root / (name + "-manifest.json"))
                                                for name in ("runtime", "mesa", "ffmpeg")}})
    # esp-manifest.json is produced upstream and keeps its internal esp.img /
    # boot.img keys; verify-bundle maps them to the release asset names.
    write("esp-manifest.json", {"kernel_manifest_sha256": kernel,
          "esp_files": ["/EFI/Piano/stable/" + name for name in ("Image", "board.dtb", "initramfs", "boot.img")],
          "files": {
              "esp.img": {"sha256": digest(root / "sunuefi_esp.img"),
                           "bytes": (root / "sunuefi_esp.img").stat().st_size},
              "boot.img": {"sha256": digest(root / "boot_sunuefi.img"),
                            "bytes": (root / "boot_sunuefi.img").stat().st_size},
              "PianoUEFI-product.img": {"sha256": digest(root / "PianoUEFI-product.img"),
                                         "bytes": (root / "PianoUEFI-product.img").stat().st_size},
          }})
    proof = {key: True for key in ("file_content_checked", "numeric_owner_checked", "mode_checked", "symlink_targets_checked",
                                   "hardlink_groups_checked", "mtime_checked", "xattr_checked", "fsck_read_only", "fsck_ran_unmounted")}
    proof.update(fsck_exit=0, fsck_changed_image=False, xattr_read_errors_ignored=False, selinux_labels=1)
    image = {"status": "IMAGE_VERIFIED", "format": "raw-f2fs", "label": "PIANOROOT", "verify": proof,
             "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
             **{name + "_manifest_sha256": digest(root / (name + "-manifest.json"))
                for name in ("rootfs", "kernel", "runtime", "mesa", "ffmpeg", "initramfs")}}
    write("root-image-manifest.json", image)
    write("source-runs.json", {"kernel_run": "fixture", "uefi_run": "12345"})
    source = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    uefi = {"schema_version": 1, "run_id": "12345", "workflow": ".github/workflows/build-uefi.yml",
            "producer_commit": "1" * 40, "sunuefi_commit": source,
            "project_source_sha256": uefi_patch_identities(ROOT),
            "product": {"sha256": digest(root / "PianoUEFI-product.img"),
                        "bytes": (root / "PianoUEFI-product.img").stat().st_size},
            "evidence": {"kind": "successful-actions-build-log", "log_sha256": "2" * 64}}
    # 交付包绑定的 UEFI 项目补丁：内存与 Gunyah RM 交接都要能在 uefi-source.json
    # 里查到，否则 bundle 可以拿一个新的 product 配旧的来源清单。
    uefi_sources = {"patches/uefi-live-memory/series.json",
                    "patches/uefi-live-memory/0001-adopt-live-memory-map.patch",
                    "patches/uefi-live-rm/series.json",
                    "patches/uefi-live-rm/0001-adopt-live-resource-manager-caps.patch"}
    assert uefi_sources <= set(uefi["project_source_sha256"]), "UEFI handoff patches are not bound to the product"
    write("uefi-source.json", uefi)

    def verify(data=raw):
        with patch.object(bundle.subprocess, "Popen") as spawn:
            process = spawn.return_value.__enter__.return_value
            process.stdout, process.wait.return_value = io.BytesIO(data), 0
            value = bundle.verify(root)
            spawn.assert_called_once()
            return value

    assert verify()["device_tested"] is False
    for changed in ({"sunuefi_commit": "0" * 40}, {"run_id": "12346"},
                    {"product": {"sha256": "0" * 64, "bytes": 7}},
                    {"project_source_sha256": {}},
                    {"project_source_sha256": {key: value for key, value in uefi["project_source_sha256"].items()
                                                if key not in uefi_sources}},
                    {"project_source_sha256": {**uefi["project_source_sha256"],
                                               "patches/uefi-live-memory/series.json": "0" * 64}},
                    {"project_source_sha256": {**uefi["project_source_sha256"],
                                               "patches/uefi-live-rm/0001-adopt-live-resource-manager-caps.patch": "0" * 64}}):
        write("uefi-source.json", {**uefi, **changed})
        try:
            verify()
        except ValueError:
            pass
        else:
            raise AssertionError("bundle accepted a foreign UEFI source, run, patch set or image")
    write("uefi-source.json", uefi)
    for name, data in (("mesa", raw), ("ffmpeg", raw), (None, b"wrong raw image payload")):
        write("root-image-manifest.json", {**image, **({name + "_manifest_sha256": "0" * 64} if name else {})})
        try:
            verify(data)
        except ValueError:
            pass
        else:
            raise AssertionError("bundle accepted mixed component metadata or corrupted raw bytes")
    write("root-image-manifest.json", image)
    # 缺 FFmpeg 身份或解码器证明的 bundle 必须拒绝：来源闭包不能只靠 manifest 名字。
    for changed in ({"status": "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED", "arch": "x86_64"},
                    {"decoders": {"h264_v4l2m2m": " V..... h264_v4l2m2m"}}):
        write("ffmpeg-manifest.json", {"status": "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED",
                                       "releasever": "44", "arch": "aarch64",
                                       "decoders": {"h264_v4l2m2m": " V..... h264_v4l2m2m",
                                                    "hevc_v4l2m2m": " V..... hevc_v4l2m2m"},
                                       **changed})
        write("root-image-manifest.json", {**image, "ffmpeg_manifest_sha256": digest(root / "ffmpeg-manifest.json")})
        write("rootfs-manifest.json", {**identity, **{name + "_manifest_sha256": digest(root / (name + "-manifest.json"))
                                                    for name in ("runtime", "mesa", "ffmpeg")}})
        try:
            verify()
        except ValueError:
            pass
        else:
            raise AssertionError("bundle accepted an unverified FFmpeg manifest: " + str(changed))
    # FFmpeg manifest 缺失时 bundle 必须判为不完整（REQUIRED 必须含它）。
    write("ffmpeg-manifest.json", {"status": "FEDORA_FFMPEG_BUILT_NOT_DEVICE_VERIFIED",
                                   "releasever": "44", "arch": "aarch64",
                                   "decoders": {"h264_v4l2m2m": " V..... h264_v4l2m2m",
                                                "hevc_v4l2m2m": " V..... hevc_v4l2m2m"}})
    write("root-image-manifest.json", {**image, "ffmpeg_manifest_sha256": digest(root / "ffmpeg-manifest.json")})
    write("rootfs-manifest.json", {**identity, **{name + "_manifest_sha256": digest(root / (name + "-manifest.json"))
                                                for name in ("runtime", "mesa", "ffmpeg")}})
    saved = (root / "ffmpeg-manifest.json").read_bytes()
    (root / "ffmpeg-manifest.json").unlink()
    try:
        verify()
    except ValueError as error:
        assert "incomplete" in str(error), error
    else:
        raise AssertionError("bundle missing the FFmpeg manifest was accepted")
    (root / "ffmpeg-manifest.json").write_bytes(saved)


def step_body(workflow, name):
    """取出某个 step 的 run 脚本体，缩进去掉一层。env 块在 run 之前，不混入。"""
    lines = (ROOT / workflow).read_text().splitlines()
    start = lines.index("      - name: " + name)
    run = lines.index("        run: |", start) + 1
    body = []
    for line in lines[run:]:
        if line.strip() and not line.startswith("          "):
            break
        body.append(line[10:])
    assert body, f"{name} step is empty"
    return "\n".join(body)


def executable(path, text):
    path.write_text("#!/bin/bash\nset -euo pipefail\n" + text)
    path.chmod(0o755)


GH_STUB = r'''
import json, os, re, sys
path = sys.argv[2]
workflows = json.load(open(os.environ['PIANO_GH_WORKFLOWS']))
artifacts = json.load(open(os.environ['PIANO_GH_ARTIFACTS']))
runs = json.load(open(os.environ['PIANO_GH_RUNS']))
ids = {name: 1000 + index for index, name in enumerate(sorted(workflows))}
m = re.search(r'/actions/runs/(\d+)/artifacts', path)
if m:
    print(json.dumps({'artifacts': [{'name': name, 'expired': False} for name in artifacts]})); sys.exit(0)
m = re.match(r'.*/actions/runs/(\d+)$', path)
if m:
    rid = m.group(1)
    print(json.dumps({'id': int(rid), 'workflow_id': ids[runs[rid]], 'status': 'completed',
                      'conclusion': 'success'})); sys.exit(0)
m = re.match(r'.*/actions/workflows/([^/]+)/runs', path)
if m:
    wf = m.group(1)
    print(json.dumps({'workflow_runs': [{'id': 5000 + ids[wf], 'workflow_id': ids[wf],
                                         'status': 'completed', 'conclusion': 'success'}]})); sys.exit(0)
m = re.match(r'.*/actions/workflows/([^/]+)$', path)
if m:
    print(json.dumps({'id': ids[m.group(1)]})); sys.exit(0)
raise SystemExit('unexpected gh path: ' + path)
'''


def resolve_step_env(root, workflows, artifacts, runs):
    """构造跑 resolve step 所需的环境：PATH 带 gh 桩，fixture 指向临时文件。"""
    binary = root / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    executable(binary / "gh", 'exec python3 "$PIANO_GH_STUB" "$@"\n')
    (root / "gh_stub.py").write_text(GH_STUB)
    (root / "workflows.json").write_text(json.dumps(workflows))
    (root / "artifacts.json").write_text(json.dumps(artifacts))
    (root / "runs.json").write_text(json.dumps(runs))
    output = root / "github-output"
    output.write_text("")
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
           "PIANO_GH_STUB": str(root / "gh_stub.py"),
           "PIANO_GH_WORKFLOWS": str(root / "workflows.json"),
           "PIANO_GH_ARTIFACTS": str(root / "artifacts.json"),
           "PIANO_GH_RUNS": str(root / "runs.json"),
           "GITHUB_REPOSITORY": "owner/repo", "GITHUB_OUTPUT": str(output)}
    return env, output


def run_resolve(root, kernel_artifact, runtime_artifact, kernel_run="", runtime_run="",
                rootfs_run="", mesa_run="", ffmpeg_run="", release_mode="false"):
    workflows = ["build-rootfs.yml", "build-kernel.yml", "build-device-runtime.yml",
                 "build-mesa.yml", "host-check.yml"]
    artifacts = ["rootfs-44-gnome", "kernel-release-7.2.9", "kernel-sdk-7.2.9",
                 "kernel-experimental-gunyah", "piano-fedora-runtime",
                 "piano-fedora-runtime-gunyah", "piano-fedora-mesa", "piano-fedora-ffmpeg"]
    runs = {"7001": "build-kernel.yml", "7002": "build-device-runtime.yml"}
    env, output = resolve_step_env(root, workflows, artifacts, runs)
    env.update(ROOTFS_ARTIFACT="rootfs-44-gnome", KERNEL_ARTIFACT=kernel_artifact,
               RUNTIME_ARTIFACT=runtime_artifact,
               KERNEL_RUN=kernel_run, RUNTIME_RUN=runtime_run,
               ROOTFS_RUN=rootfs_run, MESA_RUN=mesa_run,
               FFMPEG_RUN=ffmpeg_run, RELEASE_MODE=release_mode)
    result = subprocess.run(["/bin/bash", "-euo", "pipefail", "-c",
                             step_body(".github/workflows/build-image.yml", "Resolve successful input runs")],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    return result, output.read_text()


def check_image_kernel_tiers(root):
    """实际跑 resolve step：正式档配对，旧实验选择必须拒绝。"""
    root.mkdir()
    # 正式档：不钉 run，动态解析；必须落 standard。
    result, output = run_resolve(root / "std", "kernel-release-7.2.9", "piano-fedora-runtime")
    assert result.returncode == 0, result.stderr
    assert "kernel_tier=standard" in output, output
    # 即使提供旧实验产物的完整 run，也不能混入正式构建。
    result, output = run_resolve(root / "exp", "kernel-experimental-gunyah",
                                 "piano-fedora-runtime-gunyah", "7001", "7002")
    assert result.returncode != 0, "obsolete experimental selection accepted"
    # 只放标准 artifact、却选实验档：必须失败，证明真的请求实验 artifact 而非静默回退。
    workflows = ["build-rootfs.yml", "build-kernel.yml", "build-device-runtime.yml",
                 "build-mesa.yml", "host-check.yml"]
    env, _ = resolve_step_env(root / "onlystd",
                              workflows,
                              ["rootfs-44-gnome", "kernel-release-7.2.9", "kernel-sdk-7.2.9",
                               "piano-fedora-runtime", "piano-fedora-mesa", "piano-fedora-ffmpeg"],
                              {"7001": "build-kernel.yml", "7002": "build-device-runtime.yml"})
    env.update(KERNEL_ARTIFACT="kernel-experimental-gunyah",
               ROOTFS_ARTIFACT="rootfs-44-gnome",
               RUNTIME_ARTIFACT="piano-fedora-runtime-gunyah", KERNEL_RUN="7001", RUNTIME_RUN="7002")
    result = subprocess.run(["/bin/bash", "-euo", "pipefail", "-c",
                             step_body(".github/workflows/build-image.yml", "Resolve successful input runs")],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode != 0, "an experimental selection fell back to the standard kernel"
    # 拒绝：未知 kernel_artifact、runtime 与档位不配对、实验缺 run id。
    for kernel, runtime, krun, rrun, why in (
            ("kernel-foo", "piano-fedora-runtime", "", "", "unknown kernel artifact"),
            ("kernel-release-7.2.9", "piano-fedora-runtime-gunyah", "", "", "runtime/kernel tier mismatch"),
            ("kernel-experimental-gunyah", "piano-fedora-runtime-gunyah", "", "7002", "missing kernel run"),
            ("kernel-experimental-gunyah", "piano-fedora-runtime-gunyah", "7001", "", "missing runtime run")):
        result, _ = run_resolve(root / ("reject-" + why.replace(" ", "-")), kernel, runtime, krun, rrun)
        assert result.returncode != 0, "accepted a bad selection: " + why


def make_tar(path, member, content):
    src = path.parent / (path.name + ".src")
    src.mkdir(parents=True, exist_ok=True)
    (src / member).write_text(content)
    subprocess.run(["tar", "-I", "zstd -6 -T0", "-cf", str(path), "-C", str(src), "."], check=True)


def run_bundle_resolve(root, record):
    """实际跑 bundle 的解析 step：从记录读 kernel_artifact，旧记录默认正式档。"""
    root.mkdir(parents=True)
    image = root / "build/in/image"
    image.mkdir(parents=True)
    (image / "source-runs.json").write_text(json.dumps(record))
    (root / "scripts").symlink_to(ROOT / "scripts")
    binary = root / "bin"
    binary.mkdir()
    executable(binary / "gh", 'exec python3 "$PIANO_GH_STUB" "$@"\n')
    (root / "gh_stub.py").write_text(GH_STUB)
    (root / "workflows.json").write_text(json.dumps(["build-kernel.yml"]))
    (root / "artifacts.json").write_text(json.dumps(
        ["kernel-release-7.2.9", "kernel-experimental-gunyah"]))
    (root / "runs.json").write_text(json.dumps({"7001": "build-kernel.yml"}))
    output = root / "github-output"
    output.write_text("")
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
           "PIANO_GH_STUB": str(root / "gh_stub.py"),
           "PIANO_GH_WORKFLOWS": str(root / "workflows.json"),
           "PIANO_GH_ARTIFACTS": str(root / "artifacts.json"),
           "PIANO_GH_RUNS": str(root / "runs.json"),
           "GITHUB_REPOSITORY": "owner/repo", "GITHUB_OUTPUT": str(output)}
    result = subprocess.run(["/bin/bash", "-euo", "pipefail", "-c",
                             step_body(".github/workflows/build-bundle.yml", "Resolve the exact integrated kernel")],
                            cwd=root, env=env, capture_output=True, text=True)
    return result, output.read_text()


def check_bundle_kernel_resolution(root):
    root.mkdir()
    # 显式实验记录：新 bundle 必须拒绝。
    result, output = run_bundle_resolve(root / "exp",
                                        {"kernel_run": "7001", "kernel_artifact": "kernel-experimental-gunyah"})
    assert result.returncode != 0, "obsolete experimental bundle accepted"
    # 旧记录没有 kernel_artifact：默认正式档，保持向后兼容。
    result, output = run_bundle_resolve(root / "legacy", {"kernel_run": "7001"})
    assert result.returncode == 0, result.stderr
    assert "kernel_artifact=kernel-release-7.2.9" in output, output
    # 记录被改写成无关 artifact：必须拒绝，不能下载任意名字。
    result, _ = run_bundle_resolve(root / "bad", {"kernel_run": "7001", "kernel_artifact": "kernel-foo"})
    assert result.returncode != 0, "bundle accepted an out-of-allowlist kernel_artifact"


def check_bundle_image_binding(root):
    """The image manifest must identify the exact build-image run being bundled."""
    root.mkdir(parents=True)
    image = root / "build/in/image"
    image.mkdir(parents=True)
    record = {
        "kernel_run": "7001",
        "rootfs_run": "7002",
        "runtime_run": "7003",
        "mesa_run": "7004",
        "ffmpeg_run": "7005",
        "image_run": "7100",
        "image_source_commit": "1" * 40,
    }
    (image / "source-runs.json").write_text(json.dumps(record))
    output = root / "github-output"
    output.write_text("")
    env = {**os.environ, "ROOT_RUN": "7100", "GITHUB_OUTPUT": str(output)}
    result = subprocess.run(
        ["/bin/bash", "-euo", "pipefail", "-c",
         step_body(".github/workflows/build-bundle.yml",
                   "Require one integrated image source record")],
        cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    env["ROOT_RUN"] = "7199"
    result = subprocess.run(
        ["/bin/bash", "-euo", "pipefail", "-c",
         step_body(".github/workflows/build-bundle.yml",
                   "Require one integrated image source record")],
        cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode != 0, "bundle accepted an image record from another run"


def check_kernel_unpack(root):
    """实际跑 unpack step：相邻 SDK 归档不能被 glob 解进 build/kernel。"""
    root.mkdir()
    work = root / "work"
    (work / "build/in/kernel").mkdir(parents=True)
    (work / "build/in/base").mkdir(parents=True)
    (work / "build/in/runtime").mkdir(parents=True)
    (work / "build/in/mesa").mkdir(parents=True)
    (work / "build/in/ffmpeg").mkdir(parents=True)
    make_tar(work / "build/in/kernel/kernel-release-7.2.9.tar.zst", "KERNEL-MARKER", "kernel")
    make_tar(work / "build/in/kernel/kernel-sdk-7.2.9.tar.zst", "SDK-MARKER", "sdk")
    make_tar(work / "build/in/base/rootfs.tar.zst", "rootfs.txt", "root")
    make_tar(work / "build/in/runtime/runtime.tar.zst", "runtime.txt", "rt")
    make_tar(work / "build/in/mesa/mesa.tar.zst", "mesa.txt", "mesa")
    make_tar(work / "build/in/ffmpeg/ffmpeg.tar.zst", "ffmpeg.txt", "ff")
    binary = root / "bin"
    binary.mkdir()
    executable(binary / "sudo", 'exec "$@"\n')
    # 只有 checkout-sources.py 这一步被桩掉；其余 python 调用走真实解释器。
    executable(binary / "python3",
               'for arg in "$@"; do case "$arg" in *checkout-sources.py) exit 0 ;; esac; done\n'
               'exec "$PIANO_REAL_PYTHON" "$@"\n')
    env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
           "PIANO_REAL_PYTHON": sys.executable,
           "KERNEL_ARTIFACT": "kernel-release-7.2.9"}
    result = subprocess.run(["/bin/bash", "-euo", "pipefail", "-c",
                             step_body(".github/workflows/build-image.yml", "Unpack into separate component directories")],
                            cwd=work, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (work / "build/kernel/KERNEL-MARKER").is_file(), "the kernel archive was not extracted"
    assert not (work / "build/kernel/SDK-MARKER").exists(), \
        "the SDK tar leaked into build/kernel (glob instead of exact archive)"


def check_clean_rootfs_release_entry():
    """build-rootfs must produce the audited tree, never the old base only."""
    rootfs = (ROOT / ".github/workflows/build-rootfs.yml").read_text()
    assert "scripts/build-clean-rootfs.py" in rootfs, (
        "build-rootfs.yml still invokes the unaudited base-tree generator")
    assert "scripts/build-fedora-rootfs.py" not in rootfs, (
        "the release workflow must not use the legacy base-tree backend")
    assert "--rpms-dir build/local-rpms" in rootfs
    assert "piano-rpms" in rootfs
    assert "piano_rpms_run_id" in rootfs
    assert "clean-manifest.json" in rootfs
    assert 'record["status"] == "BUILT_AND_AUDITED"' in rootfs
    assert "missing_session_packages" in rootfs
    assert "usr/bin/dbus-run-session" in rootfs
    assert "usr/lib64/security/pam_systemd.so" in rootfs
    assert 'os.environ["RUNTIME_RUN"]' in rootfs
    assert 'os.environ["MESA_RUN"]' in rootfs
    assert 'os.environ["FFMPEG_RUN"]' in rootfs

    image = (ROOT / ".github/workflows/build-image.yml").read_text()
    assert "Reject a base rootfs without the GNOME session bus" in image, (
        "build-image must reject any rootfs that did not pass the clean audit")
    assert 'record["status"] == "BUILT_AND_AUDITED"' in image
    assert 'for package in ("dbus-daemon", "systemd-pam")' in image
    assert 'record["audit"]["selinux_labels"] > 0' in image
    assert 'assert mode == "enforcing"' in image
    assert 'assert not (root / ".autorelabel").exists()' in image
    assert "kernel-piano owns the module tree" in image
    assert "the clean rootfs must carry exactly the kernel-piano module" in image
    assert '"kernel-piano"' in image
    assert '["rpm", "--root", str(root), "-qf",' in image
    assert 'local.get("kernel_run") == os.environ["KERNEL_RUN"]' in image
    assert 'record["audit"]["kernel_release"] == kernel["kernel_release"]' in image
    assert '"kernel_run": kernel_run' in rootfs


def check_bundle_asset_names():
    """deploy-bundle、安装器与文档必须共用一套发布文件名。"""
    bundle = (ROOT / ".github/workflows/build-bundle.yml").read_text()
    verifier = (ROOT / "scripts/verify-bundle.py").read_text()
    installer = (ROOT / "scripts/install-f2fs.py").read_text()
    updater = (ROOT / "scripts/update-f2fs.py").read_text()
    for name in ("sunuefi_esp.img", "boot_sunuefi.img"):
        assert f"bundle/{name}" in bundle, f"build-bundle does not emit {name}"
        assert name in verifier, f"verify-bundle does not require {name}"
        assert name in installer, f"first-install installer does not read {name}"
        assert name in (ROOT / "docs/artifacts.md").read_text(), f"artifact docs omit {name}"
        assert name in (ROOT / "docs/installation.md").read_text(), f"install docs omit {name}"
    assert "test ! -e bundle/esp.img" in bundle
    assert "test ! -e bundle/boot.img" in bundle
    assert 'bundle / "boot_sunuefi.img"' in updater
    assert 'bundle / "sunuefi_esp.img"' in updater
    assert 'bundle / "esp.img"' not in installer
    assert 'bundle / "boot.img"' not in updater


def check_uefi_sealer(root):
    sealer = module("uefi_sealer", ROOT / "scripts/seal-uefi-source.py")
    image = root / "uefi.img"
    root.mkdir()
    image.write_bytes(b"sealed UEFI product fixture")
    expected = json.loads((ROOT / "sources.lock.json").read_text())["sources"]["sunuefi"]["commit"]
    digest = hashlib.sha256(image.read_bytes()).hexdigest()
    good_log = "uefi\tClone\t2026-10-11T12:00:00Z sunuefi at " + expected + '\n{"sha256":"' + digest + '"}\n'

    def api(path, tamper=False):
        if "/actions/runs/" in path:
            return {"head_sha": "1" * 40}
        name = path.split("/contents/", 1)[1].split("?ref=", 1)[0]
        data = b"foreign patches" if tamper else (ROOT / name).read_bytes()
        return {"encoding": "base64", "content": base64.b64encode(data).decode()}

    def invoke(log=good_log, tamper=False):
        with patch.object(sealer.runs, "resolve", return_value="12345"), \
             patch.object(sealer.runs, "api", side_effect=lambda p: api(p, tamper)), \
             patch.object(sealer.subprocess, "check_output", return_value=log):
            return sealer.seal("owner/repo", "12345", image)

    good = invoke()
    assert good["sunuefi_commit"] == expected and good["product"]["sha256"] == digest
    for log, tamper in ((good_log.replace(expected, "0" * 40), False),
                        (good_log.replace(digest, "0" * 64), False), (good_log, True)):
        try:
            invoke(log, tamper)
        except ValueError:
            pass
        else:
            raise AssertionError("UEFI sealer accepted an unrelated checkout, image or patch producer")


def executable(path, text):
    path.write_text("#!/bin/bash\nset -euo pipefail\n" + text)
    path.chmod(0o755)


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        check_mesa(root / "mesa")
        check_bundle(root / "bundle")
        check_uefi_sealer(root / "uefi-source")
        check_image_kernel_tiers(root / "tiers")
        check_bundle_kernel_resolution(root / "bundle-kernel")
        check_bundle_image_binding(root / "bundle-image-binding")
        check_kernel_unpack(root / "unpack")
        check_clean_rootfs_release_entry()
        check_bundle_asset_names()
    print("OK: audited rootfs entry, A830v1 device tables and sealed bundle identity checks")


if __name__ == "__main__":
    main()
