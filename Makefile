.PHONY: test test-device test-device-media plan plan-desktop clean build-rpms selinux

test:
	python3 scripts/run-checks.py

# 平板上的只读冒烟：内核与模块树、失败单元、相机与触控服务、音频默认输出、
# 上一次 ESP 升级的审计记录。DEVICE_HOST 是 ssh 目标。报告落在 SMOKE_OUTPUT。
test-device:
	test -n "$(DEVICE_HOST)"
	bash scripts/run-device-smoke.sh --host "$(DEVICE_HOST)" $(if $(SMOKE_OUTPUT),--out "$(SMOKE_OUTPUT)")

# 从主机驱动平板上的编解码验收：素材在主机生成，脚本推到设备上执行，
# 证据拉回 MEDIA_OUTPUT。MEDIA_HOST 是 ssh 目标。只在维护者机器上跑。
test-device-media:
	test -n "$(MEDIA_OUTPUT)" && test -n "$(MEDIA_HOST)"
	bash scripts/run-device-media.sh "$(MEDIA_HOST)" "$(MEDIA_OUTPUT)" $(FIXTURE_DIR)

RELEASEVER ?= $(shell python3 -c "import json; print(json.load(open('product.json'))['target']['releasever'])")
DESKTOP ?= gnome
plan:
	python3 scripts/build-clean-rootfs.py --plan --releasever $(RELEASEVER)

plan-desktop:
	python3 scripts/build-clean-rootfs.py --plan --releasever $(RELEASEVER) --desktop $(DESKTOP)

clean:
	rm -rf build

build-rpms:
	./scripts/build-rpms.sh

# rootfs/selinux/piano_hardware.pp 是本仓库唯一的已提交生成物：运行时
# semodule -i 加载的是编译后的模块，.te 本身不会被设备读取。改动 .te 之后
# 必须重跑这一步，否则镜像里装的是旧策略，而源码看起来已经改过。
# 需要 checkpolicy 与 policycoreutils（Fedora: dnf install checkpolicy）。
SELINUX_TE ?= rootfs/selinux/piano_hardware.te
SELINUX_FC ?= rootfs/selinux/piano_hardware.fc
SELINUX_PP ?= rootfs/selinux/piano_hardware.pp
selinux:
	mkdir -p build
	checkmodule -M -m -o build/piano_hardware.mod $(SELINUX_TE)
	semodule_package -o $(SELINUX_PP) -m build/piano_hardware.mod -f $(SELINUX_FC)
