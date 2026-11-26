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
import hashlib
import importlib.machinery
import importlib.util
import json
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

    # 板级 DTB 随包发布，%posttrans 把它写进 ESP。缺它或缺摘要都不能出包。
    for token in ("test -s board.dtb", "test -s board-dtb.json",
                  "install -m 0644 board.dtb board-dtb.json %{buildroot}%{_prefix}/lib/piano/",
                  "%{_prefix}/lib/piano/board.dtb", "%{_prefix}/lib/piano/board-dtb.json"):
        if token not in body:
            failures.append(f"kernel-piano.spec does not ship the composed board DTB: {token!r}")
    # /boot/dtb 下那份是没叠 overlay 的 vendor DTB，CMA 与 UFS MCQ 都没有。
    # 包里留着它，迟早有人把它当成可以写进 ESP 的那一份。
    if "/boot/dtb" in body.split("%changelog")[0]:
        failures.append("kernel-piano.spec still ships the raw vendor DTB under /boot/dtb")

    # An empty `touch` of the kernel image is the signature of the stub build.
    if re.search(r"^\s*touch\s+%\{buildroot\}/boot/vmlinuz", body, re.MULTILINE):
        failures.append("kernel-piano.spec still `touch`es an empty vmlinuz")

    # rpm's brp-strip rewrites .symtab inside every .ko. The RPM would then stop
    # matching the sealed kernel artifact that build-image verifies, and the
    # difference is invisible until the image build fails much later.
    if not re.search(r"^%global\s+__strip\s+/bin/true\s*$", body, re.MULTILINE):
        failures.append("kernel-piano.spec does not disable strip; modules would "
                        "differ from the sealed kernel artifact")

    # DNF 的 installonlypkgs 按包名或 provides 匹配。内核模块目录只由编译期
    # release 决定，RPM Release 变化不改变目录名；提供任何一个 installonly
    # 名字，dnf 就会把新 Release 当“再装一份”，在安装阶段撞 file conflict。
    for provided in ("kernel", "kernel-core", "kernel-modules", "kernel-modules-core"):
        if re.search(rf"^Provides:\s+{re.escape(provided)}\s*=", body, re.MULTILINE):
            failures.append(
                f"kernel-piano.spec provides {provided}; dnf would treat a Release "
                "bump as installonly and collide on the shared module tree")

    # 设备侧内核升级：%posttrans 重建 initramfs 并为新内核写 systemd-boot
    # 启动项（BLS 布局，可选 UKI）。镜像构建（dnf --installroot）里没有 ESP
    # 载荷，必须先跳过再动手，否则 build-rootfs 会在 chroot 里失败。
    posttrans = body.split("%posttrans", 1)
    if len(posttrans) != 2:
        failures.append("kernel-piano.spec has no %posttrans; a dnf kernel upgrade "
                        "would leave the ESP booting the old Image")
    else:
        script = posttrans[1].split("%changelog", 1)[0]
        for path in ("loader/entries", "EFI/Linux"):
            if path not in script:
                failures.append(f"%posttrans does not name the ESP layout path {path!r}")
        if 'exit 0' not in script:
            failures.append("%posttrans does not skip a rootfs with no ESP payload")
        skip = script.split("exit 0", 1)[0]
        if "depmod" in skip or "piano-kernel-esp-update" in skip:
            failures.append("%posttrans must skip before depmod/repack when the ESP is absent")
        for token in ("depmod", "piano-kernel-esp-update"):
            if token not in script:
                failures.append(f"%posttrans never runs {token}")
        # manifest 名字带 NVRA。盘上留下多份时，%posttrans 必须点名刚装上的
        # 那一份，否则工具会拒绝在多份之间猜。
        if "--manifest" not in script:
            failures.append("%posttrans does not name the manifest it just installed")

    # 实机教训（2026-10-22）：manifest 曾是固定路径 /usr/share/piano/
    # kernel-manifest.json。两个包同时拥有该路径时，rpm 报 "file ... conflicts
    # with file from package" 并中止整个事务，设备上的内核因此永远升不上去。
    # 名字必须带 NVRA。
    named = "%{_datadir}/piano/kernel-manifest-%{version}-%{release}.json"
    if named not in body:
        failures.append("kernel-piano.spec does not ship an NVRA-named kernel manifest; "
                        "installonly upgrades would collide on a fixed path")
    if re.search(r"^\s*%\{_datadir\}/piano/kernel-manifest\.json\s*$", body, re.MULTILINE):
        failures.append("kernel-piano.spec still owns the fixed kernel-manifest.json path")
    if body.count(named) < 3:
        failures.append("kernel-piano.spec must use the NVRA-named manifest in %install, "
                        "%files and %posttrans alike")

    # 内核升级所需的那套工具必须随本包发布，且路径与 %posttrans 里的一致。
    for name in ("f2fs-disk-bootstrap", "piano-kernel-esp-update",
                 "build-initramfs.py", "piano_artifacts.py"):
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
    # UKI 重打需要 ukify 与 aa64 stub；缺任何一个设备侧升级都写不了 ESP。
    for dependency in ("systemd-ukify", "systemd-boot-unsigned"):
        if f"Requires:       {dependency}" not in body:
            failures.append(f"%posttrans needs {dependency} at runtime")


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
        failures.append("build-rpms.sh does not resolve the composed board DTB")
    if not re.search(r"compose-board-dtb\.py\"?\s+--verify", body):
        failures.append("build-rpms.sh does not verify the board DTB against its manifest")
    if not re.search(r"resolve_board_dtb\)\s*\|\|\s*\{", body):
        failures.append("build-rpms.sh does not fail when no composed board DTB is found")
    # 裸的 vendor DTB 写进 ESP 设备起不来；构建脚本不能再有拿它兜底的路径。
    if "vendor/piano-linux/board.dtb" in body:
        failures.append("build-rpms.sh can still package the raw vendor board DTB")

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
    dtb = [f for f in files if f == "/usr/lib/piano/board.dtb"]
    if not dtb or "/usr/lib/piano/board-dtb.json" not in files:
        failures.append(f"{rpm.name} ships no composed board DTB with its manifest")
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


