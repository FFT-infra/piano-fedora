"""主机测试用的内核替身：只带合成板级 DTB 要读的两处。

真实的 sm8750.dtsi 在重放出来的内核源码树里，make test 拿不到。替身的温区节点
带 piano-tsens.dtso 引用的 CPU 温区和一个带降频映射的 GPU 温区，dt-bindings 取自
SunUEFI 检出的内核头。真实的那棵树由 RPM 矩阵 job 合成后再核对。
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

TSENS_OVERLAY = "linux/dts/piano-tsens.dtso"
SOC_SOURCE = "arch/arm64/boot/dts/qcom/sm8750.dtsi"

GPU_ZONE = """
		gpuss-0-thermal {
			thermal-sensors = <&tsens3 1>;
			trips {
				gpu_alert: trip-point0 {
					temperature = <105000>;
					hysteresis = <5000>;
					type = "passive";
				};
			};
			cooling-maps {
				map0 {
					trip = <&gpu_alert>;
					cooling-device = <&gpu THERMAL_NO_LIMIT THERMAL_NO_LIMIT>;
				};
			};
		};
"""


def cpu_zones(sunuefi):
    """overlay 绑了降频映射的 CPU 温区名，替身跟着上游的列表走。"""
    text = (Path(sunuefi) / TSENS_OVERLAY).read_text()
    return re.findall(r"PIANO_CPU_COOLING\(([\w-]+-thermal),", text)


def git(repository, *args):
    # 身份、签名与钩子都钉死，结果不随跑测试的人的 git 配置变。
    subprocess.run(["git", "-C", str(repository), "-c", "user.name=fixture",
                    "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false",
                    "-c", "core.hooksPath=/dev/null", *args],
                   check=True, capture_output=True)


def make_kernel_source(root, sunuefi, zones=None, commit=None):
    """建出 (内核产物目录, 内核源码树)。zones 省略时带 overlay 引用的全部 CPU 温区。"""
    root, sunuefi = Path(root), Path(sunuefi)
    source = root / "source"
    # 稀疏检出里有指向 uapi 的悬空软链，照原样带过去。
    shutil.copytree(sunuefi / "upstream/linux-piano/include/dt-bindings",
                    source / "include/dt-bindings", symlinks=True)
    names = cpu_zones(sunuefi) if zones is None else zones
    body = "".join(f"\t\t{name} {{\n\t\t\tthermal-sensors = <&tsens0 {index + 1}>;\n\t\t}};\n"
                   for index, name in enumerate(names))
    soc = source / SOC_SOURCE
    soc.parent.mkdir(parents=True)
    soc.write_text("// SPDX-License-Identifier: BSD-3-Clause\n"
                   "/* TEST FIXTURE: not the SM8750 SoC description. */\n\n"
                   "#include <dt-bindings/thermal/thermal.h>\n\n"
                   "/ {\n\tthermal-zones {\n" + body + GPU_ZONE + "\t};\n};\n")
    git(source, "init", "-q")
    git(source, "add", "-A")
    git(source, "commit", "-q", "-m", "fixture")
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    kernel = root / "kernel"
    kernel.mkdir()
    (kernel / "manifest.json").write_text(json.dumps({"source_commit": commit or head}))
    return kernel, source
