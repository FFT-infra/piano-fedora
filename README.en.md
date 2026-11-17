> **Experimental project.** This repository targets driver work and hardware bring-up. It is not a stable system for daily use.
>
> Flashing unlocks the bootloader and rewrites the GPT. It replaces the combined BOOT in the active slot's `boot` partition and rewrites the Linux partition. A careless step can brick the tablet or destroy all data. Back up every partition and your personal data before you start.
>
> This project ships under the [MIT license](LICENSE) as is. The authors and contributors take no responsibility for damaged hardware, lost data, or a voided warranty.

# Fedora 45 for Xiaomi Pad 8 Pro (piano)

English | [简体中文](README.md)

[![host-check](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml/badge.svg)](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml)

Fedora 45 runs natively on the Xiaomi Pad 8 Pro (codename `piano`, Snapdragon 8 Elite SM8750), with GNOME as the default desktop. The kernel is a mainline Linux 7.3 port. The root filesystem is F2FS. The tablet dual-boots Linux and the stock HyperOS. A 3-second chooser in the combined BOOT and the Android boot-assistant module handle both directions without a host.

[SunUEFI](https://github.com/slwyts/Project-SunUEFI) (EDK2) provides the boot chain and kernel packaging. This repository adds a Fedora backend, the F2FS path end to end, first-time partitioning and UEFI boot patches. [Architecture](docs/architecture.md) has the split by layer.

## Hardware status

Legend:
- ✅ Verified on device
- ⚠️ Partially working (gaps noted in description)
- ❌ Not implemented or unverified

Hardware peripheral support status for the current release:

| Module | Status | Notes |
|---|---|---|
| Display | ✅ | 3200×2136 at 120 Hz, Mesa 26.2.6 Turnip (Adreno 830) |
| Touch | ✅ | Kalman filter, 24 px dead zone, orthogonal axis lock. 97 SYN reports per second over a 45-second continuous drag, full screen reachable, multitouch works |
| Video | ✅ | Iris V4L2 M2M works. H.264 and HEVC encode and decode in hardware, 300 frames each. B-frame sources decode too, all at 1080p. Frame-by-frame digests match software decode, and the Iris interrupt count grows. In-browser decode is ready: Chrome and Firefox decode in hardware by default, Fedora's Chromium uses software decode, see [known issues](docs/known-issues.md#触屏网络音频与相机). VP9, 10-bit and 4K60 are pending further testing |
| Power | ✅ | Dual-cell battery reporting, deep suspend |
| Boot | ✅ | `qbootctl.service` writes `Successful: 1`, so the ABL counter cannot drop to Fastboot |
| Camera | ✅ | `piano-camerad` streams NV12 straight from the ISP. Both the front and the rear camera image normally. The first frames after streaming starts are black. Auto-exposure takes a few frames to converge. See [known issues](docs/known-issues.md#触屏网络音频与相机) |
| Audio | ✅ | Speaker amp capped at 215 (-15 dB), microphone gain +40 dB. 120 seconds of playback with no xrun, 130 seconds of recording with signal throughout |
| Scheduling | ✅ | `uperf-linux` raises frequency on touch |
| Wireless | ✅ | Wi-Fi 6E/7, Bluetooth |
| Sensors | ✅ | SSC backend (`libssc` + `iio-sensor-proxy-ssc` over QRTR/MHI). The accelerometer, ambient light and proximity sensors all report data. Auto-rotate supported: screen rotates 90° when orientation changes |
| Virtualization | ✅ | `/dev/gunyah`, `librevm`, virtio-blk and virtio-net, single-vCPU guest boot. SMP, graphics and GUI operation are not verified yet |
| Dual boot | ✅ | The combined BOOT lives in the active slot's `boot` partition (`boot_a` or `boot_b`). Linux→Android uses `piano-next-boot android --reboot`. Android→Linux uses the SunUEFI boot-assistant module or the 3-second chooser. Two-way boot switching is functional, see [known issues](docs/known-issues.md#双系统切换) |
| Stylus | ✅ | Type 29 protocol, pressure 0–1, position and tilt behave normally. Axis events, proximity detection and tip response are functional, see [known issues](docs/known-issues.md#手写笔) |
| Fingerprint | ✅ | Side-mounted capacitive (Goodix), `piano-fingerprint` and `piano-libfprint` ship in the default image. Enrolment and lock-screen unlock verified; current status and boundary see [known issues](docs/known-issues.md#指纹红外与-hdr) |
| IR | ✅ | Consumer IR blaster, `/dev/lirc0` node and `ir-spi` driver ready, supports `ir-ctl` transmission. Testing boundary see [known issues](docs/known-issues.md#指纹红外与-hdr) |
| HDR | ✅ | Panel supports HDR10. When enabled, DSI-1 submits PQ metadata and BT.2020, and the DPU converts through the panel-calibrated IGC, Gamut and GC stages; control-word readback matches the upstream record. Extras patched KWin on KDE Plasma supports toggle and backlight restore; colour accuracy and peak luminance are not instrument-measured, see [known issues](docs/known-issues.md#指纹红外与-hdr) |

## Desktops

Images are built per profile from the same release chain. They differ only in the root filesystem and `root-install/`. The kernel, ESP, one-step script and flash scripts are shared.

| Profile | Session | Login | Auto-rotate | Acceptance |
|---|---|---|---|---|
| `gnome` | GNOME 51 (Workstation environment) | GDM autologin | Mutter | Desktop reached. Rotation verified on device |
| `kde` | KDE Plasma | plasma-login-manager autologin | KWin, needs `qt6-qtsensors` | Desktop reached. Rotation verified |
| `niri` | niri | greetd + tuigreet | None, manual `transform` | Desktop reached |
| `hyprland` | Hyprland (third-party COPR) | greetd + tuigreet | None, manual rotate | Desktop reached |
| `denial` | Denial (third-party DNF repository) | greetd + tuigreet | None | Not validated on Fedora 45 hardware |

Each profile ships as one archive. Download only the profile you install. Login, power behavior, rotation and known gaps per profile are in [Desktops](docs/desktops.md).

### Optional extras

Default images use stock Fedora desktop components and work out of the box. The extras repository is a separate, default-disabled source that adds these:

| What you get | Profile | What the patch does |
|---|---|---|
| Screen brightness follows ambient light | GNOME | GSD converts target brightness with the stock lux-to-nits curve |
| Keyboard backlight works after plugging the folio keyboard | GNOME | GSD refreshes the keyboard backlight device |
| Stylus works in X11 applications | All five | GTK3 looks up XI2 tool atoms on the device's own display |
| Touch does not crash GNOME | GNOME | mutter guards against an empty KMS gamma LUT and a cancelled touch grab |
| Backlight falls back when HDR is turned off | KDE | KWin restores the previous brightness on HDR exit |
| GNOME waits for the display before login | GNOME | gdm waits for the KMS connector before starting a session |

Commands to enable it, install, and remove it, plus the full package list, are in [COPR repository](docs/copr-repository.md#可选的-extras-软件源).

## Install

There are two paths. The manual path is the primary one: it lists each command, so you can check every step. The one-step script performs the same writes and is optional. Both paths are in the [installation guide](docs/installation.md).

1. Unlock the bootloader and set up `fastboot`.
2. On a device with the stock GPT, follow [First install from the stock GPT/UFS](docs/installation.md#四-从原始-gptufs-开始的首次安装): create the partitions, erase `userdata`, and let Android boot once. **The one-step script does not work without this step.**
3. Download `piano-<tag>-<profile>.sha256` and every piece of `piano-<tag>-<profile>.tar.zst` from [Releases](https://github.com/FFT-infra/piano-fedora/releases). Put them in one empty directory and run:

   ```sh
   sha256sum -c piano-<tag>-<profile>.sha256
   cat piano-<tag>-<profile>.tar.zst.part-* | zstd -dc | tar -x
   sha256sum -c SHA256SUMS
   ```

   The other profiles are separate archives and are not needed. The unpacked directory is the staging directory. It holds the flash scripts, `root-install/` and the component manifests. Run `bash scripts/unpack-release.sh --tag <tag> --desktop <profile>` to perform these three steps. When you build from source, use the `deploy-bundle-<profile>` from one successful `build-bundle` run and the `root-install-<profile>` that `prepare-install` produced from that same run. Do not mix assets from different runs.
4. Run `./make-device-boot.sh` in the staging directory. `make-device-boot.sh` repacks this tablet's own stock BOOT into `device-boot/boot_linux.img`. The archived ESP image cannot replace `boot_linux.img`.
5. Power off the device. Hold `Power + Volume Down` until Fastboot appears. Verify that `fastboot getvar current-slot` returns `a` or `b`. Then follow [Manual flashing](docs/installation.md#五-手动刷写-linux). Check both `SHA256SUMS` files and confirm the device identity. Write the active slot's `boot` partition (`boot_a` or `boot_b`), `sunuefi_esp` and `sunuefi_root` in that order, then run `fastboot reboot`.

   To do it in one run, execute `./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub` from the staging directory (Linux / macOS), or double-click `flash-linux.bat` (Windows, which does not inject a key). The one-step script performs the same writes and adds a few checks before writing.

6. The tablet reboots into the chosen desktop. The user is `piano` and the default password is `piano123456`. Run `passwd` after the first login.

**Never erase `dtbo_a` or `dtbo_b`**: the ABL rejects the whole boot chain when the active slot's DTBO is missing. The rollback steps are in [the manual flashing rollback section](docs/installation.md#4-回滚到-android).

### Switching between systems

- Linux to Android: click "Return to Android" in the app grid, or run `piano-next-boot android --reboot`.
- Android to Linux: use the SunUEFI boot-assistant module, or press a volume key within 3 seconds at boot.
- Both directions write the active-slot BOOT request page without switching slots or altering the GPT.

## Release artifacts

A maintainer dispatches the release workflow with a tag. A tag push does not start a release. A release ships only verified artifacts from the same `build-bundle` run and the `prepare-install` run derived from that run. Nothing is rebuilt. One release carries five profiles, each as one archive `piano-<tag>-<profile>.tar.zst`, split into `.part-NN` pieces above 2 GiB.

An archive holds the platform images, that profile's `root-install/`, the component manifests, the one-step script and the boot tools. An install downloads one profile: about 2.1 GiB for GNOME, about 1 GiB for niri. The archive's own `SHA256SUMS` lists only the files it holds, so both verification commands run clean. For the purpose of each file, see [Artifacts and pipeline](docs/artifacts.md#release-发布).

## Documentation

- [Documentation Hub](docs/README.md): index for installation, upgrades, daily use, and maintenance
- [Contributing](CONTRIBUTING.md): local checks, commit format, and the PR gate
- [Security policy](SECURITY.md): how to report a vulnerability and what is in scope


## Build and check

```bash
make test                  # offline suite: GPT math, image and metadata checks
make plan RELEASEVER=45    # print the Fedora 45 root filesystem plan
```

The pipeline lives in `.github/workflows/`. The main chain is `host-check` (with `build_piano_rpms=true`) → `build-rootfs` → `build-image` → `build-bundle` → `prepare-install`. Every stage takes explicit run ids for its inputs. `build-rootfs` needs the RPM matrix, runtime, Mesa and FFmpeg. `build-image` adds the kernel. `build-bundle` needs the image and UEFI. `prepare-install` needs the bundle. The chain is not a plain linear hand-off.

Each stage accepts only artifacts from successful runs. The pipeline rejects assets from mixed runs. See [docs/internals.md](docs/internals.md).

## License

Our own code is MIT, see [LICENSE](LICENSE). Third-party upstream components and kernel patches keep their own licenses, see [sources.lock.json](sources.lock.json) and [NOTICE](NOTICE).