def load_esp_update():
    loader = importlib.machinery.SourceFileLoader(
        "piano_kernel_esp_update", str(ROOT / "scripts" / "piano-kernel-esp-update"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def fake_fdt(tag):
    """更新器只看 FDT 头：魔数与 totalsize。"""
    body = tag.ljust(32, b"\0")
    return b"\xd0\x0d\xfe\xed" + (len(body) + 8).to_bytes(4, "big") + body


def packaged_root(root, data, sealed=None):
    piano = root / "usr" / "lib" / "piano"
    piano.mkdir(parents=True, exist_ok=True)
    (piano / "board.dtb").write_bytes(data)
    (piano / "board-dtb.json").write_text(json.dumps(
        {"sha256": hashlib.sha256(data if sealed is None else sealed).hexdigest()}))


def bls_entry(esp, version, dtb=b"old-dtb"):
    payload = esp / "linux" / version
    payload.mkdir(parents=True)
    for name, data in (("linux", b"old-image"), ("initrd", b"old-initrd"), ("board.dtb", dtb)):
        (payload / name).write_bytes(data)
    entry = esp / "loader" / "entries" / f"linux-{version}.conf"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        f"title Linux\nversion {version}\nlinux /linux/{version}/linux\n"
        f"initrd /linux/{version}/initrd\ndevicetree /linux/{version}/board.dtb\n"
        f"options root=LABEL=PIANOROOT rw\n", encoding="utf-8")
    return entry


def check_packaged_dtb(failures):
    """板级 DTB 来自内核包。旧 DTB 配新内核会缺节点，残缺的 DTB 起不来。"""
    updater = load_esp_update()
    good = fake_fdt(b"new-dtb")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        packaged_root(root, good)
        if updater.packaged_dtb(root).read_bytes() != good:
            failures.append("packaged_dtb does not return the DTB shipped in the package")
        for label, data, sealed in (("a digest mismatch", good, b"other"),
                                    ("a truncated DTB", good[:-4], None),
                                    ("a file that is not a DTB", b"not-a-dtb".ljust(40, b"\0"), None)):
            packaged_root(root, data, sealed)
            try:
                updater.packaged_dtb(root)
            except updater.EspUpdateError:
                continue
            failures.append(f"packaged_dtb accepts {label}")
        (root / "usr/lib/piano/board-dtb.json").unlink()
        try:
            updater.packaged_dtb(root)
        except updater.EspUpdateError:
            pass
        else:
            failures.append("packaged_dtb accepts a DTB without its manifest")

    # update() 整条路径：新条目里的 DTB 是包里那一份，不是旧条目里的。
    real_inspect, real_build = updater.inspect_installed_kernel, updater.build_initramfs
    with tempfile.TemporaryDirectory() as tmp:
        root, esp = Path(tmp) / "root", Path(tmp) / "esp"
        packaged_root(root, good)
        image = root / "vmlinuz"
        image.write_bytes(b"new-image")
        bls_entry(esp, "7.3.3")
        built = []

        def build(_root, output, manifest=None):
            output.mkdir(parents=True)
            (output / "initramfs.cpio.gz").write_bytes(b"new-initrd")
            built.append(output)
            return {"kernel_manifest_sha256": "sealed", "initramfs_sha256": "x",
                    "initramfs_bytes": 10, "modules": []}

        updater.inspect_installed_kernel = lambda _root, manifest=None: (
            {"kernel_release": "7.3.4"}, "sealed", image)
        updater.build_initramfs = build
        try:
            record = updater.update(root, esp)
            written = esp / "linux" / "7.3.4" / "board.dtb"
            if not written.is_file() or written.read_bytes() != good:
                failures.append("update() does not write the packaged board DTB into the new entry")
            if (esp / "linux" / "7.3.3" / "board.dtb").read_bytes() != b"old-dtb":
                failures.append("update() touched the previous generation's board DTB")
            if record.get("board_dtb", {}).get("sha256") != hashlib.sha256(good).hexdigest():
                failures.append("update() does not record the board DTB it wrote")
            # 包里的 DTB 不可用时在碰 ESP 之前就停：不重建 initramfs，不写条目。
            packaged_root(root, good[:-4])
            before = sorted(str(p.relative_to(esp)) for p in esp.rglob("*"))
            built.clear()
            try:
                updater.update(root, esp)
            except updater.EspUpdateError:
                pass
            else:
                failures.append("update() proceeds with an unusable packaged board DTB")
            if built or sorted(str(p.relative_to(esp)) for p in esp.rglob("*")) != before:
                failures.append("update() touched the ESP before rejecting the board DTB")
        finally:
            updater.inspect_installed_kernel, updater.build_initramfs = real_inspect, real_build


def check_same_release_rewrite(failures):
    """同一个 release 换 DTB：条目正指着这个目录，任何时刻三个文件都得在。

    整目录换名的写法会在两次 rename 之间留下条目悬空的窗口，此时断电设备
    起不来，而 ESP 上只有这一代。
    """
    updater = load_esp_update()
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp) / "esp"
        entry = bls_entry(esp, "7.3.4")
        target = esp / "linux" / "7.3.4"
        sources = Path(tmp) / "src"
        sources.mkdir()
        image, initramfs, dtb = sources / "image", sources / "initrd", sources / "dtb"
        image.write_bytes(b"old-image")
        initramfs.write_bytes(b"new-initrd")
        dtb.write_bytes(b"new-dtb")
        fields = updater.read_entry(entry)
        real = updater.install_payload_file
        calls = []

        def interrupted(source, destination):
            calls.append(destination.name)
            if len(calls) == 2:
                raise OSError("power lost between two files")
            real(source, destination)

        updater.install_payload_file = interrupted
        try:
            updater.update_bls(esp, fields, image, initramfs, dtb, "7.3.4", False, {"changed": {}})
        except OSError:
            pass
        else:
            failures.append("the simulated interruption did not reach update_bls")
        finally:
            updater.install_payload_file = real
        if calls[:1] != ["initrd"]:
            failures.append(f"an unchanged kernel image was rewritten in place: {calls}")
        state = {name: (target / name).read_bytes() if (target / name).is_file() else None
                 for name in ("linux", "initrd", "board.dtb")}
        if state != {"linux": b"old-image", "initrd": b"new-initrd", "board.dtb": b"old-dtb"}:
            failures.append(f"an interrupted same-release rewrite left {state}")
        record = {"changed": {}}
        moved, real_rename = [], Path.rename

        def rename(self, destination):
            moved.append(self)
            return real_rename(self, destination)

        Path.rename = rename
        try:
            updater.update_bls(esp, fields, image, initramfs, dtb, "7.3.4", False, record)
        finally:
            Path.rename = real_rename
        if target in moved:
            failures.append("a same-release rewrite renamed the directory the entry points at")
        if (target / "board.dtb").read_bytes() != b"new-dtb" \
                or (target / "initrd").read_bytes() != b"new-initrd":
            failures.append("a same-release rewrite did not land the new DTB and initramfs")
        leftovers = sorted(p.name for p in (esp / "linux").rglob("*") if p.name.startswith("."))
        if leftovers:
            failures.append(f"a same-release rewrite left temporary files: {leftovers}")
        if record["changed"].get("payload") is not True:
            failures.append("a DTB-only change was not reported as a changed payload")
    # 写到一半失败（这里让 fsync 报错）不留临时文件，条目引用的文件原样。
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp) / "esp"
        entry = bls_entry(esp, "7.3.4")
        target = esp / "linux" / "7.3.4"
        source = Path(tmp) / "new"
        source.write_bytes(b"new-dtb")
        real_fsync = updater.fsync_path

        def failing(path):
            raise OSError("no space left on device")

        updater.fsync_path = failing
        try:
            updater.update_bls(esp, updater.read_entry(entry), target / "linux", target / "initrd",
                               source, "7.3.4", False, {"changed": {}})
        except OSError:
            pass
        else:
            failures.append("a failed payload write was not reported")
        finally:
            updater.fsync_path = real_fsync
        if sorted(p.name for p in target.iterdir()) != ["board.dtb", "initrd", "linux"] \
                or (target / "board.dtb").read_bytes() != b"old-dtb":
            failures.append("a failed payload write left "
                            f"{sorted(p.name for p in target.iterdir())} in the live directory")
    # 同 release 重写没有新增一代：ESP 上已有的上一代不能被当成更早的历史清掉。
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp) / "esp"
        bls_entry(esp, "7.3.3")
        entry = bls_entry(esp, "7.3.4")
        sources = Path(tmp) / "src"
        sources.mkdir()
        for name in ("image", "initrd", "dtb"):
            (sources / name).write_bytes(b"new-" + name.encode())
        record = {"changed": {}}
        updater.update_bls(esp, updater.read_entry(entry), sources / "image", sources / "initrd",
                           sources / "dtb", "7.3.4", False, record, running="7.3.4")
        if sorted(p.name for p in (esp / "linux").iterdir()) != ["7.3.3", "7.3.4"] \
                or not (esp / "loader/entries/linux-7.3.3.conf").is_file():
            failures.append("a same-release rewrite removed the previous generation")
        if record.get("history", {}).get("removed"):
            failures.append(f"a same-release rewrite pruned {record['history']['removed']}")


