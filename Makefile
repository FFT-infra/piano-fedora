.PHONY: test test-runtime-camera plan clean

RUNTIME_CAMERA_SUNUEFI ?= build/sources/sunuefi
RUNTIME_CAMERA_PUBLIC_SOURCE ?= $(RUNTIME_CAMERA_SUNUEFI)/upstream/debian-piano-current
RUNTIME_CAMERA_UAPI ?= build/kernel-runtime/uapi
RUNTIME_CAMERA_OUTPUT ?= build/runtime-camera-test

test:
	python3 tests/check_sources.py
	python3 tests/check_artifact_run.py
	python3 tests/check_product.py
	python3 tests/check_links.py
	python3 tests/check_document_scope.py
	python3 tests/check_rootfs_plan.py
	python3 tests/check_f2fs_image.py
	python3 tests/check_f2fs_metadata.py
	python3 tests/check_first_partition.py
	python3 tests/check_root_capacity.py
	python3 tests/check_installer.py
	python3 tests/check_installer_export.py
	python3 tests/check_update_installer.py
	python3 tests/check_update_memory.py
	python3 tests/check_esp_image.py
	python3 tests/check_patches.py
	python3 tests/check_initramfs.py
	python3 tests/check_bootstrap_contract.py
	python3 tests/check_boot_evidence.py
	python3 tests/check_rootfs_policy.py
	python3 tests/check_stage_payload.py
	python3 tests/check_image_workflow.py
	python3 tests/check_rootfs_finalize.py

test-runtime-camera:
	python3 tests/check_runtime_camera.py \
		--sunuefi "$(RUNTIME_CAMERA_SUNUEFI)" \
		--public-source "$(RUNTIME_CAMERA_PUBLIC_SOURCE)" \
		--uapi "$(RUNTIME_CAMERA_UAPI)" \
		--output "$(RUNTIME_CAMERA_OUTPUT)"

RELEASEVER ?= 44
plan:
	python3 scripts/build-fedora-rootfs.py --plan --releasever $(RELEASEVER)

clean:
	rm -rf build
