#!/usr/bin/env python3
"""回归测试：kernel-piano 不得再退化成空壳 RPM。

实机教训（2026-10-17）：`packages/kernel-piano/kernel-piano.spec` 的 `%build`
只 `echo`、`%install` 只 `touch`，真内核靠 `if [ -d kernel-payload ]` 覆盖，
而 `build-rpms.sh` 从不生成该目录。结果是矩阵里躺着一个
`kernel-piano-7.2.9-1.piano.fc44.aarch64.rpm`：vmlinuz 与 DTB 都是 0 字节、
config 只有 6 行硬编码。装进镜像等于没有内核，但构建全程绿灯。

本测试只读源码与已构建产物，不接触设备，分两层：

1. 源码契约：spec 必须无条件校验载荷（`test -s Image` 等），不得再有
   “目录存在才拷贝”的静默降级；`build-rpms.sh` 必须真的把内核树暂存成
   Source0，并且找不到载荷时以非零码退出。
2. 产物契约：`out/rpms/` 里若已有 kernel-piano RPM，其 vmlinuz、DTB 与
   modules 目录必须非空，且内核 release 与模块目录名一致。
"""

from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "packages" / "kernel-piano" / "kernel-piano.spec"
BUILD_SH = ROOT / "scripts" / "build-rpms.sh"
RPMS = ROOT / "out" / "rpms"


def strip_spec_comments(text):
    return "\n".join(line for line in text.splitlines()
                     if not line.lstrip().startswith("#"))


def check_spec(failures):
    if not SPEC.is_file():
        failures.append(f"missing {SPEC.relative_to(ROOT)}")
        return
    body = strip_spec_comments(SPEC.read_text(encoding="utf-8"))

    # The payload must be mandatory. The old bug was exactly this pattern.
    if re.search(r"if\s*\[\s*-d\s+kernel-payload\s*\]", body):
        failures.append(
            "kernel-piano.spec still copies the payload only when the directory "
            "exists; that silent fallback produced a 0-byte vmlinuz")

    for token in ("test -s Image", "test -s System.map", "test -s config",
                  "test -d lib/modules"):
        if token not in body:
            failures.append(f"kernel-piano.spec does not assert payload: {token!r}")

    # The DTB is not part of the kernel artifact; it must be asserted too.
    if "test -s %{buildroot}/boot/dtb/qcom/sm8750-xiaomi-piano.dtb" not in body:
        failures.append("kernel-piano.spec does not assert a non-empty board DTB")

    # An empty `touch` of the kernel image is the signature of the stub build.
    if re.search(r"^\s*touch\s+%\{buildroot\}/boot/vmlinuz", body, re.MULTILINE):
        failures.append("kernel-piano.spec still `touch`es an empty vmlinuz")

    # rpm's brp-strip rewrites .symtab inside every .ko. The RPM would then stop
    # matching the sealed kernel artifact that build-image verifies, and the
    # difference is invisible until the image build fails much later.
    if not re.search(r"^%global\s+__strip\s+/bin/true\s*$", body, re.MULTILINE):
        failures.append("kernel-piano.spec does not disable strip; modules would "
                        "differ from the sealed kernel artifact")

    # 设备侧内核升级：%posttrans 重建 initramfs 并重打 ESP 的 boot.img。镜像
    # 构建（dnf --installroot）里没有 ESP 载荷，必须先跳过再动手，否则
    # build-rootfs 会在 chroot 里失败。
    posttrans = body.split("%posttrans", 1)
    if len(posttrans) != 2:
        failures.append("kernel-piano.spec has no %posttrans; a dnf kernel upgrade "
                        "would leave the ESP booting the old Image")
    else:
        script = posttrans[1].split("%changelog", 1)[0]
        if "EFI/Piano/stable" not in script:
            failures.append("%posttrans does not name the ESP payload path")
        if "board.dtb" not in script or "exit 0" not in script:
            failures.append("%posttrans does not skip a rootfs with no ESP payload")
        skip = script.split("exit 0", 1)[0]
        if "depmod" in skip or "piano-kernel-esp-update" in skip:
            failures.append("%posttrans must skip before depmod/repack when the ESP is absent")
        for token in ("depmod", "piano-kernel-esp-update"):
            if token not in script:
                failures.append(f"%posttrans never runs {token}")
        # installonly 升级后新旧内核共存，旧 manifest 仍在盘上；%posttrans 必须
        # 点名刚装上的那一份，否则工具会拒绝在多份之间猜。
        if "--manifest" not in script:
            failures.append("%posttrans does not name the manifest it just installed")

    # 实机教训（2026-10-22）：manifest 曾是固定路径 /usr/share/piano/
    # kernel-manifest.json。kernel-piano 是 installonly，升级时两个版本同时
    # 安装，rpm 报 "file ... conflicts with file from package" 并中止整个事务，
    # 设备上的内核因此永远升不上去。名字必须带 NVRA。
    named = "%{_datadir}/piano/kernel-manifest-%{version}-%{release}.json"
    if named not in body:
        failures.append("kernel-piano.spec does not ship an NVRA-named kernel manifest; "
                        "installonly upgrades would collide on a fixed path")
    if re.search(r"^\s*%\{_datadir\}/piano/kernel-manifest\.json\s*$", body, re.MULTILINE):
        failures.append("kernel-piano.spec still owns the fixed kernel-manifest.json path")

    # 内核升级所需的那套工具必须随本包发布，且路径与 %posttrans 里的一致。
    for name in ("f2fs-disk-bootstrap", "piano-kernel-esp-update",
                 "build-initramfs.py", "piano_artifacts.py", "piano_boot_container.py"):
        if f"%{{_prefix}}/lib/piano/{name}" not in body:
            failures.append(f"kernel-piano.spec does not ship /usr/lib/piano/{name}")
    if "/usr/lib/piano/piano-kernel-esp-update" not in body:
        failures.append("%posttrans calls a path the package does not own")

    # v4l2loopback 由本包拥有：它给这一版内核编，落在该内核的模块树里。
    # 运行时不再产出第二份，否则 DNF 会因为双所有者中止事务。
    if not re.search(r"^%\{_prefix\}/lib/modules/\*$", body, re.MULTILINE):
        failures.append("kernel-piano.spec must own the whole module tree, "
                        "including updates/v4l2loopback.ko")
    if "Requires:       python3" not in body or "Requires:       kmod" not in body:
        failures.append("%posttrans needs python3 and kmod at runtime")


