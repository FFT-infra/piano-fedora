> **Experimental project.** This repository targets driver work and hardware bring-up. It is not a stable system for daily use.
>
> Flashing unlocks the bootloader and rewrites the GPT. It replaces the combined BOOT in `boot_a` and rewrites the Linux partition. A careless step can brick the tablet or destroy all data. Back up every partition and your personal data before you start.
>
> This project ships under the [MIT license](LICENSE) as is. The authors and contributors take no responsibility for damaged hardware, lost data, or a voided warranty.

# Fedora 44 for Xiaomi Pad 8 Pro (piano)

English | [简体中文](README.md)

[![host-check](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml/badge.svg)](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml)

Fedora 44 runs natively on the Xiaomi Pad 8 Pro (codename `piano`, Snapdragon 8 Elite SM8750), with GNOME as the default desktop. The boot chain is SunUEFI (EDK2). The kernel is a mainline Linux 7.2.9 port. The root filesystem is F2FS. The tablet dual-boots Linux and the stock HyperOS. A 3-second chooser in the combined BOOT and the Android boot-assistant module handle both directions without a host.

## Hardware status

| Module | Status | Notes |
|---|---|---|
| Display | ✅ | 3200×2136 at 120 Hz, Mesa 26.2.3 Turnip (Adreno 830) |
| Touch | ✅ | Kalman filter, 24 px dead zone, orthogonal axis lock. 97 SYN reports per second over a 45-second continuous drag, full screen reachable, multitouch works |
| Video | ✅ | Iris V4L2 M2M works. H.264 and HEVC encode and decode in hardware, 300 frames each. B-frame sources decode too, all at 1080p. Frame-by-frame digests match software decode, and the Iris interrupt count grows. In-browser decode is verified: Chrome and Firefox decode in hardware by default, Fedora's Chromium does not, see [known issues](docs/known-issues.md#触屏网络音频与相机). VP9, 10-bit and 4K60 are not tested |
| Power | ✅ | Dual-cell battery reporting, deep suspend |
| Boot | ✅ | `qbootctl.service` writes `Successful: 1`, so the ABL counter cannot drop to Fastboot |
| Camera | ✅ | `piano-camerad` streams NV12 straight from the ISP. Both the front and the rear camera image normally. The first frames after streaming starts are black. Auto-exposure takes a few frames to converge. See [known issues](docs/known-issues.md#触屏网络音频与相机) |
| Audio | ✅ | Speaker amp capped at 215 (-15 dB), microphone gain +40 dB. 120 seconds of playback with no xrun, 130 seconds of recording with signal throughout |
| Scheduling | ✅ | `uperf-linux` raises frequency on touch |
| Wireless | ✅ | Wi-Fi 6E/7, Bluetooth |
| Sensors | ✅ | SSC backend (`libssc` + `iio-sensor-proxy-ssc` over QRTR/MHI). The accelerometer, ambient light and proximity sensors all report data. Auto-rotate works on device: the screen turns 90° when the tablet is set to `left-up` |
| Virtualization | ✅ | `/dev/gunyah`, `librevm`, virtio-blk and virtio-net, single-vCPU guest boot. SMP, graphics and GUI operation are not verified yet |
| Dual boot | ✅ | The combined BOOT lives in `boot_a`. Linux→Android uses `piano-next-boot android --reboot`. Android→Linux uses the SunUEFI boot-assistant module or the 3-second chooser. Two round trips are verified on device, see [known issues](docs/known-issues.md#双系统切换) |
| Stylus | ✅ | Type 29 protocol, pressure 0–1, position and tilt behave normally. 1906 axis events, 22 proximity events and 18 tip events were captured, see [known issues](docs/known-issues.md#手写笔) |
| Fingerprint | ❌ | Side-mounted capacitive, Goodix. Authentication runs in the TEE's `gfpia` TA. Mainline has neither the control driver nor a libfprint backend, so the sensor is not usable short-term. See [known issues](docs/known-issues.md#指纹红外与-hdr) |
| IR | ❌ | Consumer IR blaster (QUPv3 SE15). Mainline's `ir-spi` driver is complete. Our DT has no IR node, and the kernel is built without `LIRC`. Unrelated to TEE and fixable. See [known issues](docs/known-issues.md#指纹红外与-hdr) |
| HDR | ❌ | The panel supports HDR10, but msm attaches no `HDR_OUTPUT_METADATA` and has no colorop pipeline. The current msm static-HDR series only covers DP/eDP, not the internal DSI panel. Waiting on upstream. See [known issues](docs/known-issues.md#指纹红外与-hdr) |

## Desktops

Images are built per profile from the same release chain. They differ only in the root filesystem and `root-install/`. The kernel, ESP, one-step script and flash scripts are shared.

| Profile | Session | Login | Auto-rotate | Acceptance |
|---|---|---|---|---|
| `gnome` | GNOME 50 (Workstation environment) | GDM autologin | Mutter | Desktop reached. Rotation verified on device |
| `kde` | KDE Plasma | plasma-login-manager autologin | KWin, needs `qt6-qtsensors` | Desktop reached. Rotation verified. One brief flicker per rotation, see [known issues](docs/known-issues.md) |
| `niri` | niri | greetd + tuigreet | None, manual `transform` | Desktop reached (image requires a rebuild before it works unaided) |
| `hyprland` | Hyprland (third-party COPR) | greetd + tuigreet | None, manual rotate | Desktop reached (image requires a rebuild before it works unaided) |

On device, `niri` and `hyprland` use a wrong greeter account name in greetd
(`greeter` instead of the `greetd` account Fedora creates). The display manager
then does not start. The fix is in the source tree. Both images need a rebuild
before they work without manual intervention.

Each profile ships as one archive. Download only the profile you install. Login, power behavior, rotation and known gaps per profile are in [Desktops](docs/desktops.md).

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
4. Run `./make-device-boot.sh` in the staging directory. `make-device-boot.sh` repacks this tablet's own stock BOOT into `device-boot/boot_linux.img`. The shared `boot_sunuefi.img` cannot replace `boot_linux.img`.
5. Power off the device. Hold `Power + Volume Down` until Fastboot appears. Verify that `fastboot getvar current-slot` returns `a`. Then follow [Manual flashing](docs/installation.md#五-手动刷写-linux). Check both `SHA256SUMS` files and confirm the device identity. Write `boot_a`, `sunuefi_esp` and `sunuefi_root` in that order, then run `fastboot reboot`.

   To do it in one run, execute `./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub` from the staging directory (Linux / macOS), or double-click `flash-linux.bat` (Windows, which does not inject a key). The one-step script performs the same writes and adds a few checks before writing.

6. The tablet reboots into the chosen desktop. The user is `piano` and the default password is `piano123456`. Run `passwd` after the first login.

**Never erase `dtbo_a` or `dtbo_b`**: the ABL rejects the whole boot chain when the active slot's DTBO is missing. The rollback steps are in [the manual flashing rollback section](docs/installation.md#4-回滚到-android).

### Switching between systems

- Linux to Android: click "Return to Android" in the app grid, or run `piano-next-boot android --reboot`. Verified on device.
- Android to Linux: install the SunUEFI boot-assistant module and choose Linux there. Without the module, press a volume key within the first 3 seconds and pick Linux. The choice is saved.
- Both directions write the same request page in `boot_a`. Neither direction switches slots or writes the GPT.

## Release artifacts

A maintainer dispatches the release workflow with a tag. A tag push does not start a release. A release ships only verified artifacts from the same `build-bundle` run and the `prepare-install` run derived from that run. Nothing is rebuilt. One release carries four profiles, each as one archive `piano-<tag>-<profile>.tar.zst`, split into `.part-NN` pieces above 2 GiB.

An archive holds the platform images, that profile's `root-install/`, the component manifests, the one-step script and the boot tools. An install downloads one profile: about 2.1 GiB for GNOME, about 1 GiB for niri. The archive's own `SHA256SUMS` lists only the files it holds, so both verification commands run clean. For the purpose of each file, see [Artifacts and pipeline](docs/artifacts.md#release-发布).

## Documentation

- [Architecture](docs/architecture.md): the boundary between Fedora, SunUEFI, and Android
- [Desktops](docs/desktops.md): login, power, rotation, and acceptance for each profile
- [Installation and partitioning](docs/installation.md): partition sizing, GPT checks, one-step and manual flashing, first login
- [Artifacts and pipeline](docs/artifacts.md): how the kernel, RPMs, initramfs, and bundle are assembled
- [Maintenance and acceptance](docs/internals.md): pipeline entry points, artifact binding, run id plumbing, PR gate
- [COPR repository](docs/copr-repository.md): online upgrades for all ten packages, kernel included, plus the rollback boundary and failure modes
- [Upstream tracking](docs/upstream.md): current pin, drift dashboard, follow-up rules
- [Known issues](docs/known-issues.md): open problems and items under observation
- [Gunyah virtualization](docs/virtualization.md): VM lifecycle and networking on SM8750
- [Gaps before public distribution](docs/public-release-gaps.md): build inputs still tied to this one device and this one person
- [Contributing](CONTRIBUTING.md): local checks, commit format, and the PR gate
- [Security policy](SECURITY.md): how to report a vulnerability and what is in scope


## Build and check

```bash
make test                  # offline suite: GPT math, image and metadata checks
make plan RELEASEVER=44    # print the Fedora 44 root filesystem plan
```

The pipeline lives in `.github/workflows/`. The main chain is `host-check` (with `build_piano_rpms=true`) → `build-rootfs` → `build-image` → `build-bundle` → `prepare-install`. Every stage takes explicit run ids for its inputs. `build-rootfs` needs the RPM matrix, runtime, Mesa and FFmpeg. `build-image` adds the kernel. `build-bundle` needs the image and UEFI. `prepare-install` needs the bundle. The chain is not a plain linear hand-off.

Each stage accepts only artifacts from successful runs. The pipeline rejects assets from mixed runs. See [docs/internals.md](docs/internals.md).

## License

Our own code is MIT, see [LICENSE](LICENSE). Third-party upstream components and kernel patches keep their own licenses, see [sources.lock.json](sources.lock.json) and [NOTICE](NOTICE).
