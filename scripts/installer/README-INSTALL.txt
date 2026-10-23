====================================================================
  Xiaomi Pad 8 Pro (piano) - Fedora 44 GNOME Linux Flashing Guide
====================================================================

THIS INSTALLER DOES NOT PARTITION A STOCK TABLET.
It only updates a tablet where install-f2fs.py apply has already created
sunuefi_esp and sunuefi_root. For a brand-new or stock GPT/UFS device, stop
here and follow docs/installation.md section 4 first. That flow creates the
partitions, erases userdata, and lets Android initialize it before Linux is
installed. This installer writes the combined SunUEFI BOOT to the active
Slot A; Android stays bootable from the same slot through the chooser.

Requirements:
1. Xiaomi Pad 8 Pro with Bootloader UNLOCKED.
2. Battery above 30%.
3. Fastboot and ADB installed on PC.
4. USB cable connected directly to tablet.
5. zstd and tar, to unpack the archive. GNU tar's --zstd also runs an external
   zstd binary, so zstd cannot be skipped.
   Fedora "sudo dnf install zstd", Ubuntu/Debian "sudo apt install zstd",
   macOS "brew install zstd".
6. mtools (mcopy), only when you install an SSH key with --ssh-key:
   Fedora "sudo dnf install mtools", Ubuntu/Debian "sudo apt install mtools",
   macOS "brew install mtools".

Using a GitHub Release:
Each desktop ships as one self-contained archive. Download the archive for the
desktop you want plus its checksum file, nothing else:
   piano-<tag>-<desktop>.sha256
   piano-<tag>-<desktop>.tar.zst            (or every .part-NN piece)
Put them in an empty folder and run, in this order:
   sha256sum -c piano-<tag>-<desktop>.sha256
   cat piano-<tag>-<desktop>.tar.zst.part-* | zstd -dc | tar -x
   sha256sum -c SHA256SUMS
Omit the .part-* glob when the archive is a single file:
   zstd -dc piano-<tag>-<desktop>.tar.zst | tar -x
The other desktops are separate archives and are not needed. A release can
carry gnome, kde, niri and hyprland. See docs/desktops.md for what each one
provides.
Requires zstd and tar. GNU tar's --zstd also shells out to a zstd binary, so
installing zstd is unavoidable.
Windows: open the .tar.zst with 7-Zip. Join split pieces first:
   copy /b piano-<tag>-<desktop>.tar.zst.part-00+piano-<tag>-<desktop>.tar.zst.part-01 piano-<tag>-<desktop>.tar.zst
On Linux or macOS, unpack-release.sh performs the download, the two checksum
passes and the extraction:
   bash unpack-release.sh --tag <tag> --desktop <desktop> --dir piano-install