def check_tools(failures):
    """spec 里点名的那两个工具必须真的接受 manifest 参数。

    %posttrans 传 --manifest 而工具不认，升级会在事务收尾时报 2，并且是装完
    才失败——比升级本身失败更难收拾。
    """
    esp_update = ROOT / "scripts" / "piano-kernel-esp-update"
    initramfs = ROOT / "scripts" / "build-initramfs.py"
    for path in (esp_update, initramfs):
        if not path.is_file():
            failures.append(f"missing {path.relative_to(ROOT)}")
            return
    updater = esp_update.read_text(encoding="utf-8")
    if '"--manifest"' not in updater:
        failures.append("piano-kernel-esp-update does not accept --manifest")
    if updater.count("manifest_name") < 2:
        failures.append("piano-kernel-esp-update does not forward the named manifest "
                        "to inspect_installed_kernel and build-initramfs")
    builder = initramfs.read_text(encoding="utf-8")
    if '"--manifest"' not in builder:
        failures.append("build-initramfs.py does not accept --manifest")
    if "installed_manifest" not in builder:
        failures.append("build-initramfs.py does not forward the named manifest "
                        "to InstalledKernel")


def check_build_script(failures):
    if not BUILD_SH.is_file():
        failures.append(f"missing {BUILD_SH.relative_to(ROOT)}")
        return
    body = strip_spec_comments(BUILD_SH.read_text(encoding="utf-8"))

    if "resolve_kernel_payload" not in body:
        failures.append("build-rpms.sh does not resolve a real kernel payload")
    if "kernel-payload-" not in body:
        failures.append("build-rpms.sh does not stage a kernel-payload tarball")
    if "resolve_board_dtb" not in body:
        failures.append("build-rpms.sh does not resolve the SunUEFI board DTB")

    # Failing to find the payload must abort, not fall through to a stub build.
    if not re.search(r"resolve_kernel_payload\)\s*\|\|\s*\{", body):
        failures.append("build-rpms.sh does not fail when no kernel payload is found")

    # v4l2loopback 同理：它由本包拥有，缺了就是一个没有相机模块的内核包。
    # 早先这里用 `if [ -n "$LOOPBACK_MODULE" ]` 静默跳过，正是空壳 RPM 的同一
    # 种失败形态，只是换了个文件。
    if "resolve_loopback_module" not in body:
        failures.append("build-rpms.sh does not resolve v4l2loopback for kernel-piano")
    if not re.search(r"resolve_loopback_module\)\s*\|\|\s*\{", body):
        failures.append("build-rpms.sh does not fail when v4l2loopback is missing")
    if re.search(r"if\s*\[\s*-n\s+\"?\$\{LOOPBACK_MODULE:-\}\"?\s*\]", body):
        failures.append("build-rpms.sh still silently skips a missing v4l2loopback")
    if "updates/v4l2loopback.ko" not in body:
        failures.append("build-rpms.sh does not stage updates/v4l2loopback.ko")

    # spec 从 piano-tools/ 装哪些文件，build-rpms.sh 就必须暂存哪些文件。
    # 少一个不是缺文件那么简单：rpmbuild 的 %install 在矩阵 job 里才失败，
    # 而前面每一步都是绿的。
    spec = strip_spec_comments(SPEC.read_text(encoding="utf-8"))
    staged = set(re.findall(r"piano-tools/([A-Za-z0-9_.+-]+)", spec))
    for name in sorted(staged):
        if not re.search(r'"?\$REPO_ROOT/[^"\s]*' + re.escape(name) + r'"?', body):
            failures.append(f"kernel-piano.spec installs piano-tools/{name} "
                            "but build-rpms.sh never stages it")


