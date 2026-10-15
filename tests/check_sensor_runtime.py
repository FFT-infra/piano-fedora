#!/usr/bin/env python3
"""离线复核 iio-sensor-proxy 的 SSC 后端真的被编入，而不是被 meson 静默跳过。

根因：iio-sensor-proxy 3.9 把 ssc-support 声明为 feature/auto，src/meson.build 用
get_option('ssc-support').enabled() 决定是否编 drv-ssc-*。auto 即使找到 libssc 仍是
not enabled，SSC 后端会被静默省略，ELF 不链接 libssc。这里用真实 meson 复现该语义、
用真实编译器验证 ELF 真实性检查，并对固定源码做静态断言。缺工具或源码时跳过对应部分。
"""
import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

_SPEC = importlib.util.spec_from_file_location(
    "fedora_runtime", ROOT / "scripts/build-device-runtime.py")
runtime = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(runtime)

STUB_VERSION = "238.0"


def write_stub_pc(directory, name, version=STUB_VERSION):
    body = f"Name: {name}\nDescription: stub\nVersion: {version}\nLibs:\nCflags:\n"
    if name == "gio-2.0":
        # 真实 iio-sensor-proxy 的 gnome.compile_resources 会查这些 pkg-config 变量；
        # 缺它们会让 configure 在 config.h 之后报错，掩盖红绿判定。给出真实工具或占位。
        tools = {"glib_compile_resources": shutil.which("glib-compile-resources") or "/bin/true"}
        body += "giomodulesdir=/usr/lib64/gio/modules\n"
        body += "".join(f"{k}={v}\n" for k, v in tools.items())
    (directory / f"{name}.pc").write_text(body)


def meson_available():
    return shutil.which("meson") is not None


def readelf_available():
    return shutil.which("readelf") is not None


def check_readelf_dependency(path, expected, work):
    """用构建机 binutils 的 readelf -dW 直接验证 DT_NEEDED，与主机架构无关。"""
    output = subprocess.run(["readelf", "-dW", str(path)], check=True,
                            capture_output=True, text=True).stdout
    assert f"Shared library: [{expected}]" in output, output


def check_option_flag():
    """修复点：iio-sensor-proxy 的 meson setup 必须带上显式启用。"""
    assert runtime.sensor_meson_options("iio-sensor-proxy") == ["-Dssc-support=enabled"]
    assert runtime.sensor_meson_options("libssc") == []


def check_meson_auto_semantics(work):
    """用真实 meson 复现 feature/auto 在 libssc 存在时仍 not enabled，并验证显式启用生效。"""
    if not meson_available():
        print("SKIP: meson unavailable; auto-semantics reproduction not run")
        return
    project = work / "auto-project"
    project.mkdir()
    (project / "meson_options.txt").write_text(
        "option('ssc-support',\n"
        "       description: 'Enable Qualcomm SSC support',\n"
        "       type: 'feature',\n"
        "       value: 'auto')\n")
    (project / "meson.build").write_text(
        "project('ssc-auto-repro', 'c')\n"
        "o = get_option('ssc-support')\n"
        "dep = dependency('libssc', version: '>=0.2.1', required: o)\n"
        "message('RESULT enabled=@0@ auto=@1@ found=@2@'.format("
        "o.enabled(), o.auto(), dep.found()))\n")
    pc = work / "pc"
    pc.mkdir()
    write_stub_pc(pc, "libssc")
    env = {**os.environ, "PKG_CONFIG_PATH": str(pc)}

    def result(builddir, *extra):
        completed = subprocess.run(["meson", "setup", str(builddir), str(project), *extra],
                                   capture_output=True, text=True, env=env, timeout=120)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        line = [l for l in completed.stdout.splitlines() if "RESULT" in l]
        assert line, completed.stdout
        return line[0]

    auto = result(work / "b-auto")
    assert "enabled=false" in auto and "auto=true" in auto and "found=true" in auto, auto
    enabled = result(work / "b-enabled", "-Dssc-support=enabled")
    assert "enabled=true" in enabled, enabled


