@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
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
set "DEVICE_LIST_FILE=%TEMP%\piano_fb_devices_%RANDOM%%RANDOM%.txt"
fastboot devices > "%DEVICE_LIST_FILE%" 2>nul
REM "usebackq" is required: a plain quoted string is read as a literal filename.
set DEVICE_FOUND=
set DEVICE_COUNT=0
set DEVICE_NUMBERS=
for /f "usebackq tokens=1" %%i in ("%DEVICE_LIST_FILE%") do (
    set /a DEVICE_COUNT+=1
    if not defined DEVICE_FOUND set DEVICE_FOUND=%%i
    set "DEVICE_NUMBERS=!DEVICE_NUMBERS! %%i"
)
del "%DEVICE_LIST_FILE%" >nul 2>nul
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
if not "%DEVICE_COUNT%"=="1" (
    echo [ERROR] %DEVICE_COUNT% Fastboot devices detected.
    echo Disconnect every other device; flashing the wrong one is unrecoverable.
    echo Connected serials:%DEVICE_NUMBERS%
    pause
    exit /b 1
)
echo [INFO] Fastboot device detected: %DEVICE_FOUND%

REM Pin every call to the one device that passed the count check.
set "FASTBOOT=fastboot -s %DEVICE_FOUND%"

REM 3. Product check
echo [INFO] Querying product model...
%FASTBOOT% getvar product 2>&1 | findstr /i "product: piano" >nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Device product does not match 'piano'!
    echo Refusing to flash another device model; that can cause a hard brick.
    echo.
    pause
    exit /b 1
) else (
    echo [INFO] Product check passed: piano (Xiaomi Pad 8 Pro)
)

REM 3b. Bootloader state: stock ABL, not fastbootd, and unlocked.
%FASTBOOT% getvar unlocked 2>&1 | findstr /i "unlocked: yes" >nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Bootloader is not unlocked.
    echo Unlock it through Xiaomi's official process before flashing.
    echo.
    pause
    exit /b 1
)
%FASTBOOT% getvar is-userspace 2>&1 | findstr /i "is-userspace: no" >nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Device is in fastbootd, not the stock bootloader.
    echo Reboot with: fastboot reboot bootloader
    echo.
    pause
    exit /b 1
)

REM 3c. Target partitions must exist before anything is written.
for %%P in (boot_b sunuefi_esp sunuefi_root) do (
    %FASTBOOT% getvar partition-size:%%P 2>&1 | findstr /i "partition-size:%%P:" >nul
    if ERRORLEVEL 1 (
        echo [ERROR] Partition '%%P' was not found on the device.
        echo This tablet has not been partitioned for Linux yet.
        echo.
        pause
        exit /b 1
    )
)
echo [INFO] Target partitions boot_b, sunuefi_esp, sunuefi_root verified.

REM 4. Image file validation
set BOOT_IMG=boot_sunuefi.img
if not exist "%BOOT_IMG%" (
    echo [ERROR] Missing boot image: boot_sunuefi.img!
    pause
    exit /b 1
)

set ESP_IMG=sunuefi_esp.img
if not exist "%ESP_IMG%" (
    echo [ERROR] Missing ESP image: sunuefi_esp.img!
    pause
    exit /b 1
)

set ROOT_IMG=root-install\pianoroot.f2fs.sparse.img
if not exist "%ROOT_IMG%" (
    echo [ERROR] Missing prepared rootfs transport: pianoroot.f2fs.sparse.img
    echo Run prepare-install for this deploy-bundle first and keep the
    echo root-install directory next to this installer.
    echo A sealed pianoroot.f2fs.img.zst is not a first-install image.
    pause
    exit /b 1
)

echo [INFO] Target images found:
echo   - Boot   : %BOOT_IMG%
echo   - ESP    : %ESP_IMG%
echo   - RootFS : %ROOT_IMG%
echo.

