#!/usr/bin/env python3
"""COPR 发布链的离线测试。

覆盖：错误 run、过期/缺失 artifact、manifest 哈希不符、缺 RPM、缺 SRPM、
错误的 kernel run 绑定、token 不泄露、仓库文件契约。所有外部调用都被替换，
不会访问网络。
"""

import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "scripts" / "publish-copr.py"
WORKFLOW = ROOT / ".github" / "workflows" / "publish-copr.yml"
REPO_FILE = (ROOT / "packages" / "piano-fedora-configs" / "etc" / "yum.repos.d"
             / "piano-fedora-copr.repo")
SPEC = ROOT / "packages" / "piano-fedora-configs" / "piano-fedora-configs.spec"
CONFIGS = ROOT / "packages" / "piano-fedora-configs"
DEVICE_VERIFIER = ROOT / "scripts" / "verify-copr-device.py"

PACKAGES = ("piano-fedora-configs", "alsa-xiaomi-piano", "xiaomi-piano-firmware",
            "piano-camera", "xiaomi-piano-thp", "sm8750-video-accel",
            "uperf-linux", "libssc", "iio-sensor-proxy-ssc", "kernel-piano")


def load_module():
    spec = importlib.util.spec_from_file_location("publish_copr", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_matrix(directory, *, status="PIANO_RPMS_BUILT", kernel_run="38777633742",
                drop_rpm=None, drop_srpm=None, corrupt=None):
    directory = Path(directory)
    (directory / "srpms").mkdir(parents=True, exist_ok=True)
    files, srpms = {}, {}
    for name in PACKAGES:
        payload = f"{name} binary".encode()
        row = {"bytes": len(payload), "sha256": __import__("hashlib").sha256(payload).hexdigest()}
        files[f"{name}-0.1.0-1.fc44.aarch64.rpm"] = row
        if name != drop_rpm:
            (directory / f"{name}-0.1.0-1.fc44.aarch64.rpm").write_bytes(payload)
    for name in PACKAGES:
        payload = f"{name} source".encode()
        row = {"bytes": len(payload), "sha256": __import__("hashlib").sha256(payload).hexdigest()}
        srpms[f"{name}-0.1.0-1.fc44.src.rpm"] = row
        if name != drop_srpm:
            (directory / "srpms" / f"{name}-0.1.0-1.fc44.src.rpm").write_bytes(payload)
    manifest = {
        "status": status,
        "releasever": "44",
        "kernel_run": kernel_run,
        "kernel_release": "7.2.9-piano-gnome-g8c0e589d8829",
        "sunuefi_commit": "065b192b863b6f99e12ba65d3cab27c0ddd6dca4",
        "files": files,
        "srpms": srpms,
    }
    (directory / "piano-rpms-manifest.json").write_text(json.dumps(manifest))
    if corrupt:
        (directory / corrupt).write_bytes(b"tampered")
    return directory


def expect_error(module, call, needle):
    try:
        call()
    except module.PublishError as error:
        if needle not in str(error):
            raise AssertionError(f"wrong error: {error!r}, expected {needle!r}")
        return
    raise AssertionError(f"expected PublishError containing {needle!r}")


def test_valid_matrix(module, tmp):
    directory = make_matrix(tmp / "good")
    manifest = module.load_manifest(directory)
    names, kernel_run = module.verify_artifacts(directory, manifest, {"id": 1})
    assert set(PACKAGES) <= names, names
    assert kernel_run == "38777633742"


def test_wrong_status(module, tmp):
    directory = make_matrix(tmp / "status", status="PIANO_RPMS_FAILED")
    expect_error(module, lambda: module.load_manifest(directory), "status")


def test_tampered_rpm(module, tmp):
    directory = make_matrix(tmp / "tamper", corrupt="libssc-0.1.0-1.fc44.aarch64.rpm")
    manifest = module.load_manifest(directory)
    expect_error(module, lambda: module.verify_artifacts(directory, manifest, {"id": 1}), "changed")


def test_missing_rpm(module, tmp):
    directory = make_matrix(tmp / "missing", drop_rpm="uperf-linux")
    manifest = module.load_manifest(directory)
    # The manifest still lists it, so the missing file is caught by name before
    # the package-set check reports it.
    expect_error(module, lambda: module.verify_artifacts(directory, manifest, {"id": 1}), "uperf-linux")


def test_missing_srpm(module, tmp):
    directory = make_matrix(tmp / "nosrpm", drop_srpm="libssc")
    manifest = module.load_manifest(directory)
    expect_error(module, lambda: module.verify_artifacts(directory, manifest, {"id": 1}), "libssc")


def test_bad_kernel_run(module, tmp):
    directory = make_matrix(tmp / "kernel", kernel_run="")
    manifest = module.load_manifest(directory)
    expect_error(module, lambda: module.verify_artifacts(directory, manifest, {"id": 1}), "kernel run")


def test_run_id_validation(module, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "FFT-infra/piano-fedora")
    expect_error(module, lambda: module.verify_run("not-a-run"), "invalid run id")

    def fake_check_output(args, text=True, **kwargs):
        if args[-1].endswith("host-check.yml"):
            return json.dumps({"id": 42})
        return json.dumps({"workflow_id": 7, "status": "completed", "conclusion": "success"})

    monkeypatch.setattr(module.subprocess, "check_output", fake_check_output)
    expect_error(module, lambda: module.verify_run("123"), "not a host-check run")


def test_failed_run_rejected(module, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "FFT-infra/piano-fedora")

    def fake_check_output(args, text=True, **kwargs):
        if args[-1].endswith("host-check.yml"):
            return json.dumps({"id": 42})
        return json.dumps({"workflow_id": 42, "status": "completed", "conclusion": "failure"})

    monkeypatch.setattr(module.subprocess, "check_output", fake_check_output)
    expect_error(module, lambda: module.verify_run("123"), "completed/failure")


def test_copr_config_is_private_and_removed(module, tmp, monkeypatch):
    monkeypatch.setenv("COPR_LOGIN", "api-login-hash")
    monkeypatch.setenv("COPR_TOKEN", "secret-token-value")
    path = tmp / "copr.conf"
    module.write_copr_config(path, "xinyang26")
    assert path.stat().st_mode & 0o777 == 0o600
    text = path.read_text()
    assert "login = api-login-hash" in text
    assert "username = xinyang26" in text
    assert "secret-token-value" in text
    path.unlink()
    assert not path.exists()


def test_missing_credentials(module, monkeypatch):
    monkeypatch.delenv("COPR_LOGIN", raising=False)
    monkeypatch.delenv("COPR_TOKEN", raising=False)
    expect_error(module, lambda: module.write_copr_config("/tmp/never-written.conf", "xinyang26"),
                 "COPR_LOGIN and COPR_TOKEN")


def test_project_create_is_idempotent(module, tmp):
    calls = []

    class Result:
        def __init__(self, code=0, out=""):
            self.returncode, self.stdout, self.stderr = code, out, ""

    # copr-cli 2.4 prints each project through print_project_info(), so the
    # name lives on a `Name: <project>` line. A bare-name match never hits.
    def run_cli(binary, config, args, check=True):
        calls.append(list(args))
        if args[0] == "list":
            return Result(0, "Name: piano-fedora\n  Description: Fedora 44 aarch64\n\n")
        return Result(0, "")

    original = module.run_cli
    module.run_cli = run_cli
    try:
        state = module.ensure_project("copr-cli", tmp / "conf", "xinyang26/piano-fedora")
    finally:
        module.run_cli = original
    assert state == "exists"
    assert not any(c[0] == "create" for c in calls), calls


def test_project_listing_formats(module):
    listed = "Name: piano-fedora\n  Description: demo\n"
    assert module.project_listed(listed, "piano-fedora")
    # A different project must not count as present.
    assert not module.project_listed(listed, "piano-extras")
    assert not module.project_listed("", "piano-fedora")


def cli_result(stdout="", code=0, stderr=""):
    class Result:
        pass

    result = Result()
    result.returncode, result.stdout, result.stderr = code, stdout, stderr
    return result


def test_submit_parses_every_created_build(module, tmp):
    srpm_dir = Path(tmp) / "srpms"
    srpm_dir.mkdir(parents=True, exist_ok=True)
    (srpm_dir / "libssc-0.1.0-1.fc44.src.rpm").write_bytes(b"srpm")
    # copr-cli 2.4 always prints `Created builds: <id>`; the old code only
    # captured the `Build was created:` alternative and ended up with no ids.
    created = ("Build was added to piano-fedora:\n"
               "  https://copr.fedorainfracloud.org/coprs/build/1234/\n"
               "Created builds: 1234\n")
    assert module.parse_build_ids(created) == ["1234"]
    # A batch-shaped line must keep every id.
    assert module.parse_build_ids("Created builds: 11 22\n") == ["11", "22"]

    original = module.run_cli
    module.run_cli = lambda *a, **k: cli_result(stdout=created)
    try:
        _, build_id = module.submit("copr-cli", "conf", "xinyang26/piano-fedora",
                                    srpm_dir, "libssc")
    finally:
        module.run_cli = original
    assert build_id == "1234"

    # Unparseable output must fail loudly instead of yielding an empty id set
    # that later reads as "nothing pending, publish succeeded".
    module.run_cli = lambda *a, **k: cli_result(stdout="something else\n")
    try:
        expect_error(module,
                     lambda: module.submit("copr-cli", "conf", "p", srpm_dir, "libssc"),
                     "expected one build id")
    finally:
        module.run_cli = original


def test_build_failure_is_not_success(module):
    class Result:
        returncode, stdout, stderr = 0, "succeeded\n", ""

    original = module.run_cli
    module.run_cli = lambda *a, **k: Result()
    try:
        module.wait_for_builds("copr-cli", "conf", "p", ["1"], timeout=1, interval=0)
    finally:
        module.run_cli = original

    class Failed(Result):
        stdout = "failed\n"

    module.run_cli = lambda *a, **k: Failed()
    try:
        expect_error(module,
                     lambda: module.wait_for_builds("copr-cli", "conf", "p", ["1"], timeout=1, interval=0),
                     "ended failed")
    finally:
        module.run_cli = original


def test_repo_file_contract():
    text = REPO_FILE.read_text()
    assert "gpgcheck=1" in text
    assert "repo_gpgcheck=0" in text
    # 内核在线升级已放开：仓库不能再排除 kernel-piano，否则设备永远拿不到内核。
    assert "exclude=kernel-piano" not in text, "the COPR repo must not exclude kernel-piano"
    assert "download.copr.fedorainfracloud.org/results/xinyang26/piano-fedora/" in text
    assert "gpgkey=" in text and "pubkey.gpg" in text
    # The file must actually be packaged, otherwise the repo never reaches a
    # system built from these RPMs. It must not be %config(noreplace): an
    # installed device keeps the old file with the exclude rule, and the
    # upgrade never sees kernel-piano.
    spec = SPEC.read_text()
    entry = next(line for line in spec.splitlines()
                 if "piano-fedora-copr.repo" in line and "%{_sysconfdir}" in line)
    assert "%config(noreplace)" not in entry, (
        "the shipped .repo must not be noreplace; an old copy keeps exclude=kernel-piano")
    assert "%config " in entry
    assert (CONFIGS / "etc" / "yum.repos.d" / "piano-fedora-copr.repo").is_file()


def test_workflow_contract():
    text = WORKFLOW.read_text()
    assert "piano-rpms" in text
    assert "scripts/publish-copr.py" in text
    assert "--execute" in text
    # The dry-run path must not receive credentials, and the real path must
    # fail when they are absent.
    assert "COPR_LOGIN is not configured" in text
    assert "COPR_TOKEN is not configured" in text
    # No device writes anywhere.
    for forbidden in ("fastboot flash", "fastboot erase", "adb push", "adb shell"):
        assert forbidden not in text, forbidden


def test_script_has_no_hardcoded_token():
    text = MODULE.read_text()
    for marker in ("COPR_TOKEN = ", "token = '", 'token = "'):
        if marker in text:
            raise AssertionError(f"credential-looking literal in script: {marker}")


def test_device_verifier_contract():
    text = DEVICE_VERIFIER.read_text()
    # The device check must not install or upgrade anything unless asked.
    assert "--install-test" in text
    assert "--assumeno" in text, "upgrade dry-run must never accept the transaction"
    assert "kernel-piano" in text
    # It reads the same repo id the shipped .repo defines.
    repo = REPO_FILE.read_text()
    assert "xinyang26/piano-fedora" in text and "xinyang26/piano-fedora" in repo
    # signature policy must be asserted, not assumed.
    for token in ("gpgcheck=1", "repo_gpgcheck=0"):
        assert token in repo
    # The verifier now asserts the opposite of the old contract: kernel-piano
    # must be visible and the upgrade plan must carry it.
    assert "kernel_visibility" in text
    assert "does not carry kernel-piano" in text
    assert "still excludes kernel-piano" in text


def test_device_verifier_fetches_compressed_repodata():
    """repodata 的 primary 是 .xml.gz，按文本解码会在 0x8b 上抛异常。

    2026-10-21 首次在设备上跑时才发现：脚本一直只做过文本断言，没人真跑过。
    """
    text = DEVICE_VERIFIER.read_text()
    assert "import gzip" in text, "fetch() 必须能解 gzip 的 repodata"
    assert "gzip.decompress" in text
    # subprocess 必须以二进制取回 curl 输出，否则解码发生在解压之前。
    assert "binary=True" in text, "run() 需要二进制模式，text=True 会在解压前解码"
    assert "text=not binary" in text


def test_device_verifier_repoquery_is_bounded():
    """临时仓库必须自带 baseurl，且不能被设备上其它仓库拖住。

    2026-10-22 在设备上跑时，`dnf repoquery --repofrompath=coprcheck,<url>`
    只给了 repo id 和 URL 的一半，dnf5 报 "Empty mirrorlist and no basepath
    specified"，却仍然先把所有 enabled 仓库解析一遍；rpmfusion 一超时，整条
    验收就挂十分钟没有输出。两个修法缺一不可：显式 baseurl 让这个临时仓库
    可用，--disablerepo=* 让它不依赖别的仓库，超时兜住任何仍然卡住的镜像。
    """
    text = DEVICE_VERIFIER.read_text()
    assert "--setopt=coprcheck.baseurl=" in text, (
        "repoquery 必须给临时仓库显式 baseurl，否则 dnf5 认为仓库是坏的")
    assert '"--disablerepo=*"' in text, (
        "repoquery 不该解析设备上其它 enabled 仓库")
    assert "timeout=180" in text, "repoquery 必须有超时上限"
    assert "TimeoutExpired" in text, "run() 必须把超时转成可报告的错误"


def test_device_verifier_pubkey_is_at_repo_root():
    """公钥在 results/<owner>/<project>/pubkey.gpg，不在 fedora-<rel>-<arch>/ 下。

    baseurl 以 `fedora-<rel>-<arch>/` 结尾，直接拼 `pubkey.gpg` 会 404。
    """
    text = DEVICE_VERIFIER.read_text()
    assert 'baseurl + "../pubkey.gpg"' in text, (
        "公钥必须从仓库根目录取；baseurl 已经带 arch 子目录")
    # 随包的 .repo 里 gpgkey 指向根目录，两处必须一致。
    repo = REPO_FILE.read_text()
    assert "/results/xinyang26/piano-fedora/pubkey.gpg" in repo
    assert "fedora-$releasever-$basearch/pubkey.gpg" not in repo


def test_device_verifier_passes_setopt_as_separate_argv():
    """每个 --setopt 必须是独立 argv 项，拼成一个字符串 dnf 只看到第一个。"""
    text = DEVICE_VERIFIER.read_text()
    body = text.split("def repoquery_kernel")[1].split("\ndef ")[0]
    assert '" ".join([' not in body, "repoquery 的选项不能拼成单个字符串再传给 dnf"
    assert "--setopt=coprcheck.gpgcheck=1" in body
    assert "--setopt=coprcheck.repo_gpgcheck=0" in body


def main():
    module = load_module()
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        test_valid_matrix(module, tmp)
        test_wrong_status(module, tmp)
        test_tampered_rpm(module, tmp)
        test_missing_rpm(module, tmp)
        test_missing_srpm(module, tmp)
        test_bad_kernel_run(module, tmp)
        test_copr_config_is_private_and_removed(module, tmp, _MonkeyPatch())
        test_project_create_is_idempotent(module, tmp)
        test_submit_parses_every_created_build(module, tmp)
    test_project_listing_formats(module)
    test_run_id_validation(module, _MonkeyPatch())
    test_failed_run_rejected(module, _MonkeyPatch())
    test_missing_credentials(module, _MonkeyPatch())
    test_build_failure_is_not_success(module)
    test_repo_file_contract()
    test_workflow_contract()
    test_device_verifier_contract()
    test_device_verifier_fetches_compressed_repodata()
    test_device_verifier_pubkey_is_at_repo_root()
    test_device_verifier_passes_setopt_as_separate_argv()
    test_script_has_no_hardcoded_token()
    print("OK: COPR publish validates the exact matrix and refuses unsafe publishes")


class _MonkeyPatch:
    """Minimal pytest-style monkeypatch for a stdlib-only test."""

    def __init__(self):
        self._saved = []

    def setenv(self, name, value):
        import os
        self._saved.append((name, os.environ.get(name)))
        os.environ[name] = value

    def delenv(self, name, raising=False):
        import os
        self._saved.append((name, os.environ.get(name)))
        os.environ.pop(name, None)

    def setattr(self, target, name, value):
        self._saved.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def undo(self):
        import os
        for entry in reversed(self._saved):
            if len(entry) == 2 and isinstance(entry[0], str):
                name, value = entry
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            else:
                target, name, value = entry
                setattr(target, name, value)
        self._saved.clear()


if __name__ == "__main__":
    main()
