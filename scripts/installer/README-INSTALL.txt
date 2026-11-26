====================================================================
  Xiaomi Pad 8 Pro (piano) - Fedora Linux Flashing Guide
====================================================================

THIS INSTALLER DOES NOT PARTITION A STOCK TABLET.
It only updates a tablet where install-f2fs.py apply (or manual partitioning)
has already created sunuefi_esp and sunuefi_root. On a stock GPT/UFS tablet,
follow docs/installation.md section 4 first to partition, erase userdata, and
boot Android once before flashing Linux. This installer writes the combined
SunUEFI BOOT to the active slot (boot_a or boot_b); Android stays bootable
from the same slot via the 3-second boot chooser.

Requirements:
1. Xiaomi Pad 8 Pro with Bootloader unlocked and battery above 30%.
2. fastboot and adb installed on the host PC, USB cable connected.
3. zstd and tar to unpack (GNU tar --zstd still shells out to external zstd).
4. mtools (mcopy), only needed if injecting an SSH key with --ssh-key.

Downloading and Unpacking a Release:
Each desktop ships as its own archive; download only the profile you want
(gnome, kde, niri, hyprland, or denial; see docs/desktops.md):
   piano-<tag>-<desktop>.sha256
   piano-<tag>-<desktop>.tar.zst            (or all .part-NN files)

Verify and extract in an empty directory:
   sha256sum -c piano-<tag>-<desktop>.sha256
   cat piano-<tag>-<desktop>.tar.zst.part-* | zstd -dc | tar -x
   sha256sum -c SHA256SUMS
For single-file archives, run:
   zstd -dc piano-<tag>-<desktop>.tar.zst | tar -x
Windows: join parts first ("copy /b ...part-00+...part-01 out.tar.zst"),
then extract with 7-Zip.
On Linux/macOS you can automate download + checks with:
   bash scripts/unpack-release.sh --tag <tag> --desktop <desktop> --dir piano-install

Files in This Staging Folder:
- flash-linux.sh / flash-linux.bat (one-step flashing scripts)
- README-INSTALL.txt               (this guide)
- make-device-boot.sh              (repacks device-boot/ from active slot boot)
- sunuefi_esp.img                  (SunUEFI ESP holding systemd-boot, kernel, DTB)
- PianoUEFI-product.img            (SunUEFI product firmware)
- sunuefi-module.zip               (Android boot-assistant module, matches product)
- module-manifest.json             (binds sunuefi-module.zip to PianoUEFI-product.img)
- uefi-source.json                 (UEFI source pin and patch digests)
- SHA256SUMS                       (checksums matching this folder's files)
- bundle-manifest.json / root-image-manifest.json / source-runs.json
- boot-tools/ / host-boot-tool/ / scripts/
- root-install/
  - pianoroot.f2fs.sparse.img      (prepared sparse F2FS root)
  - root-install-manifest.json
  - SHA256SUMS                     (root-install checksums)
- device-boot/boot_linux.img       (generated locally by make-device-boot.sh)

When assembling from CI Actions instead of a Release, extract
deploy-bundle-<desktop> here, extract root-install-<desktop> into
root-install/, and copy flash-linux.sh, flash-linux.bat, make-device-boot.sh,
scripts/prepare-device-boot.py, scripts/piano_artifacts.py, and
scripts/piano_sparse.py from the repo. Keep both SHA256SUMS files intact.

One-Step Flashing:
Before flashing, verify current-slot is "a" or "b" matching root-install's
android_slot (release archives target _a; Slot B devices must run
prepare-install.yml with android_slot=_b first, never switch slots).

1. Power off -> Hold [Power] + [Volume Down] for Fastboot and connect USB.
2. Run the script in this folder:
   - Linux / macOS:
       chmod +x flash-linux.sh
       ./flash-linux.sh
       # Or inject an SSH public key for user piano:
       ./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub
   - Windows: double-click flash-linux.bat (use mcopy manually if needed)
3. Type YES when prompted; the tablet flashes and reboots into the desktop.

SSH key injection writes /piano-ssh-key.pub inside sunuefi_esp.img after
checksums pass, changing the ESP digest (use the newly printed digest if you
recheck or re-extract a clean copy before rerunning).

Manual Flashing (equivalent to the one-step script):
1. Verify checksums (on macOS use "shasum -a 256 -c SHA256SUMS"):
     sha256sum -c SHA256SUMS
     (cd root-install && sha256sum -c SHA256SUMS)
2. Build device-boot/boot_linux.img (requires root adb in Android):
     ./make-device-boot.sh
3. Check device identity & target root capacity (expanded_bytes in manifest):
     fastboot devices
     fastboot getvar product                    # must be "piano"
     fastboot getvar unlocked                   # must be "yes"
     fastboot getvar is-userspace               # must be "no" (stock ABL)
     fastboot getvar current-slot               # must be "a" or "b" for this first-install path
     fastboot getvar partition-size:sunuefi_root
4. Flash with -S 128M (stock ABL hangs on raw writes over 128 MiB):
     SLOT=$(fastboot getvar current-slot 2>&1 | awk '/^current-slot:/{print $2}')
     fastboot -S 128M flash "boot_${SLOT}" device-boot/boot_linux.img
     fastboot -S 128M flash sunuefi_esp sunuefi_esp.img
     fastboot -S 128M flash sunuefi_root root-install/pianoroot.f2fs.sparse.img
     fastboot reboot

Do NOT add os_version or re-sign boot_linux.img (the Android boot-assistant
compares the header byte-for-byte). Do NOT flash sunuefi_esp.img to boot_a or
boot_b: systemd-boot loads the kernel from /linux/<release>/ inside ESP, so the
ESP image is not a device boot image.

5. Post-boot check & login:
   Confirm the chosen desktop loads, "uname -r" matches the release kernel,
   "findmnt /" shows f2fs, and "systemctl --failed" is empty.
   Default credentials: user "piano", password "piano123456" (run "passwd"
   immediately after login).

Android-side Boot-Assistant Module:
Install sunuefi-module.zip in KernelSU/SukiSU and reboot so Android matches
PianoUEFI-product.img (carries UFS and DWC3 fixes missing from upstream).

NEVER RUN THESE COMMANDS:
- Erasing dtbo_a or dtbo_b (ABL rejects the boot chain with Device Error)
- Erasing userdata (destroys Android user data)
- Erasing boot_a/boot_b, running "fastboot -w", or touching abl/xbl/tz/modem

Dual-Boot Switching:
- Linux to Android : "Return to Android" app icon or "piano-next-boot android --reboot"
- Android to Linux : SunUEFI boot-assistant module in KernelSU/SukiSU
- Boot menu        : Press a volume key during the 3-second boot chooser

Credits and Licenses:
Boot chain, kernel packaging, and DT overlays come from or are ported from
Project SunUEFI (https://github.com/slwyts/Project-SunUEFI). Each file keeps
its own copyright/license notices; NOTICE lists every upstream item by item
and LICENSE covers repository code.
