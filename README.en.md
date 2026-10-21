> **Experimental project.** This repository targets driver work and hardware bring-up. It is not a stable system for daily use.
>
> Flashing unlocks the bootloader, rewrites the GPT, and writes the A/B slots and the boot chain. A careless step can brick the tablet or destroy all data. Back up every partition and your personal data before you start.
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
| Touch | ✅ | Kalman filter, 24 px dead zone, orthogonal axis lock |
| Video | 🟡 | Iris V4L2 M2M works: 300-frame H.264 and HEVC hardware encode and decode, plus decode of B-frame sources, all at 1080p with frame-by-frame digests identical to software decode and growing Iris interrupt counts. VP9, 10-bit and 4K60 are untested, and in-browser hardware decode is still pending. See [known issues](docs/known-issues.md#触屏网络音频与相机) |
| Power | ✅ | Dual-cell battery reporting, deep suspend |
| Boot | ✅ | `qbootctl.service` writes `Successful: 1`, so the ABL counter cannot drop to Fastboot |
| Camera | 🟡 | `piano-camerad` streams NV12 straight from the ISP; the front camera produces a normal picture, the rear camera produces frames too but needs a check in good light, and continuous preview is still pending. See [known issues](docs/known-issues.md#触屏网络音频与相机) |
| Audio | ✅ | Speaker amp capped at 215 (-15 dB), microphone gain +40 dB |
| Scheduling | ✅ | `uperf-linux` raises frequency on touch |
| Wireless | ✅ | Wi-Fi 6E/7, Bluetooth |
| Sensors | ✅ | `iio-sensor-proxy` reads accelerometer, ambient light, proximity, compass |
| Virtualization | ✅ | `/dev/gunyah`, `librevm`, virtio-blk and virtio-net, single-vCPU guest boot. SMP, graphics and GUI operation are still pending |
| Dual boot | ✅ | The combined BOOT lives in `boot_a`: Linux→Android uses `piano-next-boot android --reboot`, Android→Linux uses the SunUEFI boot-assistant module or the 3-second chooser. Two round trips verified; see [known issues](docs/known-issues.md#双系统切换) |
| Stylus | 🟡 | Daemon and Type 29 protocol are ready; a physical drawing test is pending |
| Fingerprint / IR / HDR | ❌ | Require closed TEE firmware; mainline does not support them |

## Desktops

Images are built per desktop from the same release chain and differ only in the root filesystem and `root-install/`. The kernel, ESP, installer, and flash scripts are shared.

| Profile | Session | Login | Auto-rotate | Acceptance |
|---|---|---|---|---|
| `gnome` | GNOME 44 workstation | GDM autologin | Mutter | On device |
| `kde` | KDE Plasma | plasma-login-manager autologin | KWin, needs `qt6-qtsensors` | Not on device |
| `niri` | niri | greetd + tuigreet | None; manual `transform` | Not on device |
| `hyprland` | Hyprland (third-party COPR) | greetd + tuigreet | None; manual rotate | Not on device |

Pick a profile at download time with `bash unpack-release.sh --desktop <profile>`. Without the flag the script takes the only profile in the release. Login, power behavior, rotation, and known gaps per profile are in [Desktops](docs/desktops.md).

## Install

1. Unlock the bootloader and set up `fastboot`.
2. On a device with the stock GPT, follow [First install from the stock GPT/UFS](docs/installation.md#四-从原始-gptufs-开始的首次安装) to create the partitions, erase `userdata`, and let Android boot once. **The one-step script does not work without this step.**
3. Download the latest release from [Releases](https://github.com/FFT-infra/piano-fedora/releases), run `sha256sum -c RELEASE-SHA256SUMS`, then `bash unpack-release.sh --desktop <profile>` (the flag is optional when the release carries one profile). When building from source, use the `deploy-bundle` from one successful `build-bundle` run and the `root-install` that `prepare-install` produced from that same bundle run. Do not mix assets from different runs.
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

A release is triggered by a tag and only ships verified artifacts from the same `build-bundle` run and the `prepare-install` run derived from it. Large images are split automatically, with `RELEASE-SHA256SUMS` and `unpack-release.sh`. One release can carry several desktops: the ESP, kernel, initramfs and UEFI are byte-identical across profiles and are published once, while the root image and `root-install/` are published per profile with a `-<desktop>` suffix:

| File | Contents |
|---|---|
| `boot_sunuefi.img` | Linux payload inside the ESP; never flash it to `boot_a` |
| `sunuefi_esp.img` | SunUEFI EDK2, DTB, Linux 7.2.9 kernel, initramfs |
| `PianoUEFI-product.img` | SunUEFI product firmware; the payload inside `sunuefi_esp.img` |
| `pianoroot.f2fs.img.zst` / `pianoroot-<desktop>.f2fs.img.zst` | Sealed root filesystem; never flash it directly. No suffix when the release carries one profile |
| `root-install/` | Sparse root transport with device configuration written (restored by `unpack-release.sh --desktop <profile>`) |
| `SHA256SUMS` | Hashes of every asset |

The release also carries `piano-installer-<tag>.zip` (flash scripts and guide) and `piano-boot-tools-<tag>.tar.zst` (builds `device-boot/boot_linux.img` locally). `scripts/package-release-bundle.sh` is a local compatibility packer, not the release entry point.

## Documentation

- [Architecture](docs/architecture.md): the boundary between Fedora, SunUEFI, and Android
- [Desktops](docs/desktops.md): login, power, rotation, and acceptance for each profile
- [Installation and partitioning](docs/installation.md): partition sizing, GPT checks, one-step and manual flashing, first login
- [Artifacts and pipeline](docs/artifacts.md): how the kernel, RPMs, initramfs, and bundle are assembled
- [Maintenance and acceptance](docs/internals.md): pipeline entry points, artifact binding, run id plumbing, PR gate
- [COPR repository](docs/copr-repository.md): online upgrades for userspace packages, and the rollback boundary (the kernel is not included)
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

The pipeline lives in `.github/workflows/`. The main chain is `host-check` (with `build_piano_rpms=true`) → `build-rootfs` → `build-image` → `build-bundle` → `prepare-install`. The last four also take successful kernel, runtime, Mesa, FFmpeg and UEFI run ids, so it is not a plain linear hand-off.

Each stage accepts only artifacts from successful runs. Mixing runs is rejected. See [docs/internals.md](docs/internals.md).

## License

Our own code is MIT, see [LICENSE](LICENSE). Third-party upstream components and kernel patches keep their own licenses, see [sources.lock.json](sources.lock.json) and [NOTICE](NOTICE).