REM 5. Integrity verification: deploy-bundle for ESP/BOOT, root-install for root.
if not exist "SHA256SUMS" (
    echo [ERROR] SHA256SUMS not found next to the images.
    echo Refusing to flash unverified images. Re-download the Release bundle.
    pause
    exit /b 1
)
if not exist "root-install\SHA256SUMS" (
    echo [ERROR] root-install\SHA256SUMS not found.
    echo Download the root-install artifact from prepare-install and keep its
    echo SHA256SUMS beside pianoroot.f2fs.sparse.img.
    pause
    exit /b 1
)
echo [INFO] Verifying deploy-bundle and root-install integrity...
for %%F in ("%BOOT_IMG%" "%ESP_IMG%") do (
    call :verify_one "%%~nxF" "%%~fF" "SHA256SUMS"
    if errorlevel 1 goto error
)
call :verify_one "pianoroot.f2fs.sparse.img" "%ROOT_IMG%" "root-install\SHA256SUMS"
if errorlevel 1 goto error
call :verify_one "root-install-manifest.json" "root-install\root-install-manifest.json" "root-install\SHA256SUMS"
if errorlevel 1 goto error

REM Bind root-install to this exact deploy-bundle.
call :verify_provenance
if errorlevel 1 (
    pause
    exit /b 1
)
echo [INFO] root-install provenance matches this deploy-bundle.

REM The manifest transport digest must match the sparse file itself. This is
REM checked in addition to root-install\SHA256SUMS so a rewritten manifest cannot
REM silently point at different bytes.
call :verify_transport_digest
if errorlevel 1 (
    pause
    exit /b 1
)

REM Slot and exact capacity are part of the same pre-write contract.
where powershell >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PowerShell was not found.
    echo It is required for JSON manifest, slot and capacity checks.
    pause
    exit /b 1
)
call :verify_slot_and_capacity
if errorlevel 1 (
    pause
    exit /b 1
)

echo [INFO] All image checksums match.
echo.

REM 6. Safety confirmation
echo ====================================================================
echo                  ⚠️  CRITICAL SAFETY WARNING ⚠️
echo ====================================================================
echo 1. This will flash Linux SunUEFI into Slot B (boot_b only).
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

echo --^> Step 1/4: Setting active slot to B...
%FASTBOOT% set_active b
if %ERRORLEVEL% neq 0 goto error

echo --^> Skipping dtbo_b: keeping the stock DTBO for the active slot.
echo     Qualcomm ABL rejects an empty DTBO with "Device Error"; SunUEFI reads
echo     its board DTB from vendor_boot_b plus the stock dtbo_b overlay.

echo --^> Step 2/4: Flashing SunUEFI bootloader to boot_b...
%FASTBOOT% flash boot_b "%BOOT_IMG%"
if %ERRORLEVEL% neq 0 goto error

echo --^> Step 3/4: Flashing SunUEFI ESP to sunuefi_esp...
%FASTBOOT% flash sunuefi_esp "%ESP_IMG%"
if %ERRORLEVEL% neq 0 goto error

