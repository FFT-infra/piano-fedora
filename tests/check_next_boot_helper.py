#!/usr/bin/env python3
"""离线验证 piano-next-boot-helper 的槽位切换与请求页分支。

r4 布局把 Android 留在 A 槽、Linux 写在 B 槽。ABL 引导 B 槽时给 Android
内核传 `androidboot.slot_suffix=_b`，而 super 只有 `_a` 的逻辑分区，包装器
的 Android 直通会反复重启。所以 `android` 必须切活动槽，不能写 target=0。

`linux`/`menu`/`setup` 只能确认活动槽是 B，绝不能调用 `qbootctl -s b`：
qbootctl 不交换槽位 type GUID，在 A 活动时执行 -s b 会留下“B 活动 + generic
GUID”的状态，ABL 的 ValidateSlotGuids 会拒绝启动。需要回 B 时走主机的
`fastboot set_active b`。

这里用假的 qbootctl 与 piano-boot-request 检查调用序列、读回与输出，不接触
设备。真机行为由安装后的 `piano-next-boot` 与第五门验收覆盖。
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packages/piano-fedora-configs/usr/libexec/piano-next-boot-helper"
DEVICE = "/dev/disk/by-partlabel/boot_b"
GENERATION = "bcd89b66dde56140979a8ce3b46950c8"

QBOOTCTL = """#!/usr/bin/env python3
import pathlib, sys
state = pathlib.Path(__file__).with_name('slot.state')
log = pathlib.Path(__file__).with_name('qbootctl.log')
with log.open('a') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
slot = state.read_text().strip() if state.exists() else 'b'
if sys.argv[1] == '-a':
    print('Active slot: _' + slot)
elif sys.argv[1] == '-c':
    print('Current slot: _' + slot)
elif sys.argv[1] == '-s':
    state.write_text(sys.argv[2])
    print('SLOT 0: Set as active slot')
else:
    sys.exit('unsupported qbootctl argument')
"""

NATIVE = """#!/usr/bin/env python3
import json, pathlib, sys
log = pathlib.Path(__file__).with_name('native.log')
with log.open('a') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
targets = {'android': 0, 'menu': 1, 'linux': 2, 'setup': 3}
if args[0] == 'status':
    print(json.dumps({'target': 2, 'sequence': 1, 'app_generation': '%s'}))
else:
    target = args[args.index('--target') + 1]
    print(json.dumps({'target': targets[target], 'sequence': 2, 'app_generation': '%s'}))
""" % (GENERATION, GENERATION)


def load_helper():
    # helper 没有 .py 后缀，spec_from_file_location 推断不出 loader。
    loader = importlib.machinery.SourceFileLoader("next_boot_helper", str(HELPER))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("next_boot_helper", loader))
    loader.exec_module(module)
    return module


def fake_result(mode, uid=0, size=0):
    return os.stat_result((mode, 1, 1, 1, uid, 0, size, 0, 0, 0))


def main():
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        config = work / "boot-request.json"
        config.write_text(json.dumps({"version": 1, "boot_device": DEVICE, "app_generation": GENERATION}))
        qbootctl, native = work / "qbootctl", work / "piano-boot-request"
        qbootctl.write_text(QBOOTCTL)
        native.write_text(NATIVE)
        qbootctl.chmod(0o755)
        native.chmod(0o755)
        slot_state = work / "slot.state"
        native_log = work / "native.log"
        qbootctl_log = work / "qbootctl.log"

        helper = load_helper()
        helper.CONFIG, helper.NATIVE, helper.QBOOTCTL = config, native, qbootctl

        real_stat, real_fstat, real_euid = os.stat, os.fstat, os.geteuid
        config_inode = real_stat(config).st_ino

        def fake_stat(path, *args, **kwargs):
            name = os.fspath(path)
            if name in (DEVICE, "/dev/disk/by-partlabel/boot_a"):
                return fake_result(stat.S_IFBLK | 0o660)
            if name == str(native):
                return fake_result(stat.S_IFREG | 0o755, size=len(NATIVE))
            return real_stat(path, *args, **kwargs)

        def fake_fstat(fd):
            info = real_fstat(fd)
            if info.st_ino != config_inode:
                return info
            return fake_result(stat.S_IFREG | 0o644, size=len(config.read_bytes()))

        def run(action):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                helper.main([action])
            return json.loads(output.getvalue())

        def native_calls():
            return native_log.read_text().splitlines() if native_log.exists() else []

        def qbootctl_calls():
            return qbootctl_log.read_text().splitlines() if qbootctl_log.exists() else []

        def reset_logs():
            for path in (native_log, qbootctl_log):
                path.unlink(missing_ok=True)

        os.geteuid, os.stat, os.fstat = (lambda: 0), fake_stat, fake_fstat
        try:
            # android：切活动槽（-s a），不碰请求页。
            slot_state.write_text("b")
            record = run("android")
            assert record["mode"] == "active-slot" and record["active_slot"] == "_a", record
            assert record["request_page_unchanged"] is True, record
            assert slot_state.read_text().strip() == "a", slot_state.read_text()
            assert qbootctl_calls() == ["-s a", "-a"], qbootctl_calls()
            assert native_calls() == [], native_calls()

            # linux：活动槽已是 B 时不写 GPT，只读确认后写请求页。
            reset_logs()
            slot_state.write_text("b")
            record = run("linux")
            assert record["active_slot"] == "_b" and record["target"] == 2, record
            # 一次是写请求页前的只读确认，一次是结果里的活动槽读回。
            assert qbootctl_calls() == ["-a", "-a"], qbootctl_calls()
            calls = native_calls()
            assert len(calls) == 2, calls
            assert calls[0].startswith("status --device"), calls
            assert "set --device" in calls[1] and "--target linux" in calls[1], calls
            assert "--expect-generation " + GENERATION in calls[1], calls

            # linux：活动槽是 A 时拒绝，绝不调用 -s b，也不写请求页。
            reset_logs()
            slot_state.write_text("a")
            try:
                run("linux")
            except ValueError as error:
                assert "fastboot set_active b" in str(error), error
            else:
                raise AssertionError("linux accepted an A-active slot")
            assert qbootctl_calls() == ["-a"], qbootctl_calls()
            assert native_calls() == [], native_calls()
            assert slot_state.read_text().strip() == "a", slot_state.read_text()

            # status：只读，不切槽也不写请求页。
            reset_logs()
            record = run("status")
            assert record["active_slot"] == "_a" and record["target"] == 2, record
            assert qbootctl_calls() == ["-a"], qbootctl_calls()
            assert native_calls() == ["status --device " + DEVICE], native_calls()

            # 配置指向 boot_a 时不是这条布局，拒绝切槽而不是猜另一个槽。
            config.write_text(json.dumps({"version": 1, "boot_device": "/dev/disk/by-partlabel/boot_a",
                                          "app_generation": GENERATION}))
            try:
                run("android")
            except ValueError as error:
                assert "只支持 Linux 在 boot_b" in str(error), error
            else:
                raise AssertionError("accepted a boot_a layout for the Android slot switch")
        finally:
            os.geteuid, os.stat, os.fstat = real_euid, real_stat, real_fstat

    print("OK: android 用 -s a 切槽；linux 只读确认 B 且从不调用 -s b；status 只读")
    return 0


if __name__ == "__main__":
    sys.exit(main())
