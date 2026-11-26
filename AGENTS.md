# piano-fedora Agent Guide

改仓库前先读本文件；修改 README 或 `docs/` 下文档时再按需读取 [docs/AGENTS.md](docs/AGENTS.md)。

## 项目

小米平板 8 Pro（`piano` / `25091RP04C` / `SM8750`）的 Fedora + HyperOS 双系统构建仓库（F2FS 根分区，[SunUEFI](https://github.com/slwyts/Project-SunUEFI) 按 commit 锁定为上游）。仓库地址为 `FFT-infra/piano-fedora`（补丁 `From:` 行与文档均用此名）。

## 仓库地图

每个事实只在一处定义，别处引用：

| 路径 | 拥有的真源 |
| --- | --- |
| `sources.lock.json` | 上游仓库与锁定 commit（只列实际消费的上游） |
| `product.json` | 发行版、文件系统、封存尺寸、分区标签 |
| `scripts/build-clean-rootfs.py` | Fedora 装配后端（`plan` / `execute` / `audit`） |
| `scripts/piano_desktop.py` | 桌面差异解释器（登录、服务、电源策略、产物名） |
| `scripts/release-assets.py` | Release 资产成员表与按档后缀规则 |
| `scripts/profiles/` | 发行版与桌面配方 |
| `scripts/package-sets.json` | 包集合与构建顺序（脚本与 workflow 由此派生） |
| `tests/` | 离线契约测试（`make test` 全跑） |
| `docs/AGENTS.md` | 文档归属边界、风格红线与文档测试门槛 |
| `.github/workflows/` | 主机侧 CI（不接触设备） |

## 硬规则

- 上游不 fork：SunUEFI 按 `sources.lock.json` 取用，改动走自有脚本或补丁。
- 桌面差异只出现在 `scripts/profiles/`、`scripts/piano_desktop.py` 与桌面运行时探测里；其余层不硬编码桌面列表。
- 固件、校准数据、密钥不入库。
- 证据分层：作者记录、构建通过、设备可用三者不互相代替。
- 未定值留 `null` 并让工具报错，不猜；无交付文件时不建空目录。
- 生成产物不入库，唯一例外是 `rootfs/selinux/piano_hardware.pp`（设备上 `semodule -i` 只读 `.pp`）：改 `.te` 后必须运行 `make selinux` 重新编译入库。

## 常用命令

```sh
make test                              # 全部离线检查（tests/check_*.py）
make test-device DEVICE_HOST=piano-usb # 平板只读冒烟
make plan RELEASEVER=45                # 打印默认桌面 rootfs 装配计划
make plan-desktop DESKTOP=kde RELEASEVER=45
make selinux                           # 从 .te 重新编译 piano_hardware.pp
```

## 长构建与验证

- 编译型 workflow 耗时较长，首次触发前必须先审完外部命令依赖、构建容器、产物路径/权限、`run-id` 传参及源码可达性，再把审查要点写进提交信息。
- 声称通过时必须附本轮实际命令与输出；改 `scripts/`、`product.json`、`sources.lock.json`、`docs/`、`README*` 或 `.github/workflows/` 后必须跑 `make test`。

## 提交、注释与表达

- **提交格式**：`{type}: {description}`（`feat` / `fix` / `refactor` / `docs` / `chore`）。
- **代码注释**：只写代码本身表达不出的外部协议约束、硬件/上游行为与非直白原因，不写逐句翻译或修复流水。
- **文档规则**：改 README 与 `docs/` 前读 [docs/AGENTS.md](docs/AGENTS.md)，保持事实单源，中英文与中文数字之间留空格，`README.md` 与 `README.en.md` 骨架由 `tests/check_readme_parity.py` 锁定同步。