echo --^> Step 4/4: Flashing Fedora 44 RootFS to sunuefi_root...
%FASTBOOT% flash sunuefi_root "%ROOT_IMG%"
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
%FASTBOOT% reboot
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
REM %1 = file name as listed in SHA256SUMS, %2 = full path, %3 = checksum file
setlocal enabledelayedexpansion
set "EXPECTED="
for /f "usebackq tokens=1,2" %%a in ("%~3") do (
    set "NAME=%%b"
    set "NAME=!NAME:*:=!"
    set "NAME=!NAME:.\=!"
    for %%N in ("!NAME!") do set "NAME=%%~nxN"
    if /i "!NAME!"=="%~1" if not defined EXPECTED set "EXPECTED=%%a"
)
if not defined EXPECTED (
    echo [ERROR] %~1 is not listed in %~3. Re-download the artifact.
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

:verify_provenance
setlocal enabledelayedexpansion
set "SOURCE_BUNDLE="
set "SOURCE_ROOT="
for /f "usebackq tokens=*" %%v in (`powershell -NoProfile -Command "(Get-Content -Raw 'root-install\root-install-manifest.json' | ConvertFrom-Json).source_bundle_manifest_sha256"`) do (
    set "SOURCE_BUNDLE=%%v"
)
for /f "usebackq tokens=*" %%v in (`powershell -NoProfile -Command "(Get-Content -Raw 'root-install\root-install-manifest.json' | ConvertFrom-Json).source_root_manifest_sha256"`) do (
    set "SOURCE_ROOT=%%v"
)
if not defined SOURCE_BUNDLE (
    echo [ERROR] root-install-manifest.json lacks source_bundle_manifest_sha256.
    endlocal & exit /b 1
)
if not defined SOURCE_ROOT (
    echo [ERROR] root-install-manifest.json lacks source_root_manifest_sha256.
    endlocal & exit /b 1
)

set "BUNDLE_MANIFEST_SHA="
for /f "skip=1 tokens=* delims=" %%h in ('certutil -hashfile "bundle-manifest.json" SHA256 ^| findstr /r /v "^CertUtil"') do (
    if not defined BUNDLE_MANIFEST_SHA set "BUNDLE_MANIFEST_SHA=%%h"
)
set "BUNDLE_MANIFEST_SHA=!BUNDLE_MANIFEST_SHA: =!"
set "ROOT_MANIFEST_SHA="
for /f "skip=1 tokens=* delims=" %%h in ('certutil -hashfile "root-image-manifest.json" SHA256 ^| findstr /r /v "^CertUtil"') do (
    if not defined ROOT_MANIFEST_SHA set "ROOT_MANIFEST_SHA=%%h"
)
set "ROOT_MANIFEST_SHA=!ROOT_MANIFEST_SHA: =!"

if /i not "!SOURCE_BUNDLE!"=="!BUNDLE_MANIFEST_SHA!" (
    echo [ERROR] root-install comes from a different deploy-bundle.
    echo   deploy-bundle manifest: !BUNDLE_MANIFEST_SHA!
    echo   root-install source   : !SOURCE_BUNDLE!
    endlocal & exit /b 1
)
if /i not "!SOURCE_ROOT!"=="!ROOT_MANIFEST_SHA!" (
    echo [ERROR] root-install source root manifest differs from this bundle.
    echo   deploy-bundle root manifest: !ROOT_MANIFEST_SHA!
    echo   root-install source        : !SOURCE_ROOT!
    endlocal & exit /b 1
)
endlocal & exit /b 0

:verify_slot_and_capacity
setlocal enabledelayedexpansion
set "ROOT_SLOT="
set "ROOT_EXPANDED_BYTES="
for /f "usebackq tokens=*" %%v in (`powershell -NoProfile -Command "(Get-Content -Raw 'root-install\root-install-manifest.json' | ConvertFrom-Json).android_slot"`) do (
    set "ROOT_SLOT=%%v"
)
for /f "usebackq tokens=*" %%v in (`powershell -NoProfile -Command "(Get-Content -Raw 'root-install\root-install-manifest.json' | ConvertFrom-Json).expanded_bytes"`) do (
    set "ROOT_EXPANDED_BYTES=%%v"
)
if not defined ROOT_SLOT (
    echo [ERROR] root-install-manifest.json lacks android_slot.
    endlocal & exit /b 1
)
if not defined ROOT_EXPANDED_BYTES (
    echo [ERROR] root-install-manifest.json lacks expanded_bytes.
    endlocal & exit /b 1
)

set "CURRENT_SLOT="
for /f "tokens=2 delims=:" %%v in ('%FASTBOOT% getvar current-slot 2^>^&1 ^| findstr /i "current-slot:"') do (
    if not defined CURRENT_SLOT (
        set "CURRENT_SLOT=%%v"
        set "CURRENT_SLOT=!CURRENT_SLOT: =!"
    )
)
if not defined CURRENT_SLOT (
    echo [ERROR] Could not read current-slot from the bootloader.
    endlocal & exit /b 1
)
if /i not "!CURRENT_SLOT!"=="a" (
    echo [ERROR] Android must be active on Slot A before running this installer.
    echo This installer always writes Linux to Slot B (boot_b); continuing from
    echo Android Slot B would overwrite the only Android boot path.
    endlocal & exit /b 1
)
if /i not "!ROOT_SLOT!"=="_a" (
    echo [ERROR] root-install was prepared for Android slot '!ROOT_SLOT!', but this installer requires '_a'.
    echo The first-install path keeps Android on Slot A and writes Linux to Slot B.
    echo Generate root-install with --android-slot _a before flashing.
    endlocal & exit /b 1
)

set "ROOT_PARTITION_SIZE="
for /f "tokens=2 delims=:" %%v in ('%FASTBOOT% getvar partition-size:sunuefi_root 2^>^&1 ^| findstr /i "partition-size:sunuefi_root:"') do (
    if not defined ROOT_PARTITION_SIZE (
        set "ROOT_PARTITION_SIZE=%%v"
        set "ROOT_PARTITION_SIZE=!ROOT_PARTITION_SIZE: =!"
    )
)
if not defined ROOT_PARTITION_SIZE (
    echo [ERROR] Could not read sunuefi_root capacity from the bootloader.
    endlocal & exit /b 1
)
REM Fastboot reports the capacity as hexadecimal without a 0x prefix.
powershell -NoProfile -Command "if ([Convert]::ToUInt64('!ROOT_PARTITION_SIZE!', 16) -ne [Convert]::ToUInt64('!ROOT_EXPANDED_BYTES!', 10)) { exit 1 }"
if errorlevel 1 (
    echo [ERROR] root-install expanded bytes != sunuefi_root partition capacity.
    echo   root-install : !ROOT_EXPANDED_BYTES!
    echo   sunuefi_root : 0x!ROOT_PARTITION_SIZE!
    endlocal & exit /b 1
)
for %%F in ("%ROOT_IMG%") do set "ROOT_SPARSE_BYTES=%%~zF"
if /i "!ROOT_SPARSE_BYTES!"=="!ROOT_EXPANDED_BYTES!" (
    echo [ERROR] root-install is a raw image, not the sparse transport from prepare-install.
    endlocal & exit /b 1
)
echo   OK root-install targets Android slot _a and matches sunuefi_root capacity.
endlocal & exit /b 0

:verify_transport_digest
setlocal enabledelayedexpansion
set "EXPECTED_TRANSPORT="
for /f "usebackq tokens=*" %%v in (`powershell -NoProfile -Command "(Get-Content -Raw 'root-install\root-install-manifest.json' | ConvertFrom-Json).transport.sha256"`) do (
    set "EXPECTED_TRANSPORT=%%v"
)
if not defined EXPECTED_TRANSPORT (
    echo [ERROR] root-install-manifest.json lacks transport.sha256.
    endlocal & exit /b 1
)
set "ACTUAL_TRANSPORT="
for /f "skip=1 tokens=* delims=" %%h in ('certutil -hashfile "%ROOT_IMG%" SHA256 ^| findstr /r /v "^CertUtil"') do (
    if not defined ACTUAL_TRANSPORT set "ACTUAL_TRANSPORT=%%h"
)
set "ACTUAL_TRANSPORT=!ACTUAL_TRANSPORT: =!"
if /i not "!ACTUAL_TRANSPORT!"=="!EXPECTED_TRANSPORT!" (
    echo [ERROR] root-install manifest transport digest does not match the sparse image.
    echo   manifest: !EXPECTED_TRANSPORT!
    echo   actual  : !ACTUAL_TRANSPORT!
    endlocal & exit /b 1
)
echo   OK root-install manifest describes the selected sparse image.
endlocal & exit /b 0
