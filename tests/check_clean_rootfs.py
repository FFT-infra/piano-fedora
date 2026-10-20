#!/usr/bin/env python3
"""校验基于纯净 DNF 的 RootFS 装配脚本与计划契约。"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "build-clean-rootfs.py"

EXPECTED_PIANO_PACKAGES = {
    "piano-fedora-configs",
    "alsa-xiaomi-piano",
    "piano-camera",
    "xiaomi-piano-thp",
    "sm8750-video-accel",
    "uperf-linux",
    "libssc",
    "iio-sensor-proxy-ssc",
    "xiaomi-piano-firmware",
}

# The device kernel ships from the same local repo. It is listed separately
# because a "clean" rootfs without it boots with no modules at all.
EXPECTED_KERNEL_PACKAGE = "kernel-piano"


def run(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=ROOT
    )


def check_runtime_packages_installed():
    """设备运行时包必须在安装集合里，不能是"有就装"。

    run 39272565632 之前它们被列成 LEGACY_OPTIONAL，纯净 rootfs 因此没有
    librevm、crosvm、Iris VA-API 和 piano-runtime，build-image 最后一步才
    报缺包。这里锁住安装集合。
    """
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location(
        "clean_rootfs", ROOT / "scripts/build-clean-rootfs.py")
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    planned = set(module.PIANO_PACKAGES) | set(module.RUNTIME_PACKAGES) | {module.KERNEL_PACKAGE}
    for name in ("piano-runtime", "librevm", "piano-crosvm",
                 "kernel-piano", "piano-fedora-configs"):
        assert name in planned, f"{name} is not part of the install set"
    assert "piano-runtime" not in module.LEGACY_OPTIONAL_PACKAGES
    # Superseded producers must not come back: libva-v4l2-iris built the same
    # driver path as sm8750-video-accel with different bytes, and
    # piano-session-fixes duplicated piano-fedora-configs.
    for retired in ("libva-v4l2-iris", "piano-session-fixes"):
        assert retired not in planned, f"{retired} must not be installed"
        assert retired in module.LEGACY_OPTIONAL_PACKAGES, f"{retired} must stay documented as retired"


def check_audit_field_names():
    """workflow 读的审计字段必须在脚本的输出里真实存在。

    run 39258030572 在最后一步 KeyError: 'missing_files'：脚本写的是
    missing_key_files。字段名不对会在整条装配跑完之后才失败，代价很高。
    """
    import re as _re
    script = (ROOT / "scripts/build-clean-rootfs.py").read_text()
    workflow = (ROOT / ".github/workflows/build-rootfs.yml").read_text()
    keys = set(_re.findall(r'"(missing_[a-z_]+)"\s*:', script))
    used = set(_re.findall(r'audit\["(missing_[a-z_]+)"\]', workflow))
    unknown = sorted(used - keys)
    assert not unknown, f"workflow reads audit fields the script never writes: {unknown}"


def main():
    check_audit_field_names()
    check_runtime_packages_installed()
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    # 1. 基础 plan 测试（使用包含模拟 rpm 的 fixture 仓库，确保在无预编译产物的 CI 环境中也能稳定通过）
    with tempfile.TemporaryDirectory() as fixture_dir:
        fixture_path = Path(fixture_dir)
        (fixture_path / "repodata").mkdir(parents=True, exist_ok=True)
        (fixture_path / "repodata" / "repomd.xml").write_text("<repomd/>\n", encoding="utf-8")
        for pkg in EXPECTED_PIANO_PACKAGES | {EXPECTED_KERNEL_PACKAGE}:
            (fixture_path / f"{pkg}-0.1.0-1.fc44.noarch.rpm").touch()

        res = run("--plan", "--releasever", "44", "--rpms-dir", str(fixture_path))
        check(res.returncode == 0, f"plan with fixture should succeed, got {res.returncode}: {res.stderr}")
        if res.returncode == 0:
            data = json.loads(res.stdout)
            check(data.get("status") == "PLAN_READY", "plan status must be PLAN_READY")
            check(data.get("distro") == "fedora", "distro must be fedora")
            check(data.get("releasever") == "44", "releasever must be 44")
            check(data.get("desktop") == "gnome", "desktop must be gnome")
            check(EXPECTED_PIANO_PACKAGES.issubset(set(data.get("piano_packages", []))),
                  "all 9 expected piano RPM packages must be scheduled in plan")
            check(EXPECTED_KERNEL_PACKAGE in data.get("all_packages", []),
                  "kernel-piano must be scheduled in the clean assembly; without it "
                  "the image boots with an empty /usr/lib/modules")
            repo_st = data.get("repo_status", {})
            check(repo_st.get("valid") is True, f"fixture repository must be valid: {repo_st}")
            check(len(repo_st.get("missing", [])) == 0, f"no missing packages allowed: {repo_st.get('missing')}")
            check(EXPECTED_KERNEL_PACKAGE in repo_st.get("packages", {}),
                  "kernel-piano must be resolved from the local RPM repo")

    # 1.1 默认 plan 测试（不传 --rpms-dir，检查返回值与状态结构）
    res_def = run("--plan", "--releasever", "44")
    check(res_def.returncode == 0, f"default plan should succeed, got {res_def.returncode}: {res_def.stderr}")
    if res_def.returncode == 0:
        data_def = json.loads(res_def.stdout)
        check(data_def.get("status") == "PLAN_READY", "default plan status must be PLAN_READY")
        if (ROOT / "out" / "rpms" / "repodata" / "repomd.xml").is_file():
            repo_st_def = data_def.get("repo_status", {})
            check(repo_st_def.get("valid") is True, f"local out/rpms must be valid: {repo_st_def}")

    # 2. 拒绝非法版本号
    check(run("--plan", "--releasever", "rawhide").returncode == 2, "non-numeric releasever must exit 2")

    # 3. 拒绝未知桌面环境
    check(run("--plan", "--desktop", "dde").returncode == 2, "unknown desktop must exit 2")

    # 4. 仓库路径缺失检测
    with tempfile.TemporaryDirectory() as empty_dir:
        res_missing = run("--plan", "--rpms-dir", empty_dir)
        check(res_missing.returncode == 0, "plan with empty repo should exit 0 but mark valid=False")
        if res_missing.returncode == 0:
            data_missing = json.loads(res_missing.stdout)
            check(data_missing.get("repo_status", {}).get("valid") is False,
                  "empty rpms-dir must be reported as invalid")

    # 5. 守护进程依赖校验：piano-ram-hardware-prepare 及其 DMA 模块必须内置于 piano-fedora-configs
    configs_pkg = ROOT / "packages" / "piano-fedora-configs"
    prepare_bin = configs_pkg / "usr" / "lib" / "piano" / "piano-ram-hardware-prepare"
    alias_link = configs_pkg / "usr" / "lib" / "piano" / "piano-disk-hardware-prepare"
    dma_routes = configs_pkg / "usr" / "lib" / "piano" / "piano_dma_routes.py"
    dma_ctx = configs_pkg / "usr" / "lib" / "piano" / "piano_dma_contexts.py"
    spec_file = configs_pkg / "piano-fedora-configs.spec"

    check(prepare_bin.is_file() and os.access(prepare_bin, os.X_OK),
          "piano-ram-hardware-prepare must exist and be executable in piano-fedora-configs")
    check(alias_link.is_symlink() and alias_link.readlink() == Path("piano-ram-hardware-prepare"),
          "piano-disk-hardware-prepare symlink must exist in piano-fedora-configs")
    check(dma_routes.is_file(), "piano_dma_routes.py must exist in piano-fedora-configs")
    check(dma_ctx.is_file(), "piano_dma_contexts.py must exist in piano-fedora-configs")
    if spec_file.is_file():
        spec_text = spec_file.read_text(encoding="utf-8")
        check("piano-ram-hardware-prepare" in spec_text,
              "piano-fedora-configs.spec must package piano-ram-hardware-prepare")
        check("piano-disk-hardware-prepare" in spec_text,
              "piano-fedora-configs.spec must package piano-disk-hardware-prepare")
        # Fedora merged-usr makes /usr/local/sbin a symlink to bin, so shipping
        # both spellings of the same file makes rpm 6 abort the transaction with
        # "file ... conflicts between attempted installs of <pkg> and <pkg>".
        # Only the canonical /usr/local/bin path may appear in %files.
        check("/usr/local/bin/piano-debug-stage" in spec_text,
              "piano-fedora-configs.spec must package piano-debug-stage at /usr/local/bin")
        check("\n/usr/local/sbin/piano-debug-stage\n" not in spec_text,
              "piano-fedora-configs.spec must not list /usr/local/sbin/piano-debug-stage "
              "(merged-usr alias of /usr/local/bin, duplicates the same inode)")
        check("\n/usr/local/sbin/piano-debug-bootstrap\n" not in spec_text,
              "piano-fedora-configs.spec must not list /usr/local/sbin/piano-debug-bootstrap "
              "(merged-usr alias of /usr/local/bin, duplicates the same inode)")

    # 6. 装配后必须清空 /dev、/proc、/sys、/run。DNF/chroot 会在这些目录
    #    留下普通文件（例如 /dev/null）和宿主运行状态（/run/dnf、/run/sshd）。
    #    package_release.py 的 scan_root() 会拒绝非空运行时树，F2FS 也不该带它们。
    source = TOOL.read_text(encoding="utf-8")
    check("Clearing transient /dev, /proc, /sys and /run entries" in source,
          "build-clean-rootfs.py must clear transient runtime directories before imaging")
    for runtime_dir in ("dev", "proc", "sys", "run"):
        check(f'"{runtime_dir}"' in source,
              f"build-clean-rootfs.py must clear /{runtime_dir}")

    # 7. 装配后必须规范化启动器软链接、修剪 nodocs 悬空手册与宿主内核构建目录链接、初始化 machine-id。
    #    避免 SunUEFI 的 package_release.py 静态扫描时判定符号链接逃逸或绝对宿主悬空。
    check("normalize_launcher_links" in source,
          "build-clean-rootfs.py must normalize launcher links")
    check("prune_missing_manpage_links" in source,
          "build-clean-rootfs.py must prune missing manpage links")
    check("prune_kernel_module_links" in source,
          "build-clean-rootfs.py must prune dangling kernel module build links")
    check("sanitize_rootfs_identity" in source,
          "build-clean-rootfs.py must sanitize rootfs machine identity")

    # 9. 本地 piano 仓库必须压过 Fedora 官方仓库。Fedora 44 updates 的
    #    mesa-26.2.4-1.fc44 版本号高于自建的 26.2.3-1.piano1.fc44，没有
    #    priority 时 dnf 选上游包，finalize 再以“不是已验证的 A830v1 构建”
    #    拒绝整张镜像。
    check("piano-local.priority=1" in source,
          "build-clean-rootfs.py must give the piano-local repository a lower "
          "priority number than the distro repositories, otherwise dnf installs "
          "the distro Mesa over the verified piano build")

    # 8. 根分区策略必须在成像前落地。initramfs 在 chroot 之后立刻运行
    #    piano-disk-hardware-prepare，它的 require_ram() 直接读
    #    /etc/piano/root-policy.json；缺这个文件系统就停在救援 shell。
    #    旧流水线 build-image.yml 调了 apply-rootfs-policy.py，纯净装配链
    #    一直没有这一步，2026-10-17 的镜像因此缺文件、无法启动。
    check("apply-rootfs-policy.py" in source,
          "build-clean-rootfs.py must invoke apply-rootfs-policy.py so the image "
          "carries /etc/piano/root-policy.json, fstab and the udev protection rule")
    check("root-policy.json" in source,
          "build-clean-rootfs.py audit must require /etc/piano/root-policy.json")

    # 9. fstab 只能有一处来源。装配脚本曾经自己写一份（compress_algorithm
    #    版本），策略脚本又写一份（data_flush 版本），两者会漂移。
    check("compress_algorithm=zstd" not in source,
          "build-clean-rootfs.py must not write its own fstab; apply-rootfs-policy.py "
          "is the single source of truth")

    # 10. SELinux 标签。DNF 只写文件内容，不写 security.selinux xattr；旧流水线
    #     靠 build-image.yml 里的 setfiles 补，这条链漏了，2026-10-19 的镜像
    #     selinux_labels=0，systemd 在 enforcing 下给 /dev/shm、/dev/pts、
    #     /sys/fs/* 打标签被拒，报 "Failed to mount API filesystems" 后冻结。
    #     标签数此前没有任何检查覆盖，所以漏掉时审计照样全绿。
    relabel = ROOT / "scripts" / "relabel-selinux.py"
    check(relabel.is_file(),
          "scripts/relabel-selinux.py must exist: DNF does not write security.selinux")
    check("relabel-selinux.py" in source,
          "build-clean-rootfs.py must invoke relabel-selinux.py so the image "
          "carries security.selinux labels")
    check("count_selinux_labels" in source and "selinux_labels" in source,
          "build-clean-rootfs.py audit must count security.selinux labels and fail "
          "when file_contexts exists but nothing is labelled")
    check('replace("SELINUX=enforcing", "SELINUX=permissive")' not in source,
          "build-clean-rootfs.py must keep the Fedora SELinux mode; labels are "
          "the build output, not a reason to silently switch to permissive")
    check('(root / ".autorelabel").unlink(missing_ok=True)' in source,
          "build-clean-rootfs.py must remove any pre-existing .autorelabel marker "
          "after applying labels")

    # 11. GNOME 会话总线。Fedora 44 的 GDM Wayland 启动器直接执行
    #     dbus-run-session；该文件来自 dbus-daemon 子包。pam_systemd.so
    #     来自 systemd-pam，负责让 logind 建出 /run/user/<uid>。两者在
    #     install_weak_deps=False 时都不在任何依赖闭包里。真机上漏掉后的
    #     表现是面板已点亮但桌面黑屏，日志为
    #     "Unable to run session message bus"。
    for package in ("dbus-daemon", "systemd-pam"):
        check(package in source,
              f"build-clean-rootfs.py audit must require the {package} subpackage")
    for path in ("usr/bin/dbus-daemon", "usr/bin/dbus-run-session",
                 "usr/lib64/security/pam_systemd.so"):
        check(path in source,
              f"build-clean-rootfs.py audit must require {path}")
    check("missing_session_packages" in source,
          "build-clean-rootfs.py audit must report missing session packages")

    # 12. 属主来自 RPM 事务，不来自 runner 账号。GitHub 的 ARM runner 是
    #     uid 1001；对装配树做递归 chown 会把整棵 rootfs 打包成 1001:1001，
    #     并顺手清掉 sudo/su 的 setuid 位。这样的镜像能开机进 GNOME，但
    #     sudo 报 "must be owned by uid 0 and have the setuid bit set"，
    #     sshd-keygen、ldconfig、auditd、logrotate 全失败，登录也进不了
    #     /home/piano。
    workflow = ROOT / ".github" / "workflows" / "build-rootfs.yml"
    wf_source = workflow.read_text(encoding="utf-8")
    check('chown -R "$(id -u):$(id -g)" "$OUT"' not in wf_source,
          "build-rootfs.yml must never chown -R the assembled rootfs tree; "
          "hand over only the file the Inspect step rewrites")
    check('chown "$(id -u):$(id -g)" "$OUT/clean-manifest.json"' in wf_source,
          "build-rootfs.yml must hand over clean-manifest.json before the "
          "Inspect step rewrites it")
    for needle in ("usr/bin/sudo", "stat.S_ISUID", "etc/shadow"):
        check(needle in wf_source,
              f"build-rootfs.yml must assert rootfs ownership and the setuid "
              f"bit before packaging (missing {needle})")

    # 13. ESP 的挂载上下文。vfat 默认落 dosfs_t，targeted 策略不允许 init_t
    #     读它；piano-first-boot-ssh-key.service 是普通 oneshot，跑在 init_t，
    #     首启拷贝 /boot/efi/piano-ssh-key.pub 时拿到 EACCES，导入公钥失败，
    #     传了 --ssh-key 也登不进去。2026-10-20 的真机日志：
    #     AVC denied read tcontext=dosfs_t，unit 结果 exit-code 1。
    policy = (ROOT / "scripts" / "apply-rootfs-policy.py").read_text(encoding="utf-8")
    check("context=system_u:object_r:boot_t:s0" in policy,
          "apply-rootfs-policy.py must mount the ESP with the boot_t context; "
          "the vfat default dosfs_t is unreadable from init_t, so the first-boot "
          "SSH key import fails with EACCES")
    check("umask=0077" in policy,
          "apply-rootfs-policy.py must keep umask=0077 on the ESP mount")
    check('"/boot/efi ' not in policy,
          "apply-rootfs-policy.py must build the ESP fstab line from ESP_LABEL, "
          "not from a hard-coded /boot/efi device")

    # 14. piano-sensors-import 用 dump.erofs 读 Android 的 odm/vendor EROFS
    #     镜像。缺 erofs-utils 时脚本走到 "command not found"，下游
    #     adsprpcd-sensorspd.service 以依赖失败停止，真机传感器全部不可用。
    profile = json.loads((ROOT / "scripts" / "profiles" / "fedora.json")
                         .read_text(encoding="utf-8"))
    check("erofs-utils" in profile["base_packages"],
          "scripts/profiles/fedora.json must install erofs-utils: "
          "piano-sensors-import needs dump.erofs")
    runtime = (ROOT / "scripts" / "build-device-runtime.py").read_text(encoding="utf-8")
    check('"erofs-utils"' in runtime,
          "the piano-runtime RPM must declare erofs-utils in its Requires, "
          "otherwise a rootfs built without the profile silently loses sensors")

    if failures:
        for f in failures:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1

    print("OK: clean rootfs plan and contract behave as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