def check_built_rpm(failures):
    rpms = sorted(RPMS.glob("kernel-piano-*.rpm")) if RPMS.is_dir() else []
    if not rpms:
        # Not built in this environment (e.g. a clean CI checkout). The source
        # contract above is what protects the pipeline there.
        print("NOTE: no kernel-piano RPM in out/rpms; source contract only")
        return
    rpm = rpms[-1]

    listing = subprocess.run(["rpm", "-qlp", str(rpm)],
                             capture_output=True, text=True)
    if listing.returncode != 0:
        failures.append(f"cannot list {rpm.name}: {listing.stderr.strip()}")
        return
    files = set(listing.stdout.splitlines())

    vmlinuz = [f for f in files if f.startswith("/boot/vmlinuz-")]
    if not vmlinuz:
        failures.append(f"{rpm.name} ships no /boot/vmlinuz")
    # rpm -ql lists directory entries too; only files can be size-checked.
    dtb = [f for f in files
           if f.startswith("/boot/dtb/") and not f.endswith(("/dtb", "/qcom"))]
    if not dtb:
        failures.append(f"{rpm.name} ships no /boot/dtb")
    modules = [f for f in files if f.startswith("/usr/lib/modules/")]
    if not modules:
        failures.append(f"{rpm.name} ships no /usr/lib/modules")
    if not any(f.endswith("/modules.dep") for f in modules):
        failures.append(f"{rpm.name} ships no modules.dep")

    # Unpack and check the payload is genuinely non-empty, not just listed.
    with tempfile.TemporaryDirectory() as tmp:
        unpack = subprocess.run(
            f"rpm2cpio {rpm} | cpio -idm --quiet", shell=True, cwd=tmp,
            capture_output=True, text=True)
        if unpack.returncode != 0:
            failures.append(f"cannot unpack {rpm.name}: {unpack.stderr.strip()}")
            return
        root = Path(tmp)
        for rel in vmlinuz + dtb:
            path = root / rel.lstrip("/")
            if not path.is_file() or path.stat().st_size == 0:
                failures.append(f"{rpm.name}: {rel} is missing or 0 bytes")
        ko = list(root.glob("usr/lib/modules/*/kernel/**/*.ko"))
        if len(ko) < 100:
            failures.append(f"{rpm.name}: only {len(ko)} modules unpacked")

        # The kernel release recorded in the module path must appear in the
        # Image, otherwise modules can never load.
        mod_dirs = [p for p in (root / "usr/lib/modules").iterdir() if p.is_dir()]
        if len(mod_dirs) != 1:
            failures.append(f"{rpm.name}: expected 1 module tree, got {len(mod_dirs)}")
        else:
            release = mod_dirs[0].name
            image = root / vmlinuz[0].lstrip("/")
            if image.is_file():
                text = subprocess.run(["strings", "-a", str(image)],
                                      capture_output=True, text=True).stdout
                if release not in text:
                    failures.append(
                        f"{rpm.name}: module tree {release} does not match the Image")


def main():
    failures = []
    check_spec(failures)
    check_tools(failures)
    check_build_script(failures)
    check_built_rpm(failures)
    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: kernel-piano 打包真实内核载荷，空壳 RPM 与静默降级都已封堵")
    return 0


if __name__ == "__main__":
    sys.exit(main())
