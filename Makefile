.PHONY: test plan clean

test:
	python3 tests/check_sources.py
	python3 tests/check_upstreams.py
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
	python3 tests/check_bundle_overlays.py
	python3 tests/check_patches.py
	python3 tests/check_fedora_kernel.py
	python3 tests/check_gunyah_source.py
	python3 tests/check_runtime_kernel_source.py
	python3 tests/check_fastrpc_runtime.py
	python3 tests/check_sensor_runtime.py
	python3 tests/check_camerad_adapter.py
	python3 tests/check_ffmpeg_port.py
	python3 tests/check_ffmpeg_install.py
	python3 tests/check_initramfs.py
	python3 tests/check_bootstrap_contract.py
	python3 tests/check_boot_evidence.py
	python3 tests/check_rootfs_policy.py
	python3 tests/check_stage_payload.py
	python3 tests/check_image_workflow.py
	python3 tests/check_rootfs_finalize.py

RELEASEVER ?= 44
plan:
	python3 scripts/build-fedora-rootfs.py --plan --releasever $(RELEASEVER)

clean:
	rm -rf build
