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

Login Credentials:
- Username: piano
- Password: piano123456

Dual-Boot Switching:
- Linux to Android : Click "Return to Android" in GNOME App Drawer.
- Android to Linux : Use the SunUEFI boot assistant module in KernelSU/SukiSU.
- Boot selection   : Press Volume keys during the initial 3-second logo.
