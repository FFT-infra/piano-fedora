> **Experimental project.** This repository targets driver work and hardware bring-up. It is not a stable system for daily use.
>
> Flashing unlocks the bootloader, rewrites the GPT, replaces the combined BOOT in `boot_a`, and rewrites the Linux partition. A careless step can brick the tablet or destroy all data. Back up every partition and your personal data before you start.
>
> This project ships under the [MIT license](LICENSE) as is. The authors and contributors take no responsibility for damaged hardware, lost data, or a voided warranty.

# Fedora 44 for Xiaomi Pad 8 Pro (piano)

English | [简体中文](README.md)

[![host-check](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml/badge.svg)](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml)

Fedora 44 runs natively on the Xiaomi Pad 8 Pro (codename `piano`, Snapdragon 8 Elite SM8750), with GNOME as the default desktop. The boot chain is SunUEFI (EDK2). The kernel is a mainline Linux 7.2.9 port. The root filesystem is F2FS. The tablet dual-boots Linux and the stock HyperOS: a 3-second chooser in the combined BOOT plus the Android boot-assistant module handle both directions without a host.

## Hardware status

| Module | Status | Notes |
|---|---|---|
| Display | ✅ | 3200×2136 at 120 Hz, Mesa 26.2.3 Turnip (Adreno 830) |
| Touch | ✅ | Kalman filter, 24 px dead zone, orthogonal axis lock; 97 SYN reports per second over a 45-second continuous drag, full screen reachable, multitouch works |
| Video | ✅ | Iris V4L2 M2M works: 300-frame H.264 and HEVC hardware encode and decode, plus decode of B-frame sources, all at 1080p with frame-by-frame digests identical to software decode and growing Iris interrupt counts. In-browser decode is verified too: Chrome and Firefox decode in hardware by default, Fedora's Chromium does not, see [known issues](docs/known-issues.md#触屏网络音频与相机). VP9, 10-bit and 4K60 are untested |
| Power | ✅ | Dual-cell battery reporting, deep suspend |
| Boot | ✅ | `qbootctl.service` writes `Successful: 1`, so the ABL counter cannot drop to Fastboot |
| Camera | ✅ | `piano-camerad` streams NV12 straight from the ISP; both the front and the rear camera image normally. Note that the first frames after streaming starts are black: auto-exposure takes a few frames to converge. See [known issues](docs/known-issues.md#触屏网络音频与相机) |
| Audio | ✅ | Speaker amp capped at 215 (-15 dB), microphone gain +40 dB; 120 seconds of playback with no xrun, 130 seconds of recording with signal throughout |
| Scheduling | ✅ | `uperf-linux` raises frequency on touch |
| Wireless | ✅ | Wi-Fi 6E/7, Bluetooth |
| Sensors | ✅ | SSC backend (`libssc` + `iio-sensor-proxy-ssc` over QRTR/MHI): accelerometer, ambient light and proximity all report data, and auto-rotate works on device, rotating the screen 90° when the tablet is turned to `left-up` |
| Virtualization | ✅ | `/dev/gunyah`, `librevm`, virtio-blk and virtio-net, single-vCPU guest boot. SMP, graphics and GUI operation are still pending |
| Dual boot | ✅ | The combined BOOT lives in `boot_a`: Linux→Android uses `piano-next-boot android --reboot`, Android→Linux uses the SunUEFI boot-assistant module or the 3-second chooser. Two round trips verified; see [known issues](docs/known-issues.md#双系统切换) |
| Stylus | ✅ | Type 29 protocol, pressure 0–1, position and tilt behave normally; 1906 axis events, 22 proximity and 18 tip events captured, see [known issues](docs/known-issues.md#手写笔) |
| Fingerprint | ❌ | Side-mounted capacitive, Goodix; authentication runs in the TEE's `gfpia` TA. Mainline has neither the control driver nor a libfprint backend, so it is not usable short-term. See [known issues](docs/known-issues.md#指纹红外与-hdr) |
| IR | ❌ | Consumer IR blaster (QUPv3 SE15). Mainline's `ir-spi` driver is complete, but our DT has no IR node and the kernel is built without `LIRC`. Unrelated to TEE and fixable. See [known issues](docs/known-issues.md#指纹红外与-hdr) |
| HDR | ❌ | The panel supports HDR10, but msm attaches no `HDR_OUTPUT_METADATA` and has no colorop pipeline; the current msm static-HDR series only covers DP/eDP, not the internal DSI panel. Waiting on upstream. See [known issues](docs/known-issues.md#指纹红外与-hdr) |

## Desktops

Images are built per desktop from the same release chain and differ only in the root filesystem and `root-install/`. The kernel, ESP, installer, and flash scripts are shared.

| Profile | Session | Login | Auto-rotate | Acceptance |
|---|---|---|---|---|
| `gnome` | GNOME 50 (Workstation environment) | GDM autologin | Mutter | Desktop reached; rotation verified on device |
| `kde` | KDE Plasma | plasma-login-manager autologin | KWin, needs `qt6-qtsensors` | Desktop reached; rotation verified. One brief flicker per rotation, see [known issues](docs/known-issues.md) |
| `niri` | niri | greetd + tuigreet | None; manual `transform` | Desktop reached (image requires a rebuild before it works unaided) |
| `hyprland` | Hyprland (third-party COPR) | greetd + tuigreet | None; manual rotate | Desktop reached (image requires a rebuild before it works unaided) |

On device, `niri` and `hyprland` hit a wrong greeter account name in greetd
(`greeter` instead of the `greetd` account Fedora creates), so the display
manager never starts. The source fix is in; both images need a rebuild before
they work without manual intervention.

Pick a profile at download time with `bash unpack-release.sh --desktop <profile>`. Without the flag the script takes the only profile in the release. Login, power behavior, rotation, and known gaps per profile are in [Desktops](docs/desktops.md).

## Install

1. Unlock the bootloader and set up `fastboot`.
2. On a device with the stock GPT, follow [First install from the stock GPT/UFS](docs/installation.md#四-从原始-gptufs-开始的首次安装) to create the partitions, erase `userdata`, and let Android boot once. **The one-step script does not work without this step.**
3. Download the platform assets and the `root-install-*` files of the profile you picked (the other profiles' roots are not needed) from [Releases](https://github.com/FFT-infra/piano-fedora/releases), then run `bash unpack-release.sh --desktop <profile>` (optional when the release carries one profile). The script verifies the assets you downloaded and unpacks `root-install/`. When building from source, use the `deploy-bundle` from one successful `build-bundle` run and the `root-install` that `prepare-install` produced from that same bundle run. Do not mix assets from different runs.
4. Prepare the staging directory as described in [section 3 of the installation guide](docs/installation.md#三-已有-linux-分区的刷写跨平台一键脚本). The release's `piano-installer-<tag>.zip` carries the flash scripts; when building from source, copy `flash-linux.sh` and `flash-linux.bat` from `scripts/installer/` into it.

   Before flashing, use the release's `piano-boot-tools-<tag>.tar.zst` to build `device-boot/boot_linux.img` on the host once. It is repacked from this tablet's own stock BOOT; the shared `boot_sunuefi.img` cannot replace it. The installer treats its working directory as the staging directory and never looks for images inside the repository.

5. Power off the device. Hold `Power + Volume Down` until Fastboot appears. Verify that `fastboot getvar current-slot` returns `a`, then run this from the staging directory:

   ```bash
   ./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub   # Linux / macOS
   ```

   On Windows, double-click `flash-linux.bat`. That path does not inject an SSH key.

6. The tablet reboots into the chosen desktop. The user is `piano` and the default password is `piano123456`. Run `passwd` after the first login.

   If you passed `--ssh-key`, you can log in with that key instead.

For step-by-step control, see [Manual flashing](docs/installation.md#三之二-手动刷机不使用一键脚本). That section lists every `fastboot` command, the SHA256 checks, the read-back verification, and the rollback. **Never erase `dtbo_a` or `dtbo_b`**: the ABL rejects the whole boot chain when the active slot's DTBO is missing.

### Switching between systems

- Linux to Android: click "Return to Android" in the app grid, or run `piano-next-boot android --reboot`. Verified on device.
- Android to Linux: install the SunUEFI boot-assistant module and choose Linux there; without the module, press a volume key within the first 3 seconds and pick Linux. The choice is saved.
- Both directions write the same request page in `boot_a`. No slot switch and no GPT write.

## Release artifacts

A release is triggered by a tag (for example `v44.1`) and only ships verified artifacts from the same `build-bundle` run and the `prepare-install` run derived from it; nothing is rebuilt. One release carries four desktops: the ESP, kernel, initramfs and UEFI are byte-identical across profiles and are published once (about 0.6 GiB); the root is published per profile with a `-<profile>` suffix (0.9–2.7 GiB).

So an install downloads the platform assets plus one profile, 1.5–3.3 GiB in total; you do not need every file on the Releases page. Each profile's bundle manifests ship as one `manifests-<profile>.tar.zst`. `unpack-release.sh` checks what you downloaded against `RELEASE-SHA256SUMS`, merges split pieces, and unpacks the manifests and `root-install/`. What each file is: [Artifacts and pipeline](docs/artifacts.md#release-发布).

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

The pipeline lives in `.github/workflows/`. The main chain is `host-check` (with `build_piano_rpms=true`) → `build-rootfs` → `build-image` → `build-bundle` → `prepare-install`. Every stage takes explicit run ids for its inputs: `build-rootfs` needs the RPM matrix, runtime, Mesa and FFmpeg, `build-image` adds the kernel, `build-bundle` needs the image and UEFI, and `prepare-install` needs the bundle. It is not a plain linear hand-off.

Each stage accepts only artifacts from successful runs. Mixing runs is rejected. See [docs/internals.md](docs/internals.md).

## License

Our own code is MIT, see [LICENSE](LICENSE). Third-party upstream components and kernel patches keep their own licenses, see [sources.lock.json](sources.lock.json) and [NOTICE](NOTICE).
