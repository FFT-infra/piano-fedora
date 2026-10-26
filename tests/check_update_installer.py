#!/usr/bin/env python3
"""有界执行路径测试：真实 execute_update + 记录式 Device，不接触设备。

update_plan 被打桩为固定 plan/snapshot 与小 fake 材料；execute_update、installer.factory、
installer.checked 均为真实代码。断言：

* serial/product/unlocked/is-userspace/current-slot 及两分区 capacity 错误，都在任何 flash
  之前被拒绝（Device 记录的 flash 列表为空）。
* 好路径只 fastboot flash sunuefi_esp 与 sunuefi_root，无 erase、无 BOOT/userdata 写入，
  不重启回 Android/Linux（只允许 reboot bootloader 停在 fastboot）。
* 传输失败时 result journal 不伪装成功，且异常向上抛出。
* stale plan（update_plan 重算结果与计划不一致）在 reboot 前被拒绝。

临时文件只放 TMPDIR/tasktrash，不使用 /tmp。
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

spec = importlib.util.spec_from_file_location("update_installer", ROOT / "scripts/update-f2fs.py")
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)
installer = update.installer

ESP_BYTES = 4096
ROOT_BYTES = 8192


def scratch_root():
    return Path(os.environ.get("TMPDIR")
                or os.environ.get("PIANO_TMPDIR")
                or (Path.home() / "Lab/Bridge/tmp/trash"))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class FakeDevice:
    """记录所有 call/fastboot_var；可注入身份与分区容量，可选让某次 call 失败。"""

    def __init__(self, identity, part_sizes, var_overrides=None, fail_match=None):
        self.serial = identity["factory_serial"]
        self.identity = dict(identity)
        self.part_sizes = dict(part_sizes)
        self.fail_match = fail_match
        self.calls = []
        self.var_calls = []
        self.reboot_android_called = False
        self.vars = {"serialno": identity["factory_serial"], "product": "piano",
                     "unlocked": "yes", "is-userspace": "no", "current-slot": "b"}
        self.vars.update(var_overrides or {})

    def call(self, argv, timeout=60):
        argv = [str(x) for x in argv]
        self.calls.append(argv)
        if self.fail_match is not None and any(self.fail_match in token for token in argv):
            raise ValueError("simulated transfer failure: " + self.fail_match)
        return b"", b""

    def shell(self, command):
        return self.call(["adb", "-s", self.serial, "exec-out", "su -c", command])[0]

    def text(self, command):
        raw = self.shell(command)
        return raw.decode() if isinstance(raw, bytes) else str(raw)

    def read(self, path, first, count):
        return b"\x00" * (4096 * count)

    def reboot_bootloader(self):
        return self.call(["adb", "-s", self.serial, "reboot", "bootloader"])

    def reboot_android(self, identity):
        self.reboot_android_called = True
        return self.call(["adb", "-s", self.serial, "reboot"])

    def fastboot_var(self, factory, key):
        self.var_calls.append(key)
        if key.startswith("partition-size:"):
            name = key.split(":", 1)[1]
            if name not in self.part_sizes:
                raise AssertionError("测试计划缺少分区容量: " + name)
            return format(self.part_sizes[name], "x")
        return self.vars[key]

    def flash_targets(self):
        names = []
        for argv in self.calls:
            if "flash" in argv:
                index = argv.index("flash")
                if index + 1 < len(argv):
                    names.append(argv[index + 1])
        return names

    def booted_os(self):
        """是否有任何调用会离开 fastboot 去启动 Android/Linux。"""
        for argv in self.calls:
            if "reboot" in argv and "bootloader" not in argv and "fastbootd" not in argv:
                return True
        return self.reboot_android_called

    def all_tokens(self):
        return " ".join(" ".join(argv) for argv in self.calls)


def identity():
    return {"factory_serial": "SN-TEST-1", "product": "piano", "unlocked": True,
            "disk": "/dev/block/sda", "disk_bytes": 249518080 * 4096, "block_bytes": 4096}


def build_inputs(work):
    bundle = work / "bundle"
    root_install = work / "root-install"
    boot = work / "device-boot"
    for folder in (bundle, root_install, boot):
        folder.mkdir(parents=True, exist_ok=True)
    (bundle / "sunuefi_esp.img").write_bytes(b"\x00" * ESP_BYTES)
    (root_install / "pianoroot.f2fs.sparse.img").write_bytes(b"\x00" * ROOT_BYTES)
    stock_boot = work / "stock-boot.img"
    stock_boot.write_bytes(b"stock recovery fixture; plan validation is separately stubbed")
    esp_meta = {"bytes": ESP_BYTES, "sha256": sha256_file(bundle / "sunuefi_esp.img")}
    root_meta = {"bytes": ROOT_BYTES, "sha256": sha256_file(root_install / "pianoroot.f2fs.sparse.img")}
    (bundle / "bundle-manifest.json").write_text(json.dumps({"files": {"sunuefi_esp.img": esp_meta}}) + "\n")
    (root_install / "root-install-manifest.json").write_text(json.dumps({"transport": root_meta}) + "\n")
    return bundle, root_install, boot, stock_boot


def build_plan_snapshot(esp_bytes=ESP_BYTES, root_bytes=ROOT_BYTES, slot="_b"):
    ident = identity()
    plan = {
        "schema_version": 1,
        "status": "EXISTING_PARTITIONS_READ_ONLY_PLAN",
        "device": ident,
        "android_slot": slot,
        "targets": ["sunuefi_esp", "sunuefi_root"],
        "partition_plan": {"partitions": [
            {"name": "sunuefi_root", "bytes": root_bytes},
            {"name": "sunuefi_esp", "bytes": esp_bytes},
        ]},
        "gpt_baseline": {},
        "device_writes": False,
    }
    gpt = SimpleNamespace(primary=b"P" * 92, primary_entries=b"E" * 128,
                          backup=b"B" * 92, backup_entries=b"F" * 128,
                          baseline=lambda: {})
    snapshot = {"identity": ident, "gpt": gpt, "f2fs": None}
    return plan, snapshot


def run_execute(work, device, plan, snapshot, fail_plan=None, tamper=None):
    bundle, root_install, boot, stock_boot = build_inputs(work)
    session = work / "session"  # 不预创建
    if tamper is not None:
        target = bundle / "sunuefi_esp.img" if tamper == "esp" else root_install / "pianoroot.f2fs.sparse.img"
        target.write_bytes(b"changed after manifest creation")
    returned = plan if fail_plan is None else fail_plan
    original = update.update_plan
    update.update_plan = lambda *a, **k: (returned, snapshot)
    try:
        # update_plan 已被打桩，upstream 不参与真实执行路径。
        return update.execute_update(SimpleNamespace(), device, plan, bundle, root_install,
                                     boot, stock_boot, session)
    finally:
        update.update_plan = original


def expect_reject(work, plan, snapshot, label, device):
    try:
        run_execute(work, device, plan, snapshot)
    except ValueError as error:
        assert "identity differs" in str(error) or "capacity differs" in str(error), str(error)
    else:
        raise AssertionError(label + ": execute_update 未在任何 flash 前拒绝")
    assert device.flash_targets() == [], f"{label}: 拒绝前已发生 flash {device.flash_targets()}"


def check_rejections(base):
    good = {"sunuefi_esp": ESP_BYTES, "sunuefi_root": ROOT_BYTES}
    identity_cases = [
        ("坏 serial", {"serialno": "OTHER"}),
        ("坏 product", {"product": "not-piano"}),
        ("未解锁", {"unlocked": "no"}),
        ("userspace", {"is-userspace": "yes"}),
        ("坏槽位", {"current-slot": "a"}),
    ]
    for index, (label, override) in enumerate(identity_cases):
        plan, snapshot = build_plan_snapshot()
        device = FakeDevice(identity(), good, var_overrides=override)
        expect_reject(base / f"reject-{index}", plan, snapshot, label, device)
    # 两分区容量各自错误，都必须在 flash 前拒绝。
    for index, bad in enumerate(({"sunuefi_esp": ESP_BYTES - 4096, "sunuefi_root": ROOT_BYTES},
                                 {"sunuefi_esp": ESP_BYTES, "sunuefi_root": ROOT_BYTES + 4096})):
        plan, snapshot = build_plan_snapshot()
        device = FakeDevice(identity(), bad)
        expect_reject(base / f"reject-capacity-{index}", plan, snapshot, "坏容量", device)
    print("OK: serial/product/unlocked/userspace/slot/两分区 capacity 均在 flash 前拒绝")


def check_changed_inputs(base):
    for name in ("esp", "root"):
        plan, snapshot = build_plan_snapshot()
        device = FakeDevice(identity(), {"sunuefi_esp": ESP_BYTES, "sunuefi_root": ROOT_BYTES})
        try:
            run_execute(base / ("changed-" + name), device, plan, snapshot, tamper=name)
        except ValueError as error:
            assert "hash/size differs" in str(error), str(error)
        else:
            raise AssertionError("changed update input was accepted")
        assert not device.flash_targets(), "both inputs must be checked before the first flash"


def check_happy_path(base):
    plan, snapshot = build_plan_snapshot()
    device = FakeDevice(identity(), {"sunuefi_esp": ESP_BYTES, "sunuefi_root": ROOT_BYTES})
    result = run_execute(base / "happy", device, plan, snapshot)
    assert sorted(device.flash_targets()) == ["sunuefi_esp", "sunuefi_root"], device.flash_targets()
    assert device.flash_targets().count("sunuefi_root") == 1 and device.flash_targets().count("sunuefi_esp") == 1
    tokens = device.all_tokens()
    assert "erase" not in tokens and "format" not in tokens, tokens
    assert "boot_b" not in tokens and "boot_a" not in tokens and "userdata" not in tokens, tokens
    assert device.booted_os() is False, "好路径不应重启回 Android/Linux"
    assert isinstance(result, dict) and result.get("status", "").endswith("PENDING"), result
    assert (base / "happy" / "session" / "result.json").is_file()
    print("OK: 好路径仅 flash sunuefi_esp/sunuefi_root，无 erase/BOOT/userdata，停在 fastboot")


def check_transfer_failure(base):
    plan, snapshot = build_plan_snapshot()
    device = FakeDevice(identity(), {"sunuefi_esp": ESP_BYTES, "sunuefi_root": ROOT_BYTES},
                        fail_match="pianoroot.f2fs.sparse.img")
    raised = False
    try:
        run_execute(base / "fail", device, plan, snapshot)
    except ValueError as error:
        assert "simulated transfer failure" in str(error), str(error)
        raised = True
    assert raised, "传输失败应向上抛出"
    result_path = base / "fail" / "session" / "result.json"
    assert result_path.is_file(), "传输失败应留下 result.json"
    journal = json.loads(result_path.read_text())
    assert journal["status"] == "UPDATE_INTERRUPTED", journal
    assert "PENDING" not in journal["status"] and "TRANSFERRED" not in journal["status"], journal
    assert journal.get("device_writes") is True and journal.get("linux_boot_verified") is False
    assert "factory_fastboot_accepted_sunuefi_esp" in journal["completed"]
    assert "factory_fastboot_accepted_sunuefi_root" not in journal["completed"]
    print("OK: 传输失败 journal 记为 UPDATE_INTERRUPTED，不伪装成功")


def check_stale_plan(base):
    plan, snapshot = build_plan_snapshot()
    stale = dict(plan)
    stale["android_slot"] = "_a"  # 重算结果与计划不一致
    device = FakeDevice(identity(), {"sunuefi_esp": ESP_BYTES, "sunuefi_root": ROOT_BYTES})
    try:
        run_execute(base / "stale", device, plan, snapshot, fail_plan=stale)
    except ValueError as error:
        assert "changed since the reviewed update plan" in str(error), str(error)
    else:
        raise AssertionError("stale plan 未被拒绝")
    assert device.calls == [], "stale plan 必须在任何 reboot/flash 前拒绝"
    assert not (base / "stale" / "session").exists(), "stale plan 不应创建会话"
    print("OK: stale plan 在 reboot 前被拒绝，无任何设备调用")


def main():
    scratch = scratch_root()
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="update-installer-", dir=scratch) as directory:
        base = Path(directory)
        check_rejections(base)
        check_changed_inputs(base)
        check_happy_path(base)
        check_transfer_failure(base)
        check_stale_plan(base)
    print("OK: 已有分区更新安装器的执行路径测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