def check_esp_layout(failures):
    """出厂是 BLS 布局：写出的条目要能被 systemd-boot 读，cmdline 沿用现有条目。"""
    updater = load_esp_update()
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp)
        entries = esp / "loader" / "entries"
        entries.mkdir(parents=True)
        (esp / "linux" / "7.3.3").mkdir(parents=True)
        (esp / "linux" / "7.3.3" / "linux").write_bytes(b"old-image")
        source_entry = entries / "linux-7.3.3.conf"
        source_entry.write_text(
            "title Linux\nversion 7.3.3\nlinux /linux/7.3.3/linux\n"
            "initrd /linux/7.3.3/initrd\ndevicetree /linux/7.3.3/board.dtb\n"
            "options root=LABEL=PIANOROOT rw\n", encoding="utf-8")
        dtb = esp / "linux" / "7.3.3" / "board.dtb"
        dtb.write_bytes(b"board-dtb")
        layout, entry, fields = updater.current_boot(esp)
        if layout != "bls" or entry != source_entry:
            failures.append(f"current_boot found {layout!r} at {entry}, not the BLS entry")
            return
        if fields.get("options") != "root=LABEL=PIANOROOT rw":
            failures.append("current_boot does not preserve the BLS options line")
        image = esp / "image.src"
        image.write_bytes(b"new-image")
        initramfs = esp / "initramfs.src"
        initramfs.write_bytes(b"new-initrd")
        record = {"changed": {}}
        updater.update_bls(esp, fields, image, initramfs, dtb, "7.3.4", False, record)
        target = esp / "linux" / "7.3.4"
        expected_entry = (
            "title Linux\nversion 7.3.4\nlinux /linux/7.3.4/linux\n"
            "initrd /linux/7.3.4/initrd\ndevicetree /linux/7.3.4/board.dtb\n"
            "options root=LABEL=PIANOROOT rw\n")
        written = entries / "linux-7.3.4.conf"
        if not written.is_file() or written.read_text(encoding="utf-8") != expected_entry:
            failures.append("update_bls does not write the expected loader entry")
        for name, data in (("linux", b"new-image"), ("initrd", b"new-initrd"),
                           ("board.dtb", b"board-dtb")):
            if not (target / name).is_file() or (target / name).read_bytes() != data:
                failures.append(f"update_bls does not stage /linux/7.3.4/{name}")
        if record["changed"].get("payload") is not True:
            failures.append("update_bls did not report the new payload as changed")
        again = {"changed": {}}
        updater.update_bls(esp, fields, image, initramfs, dtb, "7.3.4", False, again)
        if again["changed"].get("payload") is not False:
            failures.append("update_bls rewrites an unchanged payload")
    # 只留当前与一套回退：更早的 release 的条目与载荷要清掉。
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp)
        entries = esp / "loader" / "entries"
        entries.mkdir(parents=True)
        image = esp / "image.src"; image.write_bytes(b"new-image")
        initramfs = esp / "initramfs.src"; initramfs.write_bytes(b"new-initrd")
        dtb_src = esp / "board.src"; dtb_src.write_bytes(b"board-dtb")
        for version in ("7.3.1", "7.3.2", "7.3.3"):
            (esp / "linux" / version).mkdir(parents=True)
            (esp / "linux" / version / "linux").write_bytes(b"old")
            (esp / "linux" / version / "initrd").write_bytes(b"old")
            (esp / "linux" / version / "board.dtb").write_bytes(b"old")
            (entries / f"linux-{version}.conf").write_text(
                f"title Linux\nversion {version}\nlinux /linux/{version}/linux\n"
                f"initrd /linux/{version}/initrd\ndevicetree /linux/{version}/board.dtb\n"
                f"options root=LABEL=PIANOROOT rw\n", encoding="utf-8")
        # 非 piano 的条目不该被删：linux 路径不在 /linux/<version>/ 下。
        (entries / "linux-foreign.conf").write_text(
            "title Other\nversion other\nlinux /EFI/other/vmlinuz\n"
            "initrd /EFI/other/initrd\ndevicetree /EFI/other/dtb\n"
            "options root=/dev/sda1 rw\n", encoding="utf-8")
        fields = {"options": "root=LABEL=PIANOROOT rw", "version": "7.3.3"}
        record = {"changed": {}}
        updater.update_bls(esp, fields, image, initramfs, dtb_src, "7.3.4", False, record)
        kept = sorted(p.name for p in (esp / "linux").iterdir())
        if kept != ["7.3.3", "7.3.4"]:
            failures.append(f"history retention kept {kept}, expected 7.3.3 and 7.3.4")
        if not (entries / "linux-7.3.3.conf").is_file():
            failures.append("history retention removed the rollback entry")
        if (entries / "linux-7.3.1.conf").exists():
            failures.append("history retention kept an entry older than one generation")
        if not (entries / "linux-foreign.conf").is_file():
            failures.append("history retention removed a non-piano boot entry")
        if sorted(record.get("history", {}).get("removed", [])) != ["7.3.1", "7.3.2"]:
            failures.append(f"history record removed {record.get('history')}")
    # 新内核已写入但起不来时，启动项里最新的是坏内核，正在运行的旧内核
    # 必须留在 keep 里。
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp)
        entries = esp / "loader" / "entries"
        entries.mkdir(parents=True)
        image = esp / "image.src"; image.write_bytes(b"new-image")
        initramfs = esp / "initramfs.src"; initramfs.write_bytes(b"new-initrd")
        dtb_src = esp / "board.src"; dtb_src.write_bytes(b"board-dtb")
        for version in ("7.3.1", "7.3.2", "7.3.3", "7.3.4"):
            (esp / "linux" / version).mkdir(parents=True)
            for name in ("linux", "initrd", "board.dtb"):
                (esp / "linux" / version / name).write_bytes(b"old")
            (entries / f"linux-{version}.conf").write_text(
                f"title Linux\nversion {version}\nlinux /linux/{version}/linux\n"
                f"initrd /linux/{version}/initrd\ndevicetree /linux/{version}/board.dtb\n"
                f"options root=LABEL=PIANOROOT rw\n", encoding="utf-8")
        fields = {"options": "root=LABEL=PIANOROOT rw", "version": "7.3.4"}
        record = {"changed": {}}
        # 7.3.5 已写入但起不来，运行中的是 7.3.3，启动项最新的是坏掉的 7.3.4。
        # 7.3.4（previous）与 7.3.3（running）都在 keep 里；只剩 7.3.1/7.3.2 被清掉。
        record = {"changed": {}}
        updater.update_bls(esp, fields, image, initramfs, dtb_src, "7.3.5", False, record,
                           running="7.3.3")
        kept = sorted(p.name for p in (esp / "linux").iterdir())
        if kept != ["7.3.3", "7.3.4", "7.3.5"]:
            failures.append(f"history retention kept {kept}, expected running 7.3.3 kept")
        if sorted(record.get("history", {}).get("removed", [])) != ["7.3.1", "7.3.2"]:
            failures.append(f"running-kernel retention removed {record.get('history')}")
    # 版本化 UKI 同样只留两代；固定 sunuefi.efi 是固件契约，不能被当历史删掉。
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp)
        (esp / "EFI" / "Linux").mkdir(parents=True)
        (esp / "EFI" / "BOOT").mkdir(parents=True)
        (esp / "EFI" / "BOOT" / "BOOTAA64.EFI").write_bytes(b"boot")
        for version in ("7.3.2", "7.3.3", "7.3.4"):
            (esp / "EFI" / "Linux" / f"linux-{version}.efi").write_bytes(version.encode())
        record = {"changed": {}}
        updater.retain_history(esp, "uki", "7.3.4", "7.3.3", False, record)
        left = sorted(p.name for p in (esp / "EFI" / "Linux").iterdir())
        if left != ["linux-7.3.3.efi", "linux-7.3.4.efi"]:
            failures.append(f"UKI history retention left {left}")
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp)
        (esp / "EFI" / "Linux").mkdir(parents=True)
        fixed = esp / "EFI" / "Linux" / "sunuefi.efi"
        fixed.write_bytes(b"uke")
        record = {"changed": {}}
        updater.retain_history(esp, "uki", "7.3.4", None, False, record)
        if not fixed.is_file():
            failures.append("history retention deleted the fixed sunuefi.efi")
    # ESP 若被改成可选 UKI 布局，检测也认它，不当成“不可启动的系统”。
    # 带 systemd-boot 时它枚举版本化文件。
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp)
        (esp / "EFI" / "Linux").mkdir(parents=True)
        (esp / "EFI" / "BOOT").mkdir(parents=True)
        (esp / "EFI" / "BOOT" / "BOOTAA64.EFI").write_bytes(b"boot")
        (esp / "EFI" / "Linux" / "linux-7.3.3.efi").write_bytes(b"uke")
        layout, entry, _ = updater.current_boot(esp)
        if layout != "uki" or entry.name != "linux-7.3.3.efi":
            failures.append("current_boot does not fall back to the optional UKI layout")
        if updater.uki_target(esp, "7.3.4").name != "linux-7.3.4.efi":
            failures.append("uki_target does not use the versioned name under systemd-boot")
    # 早期单 UKI 固件没有 systemd-boot，只打开固定的 sunuefi.efi。同目录里
    # 版本化文件是旧脚本写下的，固件从不读它；升级必须原地替换固定路径。
    with tempfile.TemporaryDirectory() as tmp:
        esp = Path(tmp)
        (esp / "EFI" / "Linux").mkdir(parents=True)
        fixed = esp / "EFI" / "Linux" / "sunuefi.efi"
        fixed.write_bytes(b"uke")
        (esp / "EFI" / "Linux" / "linux-7.3.3.efi").write_bytes(b"stray")
        layout, entry, _ = updater.current_boot(esp)
        if layout != "uki" or entry != fixed:
            failures.append("current_boot picks a versioned UKI the single-UKI firmware never opens")
        if updater.uki_target(esp, "7.3.4") != fixed:
            failures.append("uki_target does not replace the firmware's fixed sunuefi.efi")


def main():
    failures = []
    check_spec(failures)
    check_tools(failures)
    check_build_script(failures)
    check_esp_layout(failures)
    check_packaged_dtb(failures)
    check_same_release_rewrite(failures)
    check_built_rpm(failures)
    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1
    print("OK: kernel-piano 打包真实内核载荷，空壳 RPM 与静默降级都已封堵")
    return 0


if __name__ == "__main__":
    sys.exit(main())
