====================================================================
  Xiaomi Pad 8 Pro (piano) - Fedora 44 GNOME Linux Flashing Guide
====================================================================

THIS INSTALLER DOES NOT PARTITION A STOCK TABLET.
It only updates a tablet where install-f2fs.py apply has already created
sunuefi_esp and sunuefi_root. For a brand-new or stock GPT/UFS device, stop
here and follow docs/installation.md section 4 first. That flow creates the
partitions, erases userdata, and lets Android initialize it before Linux is
installed. This installer writes Linux to Slot B, so Android must remain on
Slot A.

Requirements:
1. Xiaomi Pad 8 Pro with Bootloader UNLOCKED.
2. Battery above 30%.
3. Fastboot and ADB installed on PC.
4. USB cable connected directly to tablet.

Required Files in this Folder:
- flash-linux.sh / flash-linux.bat (installer scripts)
- README-INSTALL.txt               (this guide)
- boot_sunuefi.img                 (SunUEFI boot image for Slot B)
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
Run prepare-install.yml with the exact deploy-bundle run, download its
root-install artifact, and keep the complete root-install directory here.
Do not copy only the sparse image and do not merge the two SHA256SUMS files.

Flashing Instructions:
1. Put tablet into Fastboot mode:
   Power off -> Hold [Power] + [Volume Down] until FASTBOOT screen appears.
2. Connect tablet to PC via USB.
3. Run the installer:
   - Linux / macOS:
       chmod +x flash-linux.sh
       ./flash-linux.sh
   - Windows:
       Double-click flash-linux.bat
4. Follow the prompt and type YES to flash.
5. Tablet will reboot into Fedora 44 GNOME desktop!

Before step 3, confirm current-slot is "a". If it is "b", stop: the Linux
boot image is written to boot_b and would overwrite Android's active path.

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
                                     (Linux is always written to Slot B)
   fastboot getvar partition-size:sunuefi_root
                                    -> must equal expanded_bytes in
                                       root-install-manifest.json

Step 4 - Flash, one command at a time (each must print OKAY):
   fastboot set_active b
   fastboot flash boot_b boot_sunuefi.img
   fastboot flash sunuefi_esp sunuefi_esp.img
   fastboot flash sunuefi_root root-install/pianoroot.f2fs.sparse.img
   fastboot reboot

Step 5 - Verify after reboot:
   GNOME desktop appears; "uname -r" shows the release kernel;
   "findmnt /" shows an F2FS root; "systemctl --failed" is empty.

Roll back to Android at any time by switching the slot back:
   fastboot set_active a
   fastboot reboot

WARNING - never run these:
   * Erasing dtbo_b: ABL then rejects the whole boot chain with
     "Failed to load/authenticate boot image: Device Error" and the tablet
     sticks in Fastboot.
   * Erasing userdata: destroys all Android user data irreversibly.
   * Erasing boot_b, "fastboot -w", or any write to abl, xbl, tz, modem.
SunUEFI reads its board DTB from vendor_boot_b plus the stock dtbo_b overlay,
so the stock DTBO content must stay in place.

Login Credentials:
- Username: piano
- Password: piano123456

Dual-Boot Switching:
- Linux to Android : Click "Return to Android" in GNOME App Drawer.
- Android to Linux : Use the SunUEFI boot assistant module in KernelSU/SukiSU.
- Boot selection   : Press Volume keys during the initial 3-second logo.
