# 本地构建与验证

正式出货产物均在 GitHub Actions arm64 环境构建（需多次 run 产物摘要绑定）；本地主机负责取源、重放检查补丁、运行全套离线契约测试，以及按正确顺序调度 CI。

## 主机依赖工具

仅需 `python3`（纯标准库）与 `git` 即可运行核心检查；以下工具缺失时对应测试项会输出 `SKIPPED`：

| 工具 | 覆盖的检查 |
| --- | --- |
| `cpp`、`dtc`、`fdtoverlay` | overlay 应用、厂商 DT 对照 |
| `gcc`、`readelf`、`meson`、`glib-compile-resources` | 传感器运行时语法与 C 探针 |
| `busybox`、`blkid`、`fsck.f2fs`、`logrotate` | initramfs 与启动证据 |
| `mcopy`、`mdir` | 首启 SSH 公钥注入 |
| `chpasswd` | rootfs 收尾检查 |
| `podman` | 在 `registry.fedoraproject.org/fedora:45` 容器内复现全部检查 |

CI `host-check` 使用 `ubuntu-24.04-arm` 配上述容器内 `busybox binutils util-linux shadow-utils libxcrypt make git python3 gcc patch dtc logrotate glib2-devel f2fs-tools` 运行 `make test`。

## 本地取源与离线测试

`scripts/checkout-sources.py` 按 `sources.lock.json` 将上游检出到 `build/` 下（切换 `--mode` 控制范围：`check` 仅 SunUEFI，`boot` 含内核 dt-bindings 与 dtc，`device` / `graphics` / `installer` / `vendor-dt` / `vendor-blobs` 按需取对应源码；`gunyah` 与 `loopback` 需额外传 `--kernel`）：

```sh
python3 scripts/checkout-sources.py --output build/test-sources --mode boot
python3 scripts/apply-patches.py --sunuefi build/test-sources/sunuefi --check
PIANO_TEST_SUNUEFI=build/test-sources/sunuefi make test
```

- `apply-patches.py --check` 在隔离副本中按序重放全部系列并比对 `targets` 与 `creates`，打印 `CHECKED` 为通过；排查单个失败时不要仅用 `--only`（缺少前置补丁会误报），按顺序手工 `git apply --check` 定位。
- `make test` 通过 `scripts/run-checks.py` 自动运行所有 `tests/check_*.py`（可用 `--pattern <子串>` 或 `--timeout <秒>` 过滤和调整）；设置 `PIANO_TEST_SUNUEFI` 才不会跳过 `check_iris_source.py`、`check_bundle_overlays.py`、`check_installer.py` 等上游相关测试。其中 `tests/check_bundle_overlays.py` 会直接将 `build-bundle.yml` 传入的 `--dtb-overlay` 列表与上游 `config/release.json`（排除 `product.json` 的 `dtb_overlays_not_shipped`）逐项对齐，防止漏传硬件 overlay。

## CI 发布构建顺序

各 workflow 通过输入 run id 显式衔接：

| 顺序 | Workflow | 依赖的成功 run |
| --- | --- | --- |
| 1 | `build-uefi` / `build-kernel` / `build-mesa` / `build-ffmpeg` | 无 |
| 2 | `build-device-runtime` | `kernel_run_id`（`calibration_run_id` 可选） |
| 3 | `host-check`（`build_piano_rpms=true`） | `kernel_run_id` |
| 4 | `build-rootfs` | `piano_rpms`、`runtime`、`mesa`、`ffmpeg` |
| 5 | `build-image` | `kernel`、`runtime`、`rootfs`、`mesa`、`ffmpeg` |
| 6 | `build-bundle`（每档或 `all`） | `root_image_run_id`、`uefi_run_id` |
| 7 | `prepare-install` | `bundle_run`（可选扩容） |
| 8 | `release.yml` | 默认 `publish=false` 生成 draft（不可对同一已有 tag 重复覆盖发布，重发需换 tag） |

## 换 Pin 操作顺序

决定跟进哪些上游变更见[上游跟踪](upstream.md)，更新 pin 时按以下顺序调整并全量校验：

1. 更新 `sources.lock.json` commit（`sunuefi` 条目的 `tag` 一起改成新 release 的 tag），并同步 `patches/*/series.json` 的 `base.sunuefi_commit`（`tests/check_patches.py` 校验）与 `xiaomi-piano-firmware.spec` 的 `%global sunuefi_commit`（`tests/check_firmware_overlays.py` 校验）。
2. 若上游改名或移动文件，同步修改对应补丁路径与 `series.json` `targets` 摘要，跑 `apply-patches.py --check` 确认干净。
3. 若上游内核序列变动，按先上游序列、后自有补丁（整体后移）重排并重算每条 `tree` / `original_parent` / `original_commit` 及 `config/release.json` 末尾值；按同样分段边界重新导出 `patches/kernel/0001`-`0007` 累积 diff 并更新 `patches/kernel/series.json`。
4. 若重排更改了内核补丁文件名，同步更新 `tests/check_iris_source.py` 中的 `EXPECTED` 列表。
5. 不随镜像发布的上游 overlay 统一登记在 `product.json` 的 `dtb_overlays_not_shipped`。
6. 运行上面的取源、补丁与 `PIANO_TEST_SUNUEFI=... make test` 三条门禁命令，全绿后提交。
