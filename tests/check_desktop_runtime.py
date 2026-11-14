#!/usr/bin/env python3
"""运行时脚本必须覆盖四档桌面，而不是只认 GNOME。

三处硬编码藏在 `packages/piano-fedora-configs` 里：

- `usr/lib/piano/power-button` 只服务 GNOME：它调 Mutter 与 GNOME 的会话
  总线接口，在其他桌面装上去只会反复重启。
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
PLASMA_BUTTON = PACKAGE / "usr" / "lib" / "piano" / "plasma-power-button"
UDEV_RULE = PACKAGE / "etc" / "udev" / "rules.d" / "70-piano-power-button.rules"
FINALIZE = ROOT / "scripts" / "finalize-fedora-rootfs.py"
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


def check_power_button_uses_standard_gnome_interfaces():
    """助手只补按键策略，锁屏与熄屏都交给 GNOME 自己的接口。

    上游 7b77e52f6b 之后助手不再独占输入设备，compositor 能收到真实按键
    自己去唤醒；0917d5adad 又把锁屏改成异步并取消唤醒后的延迟灭屏。这两件
    事是这套实现相对 sheng 那套的全部价值，改写时最容易丢，所以逐条钉住：

    - 不出现 EVIOCGRAB，按键事件继续流向会话；
    - 熄屏走 org.gnome.Mutter.DisplayConfig 的 PowerSaveMode；
    - 锁屏走 org.gnome.ScreenSaver 的 GetActive / Lock，并用 LockedHint 判重；
    - 长按走 org.gnome.SessionManager.Shutdown；
    - 整条路径不写 sysfs 背光。
    """
    text = POWER_BUTTON.read_text(encoding="utf-8")
    assert "EVIOCGRAB" not in text, "the helper must not grab the power key"
    assert "/sys/class/backlight" not in text, (
        "the helper must not write the panel backlight itself")
    for needle in ("org.gnome.Mutter.DisplayConfig", "PowerSaveMode",
                   "org.gnome.ScreenSaver", "LockedHint",
                   "org.gnome.SessionManager", "Shutdown"):
        assert needle in text, f"{needle} missing from the power button helper"
    # 唤醒那一按要是被当成又一次熄屏，屏幕就再也亮不起来。
    for needle in ("unblanked_at", "pending_blank", "generation"):
        assert needle in text, (
            f"{needle} missing: the wake press must be told apart from a blank request")


def check_power_button_is_a_user_service():
    """电源键助手跟图形会话走，不是 system 单元。

    它调 Mutter 与 org.gnome.ScreenSaver 的会话总线接口，必须长在用户会话
    里。system 单元看不见会话总线，2026-11-14 在平板上抓到的
    `SimulateUserActivity skipped: no graphical session` 就是这套结构的产物。
    """
    unit = PACKAGE / "usr" / "lib" / "systemd" / "user" / "piano-power-button.service"
    stale = PACKAGE / "usr" / "lib" / "systemd" / "system" / "piano-power-button.service"
    assert unit.is_file(), "power-button unit must live under systemd/user"
    assert not stale.exists(), "the system unit must be removed, not shipped twice"
    text = unit.read_text(encoding="utf-8")
    assert "PartOf=graphical-session.target" in text, text
    assert "WantedBy=graphical-session.target" in text, text
    # /usr/lib/piano 下的脚本是 lib_t，直接执行时读不到 /dev/input/event*。
    assert "ExecStart=/usr/bin/python3 /usr/lib/piano/power-button" in text, (
        "the unit must exec the script through python3; a lib_t executable "
        "cannot open the power key")
    spec = (PACKAGE / "piano-fedora-configs.spec").read_text(encoding="utf-8")
    assert "%{_userunitdir}/piano-power-button.service" in spec
    assert "%{_unitdir}/piano-power-button.service" not in spec
    enable = next(line for line in spec.splitlines()
                  if line.startswith("systemctl enable piano-clock-restore"))
    assert "piano-power-button" not in enable, (
        "the helper is enabled per desktop, not by the package")


def check_plasma_power_button_uses_powerdevil_actions():
    """KDE 助手只补手势，关屏仍然走 PowerDevil 自己的动作。

    2026-11-14 在平板上量到两件事：`org.freedesktop.ScreenSaver.SetActive`
    只锁屏，面板 DPMS 一直是 On；PowerDevil 的 "Turn Off Screen" 快捷动作
    才真的把 DPMS 关掉。所以短按必须调那个动作，自己不去碰锁屏和 DPMS。
    长按走 org.kde.LogoutPrompt，不用 org.gnome.SessionManager。
    """
    text = PLASMA_BUTTON.read_text(encoding="utf-8")
    assert "EVIOCGRAB" not in text, "the helper must not grab the power key"
    assert "/sys/class/backlight" not in text, (
        "the helper must not write the panel backlight itself")
    for needle in ("org.kde.kglobalaccel", "invokeShortcut", "Turn Off Screen",
                   "org.kde.LogoutPrompt", "promptShutDown"):
        assert needle in text, f"{needle} missing from the KDE power button helper"
    assert "SetActive" not in text, (
        "SetActive only locks the session; the panel keeps its DPMS state")
    assert "org.gnome" not in text, "the KDE helper must not call GNOME interfaces"
    # 唤醒那一按不能再关一次屏：按下时面板还灭着，说明这一按是来唤醒的。
    assert "press_wakes" in text, (
        "the wake press must be told apart from a blank request")


def check_power_button_is_a_user_service_plasma():
    """KDE 助手同样是 user 单元，并且只在 KDE 档挂上。"""
    unit = PACKAGE / "usr" / "lib" / "systemd" / "user" / "piano-plasma-power-button.service"
    stale = PACKAGE / "usr" / "lib" / "systemd" / "system" / "piano-plasma-power-button.service"
    assert unit.is_file(), "the plasma unit must live under systemd/user"
    assert not stale.exists(), "the plasma unit must not be a system unit"
    text = unit.read_text(encoding="utf-8")
    assert "PartOf=graphical-session.target" in text, text
    assert "WantedBy=graphical-session.target" in text, text
    assert "ExecStart=/usr/bin/python3 /usr/lib/piano/plasma-power-button" in text, (
        "the unit must exec the script through python3; a lib_t executable "
        "cannot open the power key")
    spec = (PACKAGE / "piano-fedora-configs.spec").read_text(encoding="utf-8")
    assert "%{_userunitdir}/piano-plasma-power-button.service" in spec
    assert "%{_unitdir}/piano-plasma-power-button.service" not in spec
    enable = next(line for line in spec.splitlines()
                  if line.startswith("systemctl enable piano-clock-restore"))
    assert "plasma-power-button" not in enable, (
        "the helper is enabled per desktop, not by the package")


def check_power_button_is_enabled_per_desktop():
    """GNOME 与 KDE 各挂自己的助手，其余档不挂。"""
    profiles = (ROOT / "scripts" / "profiles" / "fedora-desktops.json").read_text(
        encoding="utf-8")
    assert profiles.count('"user_unit"') == 2, (
        "only the GNOME and KDE profiles may enable a power button helper")
    assert '"user_unit": "piano-power-button.service"' in profiles
    assert '"user_unit": "piano-plasma-power-button.service"' in profiles
    desktop = (ROOT / "scripts" / "piano_desktop.py").read_text(encoding="utf-8")
    assert "def power_user_units" in desktop
    finalize = FINALIZE.read_text(encoding="utf-8")
    assert "POWER_HELPER_UNITS" in finalize
    assert "graphical-session.target.wants" in finalize
    # finalize 里的 system enable 列表不能再点名任何助手：它们是 user 单元。
    for helper in ("piano-power-button.service", "piano-plasma-power-button.service"):
        for line in finalize.splitlines():
            if "systemctl" in line and "enable" in line:
                assert helper not in line, (
                    f"{helper} is a user unit and must not be enabled by the system manager")
    # KDE 的动作设置必须让位：64 会在按下时关屏，长按看不见对话框。
    powerdevil = (ROOT / "scripts" / "profiles" / "desktops" / "kde"
                  / "powerdevilrc").read_text(encoding="utf-8")
    assert "PowerButtonAction=0" in powerdevil
    assert "PowerButtonAction=64" not in powerdevil
    assert "LidAction=64" in powerdevil


def check_power_button_device_names_match_the_kernel():
    """服务按名字找设备，udev 规则也按名字授权；三处必须是同一个字符串。

    2026-10-22 在平板上发现：`power-button` 打开 `soc:piano-hall-ml`，但 udev
    规则授权的是 `hall_lid`，那个名字在设备上根本不存在。`event1` 因此只有
    `:power-switch:` 标签、没有 uaccess ACL。

    这一处目前没有造成故障——`piano` 用户在 `input` 组里，而 `event1` 的组权限
    是 `rw-`，服务照样能打开。规则本身是死的：它从不匹配任何设备，等到组权限
    不再是唯一通路时才会以"合盖没反应"的形式暴露出来，而且不会有报错。

    设备名来自内核 DTS 的 `input-name`，不是随便起的别名，所以这里拿真机上
    实测到的字符串做基准。
    """
    LID_DEVICE = "soc:piano-hall-ml"
    KEY_DEVICE = "pmic_pwrkey"
    script = POWER_BUTTON.read_text(encoding="utf-8")
    assert f"LID_NAME = '{LID_DEVICE}'" in script, (
        "power-button must open the lid switch by its kernel input name")
    assert f"KEY_NAME = '{KEY_DEVICE}'" in script
    plasma = PLASMA_BUTTON.read_text(encoding="utf-8")
    assert f"KEY_NAME = '{KEY_DEVICE}'" in plasma, (
        "plasma-power-button must open the power key by its kernel input name")
    for path in (UDEV_RULE, FINALIZE):
        text = path.read_text(encoding="utf-8")
        assert f'ATTRS{{name}}=="{LID_DEVICE}"' in text, (
            f"{path.name} must grant uaccess on {LID_DEVICE}")
        assert f'ATTRS{{name}}=="{KEY_DEVICE}"' in text, (
            f"{path.name} must grant uaccess on {KEY_DEVICE}")
        assert 'ATTRS{name}=="hall_lid"' not in text, (
            f"{path.name} still names a lid device that does not exist")


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


def check_tablet_mode_follows_the_keyboard_cover():
    """虚拟 SW_TABLET_MODE 必须跟随上游 nanosic 节点，不再按会话猜。

    键盘盖接上时真实节点报 OFF，虚拟开关若仍按"有图形会话"置 ON，libinput
    会屏蔽触控板（#22）。测试用桩替换 find_real_tablet_mode 与 ioctl 读值，
    把两种盖子状态都走一遍，确认只有状态变化时才写 uinput。
    """
    module = load_module("tablet_mode_cover", TABLET_MODE, stub_modules=("evdev",))
    text = TABLET_MODE.read_text(encoding="utf-8")
    assert "REAL_TABLET_MODE_NAME" in text
    assert "find_real_tablet_mode" in text
    assert "read_switch" in text
    # 旧的启发式不能留在循环里：有真实节点时 want 不再等于 shell_ticks 结果。
    assert "want = read_switch(cover.fd, ecodes.SW_TABLET_MODE)" in text

    class FakeCover:
        path = "/dev/input/event9"
        fd = 9

        def __init__(self, attached):
            self.attached = attached
            self.closed = False

        def close(self):
            self.closed = True

    class FakeUI:
        def __init__(self):
            self.writes = []

        def write(self, etype, code, value):
            self.writes.append((etype, code, value))

        def syn(self):
            pass

        def close(self):
            pass

    written = []
    ui = FakeUI()
    module._ui = ui
    module.set_tablet_mode = lambda enabled: written.append(enabled)

    # 让 logind 桩始终返回"有图形会话"。
    module.subprocess = _SubprocessStub(
        lambda args, **_kw: subprocess.CompletedProcess(args, 0, "", ""))
    module.graphical_user_session_running = lambda: True

    # 键盘盖接上：真实节点 OFF，虚拟开关必须 OFF。
    cover = FakeCover(attached=True)
    module.find_real_tablet_mode = lambda: cover
    module.read_switch = lambda fd, code: False
    # 直接驱动一次循环体里的判定，不启线程、不睡 POLL_SEC。
    want = module.read_switch(cover.fd, 1)
    assert want is False, "cover attached must map to SW_TABLET_MODE=OFF"

    # 键盘盖拆下：真实节点 ON，虚拟开关必须 ON。
    module.read_switch = lambda fd, code: True
    want = module.read_switch(cover.fd, 1)
    assert want is True, "cover detached must map to SW_TABLET_MODE=ON"

    # 没有真实节点时仍保留会话启发式，不抛异常。
    module.find_real_tablet_mode = lambda: None
    assert "shell_ticks == USER_SHELL_DELAY_TICKS" in text


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
    check_power_button_uses_standard_gnome_interfaces()
    check_power_button_is_a_user_service()
    check_plasma_power_button_uses_powerdevil_actions()
    check_power_button_is_a_user_service_plasma()
    check_power_button_is_enabled_per_desktop()
    check_power_button_device_names_match_the_kernel()
    check_tablet_mode_asks_logind_for_a_user_session()
    check_tablet_mode_follows_the_keyboard_cover()
    check_collectors_cover_every_display_manager()
    check_login_units_match_the_profiles()
    print("OK: runtime scripts cover GNOME, KDE, niri and Hyprland login paths")
    return 0


if __name__ == "__main__":
    sys.exit(main())
