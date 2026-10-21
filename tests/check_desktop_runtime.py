#!/usr/bin/env python3
"""运行时脚本必须覆盖四档桌面，而不是只认 GNOME。

三处硬编码藏在 `packages/piano-fedora-configs` 里：

- `usr/lib/piano/power-button` 长按只调 `org.gnome.SessionManager.Shutdown`，
  换到 KDE / niri / Hyprland 就静默无响应。
- `usr/libexec/piano-fake-tablet-mode` 扫 `gnome-shell` 进程，其余桌面下
  `SW_TABLET_MODE` 永远打不开。
- `usr/bin/piano-collect-boot`（initramfs 消费的副本
  `scripts/collect-boot-evidence.sh` 内容必须一致）只收集 `gdm.service`。

这里的断言执行真实源码：回退链按顺序失败/成功、logind 会话判定接受
wayland 用户会话并拒绝 greeter 与 tty、采集器按实际存在的单元展开。
不启动设备、不写 /dev。
"""

import importlib.machinery
import importlib.util
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "piano-fedora-configs"
POWER_BUTTON = PACKAGE / "usr" / "lib" / "piano" / "power-button"
TABLET_MODE = PACKAGE / "usr" / "libexec" / "piano-fake-tablet-mode"
COLLECTORS = (
    PACKAGE / "usr" / "bin" / "piano-collect-boot",
    ROOT / "scripts" / "collect-boot-evidence.sh",
)

class _Stub:
    """任意属性都返回另一个桩的占位模块。"""

    def __getattr__(self, _name):
        return _Stub()

    def __call__(self, *args, **kwargs):
        return _Stub()

    def __iter__(self):
        return iter(())


class _SubprocessStub:
    """替换模块内的 `subprocess`：只实现 run() 与 TimeoutExpired。

    不能改真正的 `subprocess` 模块（那是全进程共享的），只重绑脚本模块
    命名空间里的 `subprocess` 名字。
    """

    TimeoutExpired = subprocess.TimeoutExpired

    def __init__(self, run):
        self.run = run

    def __getattr__(self, name):
        return getattr(subprocess, name)


