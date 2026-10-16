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
REM "usebackq" is required: a plain quoted string is read as a literal filename.
set DEVICE_FOUND=
for /f "usebackq tokens=1" %%i in ("%TEMP%\piano_fb_devices.txt") do (
    if not defined DEVICE_FOUND set DEVICE_FOUND=%%i
)
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

REM 5. Integrity verification against SHA256SUMS
if not exist "SHA256SUMS" (
    echo [ERROR] SHA256SUMS not found next to the images.
    echo Refusing to flash unverified images. Re-download the Release bundle.
    pause
    exit /b 1
)
echo [INFO] Verifying image integrity with SHA256SUMS...
for %%F in ("%BOOT_IMG%" "%ESP_IMG%" "%ROOT_IMG%") do (
    call :verify_one "%%~nxF" "%%~fF"
    if errorlevel 1 (
        pause
        exit /b 1
    )
)
echo [INFO] All image checksums match.
echo.

REM 6. Safety confirmation
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

:verify_one
REM %1 = file name as listed in SHA256SUMS, %2 = full path
setlocal enabledelayedexpansion
set "EXPECTED="
for /f "usebackq tokens=1,2" %%a in ("SHA256SUMS") do (
    set "NAME=%%b"
    set "NAME=!NAME:*/=!"
    if /i "!NAME!"=="%~1" if not defined EXPECTED set "EXPECTED=%%a"
)
REM An extracted .img is not listed; the manifest lists the .zst it came from.
if not defined EXPECTED (
    set "ARCHIVE=%~1.zst"
    for /f "usebackq tokens=1,2" %%a in ("SHA256SUMS") do (
        set "NAME=%%b"
        set "NAME=!NAME:*/=!"
        if /i "!NAME!"=="!ARCHIVE!" if not defined EXPECTED set "EXPECTED=%%a"
    )
    if defined EXPECTED (
        if not exist "%~2.zst" (
            echo [ERROR] %~1 is not listed in SHA256SUMS and %~1.zst is missing.
            echo Keep the downloaded .zst next to the extracted image so it can be verified.
            endlocal & exit /b 1
        )
        echo [INFO] Verifying the source archive %~1.zst for %~1...
        call :verify_hash "%~1.zst" "%~2.zst" "!EXPECTED!"
        endlocal & exit /b %ERRORLEVEL%
    )
)
if not defined EXPECTED (
    echo [ERROR] %~1 is not listed in SHA256SUMS. Re-download the Release bundle.
    endlocal & exit /b 1
)
call :verify_hash "%~1" "%~2" "!EXPECTED!"
endlocal & exit /b %ERRORLEVEL%

:verify_hash
REM %1 = label, %2 = full path, %3 = expected hash
setlocal enabledelayedexpansion
set "ACTUAL="
for /f "skip=1 tokens=* delims=" %%h in ('certutil -hashfile "%~2" SHA256 ^| findstr /r /v "^CertUtil"') do (
    if not defined ACTUAL set "ACTUAL=%%h"
)
set "ACTUAL=!ACTUAL: =!"
if /i not "!ACTUAL!"=="%~3" (
    echo [ERROR] SHA256 mismatch for %~1
    echo   Expected: %~3
    echo   Actual  : !ACTUAL!
    endlocal & exit /b 1
)
echo   OK %~1
endlocal & exit /b 0
