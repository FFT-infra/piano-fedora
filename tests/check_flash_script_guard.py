#!/usr/bin/env python3
"""回归测试：一键刷机脚本不得擦除或改写引导链关键分区。

实机教训（2026-10-16/17）：`fastboot erase dtbo_b` 会让高通 ABL 拒绝整条
引导链，报 `Failed to load/authenticate boot image: Device Error`，平板卡在
Fastboot。脚本此前带着这条命令，文档还写着“已清空，使用 UEFI 内建 DTB”，
两者都与实测相反。

本测试只读脚本与文档源码，不接触任何设备：

* `flash-linux.sh` / `flash-linux.bat` 不得出现任何 `fastboot erase`，也不得
  出现 `fastboot -w`、`fastboot format`、`flashing` 之类的破坏性命令。
* 两个脚本只允许写 `boot_a`、`sunuefi_esp`、`sunuefi_root` 三个分区，且不得
  调用 `set_active`（合体 BOOT 留在当前槽，不切槽）。
* 安装文档不得再声称擦除活动槽的 dtbo，也不得把 `erase dtbo_a` / `erase dtbo_b`
  写进步骤里。
"""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [ROOT / "scripts/installer/flash-linux.sh",
           ROOT / "scripts/installer/flash-linux.bat"]
INSTALL_DOC = ROOT / "docs/installation.md"
INSTALLER_README = ROOT / "scripts/installer/README-INSTALL.txt"

# Partitions the one-click installer is allowed to write.
ALLOWED_FLASH_TARGETS = {"boot_a", "sunuefi_esp", "sunuefi_root"}

# The scripts route every device write through a wrapper that pins the serial:
# `fb` in shell, `%FASTBOOT%` in batch. Match all three command spellings.
FB = r"(?:fastboot|\bfb\b|%FASTBOOT%)"

# A fastboot invocation that destroys or rewrites boot-chain content. Matching is
# on the command text only, so comments mentioning the forbidden command (which
# the docs and scripts deliberately keep as a warning) do not trip the check.
# `flashing` only counts with a real subcommand, so prose like "fastboot
# flashing!" in an error message is not mistaken for a command.
DESTRUCTIVE = re.compile(
    FB + r"\s+(?:-\S+\s+)*(?:erase|format)\b"
    r"|" + FB + r"\s+(?:-\S+\s+)*-w\b"
    r"|" + FB + r"\s+(?:-\S+\s+)*flashing\s+(?:lock|unlock)\b")

# `fastboot flash <partition>` / `fb flash <partition> "$VAR"`.
FLASH_CALL = re.compile(FB + r"\s+(?:-\S+\s+)*flash\s+([A-Za-z0-9_]+)")


def strip_comments(text, suffix):
    """Drop comment lines so explanatory mentions do not count as commands."""
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if suffix == ".sh" and stripped.startswith("#"):
            continue
        if suffix == ".bat" and (stripped.lower().startswith("rem ")
                                 or stripped.startswith("::")):
            continue
        kept.append(line)
    return "\n".join(kept)


