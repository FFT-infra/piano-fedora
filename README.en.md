> **Experimental project.** Built for driver bring-up and hardware debugging, not as a stable daily driver.
> Flashing unlocks the bootloader, modifies the GPT, and overwrites the active `boot` slot and Linux partitions. Back up your tablet and data first. Provided as is under the [MIT license](LICENSE).

# Fedora 45 for Xiaomi Pad 8 Pro (piano)

English | [简体中文](README.md)

[![host-check](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml/badge.svg)](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml)

Native Fedora 45 (F2FS root, Linux 7.3 mainline port) on the Xiaomi Pad 8 Pro (`piano`, Snapdragon 8 Elite SM8750), dual-booted alongside stock HyperOS via [SunUEFI](https://github.com/slwyts/Project-SunUEFI). Switching works in both directions without a host PC.

Project SunUEFI supplies the boot chain, kernel packaging, DT overlays, and base UEFI patches. This repository adds the Fedora backend, end-to-end F2FS support, first-time partitioning, and boot patches. See [Architecture](docs/architecture.md) for the layer split and [NOTICE](NOTICE) for full license attribution.

## Hardware Support

Legend: ✅ Verified on device | ⚠️ Partially working (see notes) | ❌ Not implemented or unverified

| Subsystem | Status | Notes |
| --- | --- | --- |
| Display | ✅ | 3200×2136 @ 120 Hz, Mesa 26.2.7 Turnip (Adreno 830) hardware acceleration |
| Touch | ⚠️ | In-kernel finger matrix decode enabled in IRQ thread; user-space Kalman bridge kept as fallback. Kernel path does not read per-panel factory electrode calibration; long-term multitouch feel awaits testing |
| Video | ✅ | Iris V4L2 M2M 1080p H.264/HEVC encode and decode; Chrome and Firefox decode in hardware, Fedora Chromium falls back to software decode, see [known issues](docs/known-issues.md#触屏网络音频与相机) |
| Power | ✅ | Dual-cell battery reporting, deep suspend and resume working |
| Boot stability | ✅ | `qbootctl.service` marks `Successful: 1` so ABL will not drop to Fastboot |
| Camera | ✅ | `piano-camerad` streams NV12 from front and rear ISPs; first-frame black drop and AE convergence notes in [known issues](docs/known-issues.md#触屏网络音频与相机) |
| Audio | ⚠️ | Four-speaker DAP matrix topology (amp cap 235) and glitch-free playback/capture; DAP loading and crossover await full device sign-off |
| Scheduling | ✅ | `uperf-linux` touch boost, bridged to `tuned-ppd` profiles |
| Wireless | ✅ | Wi-Fi 6E/7, Bluetooth |
| Sensors | ✅ | SSC backend (`libssc` + `iio-sensor-proxy-ssc`) exposing accelerometer, ambient light, proximity, and auto-rotate |
| Virtualization | ✅ | `/dev/gunyah`, `librevm`, virtio-blk/net, single-vCPU guest boot verified; SMP and GUI guests unverified |
| Dual boot | ✅ | Hybrid BOOT in active `boot` slot switches between Linux and Android; see [dual-boot notes](docs/known-issues.md#双系统切换) |
| Stylus | ⚠️ | Type 29 pressure (0-1), tilt, and coordinates work; side buttons and magnetic attachment unimplemented, see [stylus notes](docs/known-issues.md#手写笔) |
| Fingerprint | ⚠️ | Ships `piano-fingerprint` / `piano-libfprint` (Goodix TEE path), isolated from Android templates; enrolment and unlock unverified on current image, see [fingerprint notes](docs/known-issues.md#指纹红外与-hdr) |
| IR blaster | ✅ | `/dev/lirc0` and `ir-spi` ready for `ir-ctl` transmission |
| HDR | ⚠️ | HDR10 and BT.2020 DPU output active; patched KWin restores backlight on exit; colour accuracy not instrument-measured, see [HDR notes](docs/known-issues.md#hdr) |

## Desktops

All five profiles share the kernel, ESP, and installer scripts, differing only in rootfs and `root-install/`. Login managers, power policies, and profile quirks are covered in [Desktops](docs/desktops.md).

| Profile | Session | Login | Auto-rotate | Status |
| --- | --- | --- | --- | --- |
| `gnome` | GNOME 51 (default) | GDM autologin | Mutter | Booted to desktop, rotation verified on device |
| `kde` | KDE Plasma | `plasma-login-manager` | KWin (`qt6-qtsensors`) | Booted to desktop, rotation verified on device |
| `niri` | niri | greetd + tuigreet | None (manual `transform`) | Booted to desktop |
| `hyprland` | Hyprland (3rd-party COPR) | greetd + tuigreet | None (manual monitor rule) | Booted to desktop |
| `denial` | Denial (3rd-party DNF repo) | greetd + tuigreet | None (Denial does not read SensorProxy) | Session starts on device; see [known issues](docs/known-issues.md#denial-桌面) for what was verified |

### Optional extras

Default images ship stock Fedora desktop packages. For ambient-light auto-brightness, KWin HDR backlight restore, and related desktop patches, enable the opt-in `xinyang26/piano-fedora-extras` repo documented in [COPR repository](docs/copr-repository.md#可选的-extras-软件源):

| Feature | Profiles | Package & change |
| --- | --- | --- |
| Ambient auto-brightness & keyboard backlight hotplug | GNOME | `gnome-settings-daemon-piano` applies stock lux curve and refreshes keyboard backlight |
| Stylus input in X11 apps | All five | `gtk3` fixes XI2 tool atom lookup |
| GNOME touch/KMS crash & startup guards | GNOME | `mutter` guards empty gamma LUT/touch grabs; `gdm` waits for KMS connector; `gnome-control-center` waits for `fprintd` verify |
| Backlight restore after exiting HDR | KDE | `kwin` resets brightness after HDR disable |

## Install

Full walkthrough and troubleshooting live in the [installation guide](docs/installation.md). The manual path gives you every command explicitly; the one-step script runs the exact same flashes.

1. Unlock the bootloader and make sure `adb` and `fastboot` work on your host.
2. **Stock-GPT devices must partition first**: follow [First install from stock GPT/UFS](docs/installation.md#四-从原始-gptufs-开始的首次安装) to shrink `userdata`, create the Linux partitions, erase `userdata`, and boot Android once. **The one-step script cannot run without this step.**
3. Download `piano-<tag>-<profile>.sha256` and all `.part-*` files for your chosen profile from [Releases](https://github.com/FFT-infra/piano-fedora/releases) into an empty directory, verify, and extract (or run `bash scripts/unpack-release.sh --tag <tag> --desktop <profile>`):

   ```sh
   sha256sum -c piano-<tag>-<profile>.sha256
   cat piano-<tag>-<profile>.tar.zst.part-* | zstd -dc | tar -x
   sha256sum -c SHA256SUMS
   ```

   When building from source, pair `deploy-bundle-<profile>` from a `build-bundle` run with `root-install-<profile>` generated by `prepare-install` from that same run. Never mix assets across runs.
4. Run `./make-device-boot.sh` in the staging directory to repack your tablet's own stock BOOT into `device-boot/boot_linux.img`.
5. Power off, hold `Power + Volume Down` for Fastboot, and verify `fastboot getvar current-slot` returns `a` or `b`. Follow [Manual flashing](docs/installation.md#五-手动刷写-linux) to verify manifests and flash active `boot`, `sunuefi_esp`, and `sunuefi_root`, or run the one-step script:

   ```sh
   ./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub   # Windows: flash-linux.bat (no SSH key injection)
   ```

6. The tablet reboots into your chosen desktop. Log in as `piano` (default password `piano123456`) and change the password immediately with `passwd`.

**Never erase `dtbo_a` or `dtbo_b`**: ABL refuses to boot if the active slot's DTBO is missing. Rollback instructions are in [Rollback to Android](docs/installation.md#4-回滚到-android).

### Switching between systems

- **Linux to Android**: click "Return to Android" in the app launcher, or run `piano-next-boot android --reboot`.
- **Android to Linux**: switch via the "SunUEFI boot-assistant" KernelSU/SukiSU module, or press a volume key within 3 seconds at power-on.

Once installed, `sudo dnf upgrade --refresh` updates the kernel and platform packages directly without reflashing; see [Upgrade guide](docs/upgrade.md).

## Documentation & Release Artifacts

Maintainers trigger releases manually per tag. Each desktop ships as a single self-contained archive `piano-<tag>-<profile>.tar.zst` (split at 1900 MiB if over 2 GiB; GNOME ~2.1 GiB, niri ~1 GiB). See [Artifacts & pipeline](docs/artifacts.md#release-发布) for exact archive contents.

- [Documentation Hub](docs/README.md): full index for users, desktops, and maintainers
- [Maintainer & Verification Reference](docs/internals.md): CI pipeline gates, device smoke/media scripts, and PR checklist
- [Contributing](CONTRIBUTING.md): local tests, patch validation, and PR conventions
- [Security Policy](SECURITY.md): vulnerability reporting and scope

## Build and Test

```sh
make test                  # full offline suite (GPT math, image contracts, patches, doc links)
make plan RELEASEVER=45    # print the Fedora 45 default rootfs assembly plan
```

Original code is [MIT](LICENSE) licensed; third-party upstream sources and kernel patches retain their own licenses in [sources.lock.json](sources.lock.json) and [NOTICE](NOTICE).