def load_module(name, path, stub_modules=()):
    """按文件路径加载脚本；脚本顶层 import 的第三方模块可以被替换。

    `piano-fake-tablet-mode` 顶层 `import evdev`，构建机上没有这个包。把
    桩塞进 sys.modules 再 exec，测的是真实源码里的函数。
    """
    saved = {key: sys.modules.get(key) for key in stub_modules}
    for key in stub_modules:
        sys.modules[key] = _Stub()
    try:
        # 这些脚本没有 .py 后缀，spec_from_file_location 推不出 loader。
        loader = importlib.machinery.SourceFileLoader(name, str(path))
        spec = importlib.util.spec_from_loader(name, loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


def check_power_button_falls_back_across_desktops():
    """长按的关机入口按 GNOME → KDE → logind 依次回退，每步都记日志。

    真机上只有一种桌面会应答；其余两条必须被尝试过，而不是第一次失败就
    静默返回。桩掉模块里的 `call()`，记录每一次尝试。
    """
    module = load_module("power_button", POWER_BUTTON)
    attempts = []
    messages = []

    def stub_with(succeeds_when):
        module.call = lambda args, operation: (
            attempts.append((operation, args)),
            succeeds_when(operation))[1]
        module.say = messages.append

    stub_with(lambda operation: operation == "GNOME power menu")
    attempts.clear()
    assert module.power_dialog() is True
    assert [row[0] for row in attempts] == ["GNOME power menu"], attempts

    stub_with(lambda operation: operation == "KDE power menu")
    attempts.clear()
    assert module.power_dialog() is True
    order = [row[0] for row in attempts]
    assert order == ["GNOME power menu", "KDE power menu"], order

    stub_with(lambda _operation: False)
    attempts.clear()
    assert module.power_dialog() is False
    order = [row[0] for row in attempts]
    assert order == ["GNOME power menu", "KDE power menu", "systemctl poweroff"], order
    assert attempts[-1][1] == ["systemctl", "poweroff"], attempts[-1]
    assert any("no shutdown path succeeded" in message for message in messages), messages

    # 两条 D-Bus 调用的目标必须是各自的真实接口名。
    text = POWER_BUTTON.read_text(encoding="utf-8")
    for needle in ("org.gnome.SessionManager.Shutdown",
                   "org.kde.Shutdown.logoutAndShutdown",
                   "/org/kde/Shutdown",
                   "org.kde.Shutdown"):
        assert needle in text, f"{needle} missing from the power button fallback chain"


def check_tablet_mode_asks_logind_for_a_user_session():
    """不再按进程名判断桌面；问 logind 会话的 Class / Type / Active。

    桩 `loginctl`：列表给会话 id，`show-session` 按 id 返回属性。验证
    只有 Active=yes、Class=user|greeter、Type=wayland|x11|mir 且用户名
    不是 greeter 的会话才算"桌面已经起来"。
    """
    module = load_module("tablet_mode", TABLET_MODE, stub_modules=("evdev",))
    assert not hasattr(module, "graphical_user_shell_running"), (
        "the old process-scan helper should be gone")
    assert hasattr(module, "graphical_user_session_running")

    sessions = {}

    def fake_run(args, **_kwargs):
        if args[:2] == ["loginctl", "list-sessions"]:
            body = "\n".join(
                f"{session_id} {row['User']} seat0 {row['Class']}"
                for session_id, row in sessions.items())
            return subprocess.CompletedProcess(args, 0, body + ("\n" if body else ""), "")
        if args[:2] == ["loginctl", "show-session"]:
            session_id = args[2]
            row = sessions.get(session_id)
            if row is None:
                return subprocess.CompletedProcess(args, 1, "", "no such session")
            body = "".join(f"{key}={row[key]}\n"
                           for key in ("Name", "User", "Class", "Type") if key in row)
            body += "Active=yes\n" if row.get("Active") else "Active=no\n"
            return subprocess.CompletedProcess(args, 0, body, "")
        raise AssertionError(f"unexpected command: {args}")

    module.subprocess = _SubprocessStub(fake_run)

    assert module.graphical_user_session_running() is False  # 没有会话

    sessions["1"] = {"Name": "piano", "User": "1000", "Class": "user",
                     "Type": "wayland", "Active": True}
    assert module.graphical_user_session_running() is True, (
        "an active wayland user session must count")

    sessions["1"]["Active"] = False
    assert module.graphical_user_session_running() is False, "inactive session counted"

    sessions["1"].update(Active=True, Type="tty")
    assert module.graphical_user_session_running() is False, "a tty session counted"

    sessions["1"].update(Type="wayland", Class="manager")
    assert module.graphical_user_session_running() is False, "a non-user class counted"

    sessions["1"].update(Class="user", Name="gdm")
    assert module.graphical_user_session_running() is False, "a greeter user counted"

    # 混合场景：greeter 排在前面，用户的会话在后面。
    sessions.clear()
    sessions["1"] = {"Name": "greetd", "User": "greeter", "Class": "user",
                     "Type": "wayland", "Active": True}
    sessions["2"] = {"Name": "piano", "User": "1000", "Class": "user",
                     "Type": "x11", "Active": True}
    assert module.graphical_user_session_running() is True

    # loginctl 不可用时不抛异常。
    def missing(_args, **_kwargs):
        raise FileNotFoundError("loginctl")

    module.subprocess = _SubprocessStub(missing)
    assert module.graphical_user_session_running() is False

    # 判定逻辑本身不再翻 /proc 找进程名（模块注释里提到旧实现是允许的）。
    import ast
    tree = ast.parse(TABLET_MODE.read_text(encoding="utf-8"))
    helper = next(node for node in tree.body
                  if isinstance(node, ast.FunctionDef)
                  and node.name == "graphical_user_session_running")
    # 判定逻辑本身不再翻 /proc 找进程名（文档字符串里提到旧实现是允许的）：
    # 只看函数体里的字符串常量与属性访问。
    docstring_node = (helper.body[0].value
                      if helper.body and isinstance(helper.body[0], ast.Expr)
                      and isinstance(helper.body[0].value, ast.Constant)
                      and isinstance(helper.body[0].value.value, str) else None)
    constants = [node.value for node in ast.walk(helper)
                 if isinstance(node, ast.Constant) and isinstance(node.value, str)
                 and node is not docstring_node]
    joined = "\n".join(constants)
    assert "loginctl" in joined, "the session probe does not ask logind"
    assert "/proc" not in joined, "the session probe still scans /proc"
    assert "gnome-shell" not in joined, "the session probe still names a shell"
    assert "GREETER_USERS" in TABLET_MODE.read_text(encoding="utf-8")


def check_collectors_cover_every_display_manager():
    """采集器按镜像里实际存在的单元收集，而不是写死 gdm。"""
    collector, initramfs_copy = COLLECTORS
    first = collector.read_bytes()
    second = initramfs_copy.read_bytes()
    assert first == second, (
        "piano-collect-boot and scripts/collect-boot-evidence.sh must stay "
        "byte-identical; initramfs ships the second copy")
    text = first.decode("utf-8")
    for unit in ("gdm.service", "sddm.service", "plasmalogin.service", "greetd.service"):
        assert unit in text, f"{unit} missing from the collector"
    assert "display-manager.service" in text
    assert re.search(r"for unit in gdm\.service sddm\.service plasmalogin\.service greetd\.service; do\n"
                     r"\s+\[ ! -e \"/usr/lib/systemd/system/\$unit\" \] \|\|", text), (
        "units must be collected only when present")
    assert "journalctl" in text and "-u $unit" in text

    # 在 BusyBox sh 里以桩 rootfs 执行：只有 sddm.service 存在时，输出里
    # 只应出现 sddm，不应出现 gdm / greetd。
    busybox = _find_busybox()
    if busybox is None:
        print("SKIP: busybox unavailable, collector executed without stubs")
        return
    with tempfile.TemporaryDirectory() as temporary:
        stub = Path(temporary)
        units = stub / "usr" / "lib" / "systemd" / "system"
        units.mkdir(parents=True)
        (units / "sddm.service").write_text("[Unit]\n")
        # systemd 是否在跑与主题无关；把它指向一个一定存在的目录，让
        # systemd 段总是执行。单元目录同样换成本次的桩树。
        systemd_running = stub / "run-systemd"
        systemd_running.mkdir()
        stubbed = (text
                   .replace("/usr/lib/systemd/system", str(units))
                   .replace("/run/systemd/system", str(systemd_running)))
        script = stub / "collector"
        # 采集器的第一行会把 PATH 固定成系统目录；桩工具要能被解析到，
        # 就把那行换成桩目录（被测的是单元列表，不是 PATH 本身）。
        bin_dir = stub / "bin"
        bin_dir.mkdir()
        stubbed = re.sub(r"^export PATH=.*$", f"export PATH={bin_dir}:/usr/bin:/bin",
                         stubbed, count=1, flags=re.M)
        assert str(bin_dir) in stubbed, "collector no longer sets PATH on its first line"
        script.write_text(stubbed)
        script.chmod(0o755)
        log = stub / "calls.log"
        for name in ("systemctl", "journalctl", "dmesg", "uname", "cat", "readlink"):
            tool = bin_dir / name
            tool.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\n')
            tool.chmod(0o755)
        process = subprocess.run(
            [busybox, "sh", str(script)],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"},
        )
        assert process.returncode == 0, process.stderr
        calls = log.read_text(encoding="utf-8")
        shown = re.findall(r"systemctl show (\S+)", calls)
        assert "sddm.service" in shown, calls
        assert "gdm.service" not in shown, calls
        assert "greetd.service" not in shown, calls
        journal = [line for line in calls.splitlines() if line.startswith("journalctl")]
        assert journal, calls
        assert "-u sddm.service" in journal[0], journal
        assert "-u gdm.service" not in journal[0], journal


def _find_busybox():
    for candidate in ("busybox", "/usr/bin/busybox", "/bin/busybox"):
        try:
            process = subprocess.run([candidate, "true"], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if process.returncode == 0:
            return candidate
    return None


def check_login_units_match_the_profiles():
    """运行时脚本认识的登录单元集合要与四档 profile 一致。

    采集器里写死的三个单元、fake-tablet-mode 的 greeter 用户表，都必须覆盖
    profile 里每一档的 display_manager.unit 与用户；漏一档就意味着那档镜像
    采集不到登录证据。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import piano_desktop

    collector_text = COLLECTORS[0].read_text(encoding="utf-8")
    tablet_text = TABLET_MODE.read_text(encoding="utf-8")
    greeter_users = {"gdm": "gdm", "sddm": "sddm", "plasmalogin": "plasmalogin", "greetd": "greetd"}
    for desktop in piano_desktop.matrix():
        context = piano_desktop.load(desktop, "44")
        manager = context["profile"]["display_manager"]
        if manager is None:
            continue
        unit = manager["unit"]
        assert unit in collector_text, (
            f"{unit} ({desktop}) is never collected by piano-collect-boot")
        assert unit in piano_desktop.enable_units(context), (desktop, unit)
        greeter_user = greeter_users[manager["package"]]
        assert f'"{greeter_user}"' in tablet_text, (
            f"{greeter_user} missing from the tablet-mode greeter exclusion")


def main():
    check_power_button_falls_back_across_desktops()
    check_tablet_mode_asks_logind_for_a_user_session()
    check_collectors_cover_every_display_manager()
    check_login_units_match_the_profiles()
    print("OK: runtime scripts cover GNOME, KDE, niri and Hyprland login paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
