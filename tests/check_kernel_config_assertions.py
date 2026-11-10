#!/usr/bin/env python3
"""内核构建前的配置断言要覆盖桌面实际依赖的驱动。

历史上 LIRC 与 IR_SPI 在这条链上悄悄掉过：内核照样编出来，设备上却没有
`/dev/lirc0`。断言写在 `build-kernel.yml` 里，这里锁住那张表，防止有人把它
删回去。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "build-kernel.yml"

REQUIRED = {
    "F2FS_FS": "y", "F2FS_FS_XATTR": "y", "F2FS_FS_POSIX_ACL": "y",
    "F2FS_FS_SECURITY": "y", "SECURITY_NETWORK": "y", "SECURITY_SELINUX": "y",
    "GUNYAH": "y", "GUNYAH_HYPERCALLS": "y", "GUNYAH_TIMER": "y",
    "GUNYAH_PLATFORM_HOOKS": "y", "GUNYAH_QCOM_PLATFORM": "y",
    "GUNYAH_QCOM_TRUSTED_VM": "y", "GUNYAH_IRQFD": "y", "GUNYAH_IOEVENTFD": "y",
    "LIRC": "y", "IR_SPI": "m", "SND_SEQUENCER": "m",
    "ARM64_PIANO_OPTIMIZE": "y", "LEDS_CLASS_FLASH": "m", "UIO_PIANO_FINGERPRINT": "m",
}


def main():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "required = {" in text, "configuration assertion table is gone"
    for name, value in REQUIRED.items():
        pattern = r"'%s':\s*'%s'" % (re.escape(name), value)
        assert re.search(pattern, text), f"{name}={value} is not asserted before the build"
    assert "CONFIG_LSM=\"selinux\"" in text, "SELinux LSM assertion is gone"
    print(f"OK: {len(REQUIRED)} 项内核配置在编译前被断言")


if __name__ == "__main__":
    main()
