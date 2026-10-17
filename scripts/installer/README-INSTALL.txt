====================================================================
  Xiaomi Pad 8 Pro (piano) - Fedora 44 GNOME Linux Flashing Guide
====================================================================

Requirements:
1. Xiaomi Pad 8 Pro with Bootloader UNLOCKED.
2. Battery above 30%.
3. Fastboot and ADB installed on PC.
4. USB cable connected directly to tablet.

Required Files in this Folder:
- boot_sunuefi.img                 (SunUEFI boot image for Slot B)
- sunuefi_esp.img                  (SunUEFI EFI System Partition)
- pianoroot-fedora44-gnome-f2fs.img (Fedora 44 RootFS image)
  * If you downloaded pianoroot-fedora44-gnome-f2fs.img.zst,
    extract it using 7-Zip (with Zstandard) or the zstd CLI first.

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

Manual Flashing (no installer script):
The scripts only wrap the commands below. If you prefer to run each step
yourself, or the script does not work on your platform, do this instead.

Step 1 - Verify the downloaded files:
   sha256sum -c SHA256SUMS          (macOS: shasum -a 256 -c SHA256SUMS)
   Run it in the folder holding the images; every line must say OK.

Step 2 - Expand the root filesystem archive:
   zstd -d pianoroot-fedora44-gnome-f2fs.img.zst

Step 3 - Confirm the device before writing anything:
   fastboot devices
   fastboot getvar product          -> must be "piano"
   fastboot getvar unlocked         -> must be "yes"
   fastboot getvar is-userspace     -> must be "no" (stock ABL, not fastbootd)
   fastboot getvar current-slot     -> note it, you need it to roll back

Step 4 - Flash, one command at a time (each must print OKAY):
   fastboot set_active b
   fastboot flash boot_b boot_sunuefi.img
   fastboot flash sunuefi_esp sunuefi_esp.img
   fastboot flash sunuefi_root pianoroot-fedora44-gnome-f2fs.img
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