def check_elf_authenticity(work):
    """用真实编译器验证：不链接 libssc 或缺 SSC 标记的 ELF 必须被拒绝。"""
    if shutil.which("gcc") is None or not readelf_available():
        print("SKIP: gcc or readelf unavailable; ELF authenticity test not run")
        return
    lib_c = work / "libssc.c"
    lib_c.write_text("int ssc_marker(void){return 0;}\n")
    lib = work / "libssc.so.2"
    subprocess.run(["gcc", "-shared", "-fPIC", "-Wl,-soname,libssc.so.2",
                    "-o", str(lib), str(lib_c)], check=True, capture_output=True)
    markers = "".join(f'"{m}\\n"' for m in runtime.SSC_DRIVER_MARKERS)
    good_c = work / "good.c"
    good_c.write_text(
        "#include <stdio.h>\n"
        f"static const char markers[] = {markers};\n"
        "extern int ssc_marker(void);\n"
        "int main(void){printf(\"%s %d\\n\", markers, ssc_marker());return 0;}\n")
    good = work / "good"
    subprocess.run(["gcc", str(good_c), "-o", str(good), str(lib)],
                   check=True, capture_output=True)
    # 主机架构无关：真实 readelf 必须能看到 libssc.so.2。
    check_readelf_dependency(good, "libssc.so.2", work)
    proof = runtime.verify_ssc_runtime(good)
    assert "libssc.so.2" in proof["libssc"], proof
    assert proof["ssc_driver_markers"] == list(runtime.SSC_DRIVER_MARKERS)

    # 反面一：完全没有 libssc 依赖（就是本次构建遗漏产生的 ELF）。
    plain = work / "plain"
    plain_c = work / "plain.c"
    plain_c.write_text('#include <stdio.h>\nint main(void){puts("no ssc");return 0;}\n')
    subprocess.run(["gcc", str(plain_c), "-o", str(plain)], check=True, capture_output=True)
    try:
        runtime.verify_ssc_runtime(plain)
    except ValueError as error:
        assert "does not link libssc" in str(error), error
    else:
        raise AssertionError("ELF without libssc accepted")

    # 反面二：链接了 libssc 但没有 SSC 驱动标记（后端被跳过但库仍在）。
    linked = work / "linked"
    linked_c = work / "linked.c"
    linked_c.write_text(
        "#include <stdio.h>\n"
        "extern int ssc_marker(void);\n"
        "int main(void){return ssc_marker();}\n")
    subprocess.run(["gcc", str(linked_c), "-o", str(linked), str(lib)],
                   check=True, capture_output=True)
    try:
        runtime.verify_ssc_runtime(linked)
    except ValueError as error:
        assert "missing SSC driver markers" in str(error), error
    else:
        raise AssertionError("ELF without SSC driver markers accepted")

    # 反面三：非 ELF 输入直接拒绝，不误报通过。
    junk = work / "junk"
    junk.write_bytes(b"not an elf")
    try:
        runtime.verify_ssc_runtime(junk)
    except ValueError:
        pass
    else:
        raise AssertionError("non-ELF accepted")


def check_pinned_source(source):
    """固定源码必须仍是 feature/auto + .enabled() 的形态，否则根因前提已漂移。"""
    if source is None:
        print("SKIP: pinned iio-sensor-proxy source not provided; static assertion not run")
        return
    options = (source / "meson_options.txt").read_text()
    assert "type: 'feature'" in options and "value: 'auto'" in options, options
    assert "ssc-support" in options
    src = (source / "src/meson.build").read_text()
    assert "get_option('ssc-support').enabled()" in src


def check_real_source_red_green(source, work):
    """在真实 meson.build 上做红绿对照：auto 不定义 HAS_LIBSSC，显式启用才定义。"""
    if source is None or not meson_available():
        return
    pc = work / "real-pc"
    pc.mkdir()
    for name in ("udev", "systemd", "gio-2.0", "gudev-1.0", "polkit-gobject-1", "libssc"):
        write_stub_pc(pc, name)
    env = {**os.environ, "PKG_CONFIG_PATH": str(pc)}

    def has_libssc(builddir, *extra):
        completed = subprocess.run(["meson", "setup", str(builddir), str(source), *extra],
                                   capture_output=True, text=True, env=env, timeout=120)
        # 红绿都必须是真的成功配置（returncode 0）；把配置失败当成红会掩盖问题。
        assert completed.returncode == 0, completed.stdout + completed.stderr
        return "HAS_LIBSSC" in (builddir / "src/config.h").read_text()

    assert not has_libssc(work / "real-auto"), "plain meson setup unexpectedly enabled SSC"
    assert has_libssc(work / "real-on", *runtime.sensor_meson_options("iio-sensor-proxy")), \
        "explicit -Dssc-support=enabled did not enable SSC"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=Path(os.environ.get(
        "PIANO_TEST_IIO_SOURCE", "")) if os.environ.get("PIANO_TEST_IIO_SOURCE") else None)
    args = ap.parse_args()
    check_option_flag()
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        check_meson_auto_semantics(work)
        check_elf_authenticity(work)
        check_real_source_red_green(args.source, work)
    check_pinned_source(args.source)
    print("OK: SSC option enabled, auto-semantics reproduced, ELF authenticity rejects "
          "missing-libssc and missing-marker binaries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
