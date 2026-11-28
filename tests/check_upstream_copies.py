#!/usr/bin/env python3
"""校验 packages/ 里原样搬运自 SunUEFI 的文件未在本地静默漂移。

这里覆盖除 `tests/check_tuned_profiles.py`（电源三档与映射）和
`tests/check_touch_identity.py`（`piano-touch.service`）之外的全部
原生仓库原样副本；桌面策略、Fedora F2FS/RPM 包装差异文件不在此列。
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUNUEFI = Path(os.environ.get("PIANO_TEST_SUNUEFI",
                              ROOT / "build/test-sources/sunuefi"))

# (本仓相对路径, 锁定 SunUEFI 相对路径)
COPIES = (
    # alsa-xiaomi-piano
    ("packages/alsa-xiaomi-piano/usr/share/alsa/ucm2/Qualcomm/sm8750/Xiaomi-Pad-8-Pro/HiFi.conf",
     "linux/bsp/common/usr/share/alsa/ucm2/Qualcomm/sm8750/Xiaomi-Pad-8-Pro/HiFi.conf"),
    ("packages/alsa-xiaomi-piano/usr/share/alsa/ucm2/Qualcomm/sm8750/Xiaomi-Pad-8-Pro/Xiaomi-Pad-8-Pro.conf",
     "linux/bsp/common/usr/share/alsa/ucm2/Qualcomm/sm8750/Xiaomi-Pad-8-Pro/Xiaomi-Pad-8-Pro.conf"),
    ("packages/alsa-xiaomi-piano/usr/share/alsa/ucm2/conf.d/sm8750/Xiaomi Pad 8 Pro.conf",
     "linux/bsp/common/usr/share/alsa/ucm2/Qualcomm/sm8750/Xiaomi-Pad-8-Pro/Xiaomi-Pad-8-Pro.conf"),
    # iio-sensor-proxy-ssc / libssc
    ("packages/iio-sensor-proxy-ssc/ssc-report-polling-failure.patch",
     "patches/piano-sensors/5/iio-sensor-proxy/ssc-report-polling-failure.patch"),
    ("packages/libssc/0004-bound-sensor-open.patch",
     "patches/piano-sensors/5/libssc/0005-bound-sensor-open.patch"),
    # piano-bluez
    ("packages/piano-bluez/0001-hog-read-uncached-pnp-id.patch",
     "patches/bluez/5.88/0001-hog-read-uncached-pnp-id.patch"),
    ("packages/piano-bluez/0002-mpris-proxy-handle-signals-in-main-loop.patch",
     "patches/bluez/5.88/0002-mpris-proxy-handle-signals-in-main-loop.patch"),
    # piano-camera
    ("packages/piano-camera/etc/udev/rules.d/70-piano-camera.rules",
     "linux/bsp/optional/camera/etc/udev/rules.d/70-piano-camera.rules"),
    ("packages/piano-camera/etc/wireplumber/wireplumber.conf.d/50-piano-camera.conf",
     "linux/bsp/optional/camera/etc/wireplumber/wireplumber.conf.d/50-piano-camera.conf"),
    # piano-fedora-configs
    ("packages/piano-fedora-configs/etc/systemd/sleep.conf.d/piano.conf",
     "linux/desktops/gnome/overlay/etc/systemd/sleep.conf.d/piano.conf"),
    ("packages/piano-fedora-configs/usr/bin/piano-next-boot",
     "linux/userspace/piano-next-boot"),
    ("packages/piano-fedora-configs/usr/lib/piano/piano-boot-task-snapshot",
     "linux/userspace/piano-boot-task-snapshot"),
    ("packages/piano-fedora-configs/usr/lib/systemd/system/piano-linux-debug.service",
     "linux/userspace/piano-linux-debug.service"),
    ("packages/piano-fedora-configs/usr/libexec/piano-next-boot-helper",
     "linux/userspace/piano-next-boot-helper"),
    ("packages/piano-fedora-configs/usr/local/bin/piano-debug-bootstrap",
     "linux/userspace/piano-debug-bootstrap"),
    ("packages/piano-fedora-configs/usr/local/bin/piano-debug-stage",
     "linux/userspace/piano-debug-stage"),
    ("packages/piano-fedora-configs/usr/share/applications/org.sunuefi.BootMenu.desktop",
     "linux/userspace/org.sunuefi.BootMenu.desktop"),
    ("packages/piano-fedora-configs/usr/share/applications/org.sunuefi.ReturnAndroid.desktop",
     "linux/userspace/org.sunuefi.ReturnAndroid.desktop"),
    ("packages/piano-fedora-configs/usr/share/polkit-1/actions/org.sunuefi.boot-request.policy",
     "linux/userspace/org.sunuefi.boot-request.policy"),
    ("packages/piano-fedora-configs/usr/share/polkit-1/rules.d/49-piano-boot-request.rules",
     "linux/userspace/49-piano-boot-request.rules"),
    # piano-libfprint / piano-libwacom / piano-pipewire-gstreamer
    ("packages/piano-libfprint/0001-piano-goodix-uio.patch",
     "patches/libfprint/1.94.100/0001-piano-goodix-uio.patch"),
    ("packages/piano-libfprint/0002-build-goodix-backend-from-source.patch",
     "patches/libfprint/1.94.100/0002-build-goodix-backend-from-source.patch"),
    ("packages/piano-libwacom/0001-spi-digitizers-and-xiaomi-pad.patch",
     "patches/libwacom/2.20/0001-spi-digitizers-and-xiaomi-pad.patch"),
    ("packages/piano-pipewire-gstreamer/0001-gst-retain-running-caps.patch",
     "patches/pipewire/1.6.9/0004-gst-retain-running-caps.patch"),
    ("packages/piano-pipewire-gstreamer/0002-gst-copy-memptr.patch",
     "patches/pipewire/1.6.9/0006-gst-copy-memptr.patch"),
    ("packages/piano-pipewire-gstreamer/0003-gst-report-incompatible-capture-format.patch",
     "patches/pipewire/1.6.9/0013-gst-report-incompatible-capture-format.patch"),
    # xiaomi-piano-thp pen data
    ("packages/xiaomi-piano-thp/usr/share/piano/pen/calibration/piano-boe.ini",
     "linux/bsp/common/usr/share/piano/pen/calibration/piano-boe.ini"),
    ("packages/xiaomi-piano-thp/usr/share/piano/pen/calibration/piano-csot.ini",
     "linux/bsp/common/usr/share/piano/pen/calibration/piano-csot.ini"),
    ("packages/xiaomi-piano-thp/usr/share/piano/pen/p81c-radio.json",
     "linux/bsp/common/usr/share/piano/pen/p81c-radio.json"),
)


def main():
    if not (SUNUEFI / "build.sh").is_file():
        print("SKIP: 需要 PIANO_TEST_SUNUEFI 才能校验打包的上游副本")
        return 0
    for local_rel, upstream_rel in COPIES:
        local = ROOT / local_rel
        upstream = SUNUEFI / upstream_rel
        assert local.is_file(), f"缺少本仓文件：{local_rel}"
        assert upstream.is_file(), f"缺少上游文件：{upstream_rel}"
        assert local.read_bytes() == upstream.read_bytes(), \
            f"{local_rel} 与上游 {upstream_rel} 不再逐字节一致"
    print(f"OK: {len(COPIES)} 份打包的上游原样副本与锁定 SunUEFI 逐字节一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
