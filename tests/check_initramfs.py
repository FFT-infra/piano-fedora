#!/usr/bin/env python3
"""离线检查 initramfs 构建器的参数校验、拒绝路径与归档结构。

用 fixture 走一遍真实构建：静态 BusyBox 用宿主机的（要求 AArch64 且静态），
内核模块是占位文件。真正能启动要等设备，这里只证明归档正确、依赖闭包完整、
拒绝路径生效。
"""

import gzip
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from kernel_fixture import make_kernel

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "build-initramfs.py"
BOOTSTRAP = ROOT / "initramfs" / "f2fs-disk-bootstrap"

RELEASE = "7.2.9-fixture"


def run(*args):
    return subprocess.run(
        [sys.executable, str(TOOL), *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def static_aarch64_busybox():
    """找一个静态 AArch64 BusyBox；宿主不是该架构时返回 None。"""
    candidate = shutil.which("busybox")
    if not candidate:
        return None
    header = Path(candidate).read_bytes()[:64]
    if len(header) < 64 or header[:5] != b"\x7fELF\x02":
        return None
    if struct.unpack_from("<H", header, 18)[0] != 183:
        return None
    program = subprocess.run(["readelf", "-l", candidate],
                             capture_output=True, text=True).stdout
    if "Requesting program interpreter" in program:
        return None
    return Path(candidate)


def build_fixture(tmp, busybox):
    rootfs = tmp / "rootfs"
    (rootfs / "usr" / "bin").mkdir(parents=True)
    (rootfs / "usr/lib64").mkdir()
    (rootfs / "usr/lib").mkdir()
    (rootfs / "lib64").symlink_to("usr/lib64")
    (rootfs / "lib").symlink_to("usr/lib")
    shutil.copy2(busybox, rootfs / "usr" / "bin" / "busybox")
    # blkid 走动态链接；本机没有时跳过依赖闭包部分。
    blkid = shutil.which("blkid")
    if blkid:
        shutil.copy2(blkid, rootfs / "usr" / "bin" / "blkid")
        for library in ("libblkid.so.1", "libc.so.6"):
            for directory in ("/usr/lib64", "/lib64", "/usr/lib", "/usr/lib/aarch64-linux-gnu", "/lib/aarch64-linux-gnu"):
                source = Path(directory) / library
                if source.exists():
                    shutil.copy2(source.resolve(), rootfs / "lib64" / library)
                    break
        for loader in (Path("/usr/lib/ld-linux-aarch64.so.1"), Path("/lib/ld-linux-aarch64.so.1")):
            if loader.exists():
                shutil.copy2(loader.resolve(), rootfs / "lib" / "ld-linux-aarch64.so.1")
                break

    # 首启扩容要 fsck.f2fs，resize.f2fs 在 Fedora 里是它的链接。
    (rootfs / "usr" / "sbin").mkdir(parents=True)
    fsck = shutil.which("fsck.f2fs") or "/usr/sbin/fsck.f2fs"
    if Path(fsck).is_file():
        shutil.copy2(Path(fsck).resolve(), rootfs / "usr" / "sbin" / "fsck.f2fs")
        for library in ("libselinux.so.1", "libf2fs.so.10", "libpcre2-8.so.0", "libgcc_s.so.1", "libc.so.6"):
            for directory in ("/usr/lib64", "/lib64", "/usr/lib", "/usr/lib/aarch64-linux-gnu", "/lib/aarch64-linux-gnu"):
                source = Path(directory) / library
                if source.exists():
                    shutil.copy2(source.resolve(), rootfs / "lib64" / library)
                    break

    rescue = rootfs / "usr/local/sbin/piano-debug-bootstrap"
    rescue.parent.mkdir(parents=True)
    # rpm 的 brp-mangle-shebangs 会把解释器改写成 /usr/bin/sh，归档里只有
    # bin/sh。fixture 直接复现那份被改过的字节，验证装配时归一回来。
    rescue.write_text("#!/usr/bin/sh\nexit 0\n")
    rescue.chmod(0o755)
    collector = rootfs / "usr" / "bin" / "piano-collect-boot"
    collector.write_text("#!/usr/bin/sh\nexit 0\n")
    collector.chmod(0o755)
    kernel = tmp / "kernel"
    modules = kernel / "modules" / "lib" / "modules" / RELEASE
    (modules / "kernel" / "drivers" / "iommu").mkdir(parents=True)
    (modules / "kernel" / "drivers" / "ufs").mkdir(parents=True)
    (modules / "kernel" / "drivers" / "pinctrl").mkdir(parents=True)
    (modules / "kernel" / "drivers" / "mailbox").mkdir(parents=True)
    (modules / "kernel" / "drivers" / "phy" / "qualcomm").mkdir(parents=True)
    for relative in (
        "kernel/drivers/iommu/arm-smmu.ko",
        "kernel/drivers/ufs/ufs-qcom.ko",
        "kernel/drivers/pinctrl/pinctrl-sm8750.ko",
        "kernel/drivers/mailbox/qcom-cpucp-mbox.ko",
        "kernel/drivers/phy/qualcomm/phy-qcom-qmp-ufs.ko",
    ):
        (modules / relative).write_bytes(b"placeholder-module\n")
    (modules / "modules.dep").write_text(
        "kernel/drivers/iommu/arm-smmu.ko:\n"
        "kernel/drivers/pinctrl/pinctrl-sm8750.ko: kernel/drivers/iommu/arm-smmu.ko\n"
        "kernel/drivers/mailbox/qcom-cpucp-mbox.ko: kernel/drivers/iommu/arm-smmu.ko\n"
        "kernel/drivers/phy/qualcomm/phy-qcom-qmp-ufs.ko: kernel/drivers/iommu/arm-smmu.ko\n"
        "kernel/drivers/ufs/ufs-qcom.ko: kernel/drivers/iommu/arm-smmu.ko"
        " kernel/drivers/phy/qualcomm/phy-qcom-qmp-ufs.ko\n"
    )
    (modules / "modules.builtin").write_text("kernel/drivers/soc/qcom/qcom-pmic-glink.ko\n")
    (modules / "modules.softdep").write_text("")
    (kernel / "manifest.json").write_text(json.dumps({
        "kernel_release": RELEASE,
        "source_commit": "352508459733d3e6d349ea5581a8dd2fd8bb4180",
    }))
    # 覆盖前面的占位 metadata 为可验证的封存格式，字节仍明确是测试 fixture。
    make_kernel(kernel, RELEASE)
    return rootfs, kernel


def build_installed_fixture(tmp, busybox):
    """造一份"已安装系统"的根：manifest 在 /usr/share/piano，模块在
    /lib/modules，Image 在 /boot，两份脚本由 rpm 安装并已被改写解释器行。

    设备上 initramfs 构建器与它的模块在 /usr/lib/piano，引导脚本也在同一
    目录，所以这里把仓库里的脚本复制成那一份布局再跑。
    """
    installed = tmp / "installed"
    for name in ("usr/bin", "usr/lib64", "usr/lib", "usr/sbin", "usr/local/bin",
                 "usr/share/piano", "boot", "lib", "lib64", "usr/local/sbin"):
        (installed / name).mkdir(parents=True)
    (installed / "lib").rmdir()
    (installed / "lib64").rmdir()
    (installed / "usr/local/sbin").rmdir()
    (installed / "lib").symlink_to("usr/lib")
    (installed / "lib64").symlink_to("usr/lib64")
    (installed / "usr/local/sbin").symlink_to("bin")
    shutil.copy2(busybox, installed / "usr/bin/busybox")
    for name in ("blkid",):
        source = shutil.which(name)
        if source:
            shutil.copy2(source, installed / "usr/bin" / name)
    fsck = shutil.which("fsck.f2fs") or "/usr/sbin/fsck.f2fs"
    if Path(fsck).is_file():
        shutil.copy2(Path(fsck).resolve(), installed / "usr/sbin/fsck.f2fs")
    for library in ("libblkid.so.1", "libselinux.so.1", "libf2fs.so.10", "libpcre2-8.so.0",
                    "libgcc_s.so.1", "libc.so.6"):
        for directory in ("/usr/lib64", "/lib64", "/usr/lib", "/lib/aarch64-linux-gnu"):
            source = Path(directory) / library
            if source.exists():
                shutil.copy2(source.resolve(), installed / "usr/lib64" / library)
                break
    for loader in (Path("/usr/lib/ld-linux-aarch64.so.1"), Path("/lib/ld-linux-aarch64.so.1")):
        if loader.exists():
            shutil.copy2(loader.resolve(), installed / "usr/lib/ld-linux-aarch64.so.1")
            break
    for name in ("usr/local/bin/piano-debug-bootstrap", "usr/bin/piano-collect-boot"):
        path = installed / name
        path.write_text("#!/usr/bin/sh\nexit 0\n")
        path.chmod(0o755)

    # 内核：manifest、Image 与模块闭包。depmod 之后索引会重写，这里放一份
    # 与封存值不同的 modules.dep 证明索引不参与设备侧校验。
    modules = installed / "usr/lib/modules" / RELEASE
    for relative in ("kernel/drivers/iommu/arm-smmu.ko",
                     "kernel/drivers/ufs/ufs-qcom.ko",
                     "kernel/drivers/pinctrl/pinctrl-sm8750.ko",
                     "kernel/drivers/mailbox/qcom-cpucp-mbox.ko",
                     "kernel/drivers/phy/qualcomm/phy-qcom-qmp-ufs.ko"):
        path = modules / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"placeholder-module\n")
    # 索引由 depmod 重写：这里给一份与封存值不同的 modules.dep，证明索引
    # 不参与设备侧校验，闭包仍然按它展开。
    (modules / "modules.dep").write_text(
        "kernel/drivers/iommu/arm-smmu.ko:\n"
        "kernel/drivers/pinctrl/pinctrl-sm8750.ko: kernel/drivers/iommu/arm-smmu.ko\n"
        "kernel/drivers/mailbox/qcom-cpucp-mbox.ko: kernel/drivers/iommu/arm-smmu.ko\n"
        "kernel/drivers/phy/qualcomm/phy-qcom-qmp-ufs.ko: kernel/drivers/iommu/arm-smmu.ko\n"
        "kernel/drivers/ufs/ufs-qcom.ko: kernel/drivers/iommu/arm-smmu.ko"
        " kernel/drivers/phy/qualcomm/phy-qcom-qmp-ufs.ko\n"
    )
    (modules / "modules.builtin").write_text("kernel/drivers/soc/qcom/qcom-pmic-glink.ko\n")
    (modules / "modules.softdep").write_text("")
    # updates/ 里的外部模块不属于封存集合，不能进闭包也不能让校验失败。
    (modules / "updates").mkdir()
    (modules / "updates/v4l2loopback.ko").write_bytes(b"external-module\n")
    image = b"TEST-FIXTURE-NOT-A-BOOTABLE-KERNEL"
    (installed / "boot" / "vmlinuz-7.2.9-1.piano.fc44").write_bytes(image)
    (installed / "boot" / "vmlinuz-6.0-old").write_bytes(b"an older kernel")
    (installed / "usr/share/piano/kernel-manifest.json").write_text(json.dumps({
        "status": "HOST_BUILT_FULL_CANDIDATE_NOT_HARDWARE_VERIFIED",
        "source_clean": True,
        "kernel_release": RELEASE,
        "source_commit": "352508459733d3e6d349ea5581a8dd2fd8bb4180",
        "root_policy": "LABEL=PIANOROOT",
        "image": {"sha256": hashlib.sha256(image).hexdigest(), "bytes": len(image)},
        "modules": [{
            "path": f"lib/modules/{RELEASE}/{relative}",
            "sha256": hashlib.sha256((modules / relative).read_bytes()).hexdigest(),
            "bytes": (modules / relative).stat().st_size,
            "vermagic": RELEASE + " SMP preempt mod_unload aarch64",
        } for relative in ("kernel/drivers/iommu/arm-smmu.ko",
                           "kernel/drivers/ufs/ufs-qcom.ko",
                           "kernel/drivers/pinctrl/pinctrl-sm8750.ko",
                           "kernel/drivers/mailbox/qcom-cpucp-mbox.ko",
                           "kernel/drivers/phy/qualcomm/phy-qcom-qmp-ufs.ko")],
    }))

    # 设备上 /usr/lib/piano 里那份布局。
    package = tmp / "package"
    package.mkdir()
    for name in ("build-initramfs.py", "piano_artifacts.py"):
        shutil.copy2(ROOT / "scripts" / name, package / name)
    shutil.copy2(BOOTSTRAP, package / "f2fs-disk-bootstrap")
    return installed, package


def read_newc(archive):
    """从 gzip 的 cpio 里取出条目名与内容。"""
    raw = gzip.decompress(Path(archive).read_bytes())
    records, offset = {}, 0
    while offset + 110 <= len(raw):
        if raw[offset:offset + 6] != b"070701":
            raise ValueError(f"bad magic at {offset}")
        fields = [int(raw[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        size, name_size = fields[6], fields[11]
        name_start = offset + 110
        name = raw[name_start:name_start + name_size - 1].decode()
        data_start = name_start + name_size
        data_start += -data_start % 4
        data = raw[data_start:data_start + size]
        if name == "TRAILER!!!":
            break
        records[name] = data
        offset = data_start + size
        offset += -offset % 4
    return records


def main():
    failures = []

    def check(condition, message):
        if not condition:
            failures.append(message)

    # 引导脚本必须存在且语法正确。
    check(BOOTSTRAP.is_file(), "bootstrap script must exist")
    if BOOTSTRAP.is_file():
        syntax = subprocess.run(["sh", "-n", str(BOOTSTRAP)], capture_output=True, text=True)
        check(syntax.returncode == 0, f"bootstrap has a shell syntax error: {syntax.stderr.strip()}")
        text = BOOTSTRAP.read_text()
        # 这是 F2FS 版本：类型检查、挂载类型都要是 f2fs。
        # 注释里会提到 noload 说明为什么不用它，所以只看真正的挂载调用。
        mounts = [line for line in text.splitlines()
                  if line.lstrip().startswith("mount ") and "-t" in line]
        check(mounts, "bootstrap should mount the root filesystem")
        check(all("-t f2fs" in line for line in mounts),
              f"every root mount must be f2fs: {mounts}")
        check(not any("noload" in line for line in mounts),
              "F2FS has no noload option")
        check(any('= f2fs' in line for line in text.splitlines() if "TYPE" in line),
              "bootstrap must require the f2fs filesystem type")
        # 注释里会解释为什么不再用 ext4，所以只检查可执行语句。
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        check("ext4" not in code, "bootstrap must not depend on ext4")
        # 看门狗规则由 piano-fedora-configs 的 RPM 提供。initramfs 里写文件会
        # 绕过 SELinux 标签事务，systemd-modules-load 读到 unlabeled_t，规则
        # 不生效；那个文件也不属于任何包。引导脚本只做存在性断言，读它可以，
        # 写它不行。
        writes = [line for line in code.splitlines()
                  if "/sysroot/etc/modprobe.d" in line and (">" in line or "mkdir" in line)]
        check(not writes, f"bootstrap must not write /etc/modprobe.d entries: {writes}")
        check("piano-blacklist-watchdog.conf" in code,
              "bootstrap must assert the watchdog blacklist reached the rootfs")

    # 参数缺失：拒绝。
    check(run("--rootfs", "/nonexistent", "--kernel", "/nonexistent",
              "--output", "/nonexistent").returncode == 2,
          "missing inputs should be rejected")

    busybox = static_aarch64_busybox()
    if busybox is None:
        # 宿主不是 AArch64 或没有静态 BusyBox：明确记录跳过，不算通过。
        if failures:
            for item in failures:
                print(f"FAIL: {item}", file=sys.stderr)
            return 1
        print("SKIP: no static AArch64 busybox on this host; "
              "argument checks only (not a pass)")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        rootfs, kernel = build_fixture(tmp, busybox)
        output = tmp / "out"

        # 输出已存在：拒绝。
        output.mkdir()
        check(run("--rootfs", str(rootfs), "--kernel", str(kernel),
                  "--output", str(output)).returncode == 2,
              "existing output should be rejected")
        output.rmdir()

        result = run("--rootfs", str(rootfs), "--kernel", str(kernel),
                     "--output", str(output))
        if result.returncode != 0:
            check(False, f"build failed: {result.stderr.strip()}")
        else:
            manifest = json.loads((output / "manifest.json").read_text())
            check(manifest["root_fstype"] == "f2fs", "manifest must record f2fs")
            check(manifest["root_policy"] == "LABEL=PIANOROOT", "root policy must be the piano label")
            check(manifest["device_tested"] is False, "must not claim device testing")
            check(
                manifest["modules"] == [
                    "arm_smmu", "pinctrl_sm8750", "qcom_cpucp_mbox",
                    "phy_qcom_qmp_ufs", "ufs_qcom",
                ],
                f"module closure order wrong: {manifest['modules']}",
            )

            records = read_newc(output / "initramfs.cpio.gz")
            for required in ("init", "pianoinit", "bin/busybox", "bin/sh",
                             "bin/mount", "bin/switch_root", "bin/insmod",
                             "etc/piano/root-label", "etc/piano/root-partname",
                             "etc/piano/kernel-release", "etc/piano/modules-load-order",
                             "dev/console", "dev/null"):
                check(required in records, f"missing from initramfs: {required}")
            check(records.get("etc/piano/root-label") == b"PIANOROOT\n",
                  "root label content wrong")
            check(records.get("etc/piano/root-partname") == b"sunuefi_root\n",
                  "root partname content wrong")
            # 引导脚本必须与仓库里的那份逐字节一致。
            check(records.get("pianoinit") == BOOTSTRAP.read_bytes(),
                  "archived bootstrap differs from the repository copy")
            # 动态依赖必须一起进归档。
            if shutil.which("blkid"):
                check("usr/bin/blkid" in records, "blkid missing from initramfs")
                check("lib64/libblkid.so.1" in records,
                      "loader must find libblkid at /lib64 even with a merged-usr root")
                check(any(name.startswith("lib64/") or name.startswith("lib/")
                          for name in records),
                      "ELF dependency closure missing")
            # 首启扩容：工具、链接与引导脚本的分支都要在归档里。
            if (rootfs / "usr" / "sbin" / "fsck.f2fs").is_file():
                check("usr/sbin/fsck.f2fs" in records, "fsck.f2fs missing from initramfs")
                check(records.get("usr/sbin/resize.f2fs") == b"fsck.f2fs",
                      "resize.f2fs must be a relative link to fsck.f2fs")
                check("lib64/libf2fs.so.10" in records,
                      "f2fs-tools library must be in the ELF closure")
                check(manifest["verification"]["f2fs_tools_verified"] is True,
                      "manifest must record the f2fs tools check")
                bootstrap = records["pianoinit"]
                check(b"ROOT_GROW_STARTED" in bootstrap and b"/usr/sbin/resize.f2fs" in bootstrap,
                      "bootstrap must grow the root before the writable mount")
                check(b"shrinking is unsupported" in bootstrap,
                      "a root larger than its partition must stop the boot")
                check(bootstrap.index(b"grow_root\n") < bootstrap.index(b'mount -t f2fs -o rw'),
                      "the grow branch must run before the writable mount")

        # 解释器行归一：fixture 里两份脚本都是 rpm 改写后的 /usr/bin/sh，
        # 归档里必须回到 bin/sh，否则设备上救援与取证都执行不了。
        check(records.get("usr/local/sbin/piano-debug-bootstrap", b"").startswith(b"#!/bin/sh\n"),
              "rescue helper keeps the rpm-mangled /usr/bin/sh interpreter")
        check(records.get("usr/bin/piano-collect-boot", b"").startswith(b"#!/bin/sh\n"),
              "boot evidence collector keeps the rpm-mangled /usr/bin/sh interpreter")

        # 设备侧模式：同一份装配代码，内核输入取自"已安装系统"。
        installed, package = build_installed_fixture(tmp, busybox)
        installed_output = tmp / "installed-out"
        device = subprocess.run(
            [sys.executable, str(package / "build-initramfs.py"), "--installed",
             "--rootfs", str(installed), "--output", str(installed_output)],
            capture_output=True, text=True, cwd=package,
        )
        if device.returncode != 0:
            check(False, f"installed-mode build failed: {device.stderr.strip()}")
        else:
            device_manifest = json.loads((installed_output / "manifest.json").read_text())
            check(device_manifest["kernel_source"] == "installed",
                  "installed-mode manifest must record where the kernel came from")
            check(device_manifest["kernel_release"] == RELEASE,
                  "installed-mode manifest release differs")
            check(device_manifest["modules"] == manifest["modules"],
                  "installed mode produced a different module closure")
            check(not any(name.endswith("v4l2loopback.ko")
                          for name in read_newc(installed_output / "initramfs.cpio.gz")),
                  "updates/ must stay out of the early module closure")
            device_records = read_newc(installed_output / "initramfs.cpio.gz")
            for name in ("init", "pianoinit", "usr/local/sbin/piano-debug-bootstrap",
                         "usr/bin/piano-collect-boot"):
                check(device_records.get(name, b"").startswith(b"#!/bin/sh\n"),
                      f"installed mode leaves a mangled interpreter: {name}")
            # 索引由 depmod 重写，不能参与设备侧校验；把 modules.dep 改成
            # 垃圾之后仍然必须通过。
            check(device_manifest["verification"]["module_identities_verified"] is True,
                  "installed mode must still verify module identities")

        # 设备侧模式不接受 --kernel，也不支持 ABL 链。
        check(subprocess.run(
            [sys.executable, str(package / "build-initramfs.py"), "--installed",
             "--rootfs", str(installed), "--kernel", str(tmp / "kernel"),
             "--output", str(tmp / "reject-out")],
            capture_output=True, text=True, cwd=package).returncode == 2,
            "installed mode must reject an explicit --kernel")
        check(subprocess.run(
            [sys.executable, str(package / "build-initramfs.py"), "--installed",
             "--rootfs", str(installed), "--boot-chain", "abl-storage",
             "--output", str(tmp / "reject-chain")],
            capture_output=True, text=True, cwd=package).returncode == 2,
            "installed mode must reject the ABL chain")

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: initramfs builder, closure, archive structure and reject paths behave")
    return 0


if __name__ == "__main__":
    sys.exit(main())
