#!/usr/bin/env python3
"""在 Actions 的原生 Fedora 构建器内编译 piano 程序并打包 RPM。"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

from piano_artifacts import inspect_kernel, sha256

ROOT = Path(__file__).resolve().parents[1]

# iio-sensor-proxy 3.9 的 meson_options.txt 把 ssc-support 声明为 feature/auto，
# 而 src/meson.build 用 get_option('ssc-support').enabled() 决定是否编入
# drv-ssc-*。auto 在 libssc 存在时仍是 not enabled（auto≠enabled），SSC 后端
# 会被静默跳过：ELF 不链接 libssc，设备上 service 因拿不到 sensor 而秒退。
# Debian 的 debian/rules 在 arm64 显式传 -Dssc-support=enabled，Fedora 侧必须同样显式。
SSC_DRIVER_MARKERS = ("ssc-accel", "ssc-proximity", "ssc-light", "ssc-compass")
# 构建机已装 binutils；用它的 readelf 读 DT_NEEDED，不自己解析 ELF。
SSC_NEEDED = re.compile(r"\(NEEDED\)\s+Shared library: \[(libssc\.so\.[0-9]+)\]")


def run(command, **kw):
    subprocess.run([str(x) for x in command], check=True, **kw)


def put(source, destination, executable=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_symlink():
        if destination.is_symlink() and os.readlink(destination) == os.readlink(source):
            return
        destination.symlink_to(os.readlink(source))
    else:
        shutil.copyfile(source, destination)
        destination.chmod(0o755 if executable else 0o644)


def rpm_package(name, payload, output, requires=(), provides=(), version="0.1.0", license_expression="MIT AND Apache-2.0 AND BSD-2-Clause-Patent AND BSD-3-Clause AND GPL-2.0-only"):
    """自有 RPM 只拥有实际载荷文件，不声明拥有整个 /usr 或系统目录。"""
    top = output / (name + "-rpmbuild")
    for folder in ("BUILD", "BUILDROOT", "SPECS", "SOURCES", "RPMS", "SRPMS"):
        (top / folder).mkdir(parents=True)
    archive = top / "SOURCES" / f"{name}-{version}.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(payload, arcname=f"{name}-{version}")
    paths = [p.relative_to(payload).as_posix() for p in sorted(payload.rglob("*"))
             if p.is_file() or p.is_symlink()]
    spec = top / "SPECS" / (name + ".spec")
    spec.write_text(
        "%global __os_install_post %{nil}\n"
        "%global debug_package %{nil}\n%global _build_id_links none\n"
        f"Name: {name}\nVersion: {version}\nRelease: 1%{{?dist}}\n"
        "Summary: Piano native runtime built from locked sources\n"
        f"License: {license_expression}\n"
        f"Source0: {name}-{version}.tar.gz\n"
        + "".join("Requires: " + r + "\n" for r in requires)
        + "".join("Provides: " + r + "\n" for r in provides)
        + "\n%description\nNative Piano payload; device boot is independently verified.\n"
        "\n%prep\n%setup -q\n\n%build\n\n%install\n"
        "mkdir -p %{buildroot}\ncp -a . %{buildroot}/\n\n%files\n"
        + "".join('"/' + p.replace('%', '%%').replace('"', '\\"') + '"\n' for p in paths)
        + "\n%changelog\n", encoding="utf-8")
    run(["rpmbuild", "--define", "_topdir " + str(top), "-bb", spec])
    rpms = list((top / "RPMS").rglob("*.rpm"))
    if not rpms:
        raise ValueError("rpmbuild produced no packages")
    for p in rpms:
        shutil.copyfile(p, output / "rpms" / p.name)
    return rpms


def build_fastrpc_daemon(source, payload, build):
    """Fedora 的 fastrpc RPM 只有库；从同版上游构建 dlopen listener 的守护进程。"""
    lock = json.loads((Path(__file__).resolve().parents[1] / "sources.lock.json").read_text())["sources"]["fastrpc"]
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip()
    if head != lock["commit"] or dirty:
        raise ValueError("FastRPC daemon source identity/cleanliness mismatch")
    executable = build / "adsprpcd"
    # 与上游 src/Makefile.am 的 adsprpcd_CFLAGS/LDADD 一致。共享库由 Fedora
    # 拥有，不再编译或覆盖另一套 libadsprpc/libadsp_default_listener。
    run(["gcc", "-O2", "-Wall", "-Wextra", "-I", source / "inc",
         "-DDEFAULT_DOMAIN_ID=0", "-DUSE_SYSLOG", "-DUSE_ADSP",
         source / "src/dsprpcd.c", "-ldl", "-o", executable])
    run([executable, "--help"], timeout=10)
    put(executable, payload / "usr/bin/adsprpcd", True)
    put(source / "LICENSE.txt", payload / "usr/share/licenses/piano-runtime/fastrpc-LICENSE.txt")
    return {"commit": head, "source_sha256": sha256(source / "src/dsprpcd.c"),
            "binary_sha256": sha256(executable), "listener": "libadsp_default_listener.so.1"}


def prune_matrix_owned(payload):
    """删除 RPM 矩阵已经拥有的路径，保证一个路径只有一个所有者。

    `runtime/matrix-owned-paths.txt` 与 `packages/*` 的 %files 是同一份契约；
    tests/check_package_ownership.py 会核对两边。
    """
    listing = ROOT / "runtime/matrix-owned-paths.txt"
    pruned = []
    for line in listing.read_text().splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        target = payload / name.lstrip("/")
        if target.is_symlink() or target.is_file():
            target.unlink()
            pruned.append(name)
    return sorted(pruned)


def stage_sensor_integration(sun, output):
    """准备传感器集成载荷，但不重复构建 libssc / iio-sensor-proxy。

    这两个库由 RPM 矩阵（packages/libssc、packages/iio-sensor-proxy-ssc）构建，
    补丁集与这里原先用的完全一致。再编一份会产生同名不同字节的 RPM，两个
    producer 合并进同一仓库时 DNF 直接冲突。这里只保留矩阵不负责的部分：
    piano-sensors 的 udev 规则、import/wait 辅助程序和 unit。
    """
    sensor = output / "sensor-source"
    shutil.copytree(sun / "upstream/piano-sensors-current", sensor,
                    ignore=shutil.ignore_patterns(".git"))
    for p in sorted((sun / "patches/piano-sensors").glob("*.patch")):
        run(["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", p], cwd=sensor)
    return {
        "source": "piano-sensors",
        "packages": {
            "libssc": "built by packages/libssc at the same NEVRA",
            "iio-sensor-proxy": "built by packages/iio-sensor-proxy-ssc at the same NEVRA",
        },
    }


def build(args):
    if platform.machine() not in ("aarch64", "arm64"):
        raise ValueError("native Fedora aarch64 builder required")
    sun, kernel, output = args.sunuefi.resolve(), args.kernel.resolve(), args.output.resolve()
    if output.exists():
        raise ValueError("choose a fresh runtime output")
    record, kernel_hash = inspect_kernel(kernel)
    output.mkdir(parents=True)
    (output / "rpms").mkdir()
    sys.path.insert(0, str(sun / "tools"))
    import build_piano_runtime_helpers as runtime
    import stage_piano_ram_hardware as hardware
    import build_boot_request
    from piano_device_integration import integration_source, build_touch, stage_payload
    device_source, device_pin = integration_source(sun.parent, output, run)
    public = sun / "upstream/debian-piano-current"
    runtime.repository(public, runtime.PUBLIC_COMMIT)
    payload = output / "piano-payload"
    payload.mkdir()
    build = output / "helpers"
    build.mkdir()
    compiler = shutil.which("gcc")
    flags = [compiler, "-O2", "-Wall", "-Wextra", "-Werror"]
    touch, touch_proof = runtime.derive_touch_source(public, build)
    # piano-camerad is built by packages/piano-camera: the two Fedora patches
    # (gcc bounds and owned-pix route) are part of that spec now. Building a
    # second copy here produced a same-path, different-bytes RPM and DNF
    # refused the transaction. The proof below records where it comes from.
    camera_proof = {"built_by": "packages/piano-camera",
                    "patches": {name: sha256(ROOT / "packages/piano-camera" / name)
                                for name in ("camerad-gcc-bounds.patch",
                                             "camerad-owned-pix-route.patch")}}
    pen_files, pen_proof = runtime.build_pen_core(build, flags)
    helpers = dict(pen_files)
    for name, (relative, pin, destination) in (runtime.PUBLIC_SOURCES | runtime.BSP_SOURCES).items():
        if name == "piano-camerad":
            continue
        source = (sun if name in runtime.BSP_SOURCES else public) / relative
        if pin and sha256(source) != pin:
            raise ValueError("locked helper changed: " + name)
        entry = build / (name + "-entry.c")
        entry.write_text(runtime.entry_source(name))
        obj, exe = build / (name + ".o"), build / name
        effective = touch if name == "piano-touch-view" else source
        if name == "piano-touch-view":
            item = build_touch(runtime, build, flags, args.uapi, effective, pen_proof,
                               device_source, run, sha256)
            touch_proof.update(item)
        else:
            run([*flags, "-isystem", args.uapi / "include", "-Dmain=PianoOriginalMain", "-c", effective, "-o", obj])
            run([*flags, "-static", entry, obj, "-lm", "-o", exe])
            item = runtime.verify_elf(exe)
        run([exe, "--help"], timeout=10)
        helpers[destination] = {**item, "file": name, "mode": 0o755}
    for destination, row in helpers.items():
        # Fedora 42+ 合并 /usr/sbin；新程序使用 canonical /usr/bin。
        destination = destination.replace("usr/sbin/", "usr/bin/", 1)
        put(build / row["file"], payload / destination, row["mode"] & 0o111)

    # 原始设备服务先装入 staging，再用上游已审阅的 adapter 替换启动脚本。
    for p in (public / "rootfs/overlay/usr/lib/systemd/system").glob("piano-*.service"):
        if p.name not in ("piano-swapfile.service", "piano-hostkeys.service", "piano-usb.service"):
            put(p, payload / "usr/lib/systemd/system" / p.name)
    adapters = sun / "build/fedora-adapters"
    hardware.build(adapters, public)
    for p in adapters.rglob("*"):
        if p.is_file() and p.name != "manifest.json":
            put(p, payload / p.relative_to(adapters), p.stat().st_mode & 0o111)
    for p in (sun / "linux/bsp/common").rglob("*"):
        if p.is_file() or p.is_symlink():
            put(p, payload / p.relative_to(sun / "linux/bsp/common"), p.stat().st_mode & 0o111)
    integration_proof = stage_payload(device_source, device_pin, payload, touch_proof,
                                      camera_proof, put, sha256, args.finger_config_dir)
    for name in ("cpufreq-start", "pstore-save"):
        put(public / "rootfs/overlay/usr/lib/piano" / name, payload / "usr/lib/piano" / name, True)
    put(Path(shutil.which("busybox")), payload / "usr/lib/piano/busybox", True)

    # Firmware belongs to packages/xiaomi-piano-firmware. Copying the same
    # tree here produced a second owner for 99 paths (three of them with
    # different bytes) and DNF aborted the clean-rootfs transaction. Only the
    # AudioReach topology is built here, because the firmware package does not
    # generate it.
    run(["bash", public / "scripts/build-topology.sh", sun / "upstream/audioreach-topology", build / "firmware"])
    topology = build / "firmware/qcom/sm8750/Xiaomi Pad 8 Pro-tplg.bin"
    run(["alsatplg", "-d", topology, "-o", build / "topology-decoded.conf"])
    put(topology, payload / "usr/lib/firmware/qcom/sm8750/Xiaomi Pad 8 Pro-tplg.bin")
    if args.loopback:
        put(args.loopback, payload / f"usr/lib/modules/{record['kernel_release']}/updates/v4l2loopback.ko")
    request = build_boot_request.build(build / "boot-request", "aarch64", "gcc")
    put(build / "boot-request/piano-boot-request", payload / "usr/local/sbin/piano-boot-request", True)
    mipps = sun.parent / "piano-mipps-auth"
    for name, path in (("xiaomi-mipps-auth", "usr/libexec/xiaomi-mipps-auth"),
                       ("xiaomi-mipps-auth.service", "usr/lib/systemd/system/xiaomi-mipps-auth.service"),
                       ("90-xiaomi-mipps-auth.rules", "usr/lib/udev/rules.d/90-xiaomi-mipps-auth.rules")):
        put(mipps / name, payload / path, name == "xiaomi-mipps-auth")
    # libssc and iio-sensor-proxy are built once, by the RPM matrix. Building
    # them here too produced a second pair with the same NEVRAs but different
    # bytes (different build host, different patch set), and merging both into
    # one DNF repository is a hard conflict. The matrix packages now carry
    # every patch this producer used, so only the sensor integration payload
    # (units, udev, import/wait helpers) stays here.
    sensor_proof = stage_sensor_integration(sun, output)
    fastrpc_proof = build_fastrpc_daemon(sun.parent / "fastrpc", payload, build)
    sensor = output / "sensor-source"
    integration = output / "piano-sensors"
    shutil.copytree(sensor / "piano-sensors", integration)
    # importer 与 unit 使用同一份已应用包装补丁的仓库副本。
    for name in ("piano-sensors-import", "piano-sensors-lp-table", "piano-sensors-wait"):
        put(integration / name, payload / "usr/libexec/piano-sensors" / name, True)
    for name in ("adsprpcd-sensorspd.service", "piano-sensors-import.service"):
        put(integration / "debian" / ("piano-sensors." + name), payload / "usr/lib/systemd/system" / name)
    # Fedora merged-usr 的 canonical 路径；不要依赖 Debian 的 /usr/sbin 布局。
    dropin = payload / "usr/lib/systemd/system/adsprpcd-sensorspd.service.d/20-fedora-daemon.conf"
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text("[Service]\nExecStart=\nExecStart=/usr/bin/adsprpcd sensorspd\n")
    fastrpc_proof["service_sha256"] = {
        name: sha256(payload / name) for name in (
            "usr/lib/systemd/system/adsprpcd-sensorspd.service",
            "usr/lib/systemd/system/adsprpcd-sensorspd.service.d/20-fedora-daemon.conf")}
    put(integration / "debian/piano-sensors.udev", payload / "usr/lib/udev/rules.d/70-piano-sensors.rules")
    put(integration / "59-fastrpc-remoteproc.rules", payload / "etc/udev/rules.d/59-fastrpc-remoteproc.rules")
    put(integration / "iio-sensor-proxy-piano.conf", payload / "etc/systemd/system/iio-sensor-proxy.service.d/piano.conf")
    from piano_runtime_extras import build as build_extras
    extras_proof = build_extras(sun.parent, output / "extras", rpm_package, put, sha256, run)
    for p in (output / "extras/rpms").glob("*.rpm"):
        shutil.copyfile(p, output / "rpms" / p.name)
    # Drop every path the RPM matrix already owns. The matrix is the release
    # artifact for configs, services, calibration data and the xiaomi boot
    # helpers; a second owner here made DNF abort the clean-rootfs transaction.
    pruned = prune_matrix_owned(payload)
    provenance = {"status": "FEDORA_RUNTIME_BUILT_NOT_DEVICE_VERIFIED",
                  "kernel_release": record["kernel_release"], "kernel_manifest_sha256": kernel_hash,
                  "touch": touch_proof, "camera": camera_proof, "boot_request": request,
                  "sensor_sources": sensor_proof, "fastrpc_daemon": fastrpc_proof,
                  "extras": extras_proof,
                  "integration": integration_proof, "pruned_matrix_owned": pruned, "device_tested": False}
    dest = payload / "usr/share/piano-provenance/fedora-runtime.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(provenance, indent=2) + "\n")
    rpm_package("piano-runtime", payload, output, version="0.2.0",
                requires=("python3", "python3-evdev", "busybox", "kmod", "util-linux", "fastrpc >= 1.0.8",
                          "libadsp_default_listener.so.1()(64bit)", "qrtr", "rmtfs", "tqftpserv", "alsa-utils",
                          # piano-sensors-import reads the Android odm/vendor EROFS
                          # images with dump.erofs; without it the import fails and
                          # adsprpcd-sensorspd never starts.
                          "erofs-utils",
                          # The sensor libraries now come from the RPM matrix only.
                          "libssc", "iio-sensor-proxy"))
    provenance["rpms"] = {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
                          for p in (output / "rpms").glob("*.rpm")}
    (output / "manifest.json").write_text(json.dumps(provenance, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--finger-config-dir", type=Path, required=True,
                    help="私有完整原厂 piano_nova_{csot,boe}_thp_config.ini 目录；精简笔配置不能替代")
    for name in ("sunuefi", "kernel", "uapi", "output"):
        ap.add_argument("--" + name, type=Path, required=True)
    ap.add_argument("--loopback", type=Path, required=True)
    build(ap.parse_args())


if __name__ == "__main__":
    main()