def check_script(path):
    text = path.read_text(encoding="utf-8")
    code = strip_comments(text, path.suffix)
    failures = []

    hit = DESTRUCTIVE.search(code)
    if hit:
        failures.append(f"{path.name}: destructive fastboot command: {hit.group(0)!r}")

    targets = set(FLASH_CALL.findall(code))
    unexpected = targets - ALLOWED_FLASH_TARGETS
    if unexpected:
        failures.append(f"{path.name}: unexpected flash targets {sorted(unexpected)}")
    missing = ALLOWED_FLASH_TARGETS - targets
    if missing:
        failures.append(f"{path.name}: missing flash targets {sorted(missing)}")

    if "set_active" in code:
        failures.append(f"{path.name}: switches slots; the combined BOOT must stay on the active slot")

    # The shell installer must pin the serial for every device write, otherwise a
    # second tablet appearing mid-flash would receive the partitions instead.
    if path.suffix == ".sh":
        if '-s "$FASTBOOT_SERIAL"' not in code:
            failures.append(f"{path.name}: flash commands are not pinned to FASTBOOT_SERIAL")
        # An empty `fastboot devices` output must not abort before the error text.
        if "|| true" not in code:
            failures.append(
                f"{path.name}: device probe lacks '|| true'; set -e would abort silently")
        # Pre-flight: refuse to flash into partitions that do not exist.
        for token in ("partition-size:${name}", "unlocked", "is-userspace"):
            if token not in code:
                failures.append(f"{path.name}: missing pre-flight check {token!r}")
        # --ssh-key writes into the ESP image with mtools. It must not turn into
        # a device write or an erase, and it must stay after the checksum gate.
        for token in ("--ssh-key", "mcopy -o -i", "::/piano-ssh-key.pub"):
            if token not in code:
                failures.append(f"{path.name}: missing SSH key injection piece {token!r}")
        if "mtools" not in code:
            failures.append(f"{path.name}: missing-mcopy error does not name mtools")
        inject = code.find("inject_ssh_key \"$SSH_KEY\"")
        flash_esp = code.find('fb flash sunuefi_esp "$ESP_IMG"')
        checksum = code.find('check_manifest_image "$ESP_IMG"')
        if min(inject, flash_esp, checksum) < 0:
            failures.append(f"{path.name}: SSH key injection or ESP flash step is missing")
        elif not checksum < inject < flash_esp:
            failures.append(
                f"{path.name}: SSH key injection must run after the checksum gate "
                "and before the sunuefi_esp flash")

    return failures


def check_boot_image_selection():
    """boot_a 只能写 prepare-device-boot.py 产出的 boot_linux.img。

    实机教训（2026-10-20）：deploy-bundle 的 boot_sunuefi.img 是 ESP 内部的
    Linux 载荷，Header v2、os_version=0、没有 AVB footer 和 SUNUEFI-NEXTv1
    请求页。SunUEFI 固件能加载它，ABL 不能。把它写进 boot_a，ABL 拒绝整条
    引导链，平板停在 Fastboot，活动槽被标记 unbootable。

    `ab24a6a` 只改了 shell 脚本和文档，Windows 脚本与 ZIP 内的 README 仍把
    boot_sunuefi.img 当 boot_a 镜像刷。这里对三个文件一起加约束，避免再漏一个。
    """
    failures = []
    sh = (ROOT / "scripts/installer/flash-linux.sh").read_text(encoding="utf-8")
    bat = (ROOT / "scripts/installer/flash-linux.bat").read_text(encoding="utf-8")
    readme = INSTALLER_README.read_text(encoding="utf-8")

    # 两个安装器都必须能找到设备镜像。
    for name, text in (("flash-linux.sh", sh), ("flash-linux.bat", bat)):
        if "boot_linux.img" not in text:
            failures.append(f"{name}: does not look for boot_linux.img")

    # Windows 脚本此前把 boot_sunuefi.img 硬编码成 BOOT_IMG，然后原样刷进
    # boot_a。硬编码赋值是最容易复发的形态，单独点出来。
    for lineno, line in enumerate(bat.splitlines(), 1):
        stripped = line.strip().lower()
        if stripped.startswith("set boot_img=boot_sunuefi.img") or (
                stripped.startswith("set \"boot_img=boot_sunuefi.img\"")):
            failures.append(
                f"flash-linux.bat:{lineno}: flashes boot_sunuefi.img to boot_a; "
                "use boot_linux.img from prepare-device-boot.py")

    # 设备镜像不在 deploy-bundle 的 SHA256SUMS 里，不能对它做清单校验；
    # 反过来，boot_sunuefi.img 必须仍然出现在清单校验路径上（它被刷进 ESP）。
    if 'for %%F in ("%BOOT_IMG%" "%ESP_IMG%")' in bat:
        failures.append(
            "flash-linux.bat: verifies boot_linux.img against the deploy-bundle "
            "SHA256SUMS; that image is device-specific and is not listed there")

    # ZIP 内的 README 要写清 boot_a 该用哪个文件，并明确禁止 boot_sunuefi.img。
    if "fastboot flash boot_a device-boot/boot_linux.img" not in readme:
        failures.append(
            "README-INSTALL.txt: manual flash still does not write "
            "device-boot/boot_linux.img to boot_a")
    if "fastboot flash boot_a boot_sunuefi.img" in readme:
        failures.append(
            "README-INSTALL.txt: manual flash still writes boot_sunuefi.img to boot_a")
    if "NEVER flash boot_sunuefi.img to boot_a" not in readme:
        failures.append(
            "README-INSTALL.txt: does not warn that boot_sunuefi.img is not a "
            "boot_a image")

    return failures


