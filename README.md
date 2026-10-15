# piano-fedora-test

小米平板 8 Pro（piano）的 Fedora + HyperOS 双系统构建仓库，Linux 根分区用 F2FS。

SunUEFI 按 commit 锁定为上游；本仓库维护 Fedora/F2FS 构建、安装和设备适配，标准内核包含 Gunyah，用户态以 RPM 交付。

Fedora 44/GNOME 已在本机 F2FS 根分区启动，双摄基础采集与传感器服务已复核。Iris 关键帧请求与 B 帧顺序修复已通过真机长视频回归，当前进展和各项验证边界见[本轮设备验收](docs/device-acceptance-20261015.md)。交付内容和构建顺序见 [产物说明](docs/artifacts.md)。

启动合同、离线诊断和图形服务取证见 [离线构建与设备验收](docs/offline-bringup.md)。

已有 Linux/ESP 分区使用[更新入口](docs/installation.md)，不执行首次分区 apply。上游更新与实际构建来源见[同步说明](docs/upstream-sync.md)。

相关上游的去重索引见 [upstreams.json](docs/upstreams.json)，包含构建消费源、同设备
移植、厂商驱动与虚拟化参考；当前提交通过 `python3 scripts/audit-upstreams.py
--execute` 重新核验，结果写入 `build/`，不会自动更换构建 pin。
VM 的正式构建链与未完成门槛见[虚拟化记录](docs/virtualization.md)，所有剩余项目见[交付完成清单](docs/completion-checklist.md)。

## 用法

```sh
make test                  # 离线检查
make plan RELEASEVER=44    # 打印 rootfs 构建计划
```

## 结构

| 路径 | 内容 |
| --- | --- |
| `product.json` | 要构建什么（发行版、文件系统、尺寸）。唯一目标真源 |
| `sources.lock.json` | 上游仓库与 commit。唯一上游真源 |
| `scripts/` | 构建脚本与发行版配方 |
| `tests/` | 离线检查 |
| `docs/architecture.md` | 组件边界与分阶段计划 |
| `docs/artifacts.md` | 部署需要哪些产物、各自来源 |

改动前先读 [AGENTS.md](AGENTS.md)。

## 许可

自有代码 MIT，见 [LICENSE](LICENSE)。上游依赖各自许可，见 `sources.lock.json`。
