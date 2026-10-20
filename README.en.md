> **Experimental project.** This repository targets driver work and hardware bring-up. It is not a stable system for daily use.
>
> Flashing unlocks the bootloader, rewrites the GPT, and writes the A/B slots and the boot chain. A careless step can brick the tablet or destroy all data. Back up every partition and your personal data before you start.
>
> This project ships under the [MIT license](LICENSE) as is. The authors and contributors take no responsibility for damaged hardware, lost data, or a voided warranty.

# Fedora 44 for Xiaomi Pad 8 Pro (piano)

English | [简体中文](README.md)

[![host-check](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml/badge.svg)](https://github.com/FFT-infra/piano-fedora/actions/workflows/host-check.yml)

Fedora 44 and GNOME run natively on the Xiaomi Pad 8 Pro (codename `piano`, Snapdragon 8 Elite SM8750). The boot chain is SunUEFI (EDK2). The kernel is a mainline Linux 7.2.9 port. The root filesystem is F2FS. The tablet switches between Linux and the stock HyperOS without a PC.

## Hardware status

| Module | Status | Notes |
|---|---|---|
| Display | ✅ | 3200×2136 at 120 Hz, Mesa 26.2.3 Turnip (Adreno 830) |
| Touch | ✅ | Kalman filter, 24 px dead zone, orthogonal axis lock |
| Video | 🟡 | Iris V4L2 M2M works: 300-frame H.264 and HEVC decode with frame-by-frame digests identical to software decode, and keyframe hardware encoding, all at 1080p with matching Iris interrupt counts. VP9, 10-bit and 4K60 are untested, and in-browser hardware decode is still pending. See [known issues](docs/known-issues.md#触屏网络音频与相机) |
| Power | ✅ | Dual-cell battery reporting, deep suspend |
| Boot | ✅ | `qbootctl.service` writes `Successful: 1`, so the ABL counter cannot drop to Fastboot |
| Camera | 🟡 | `piano-camerad` streams NV12 straight from the ISP; the front camera produces a picture, the rear camera and continuous preview are still pending. See [known issues](docs/known-issues.md#触屏网络音频与相机) |
| Audio | ✅ | Speaker amp capped at 215 (-15 dB), microphone gain +40 dB |
| Scheduling | ✅ | `uperf-linux` raises frequency on touch |
| Wireless | ✅ | Wi-Fi 6E/7, Bluetooth |
| Sensors | ✅ | `iio-sensor-proxy` reads accelerometer, ambient light, proximity, compass |
| Virtualization | ✅ | `/dev/gunyah`, `librevm`, virtio-blk and virtio-net, single-vCPU guest boot. SMP, graphics and GUI operation are still pending |
| Dual boot | 🟡 | Writing a Linux→Android request works. Automatic Android→Linux switching is not accepted yet, and the Android-side matching module is installed but only adopts the existing core. See [known issues](docs/known-issues.md#双系统切换) |
| Stylus | 🟡 | Daemon and Type 29 protocol are ready; a physical drawing test is pending |
| Fingerprint / IR / HDR | ❌ | Require closed TEE firmware; mainline does not support them |

## Install

1. Unlock the bootloader and set up `fastboot`.
2. On a device with the stock GPT, follow [First install from the stock GPT/UFS](docs/installation.md#四-从原始-gptufs-开始的首次安装) to create the partitions, erase `userdata`, and let Android boot once. **The one-step script does not work without this step.**
3. Use the `deploy-bundle` from one successful `build-bundle` run, and the `root-install` that `prepare-install` produced from that same bundle run. Do not mix assets from different runs.
4. Build a staging directory as described in [section 3 of the installation guide](docs/installation.md#三-已有-linux-分区的刷写跨平台一键脚本). Copy `flash-linux.sh` and `flash-linux.bat` from `scripts/installer/` next to every file of the deploy-bundle, with `root-install/` below it.

   The script treats the current directory as the staging directory and never looks for images inside the repository.

5. Power off. Hold `Power + Volume Down` until Fastboot appears. Check that `fastboot getvar current-slot` returns `a`. Then run this from the staging directory:

   ```bash
   ./flash-linux.sh --ssh-key ~/.ssh/id_ed25519.pub   # Linux / macOS
   ```

   On Windows, double-click `flash-linux.bat`. That path does not inject a key.

6. The tablet reboots into GNOME. The user is `piano` and the default password is `piano123456`. Run `passwd` after the first login.

   If you passed `--ssh-key`, you can log in with that key instead.

For step-by-step control, follow [Manual flashing](docs/installation.md#三之二-手动刷机不使用一键脚本). That section lists every `fastboot` command, the SHA256 checks, the read-back verification, and the rollback. **Never erase `dtbo_b`**: the ABL rejects the whole boot chain when it is missing.

### Switching between systems

- Linux to Android: click "Return to Android" in the app grid, or run `piano-next-boot android --reboot`.
- Android to Linux: automatic switching is not accepted yet. The installed `piano-switch-ksu` matching module only adopts the existing core and never rewrites the BOOT. See [known issues](docs/known-issues.md#android-侧).
- Boot menu: press a volume key within 3 seconds of power-on, or click "SunUEFI boot menu" on the desktop.

## Release artifacts

A release comes from the `deploy-bundle` artifact of `build-bundle`. The `root-install` directory comes from `prepare-install` running on the same bundle run:

| File | Contents |
|---|---|
| `boot_sunuefi.img` | Linux payload inside the ESP; never flash it to `boot_b` |
| `sunuefi_esp.img` | SunUEFI EDK2, DTB, Linux 7.2.9 kernel, initramfs |
| `PianoUEFI-product.img` | SunUEFI product firmware; the payload inside `sunuefi_esp.img` |
| `pianoroot.f2fs.img.zst` | Sealed Fedora 44 GNOME root filesystem; `prepare-install` expands it for a first install |
| `root-install/` | Expanded sparse root image and its manifest |
| `SHA256SUMS` | Hashes of every asset |

The repository has no release and no tag yet. The assets exist only as workflow artifacts. `scripts/package-release-bundle.sh` is a local compatibility packer, not the release entry point.

## Documentation

- [Architecture](docs/architecture.md): the boundary between Fedora, SunUEFI, and Android
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

Debug logs, incident write-ups, and handoff notes stay out of the repository.

## Build and check

```bash
make test                  # offline suite: GPT math, image and metadata checks
make plan RELEASEVER=44    # print the Fedora 44 root filesystem plan
```

The pipeline lives in `.github/workflows/`. The main chain is `host-check` (with `build_piano_rpms=true`) → `build-rootfs` → `build-image` → `build-bundle` → `prepare-install`. The last four also take successful kernel, runtime, Mesa, FFmpeg and UEFI run ids, so it is not a plain linear hand-off.

Each stage accepts only artifacts from successful runs. Mixing runs is rejected. See [docs/internals.md](docs/internals.md).

## License

Our own code is MIT, see [LICENSE](LICENSE). Third-party upstream components and kernel patches keep their own licenses, see [sources.lock.json](sources.lock.json) and [NOTICE](NOTICE).
