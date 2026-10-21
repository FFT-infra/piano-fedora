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
5. mtools (mcopy), only when you install an SSH key with --ssh-key:
   Fedora "sudo dnf install mtools", Ubuntu/Debian "sudo apt install mtools",
   macOS "brew install mtools".

Using a GitHub Release:
Download every asset of the release into one empty folder, then:
   sha256sum -c RELEASE-SHA256SUMS
   bash unpack-release.sh
   unzip piano-installer-<tag>.zip
unpack-release.sh merges the >2 GiB pieces, decompresses the zstd root
transports, and leaves the folder in the layout below. The boot tools asset
(piano-boot-tools-<tag>.tar.zst) generates device-boot/boot_linux.img for this
tablet; that image itself is never shipped in the release.

Required Files in this Folder:
- flash-linux.sh / flash-linux.bat (installer scripts)
- README-INSTALL.txt               (this guide)
- boot_sunuefi.img                 (ESP-internal Linux payload; NOT a boot_a image)
- device-boot/boot_linux.img       (boot_a image from prepare-device-boot.py)
- sunuefi_esp.img                  (SunUEFI EFI System Partition)
- SHA256SUMS                       (deploy-bundle BOOT/ESP checksums)
- bundle-manifest.json             (deploy-bundle provenance)
- root-image-manifest.json         (deploy-bundle root provenance)
- root-install/                    (complete prepare-install artifact)
  - pianoroot.f2fs.sparse.img      (prepared root transport for this tablet)
  - root-install-manifest.json
  - SHA256SUMS                     (root-install checksums)

The deploy-bundle's pianoroot.f2fs.img.zst is the sealed 8 GiB build image.
Do NOT decompress and flash it directly: it has not been expanded to the
tablet's sunuefi_root capacity or provisioned with this device's settings.
Release users already have root-install/ after unpack-release.sh. If you are
assembling from Actions instead, run prepare-install.yml with the exact
deploy-bundle run, download its root-install artifact, and keep the complete
root-install directory here.
Do not copy only the sparse image and do not merge the two SHA256SUMS files.

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
the deploy-bundle, or you compare readbacks against the digest the installer
printed. Windows users inject manually with mcopy before flashing; see the
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
   Raw or compressed deploy-bundle roots cannot be flashed directly.

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
   scripts/prepare-device-boot.py from this tablet's stock BOOT. It keeps the
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