def check_docs():
    failures = []
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    doc = INSTALL_DOC.read_text(encoding="utf-8")
    installer_readme = INSTALLER_README.read_text(encoding="utf-8")

    # A stock tablet has no sunuefi_esp/sunuefi_root. The top-level README
    # previously called the one-click installer a three-step first install,
    # which sends users straight to a script that must reject their device.
    for required in ("原始 GPT", "一键脚本不能运行",
                     "docs/installation.md#四-从原始-gptufs-开始的首次安装",
                     "current-slot` 返回 `a`",
                     "build-bundle", "deploy-bundle", "prepare-install",
                     "root-install"):
        if required not in readme:
            failures.append(f"README.md: missing first-install boundary {required!r}")
    if "三步极速刷机" in readme:
        failures.append("README.md: still presents the one-click installer as a stock-device install")
    if "资产文件名以 `scripts/package-release-bundle.sh`" in readme:
        failures.append("README.md: still names the legacy packager as the release authority")

    # The ZIP README travels without docs/installation.md. Its first screen must
    # therefore state the partition prerequisite instead of only saying "flash".
    for required in ("THIS INSTALLER DOES NOT PARTITION A STOCK TABLET.",
                     "sunuefi_esp and sunuefi_root",
                     "follow docs/installation.md section 4 first",
                     "Android stays bootable from the same",
                     'must be "a" for this first-install path',
                     "partition-size:sunuefi_root",
                     "expanded_bytes"):
        if required not in installer_readme:
            failures.append(f"README-INSTALL.txt: missing first-install boundary {required!r}")

    # The architecture diagram and install steps must not claim dtbo_b is erased.
    for bad in ("已清空", "擦除冗余 DTBO"):
        if bad in doc:
            failures.append(f"docs/installation.md: still claims dtbo_b is erased ({bad!r})")

    # Any step list that shows the erase command must be gone. The manual section
    # legitimately names it in a "do not run this" warning, so only flag it when
    # it appears as a bare command line.
    for lineno, line in enumerate(doc.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("fastboot erase dtbo_b"):
            failures.append(
                f"docs/installation.md:{lineno}: still instructs 'fastboot erase dtbo_b'")

    # The manual flash chapter must exist and cover the required ground.
    required = ("手动刷机", "SHA256SUMS", "sha256sum -c", "fastboot flash boot_a",
                "fastboot flash sunuefi_esp", "fastboot flash sunuefi_root")
    for token in required:
        if token not in doc:
            failures.append(f"docs/installation.md: manual-flash chapter missing {token!r}")
    for name in ("flash-linux.sh", "flash-linux.bat", "README-INSTALL.txt"):
        text = (ROOT / "scripts/installer" / name).read_text(encoding="utf-8")
        if "pianoroot.f2fs.sparse.img" not in text:
            failures.append(f"{name}: first install does not require the prepared sparse root")
        if "pianoroot-fedora44-gnome-f2fs.img" in text:
            failures.append(f"{name}: still accepts the sealed non-resized root")

    # The shipped README may name the erase command in a "never run this"
    # warning, so only flag it when it appears as a bare command line.
    for lineno, line in enumerate(installer_readme.splitlines(), 1):
        if line.strip().startswith("fastboot erase dtbo_b"):
            failures.append(
                f"scripts/installer/README-INSTALL.txt:{lineno}: instructs 'fastboot erase dtbo_b'")

    return failures


def main():
    failures = []
    for path in SCRIPTS:
        if not path.exists():
            failures.append(f"missing script: {path}")
            continue
        failures.extend(check_script(path))
    failures.extend(check_docs())
    failures.extend(check_boot_image_selection())

    if failures:
        for item in failures:
            print(f"FAIL: {item}", file=sys.stderr)
        return 1

    print("OK: 一键脚本无 erase/-w/format，只写 boot_a/sunuefi_esp/sunuefi_root 且不切槽；"
          "文档保留原厂 dtbo 且含手动刷机章节")
    return 0


if __name__ == "__main__":
    sys.exit(main())
