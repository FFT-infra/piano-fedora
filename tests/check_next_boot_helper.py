#!/usr/bin/env python3
"""离线验证 piano-next-boot-helper 的请求页语义。

合体 BOOT 把 Android 内核、3 秒选择器与 UEFI 核心装进同一个 boot 镜像，
请求页就在这份镜像末尾。helper 只做一件事：读当前 BOOT 的 generation，
把它作为期望值写进 `piano-boot-request set --target ... --expect-generation`。
它不切活动槽、不写 GPT，也不假设 Linux 在哪个槽；`boot_device` 由
`/etc/piano/boot-request.json` 记录，boot_a 与 boot_b 都合法。

这里用假的 piano-boot-request 检查调用序列、generation 绑定的读回与错误
分支，不接触设备。真机行为由安装后的 `piano-next-boot` 与双系统往返验收
覆盖。
"""

import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "packages/piano-fedora-configs/usr/libexec/piano-next-boot-helper"
DEVICE = "/dev/disk/by-partlabel/boot_a"
OTHER_DEVICE = "/dev/disk/by-partlabel/boot_b"
GENERATION = "bcd89b66dde56140979a8ce3b46950c8"
OTHER_GENERATION = "6c38318a290175eba5ff2bb5ef106768"

NATIVE = """#!/usr/bin/env python3
import json, pathlib, sys
work = pathlib.Path(__file__).with_name('native.state')
log = pathlib.Path(__file__).with_name('native.log')
with log.open('a') as stream:
    stream.write(' '.join(sys.argv[1:]) + '\\n')
args = sys.argv[1:]
state = json.loads(work.read_text())
targets = {'android': 0, 'menu': 1, 'linux': 2, 'setup': 3}
if args[0] == 'status':
    print(json.dumps({'target': state['target'], 'sequence': state['sequence'],
                      'operation': 'status', 'app_generation': state['app_generation']}))
else:
    failure = pathlib.Path(__file__).with_name('set-fail')
    if failure.exists():
        print(failure.read_text().strip(), file=sys.stderr)
        sys.exit(1)
    expected = args[args.index('--expect-generation') + 1]
    if expected != state['app_generation']:
        print('generation changed; no write', file=sys.stderr)
        sys.exit(1)
    state['target'] = targets[args[args.index('--target') + 1]]
    state['sequence'] += 1
    override = pathlib.Path(__file__).with_name('set-target-override')
    if override.exists():
        state['target'] = int(override.read_text())
    generation = state['app_generation']
    gen_override = pathlib.Path(__file__).with_name('set-generation-override')
    if gen_override.exists():
        generation = gen_override.read_text().strip()
    work.write_text(json.dumps(state))
    print(json.dumps({'target': state['target'], 'sequence': state['sequence'],
                      'operation': 'set', 'app_generation': generation}))
"""


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
        native = work / "piano-boot-request"
        native.write_text(NATIVE)
        native.chmod(0o755)
        state = work / "native.state"
        native_log = work / "native.log"

        helper = load_helper()
        helper.CONFIG, helper.NATIVE = config, native

        real_stat, real_fstat, real_euid = os.stat, os.fstat, os.geteuid
        config_inode = None

        def fake_stat(path, *args, **kwargs):
            name = os.fspath(path)
            if name in (DEVICE, OTHER_DEVICE):
                return fake_result(stat.S_IFBLK | 0o660)
            if name == str(native):
                return fake_result(stat.S_IFREG | 0o755, size=len(NATIVE))
            return real_stat(path, *args, **kwargs)

        def fake_fstat(fd):
            info = real_fstat(fd)
            if info.st_ino != config_inode:
                return info
            return fake_result(stat.S_IFREG | 0o644, size=len(config.read_bytes()))

        def write_config(device=DEVICE, generation=GENERATION, body=None):
            nonlocal config_inode
            body = body if body is not None else {"version": 1, "boot_device": device,
                                                  "app_generation": generation}
            config.write_text(json.dumps(body))
            config_inode = real_stat(config).st_ino

        def reset_native(target=0, generation=GENERATION):
            state.write_text(json.dumps({"target": target, "sequence": 0,
                                         "app_generation": generation}))
            for name in ("native.log", "set-target-override", "set-generation-override", "set-fail"):
                (work / name).unlink(missing_ok=True)

        def run(*argv):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                helper.main(list(argv))
            return json.loads(output.getvalue())

        def calls():
            return native_log.read_text().splitlines() if native_log.exists() else []

        def expect_error(fragment, *argv):
            try:
                run(*argv)
            except ValueError as error:
                assert fragment in str(error), (fragment, error)
            else:
                raise AssertionError(f"accepted: {argv}")

        # helper 不再有 qbootctl 分支，也没有活动槽概念。
        source = HELPER.read_text()
        assert "qbootctl" not in source and not hasattr(helper, "QBOOTCTL"), "helper still touches qbootctl"

        os.geteuid, os.stat, os.fstat = (lambda: 0), fake_stat, fake_fstat
        try:
            # status：只读，读一次原生请求页，原样带回。
            write_config()
            reset_native(target=0)
            record = run("status")
            assert record["boot_device"] == DEVICE and record["reboot_planned"] is False, record
            assert record["target"] == 0 and record["app_generation"] == GENERATION, record
            assert "active_slot" not in record and "mode" not in record, record
            assert calls() == ["status --device " + DEVICE], calls()

            # android：把 generation 作为期望值写入 target=0，读回后返回。
            reset_native(target=2)
            record = run("android")
            assert record["target"] == 0 and record["sequence"] == 1, record
            assert record["boot_device"] == DEVICE, record
            assert calls() == ["status --device " + DEVICE,
                               "set --device %s --target android --expect-generation %s" % (DEVICE, GENERATION)], calls()

            # linux：同一条路径写 target=2，boot_device 仍来自配置。
            reset_native(target=0)
            record = run("linux")
            assert record["target"] == 2 and record["boot_device"] == DEVICE, record

            # boot_b 布局同样合法：helper 不假设 Linux 只在某个槽。
            write_config(device=OTHER_DEVICE)
            reset_native(target=0)
            record = run("linux")
            assert record["boot_device"] == OTHER_DEVICE and record["target"] == 2, record
            assert calls()[1].startswith("set --device " + OTHER_DEVICE), calls()

            # 写请求页时 BOOT 的 generation 变了：报错，不请求重启。
            write_config()
            reset_native()
            (work / "set-generation-override").write_text(OTHER_GENERATION)
            expect_error("引导镜像已改变", "linux")

            # 原生读回的目标与请求不同：报错，不请求重启。
            reset_native()
            (work / "set-target-override").write_text("2")
            expect_error("读回的目标不同", "android")

            # 原生写入失败：把它的错误原样带出。
            reset_native()
            (work / "set-fail").write_text("request page is read-only")
            expect_error("request page is read-only", "linux")

            # 配置与参数校验。
            write_config(device="/dev/disk/by-partlabel/boot_c")
            expect_error("boot_device", "linux")
            write_config(generation="not-a-generation")
            expect_error("app_generation", "status")
            write_config(body={"version": 1, "boot_device": DEVICE, "app_generation": GENERATION, "extra": True})
            expect_error("启动配置需要", "status")
            write_config()
            expect_error("status 是只读操作", "status", "--reboot")
            expect_error("用法", "uefi")

            # 非 root 不写任何东西。
            os.geteuid = lambda: 1000
            expect_error("请使用 piano-next-boot", "linux")
        finally:
            os.geteuid, os.stat, os.fstat = real_euid, real_stat, real_fstat

    print("OK: helper 只写请求页（generation 绑定、读回校验），不切槽也不碰 qbootctl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