Required Files in this Folder:
- flash-linux.sh / flash-linux.bat (installer scripts)
- README-INSTALL.txt               (this guide)
- make-device-boot.sh              (builds device-boot/ from this tablet's BOOT)
- boot_sunuefi.img                 (ESP-internal Linux payload; NOT a boot_a image)
- sunuefi_esp.img                  (SunUEFI EFI System Partition)
- PianoUEFI-product.img            (SunUEFI product firmware; first-install check)
- uefi-source.json                 (UEFI source pin and patch digests)
- SHA256SUMS                       (checksums of everything in this folder)
- bundle-manifest.json             (binds root-install/ to this bundle)
- root-image-manifest.json         (binds root-install/ to this root image)
- source-runs.json                 (run ids behind this bundle; used by install-f2fs.py)
- boot-tools/                      (Android-side selector module and repacker)
- host-boot-tool/                  (x86-64 host repacker)
- scripts/                         (prepare-device-boot.py and its helpers)
- root-install/                    (complete prepare-install artifact)
  - pianoroot.f2fs.sparse.img      (prepared root transport for this tablet)
  - root-install-manifest.json
  - SHA256SUMS                     (root-install checksums)
- device-boot/boot_linux.img       (generated here; not shipped)

SHA256SUMS covers exactly the files above, so the check in Step 1 of "Manual
Flashing" passes without editing it.

Brand-new tablet: this archive does not create sunuefi_esp or sunuefi_root.
Follow docs/installation.md section 4 first: it partitions, erases userdata,
and lets Android boot once. Do not run install-f2fs.py or this installer on a
stock GPT until that is done.

Building from Actions instead of a release: unzip build-bundle's
deploy-bundle-<desktop> artifact into this folder, unzip prepare-install's
root-install-<desktop> artifact into root-install/, then copy flash-linux.sh,
flash-linux.bat, make-device-boot.sh, scripts/prepare-device-boot.py,
scripts/piano_artifacts.py and scripts/piano_sparse.py from the repository.
Do not merge the two artifacts' SHA256SUMS and do not regenerate them.

The archive does not ship the sealed build image or the bundle's own sparse
copy: both hold the same root as root-install/ and neither carries this
device's configuration, so neither can be flashed. If you need one as an
archive copy, run prepare-install.yml yourself or take it from the matching
Actions artifact.

Flashing Instructions:
1. Put tablet into Fastboot mode:
   Power off -> Hold [Power] + [Volume Down] until FASTBOOT screen appears.
2. Connect tablet to PC via USB.
3. Run the installer:
   - Linux / macOS:
       chmod +x flash-linux.sh
       ./flash-linux.sh
     To install an SSH public key for user piano:
       ./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub
   - Windows:
       Double-click flash-linux.bat
4. Follow the prompt and type YES to flash.
5. Tablet will reboot into Fedora 44 GNOME desktop!

The image ships no authorized_keys. With --ssh-key the script writes the key to
/piano-ssh-key.pub inside sunuefi_esp.img after every checksum and manifest
check passes, then flashes that image; piano-first-boot-ssh-key.service imports
it into piano's authorized_keys on first boot and deletes the ESP copy. Without
--ssh-key the documented password login is the only way in.
The key changes the ESP image digest, so a later rerun needs a fresh unpack of
the archive, or you compare readbacks against the digest the installer printed. Windows users inject manually with mcopy before flashing; see the
prompt in flash-linux.bat.

Before step 3, confirm current-slot is "a". If it is "b", stop: this first-
install path targets the stock Android layout on Slot A. Switch back once with
   fastboot set_active a
from stock Fastboot, then rerun. ABL's SwitchPtnSlots swaps the slot type GUIDs
and the UFS boot LUN, so no manual GPT edit is involved.

Manual Flashing (no installer script):
The scripts only wrap the commands below. If you prefer to run each step
yourself, or the script does not work on your platform, do this instead.

Step 1 - Verify the downloaded files:
   sha256sum -c SHA256SUMS
   (cd root-install && sha256sum -c SHA256SUMS)
   (macOS: use "shasum -a 256 -c SHA256SUMS")
   Run these in the staging folder; every line must say OK.

Step 2 - Confirm the prepared root transport exists:
   test -f root-install/pianoroot.f2fs.sparse.img
   Raw or compressed bundle roots cannot be flashed directly.

Step 2b - Build the boot_a image (needs adb with root on the tablet):
   ./make-device-boot.sh
   It reads this tablet's own boot_a through adb, refuses a read that is not
   exactly 96 MiB or that differs from the tablet's own digest, then repacks
   device-boot/boot_linux.img. Nothing is written to the tablet.

Step 3 - Confirm the device before writing anything:
   fastboot devices
   fastboot getvar product          -> must be "piano"
   fastboot getvar unlocked         -> must be "yes"
   fastboot getvar is-userspace     -> must be "no" (stock ABL, not fastbootd)
   fastboot getvar current-slot     -> must be "a" for this first-install path
                                     (the combined BOOT goes to boot_a)
   fastboot getvar partition-size:sunuefi_root
                                    -> must be at least expanded_bytes in
                                       root-install-manifest.json; the root
                                       grows to fill the partition on first boot

Step 4 - Flash, one command at a time (each must print OKAY):
   fastboot flash boot_a device-boot/boot_linux.img
   fastboot flash sunuefi_esp sunuefi_esp.img
   fastboot flash sunuefi_root root-install/pianoroot.f2fs.sparse.img
   fastboot reboot

   boot_a takes device-boot/boot_linux.img, generated once by
   make-device-boot.sh (which calls scripts/prepare-device-boot.py) from this
   tablet's stock BOOT. It keeps the
   upstream unsigned AVB NONE metadata; slot A's ABL boots it directly, the
   same way it boots the stock Android image. Do not add os_version or
   re-sign it: piano-boot-repack compares the wrapper header byte-for-byte,
   and a patched os_version breaks the Android boot-assistant module.
   NEVER flash boot_sunuefi.img to boot_a. That file is the Linux payload inside
   the ESP (Header v2): SunUEFI loads it, the ABL does not, and the tablet then
   sticks in Fastboot with no bootable slot.

Step 5 - Verify after reboot:
   GNOME desktop appears; "uname -r" shows the release kernel;
   "findmnt /" shows an F2FS root; "systemctl --failed" is empty.

Return to Android at any time from the SunUEFI chooser (volume keys during the
3-second logo) or from Linux with "piano-next-boot android --reboot".

WARNING - never run these:
   * Erasing dtbo_a (or dtbo_b): ABL then rejects the whole boot chain with
     "Failed to load/authenticate boot image: Device Error" and the tablet
     sticks in Fastboot.
   * Erasing userdata: destroys all Android user data irreversibly.
   * Erasing boot_a, "fastboot -w", or any write to abl, xbl, tz, modem.
SunUEFI reads its board DTB from vendor_boot_a plus the stock dtbo_a overlay,
so the stock DTBO content must stay in place.

Login Credentials:
- Username: piano
- Password: piano123456

Dual-Boot Switching:
- Linux to Android : Click "Return to Android" in GNOME App Drawer.
- Android to Linux : Use the SunUEFI boot assistant module in KernelSU/SukiSU.
- Boot selection   : Press Volume keys during the initial 3-second logo.
