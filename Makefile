.PHONY: test test-device-media plan plan-desktop clean build-rpms release-bundle

test:
	python3 tests/check_sources.py
	python3 tests/check_upstreams.py
	python3 tests/check_upstream_watch.py
	python3 tests/check_artifact_run.py
	python3 tests/check_release_workflow.py
	python3 tests/check_release_desktops.py
	python3 tests/check_release_assets.py
	python3 tests/check_render_screenshot.py
	python3 tests/check_device_media.py
	python3 tests/check_browser_video.py
	python3 tests/check_copr_release.py
	python3 tests/check_product.py
	python3 tests/check_links.py
	python3 tests/check_readme_parity.py
	python3 tests/check_document_scope.py
	python3 tests/check_f2fs_image.py
	python3 tests/check_f2fs_metadata.py
	python3 tests/check_first_partition.py
	python3 tests/check_manual_partition.py
	python3 tests/check_root_capacity.py
	python3 tests/check_installer.py
	python3 tests/check_prepare_device_boot.py
	python3 tests/check_boot_tools.py
	python3 tests/check_installer_export.py
	python3 tests/check_update_installer.py
	python3 tests/check_update_memory.py
	python3 tests/check_esp_image.py
	python3 tests/check_bundle_overlays.py
	python3 tests/check_patches.py
	python3 tests/check_uefi_live_rm.py
	python3 tests/check_fedora_kernel.py
	python3 tests/check_iris_source.py
	python3 tests/check_gunyah_source.py
	python3 tests/check_production_kernel.py
	python3 tests/check_runtime_kernel_source.py
	python3 tests/check_fastrpc_runtime.py
	python3 tests/check_sensor_runtime.py
	python3 tests/check_ssc_retry.py
	python3 tests/check_camerad_adapter.py
	python3 tests/check_ffmpeg_port.py
	python3 tests/check_ffmpeg_install.py
	python3 tests/check_initramfs.py
	python3 tests/check_boot_container.py
	python3 tests/check_bootstrap_contract.py
	python3 tests/check_boot_evidence.py
	python3 tests/check_boot_log_rotation.py
	python3 tests/check_rootfs_policy.py
	python3 tests/check_stage_payload.py
	python3 tests/check_image_workflow.py
	python3 tests/check_runtime_extras.py
	python3 tests/check_rootfs_finalize.py
	python3 tests/check_default_password.py
	python3 tests/check_clean_rootfs.py
	python3 tests/check_desktop_profiles.py
	python3 tests/check_desktop_runtime.py
	python3 tests/check_package_ownership.py
	python3 tests/check_flash_script_guard.py
	python3 tests/check_first_boot_ssh_key.py
	python3 tests/check_kernel_package.py
	python3 tests/check_package_replaces.py
	python3 tests/check_release_provenance.py
	python3 tests/check_manual_flash_flow.py
	python3 tests/check_next_boot_helper.py

# 从主机驱动平板上的编解码验收：素材在主机生成，脚本推到设备上执行，
# 证据拉回 MEDIA_OUTPUT。MEDIA_HOST 是 ssh 目标。只在维护者机器上跑。
test-device-media:
	test -n "$(MEDIA_OUTPUT)" && test -n "$(MEDIA_HOST)"
	bash scripts/run-device-media.sh "$(MEDIA_HOST)" "$(MEDIA_OUTPUT)" $(FIXTURE_DIR)

RELEASEVER ?= 44
DESKTOP ?= gnome
plan:
	python3 scripts/build-clean-rootfs.py --plan --releasever $(RELEASEVER)

plan-desktop:
	python3 scripts/build-clean-rootfs.py --plan --releasever $(RELEASEVER) --desktop $(DESKTOP)

clean:
	rm -rf build

build-rpms:
	./scripts/build-rpms.sh

release-bundle:
	./scripts/package-release-bundle.sh
