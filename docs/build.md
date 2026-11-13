# 本地构建与验证

出货只在 Actions 上编。内核、UEFI 产品、RPM、root 文件系统、F2FS 镜像和 bundle 需要 arm64 Fedora 环境，还要多次 run 的产物按摘要串起来，本机能做的是取源、套补丁和全套离线检查。下面按实际执行顺序写，每步都有判据。

## 工具

`python3`（仓库脚本只用标准库）与 `git` 必需。其余工具缺失时，对应的检查打印 SKIP 而不是失败，所以看输出里的 SKIP 比看退出码重要。

| 工具 | 参与的检查 |
| --- | --- |
| `cpp`、`dtc`、`fdtoverlay` | overlay 应用、厂商 DT 对照 |
| `gcc`、`readelf` | 传感器运行时的 C 探针 |
| `meson`、`glib-compile-resources` | 传感器运行时的 meson 语法 |
| `busybox`、`blkid`、`fsck.f2fs`、`logrotate` | initramfs 与启动证据 |
| `mcopy`、`mdir` | 首启 SSH 密钥注入 |
| `chpasswd` | root 文件系统收尾 |
| `podman` | 在 Fedora 45 容器里跑全部检查 |

CI 用 `ubuntu-24.04-arm` 装 podman，起 `registry.fedoraproject.org/fedora:45`，一次装齐 `busybox binutils util-linux shadow-utils libxcrypt make git python3 gcc patch dtc logrotate glib2-devel f2fs-tools` 再 `make test`。本机要等价复现就照这份清单装。

## 取源

`scripts/checkout-sources.py` 按 `sources.lock.json` 取锁定 commit，输出目录必须在被忽略的 `build/` 下。

```sh
python3 scripts/checkout-sources.py --output build/test-sources --mode boot
```

| `--mode` | 取什么 |
| --- | --- |
| `check` | 只取 SunUEFI |
| `boot` | SunUEFI、内核稀疏检出（`include/dt-bindings/`）、dtc |
| `device` | 上面之外加运行时源码、crosvm、LibreVM、VA-API、piano-integration 的稀疏路径 |
| `graphics` | Mesa 源码 |
| `installer` | Mu-Silicium 与 simple-init |
| `gunyah`、`loopback` | 要带 `--kernel`：v4l2loopback 与派生内核输入 |
| `vendor-dt` | 厂商内核稀疏检出（DTS、techpack、include）与 dtc |
| `vendor-blobs` | 厂商固件树的索引，`--filter=blob:none`，只取路径与摘要 |

取完会就地跑一次 `apply-patches.py`。`--check` 不写目标树。

上游删掉子模块时这里不会跟着报错：`checkout-sources.py` 按本仓库自己的 pin 直接取源码，路径保持下游一直在用的那个。

## 门槛

```sh
python3 scripts/checkout-sources.py --output build/test-sources --mode boot
python3 scripts/apply-patches.py --sunuefi build/test-sources/sunuefi --check
PIANO_TEST_SUNUEFI=build/test-sources/sunuefi make test
```

`--check` 在隔离副本里把每个系列真重放一遍，再比对 `targets` 的前像摘要与 `creates`，输出 `CHECKED` 才算过。`--only <系列>` 只应用这一个系列，不套前置系列，靠它判断补丁坏没坏会误报；定位真因时按 `apply-patches.py` 的顺序逐条 `git apply --check` 更准。

`make test` 里有三个检查要真实上游树，没设 `PIANO_TEST_SUNUEFI` 就 SKIP：`check_iris_source.py`、`check_bundle_overlays.py`、`check_installer.py`。CI 总是指向 `build/test-sources/sunuefi`，本机比照。

`tests/check_bundle_overlays.py` 还会把 `build-bundle.yml` 里的 `--dtb-overlay` 列表和上游 `config/release.json` 逐项对齐。少传一个 overlay 不会让构建失败，只会让对应硬件在设备上不工作，这条断言是唯一能提前发现它的地方。

## 出货顺序

依赖只看各 workflow 的 run id 输入，产物之间靠 manifest 的 SHA256 绑定。kernel release 相同不等于配置相同，同名不同字节会被拒。

| 顺序 | workflow | 依赖 |
| --- | --- | --- |
| 1 | `build-uefi` | 无 |
| 2 | `build-kernel` | 无 |
| 3 | `build-device-runtime` | `kernel_run_id`；`calibration_run_id` 可选 |
| 3 | `build-mesa`、`build-ffmpeg` | 无 |
| 4 | `host-check`（`build_piano_rpms=true`） | `kernel_run_id`。`build-piano-rpms` 是 `workflow_call` 复用件，artifact 挂在 host-check 的 run 上 |
| 5 | `build-rootfs` | piano-rpms、runtime、mesa、ffmpeg 四个 run |
| 6 | `build-image` | kernel、runtime、rootfs、mesa、ffmpeg 五个 run |
| 7 | `build-bundle`（每档一次） | `root_image_run_id`、`uefi_run_id` |
| 8 | `prepare-install` | `bundle_run`，可选 |
| 9 | `release.yml` | `bundle_run`、`prepare_run`、`extras_run` |

`release.yml` 默认 `publish=false`，停在 draft。同一个 tag 二次 dispatch 会被「Refuse to overwrite an existing release」拒绝，重发要换 tag。每个产物的内容与归档规则见[交付物与流水线](artifacts.md)。

## 换 pin

要不要跟、跟哪一部分，看[上游跟踪](upstream.md)的规则与看板。改起来是这几步，顺序不能换：后一步的输入是前一步的产物。

1. `sources.lock.json` 改 commit。`patches/*/series.json` 的 `base.sunuefi_commit` 与 `xiaomi-piano-firmware.spec` 的 `%global sunuefi_commit` 跟同一个值；前者由 `tests/check_patches.py` 对齐，后者由 `tests/check_firmware_overlays.py` 核对。
2. 上游改了目录或文件名，补丁正文里的路径与 `series.json` 的 `targets` 键一起改。`apply-patches.py --check` 会停在第一个摘要不匹配的目标上。
3. 上游的内核序列变了，本仓库的封存序列跟着重排：上游补丁按原顺序放前面，自有补丁整体后移。逐条重放，重算每条 `tree`、下一条的 `original_parent`、上一条的 `original_commit`，末条的 `original_commit` 与 `target_tree` 写进 `config/release.json`。编号允许不连续，文件名不能重复，`From <commit>` 头与每行 sha256 都会被核对。
4. `patches/kernel/0001`–`0007` 是分段累积 diff，序列重排后按同样的分段边界重新生成，`patches/kernel/series.json` 的 `targets`、`creates` 与摘要一起更新。
5. `tests/check_iris_source.py` 的 `EXPECTED` 写死了序列里几条具体文件名，重排后跟着改，否则它会对旧名做「改一字节必须被拒」的校验。
6. 跑「门槛」那三行。`CHECKED` 与 `make test` 都过，才算改完。

不发货的上游 overlay 记在 `product.json` 的 `dtb_overlays_not_shipped`，bundle overlay 测试与厂商 DT 对照都按这一处排除，不各写一份。
