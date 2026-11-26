# 维护与验收参考

面向仓库维护者与发布流程：说明 Actions 入口约束、可选装配产物、设备端真机取证脚本、SELinux 策略编译以及 PR 设备验收门槛。最终用户装机步骤请直接看[安装指南](installation.md)。

## CI 流水线入口规则

GitHub 仅对默认分支 `main` 上已注册的 workflow 提供 `workflow_dispatch`。在未合入前，`workflow_call` 复用的构建（如 `build-piano-rpms.yml`）统一经由已注册的 `host-check.yml` 触发并挂载 artifact，下游 `build-rootfs` 直接按该 `host-check` run 解析 `piano-rpms`。

```text
host-check (build_piano_rpms=true) ─▶ build-rootfs (desktop=all) ─▶ build-image ─▶ build-bundle
```

四步之间必须显式传递成功的上游 run id，且**全部五档产物必须来自同一 commit**：`release.yml` 会逐档核对 `head_sha`，发布流程建议钉在固定 tag 上连续 dispatch。

| 步骤 | 必填上游 run 输入 |
| --- | --- |
| `build-rootfs` | `piano_rpms_run_id`、`runtime_run_id`、`mesa_run_id`、`ffmpeg_run_id` |
| `build-image` | `kernel_run_id`、`runtime_run_id`、`rootfs_run_id`、`mesa_run_id`、`ffmpeg_run_id` |
| `build-bundle` | `root_image_run_id`（及 `uefi_run_id`） |
| `prepare-install` | `bundle_run` |

## 可选装配与主机只读工具

- **`prepare-install`**：按需从 `build-bundle` 扩容 F2FS 镜像并生成 `root-install-<desktop>`（固定首装 `android_slot=_a`）、`boot-tools`（与 bundle `PianoUEFI-product.img` 逐字节一致）与 `host-boot-tool`（静态重打包工具）。SSH 公钥仅在用户本地刷写时通过 `--ssh-key` 注入，不经过 CI。`host-check` 传入 `package_prepare_run` 可通过 `package-install.yml` 只压缩既有 `root-install` 而跳过重编。
- **本机只读探测**：`scripts/probe-device.sh` 读取设备身份、槽位与实时 GPT；`scripts/verify-new-image.sh` 在主机挂载镜像核对交接前置条件。

## 设备侧首次安装入口与脚本边界

首次分区、手动与一键刷写及 SSH 公钥注入的完整操作步骤全部维护在[安装指南第四节](installation.md#四-从原始-gptufs-开始的首次安装)与[第五节](installation.md#五-手动刷写-linux)。维护或调整安装脚本时注意以下不变量：

- `install-f2fs.py` GPT 写入用设备端 `dd`（不使用不保证存在的 `sgdisk`），严格**先写备份尾表、再写保护性 MBR 与主头表**并 `fsync` 回读用 `parse_gpt` 重解析校验，不一致时先头后尾恢复备份再退出。由于原厂 `/data` 为加密 dm 且缩小分区本身不清理旧 F2FS 超级块，首装路径必须在 GPT 回读确认后显式 `fastboot erase userdata`，不可跳过；擦除后停在 Recovery 需由操作者手选清除数据完成重建。
- 若在写入 GPT 或擦除后意外中断，必须先读会话目录 `result.json` 与设备实际分区状态，禁止脚本自动重试、恢复旧表或启动 Android。
- 一键脚本（`flash-linux.sh` / `flash-linux.bat`）当前仅放行 `current-slot=a` 且 `android_slot=_a`、仅校验 `boot_a`/`sunuefi_esp`/`sunuefi_root` 存在及容量下限，不重做 GPT CRC/全表校验；首次分区必须由 `install-f2fs.py` 或第四节手动步骤完成。

## 真机取证脚本

以下脚本只在维护者主机连着平板执行，不进镜像也不进 Release；离线契约测试请跑 `make test`。

- **只读冒烟测试**（`make test-device DEVICE_HOST=piano-usb SMOKE_OUTPUT=<目录>`）：通过 `scripts/run-device-smoke.sh` 推送只读检查，验证当前内核与 manifest 一致、`/usr/lib/modules` 在位、无 failed systemd unit、相机/触控服务 `active`、PipeWire 默认 sink 与 ESP 审计文件齐全。
- **视频编解码验收**（`make test-device-media MEDIA_HOST=<ssh> MEDIA_OUTPUT=<目录> [FIXTURE_DIR=<目录>]`）：主机准备或复用 B 帧测试流并预计算帧类型列表，在设备上运行并采集 `/proc/interrupts` 验证 Iris V4L2 M2M 硬件编解码。
- **浏览器硬解验收**：`scripts/verify-browser-video.py --output <目录>`（在设备上由登录用户执行）依次运行 Chromium / Chrome / Firefox 播发 H.264，同时以 Iris 中断增长和 `/dev/video0` 占用作为硬解判据（浏览器自报状态不计入）。
- **触控持续采样**：`scripts/verify-device-touch.py --output <目录>`（需人工划动屏幕触发后开始计时）按 SYN 报文统计报告率、NVT 中断增长、多指 slot 与相邻坐标跳变。
- **COPR 仓库校验**：`scripts/verify-copr-device.py`（详见 [COPR 软件源](copr-repository.md#维护者发布与验证)）。

## SELinux 策略模块

策略源码为 `rootfs/selinux/piano_hardware.te`，首次开机由 `piano-selinux-policy.service` 加载预编译产物 `rootfs/selinux/piano_hardware.pp`。每次修改 `.te` 后**必须**在装有 `checkpolicy` 的 Fedora 主机重新编译 `.pp` 并提交（或跑 `make selinux`）：

```sh
cd rootfs/selinux
checkmodule -M -m -o piano_hardware.mod piano_hardware.te
semodule_package -o piano_hardware.pp -m piano_hardware.mod
```

每条 `allow` 规则应在 `.te` 注释中写明对应的真机 AVC 拒绝单元，先在真机用 `semodule -i` 验证后再合入。

## PR 验收门槛

标注某项 PR“已通过真机设备验收”前，必须同时满足全部五条（构建与离线测试通过仅能记为 CI 证据；用旧镜像热补不算冷装验收）：

1. 记录同一次新流水线产出的 `deploy-bundle-<desktop>` 与 `root-install-<desktop>` run id 及 SHA256 摘要。
2. 从原始 GPT/UFS 完成首次分区、显式擦除 `userdata` 与 Android 首次重建开机。
3. 从活动槽冷启动直接进入所验档位（`gnome`、`kde`、`niri`、`hyprland` 或 `denial`）。
4. 完整重启一次后仍能稳定回到同一桌面，且 `systemctl --failed` 为空。
5. 完成 Android ↔ Linux 双向切换及原厂 Recovery 进入验证，并保存 `uname -r`、`/usr/lib/modules/`、F2FS 挂载及分区容量输出。
