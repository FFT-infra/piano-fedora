@echo off
chcp 65001 >nul
title Xiaomi Pad 8 Pro (piano) Fedora 44 Flasher

echo ====================================================================
echo     Xiaomi Pad 8 Pro (piano) - Fedora 44 Linux Flashing Tool        
echo ====================================================================
echo.

REM 1. Fastboot availability check
where fastboot >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] 'fastboot' was not found in your system PATH!
    echo Please download and extract Google Android Platform Tools,
    echo and add it to PATH, or place fastboot.exe in the same folder.
    echo.
    pause
    exit /b 1
)

REM 2. Device connection check
echo [INFO] Checking connected Fastboot devices...
fastboot devices > "%TEMP%\piano_fb_devices.txt" 2>nul
for /f %%i in ("%TEMP%\piano_fb_devices.txt") do set DEVICE_FOUND=%%i
if "%DEVICE_FOUND%"=="" (
    echo [ERROR] No device detected in Fastboot mode!
    echo Please make sure:
    echo   1. The tablet is turned off.
    echo   2. Hold [Power] + [Volume Down] until FASTBOOT screen appears.
    echo   3. USB cable is firmly connected to PC.
    echo.
    pause
    exit /b 1
)
echo [INFO] Fastboot device detected: %DEVICE_FOUND%

REM 3. Product check
echo [INFO] Querying product model...
fastboot getvar product 2>&1 | findstr /i "piano" >nul
if %ERRORLEVEL% neq 0 (
    echo [WARNING] Device product does not match 'piano'!
    echo Flashing onto another device model can cause a hard brick.
    echo.
) else (
    echo [INFO] Product check passed: piano (Xiaomi Pad 8 Pro)
)

REM 4. Image file validation
set BOOT_IMG=boot_sunuefi.img
if not exist "%BOOT_IMG%" (
    if exist "boot.img" set BOOT_IMG=boot.img
)
if not exist "%BOOT_IMG%" (
    echo [ERROR] Missing boot image: boot_sunuefi.img!
    pause
    exit /b 1
)

set ESP_IMG=sunuefi_esp.img
if not exist "%ESP_IMG%" (
    if exist "esp.img" set ESP_IMG=esp.img
)
if not exist "%ESP_IMG%" (
    echo [ERROR] Missing ESP image: sunuefi_esp.img!
    pause
    exit /b 1
)

set ROOT_IMG=pianoroot-fedora44-gnome-f2fs.img
if not exist "%ROOT_IMG%" (
    if exist "pianoroot.f2fs.sparse.img" set ROOT_IMG=pianoroot.f2fs.sparse.img
)
if not exist "%ROOT_IMG%" (
    if exist "pianoroot-fedora44-gnome-f2fs.img.zst" (
        echo [ERROR] Found pianoroot-fedora44-gnome-f2fs.img.zst, but it is compressed!
        echo Please extract it with 7-Zip (with Zstandard plugin) or zstd CLI first.
    ) else (
        echo [ERROR] Missing RootFS image: pianoroot-fedora44-gnome-f2fs.img!
    )
    pause
    exit /b 1
)

echo [INFO] Target images found:
echo   - Boot   : %BOOT_IMG%
echo   - ESP    : %ESP_IMG%
echo   - RootFS : %ROOT_IMG%
echo.

REM 5. Safety confirmation
echo ====================================================================
echo                  ⚠️  CRITICAL SAFETY WARNING ⚠️
echo ====================================================================
echo 1. This will flash Linux SunUEFI into Slot B (boot_b, dtbo_b).
echo 2. This will overwrite 'sunuefi_esp' and 'sunuefi_root' partitions.
echo 3. Android on Slot A is preserved for dual-boot.
echo 4. Ensure Bootloader is UNLOCKED and battery is above 30%%.
echo.
set /p CONFIRM="Type YES in all caps to proceed with flashing: "
if not "%CONFIRM%"=="YES" (
    echo Flashing cancelled by user.
    pause
    exit /b 0
)

echo.
echo ====================================================================
echo                   Starting Flashing Sequence
echo ====================================================================

echo --^> Step 1/5: Setting active slot to B...
fastboot set_active b
if %ERRORLEVEL% neq 0 goto error

echo --^> Step 2/5: Erasing dtbo_b...
fastboot erase dtbo_b
if %ERRORLEVEL% neq 0 goto error

echo --^> Step 3/5: Flashing SunUEFI bootloader to boot_b...
fastboot flash boot_b "%BOOT_IMG%"
if %ERRORLEVEL% neq 0 goto error

echo --^> Step 4/5: Flashing SunUEFI ESP to sunuefi_esp...
fastboot flash sunuefi_esp "%ESP_IMG%"
if %ERRORLEVEL% neq 0 goto error

echo --^> Step 5/5: Flashing Fedora 44 RootFS to sunuefi_root...
fastboot flash sunuefi_root "%ROOT_IMG%"
if %ERRORLEVEL% neq 0 goto error

echo.
echo ====================================================================
echo             🎉 Flashing Completed Successfully! 🎉
echo ====================================================================
echo.
echo System Summary:
echo   - Default user     : piano
echo   - Default password : piano123456
echo   - SunUEFI menu     : Press Volume keys during initial 3-second logo
echo   - Return to Android: Open GNOME App Drawer -^> Click 'Return to Android'
echo.
echo --^> Rebooting tablet into Fedora Linux...
fastboot reboot
echo.
echo Done! Tablet is rebooting.
pause
exit /b 0

:error
echo.
echo [ERROR] An error occurred during fastboot flashing!
echo Please check device connection and error logs above.
pause
exit /b 1
