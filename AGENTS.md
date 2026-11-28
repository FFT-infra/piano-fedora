# piano-fedora 协作规范

这份文件说明仓库里每个事实归谁管、哪些底线不能碰、改完怎么验证。改 `README*` 或 `docs/` 前再读 [docs/AGENTS.md](docs/AGENTS.md)。

## 项目

小米平板 8 Pro（`piano` / `25091RP04C` / `SM8750`）的 Fedora + HyperOS 双系统构建仓库，根分区是 F2FS。上游 [SunUEFI](https://github.com/slwyts/Project-SunUEFI) 按 commit 锁定。仓库名是 `FFT-infra/piano-fedora`，补丁的 `From:` 行与文档都用它。

## 仓库地图

每个事实只在一处定义，别处引用。

| 路径 | 拥有的事实 |
| --- | --- |
| `sources.lock.json` | 上游仓库与锁定 commit，只列构建实际取用的 |
| `product.json` | 发行版、文件系统、封存尺寸、分区标签 |
| `patches/` | 对锁定上游的补丁，规则见 [CONTRIBUTING.md](CONTRIBUTING.md#补丁) |
| `scripts/package-sets.json` | 包集合与构建顺序，脚本与 workflow 由此派生 |
| `scripts/profiles/` | 发行版与桌面配方 |
| `scripts/piano_desktop.py` | 桌面差异解释器（登录、服务、电源策略、产物名） |
| `scripts/build-clean-rootfs.py` | Fedora 装配后端（`plan` / `execute` / `audit`） |
| `scripts/build-inputs.py` | 每条构建链的输入，输入摘要没变的链直接复用旧产物 |
| `scripts/release-assets.py` | Release 资产成员表与按档后缀规则 |
| `tests/` | 离线契约测试，`make test` 全跑 |
| `.github/workflows/` | 主机侧 CI，不接触设备 |

## 底线

- 上游不 fork：SunUEFI 按 `sources.lock.json` 取用，改动走自有脚本或补丁。
- 桌面差异只出现在 `scripts/profiles/`、`scripts/piano_desktop.py` 与桌面运行时探测里，其余层不硬编码桌面列表。
- 固件、校准数据、密钥不入库。真实设备序列号、内网地址与个人绝对路径的写法见 [CONTRIBUTING.md](CONTRIBUTING.md#不要提交的内容)。
- 生成产物不入库。唯一例外是 `rootfs/selinux/piano_hardware.pp`（设备上 `semodule -i` 只读 `.pp`），改 `.te` 后运行 `make selinux` 重新编译入库。
- 未定值留 `null` 并让工具报错，不猜；无交付文件时不建空目录。
- 证据分层：作者记录、构建通过、设备可用三者不互相代替。

## 打包（`packages/`）

- 包内容一变就升 `Release`。同一个 NVRA 只能对应一份内容，已装设备也只按 NVRA 判断要不要升级。
- 镜像里的静态文件由包提供，装配脚本不写 RPM 拥有的路径。
- 必须送到已装设备的配置用 `%config`。`%config(noreplace)` 会把旧文件留在原位，只给允许保留本地改动的文件。

## 常用命令

```sh
make test                              # 全部离线检查（tests/check_*.py）
make test-device DEVICE_HOST=piano-usb # 平板只读冒烟
make plan                              # 打印默认桌面的 rootfs 装配计划
make plan-desktop DESKTOP=kde
make selinux                           # 从 .te 重新编译 piano_hardware.pp
```

一部分检查要有上游源码才重放真实补丁，没给就跳过，给法见 [CONTRIBUTING.md](CONTRIBUTING.md#跑检查)。

## 验证

- 提交前跑 `make test`，必须全绿。声称通过时附本轮实际命令与输出。
- 编译型 workflow 耗时长。首次触发前审完外部命令依赖、构建容器、产物路径与权限、`run-id` 传参和源码可达性，把审查要点写进提交信息。
- 动 `product.json`、`patches/` 这类多条链共用的输入前，先在 `scripts/build-inputs.py` 里看它属于哪些链。输入摘要变了的链要整条重编。
- “已通过真机设备验收”的条件见 [PR 验收门槛](docs/internals.md#pr-验收门槛)，在已装系统上打热补丁不算。

## 提交与注释

- 提交标题写 `type(scope): 描述`，`type` 取 `feat` / `fix` / `refactor` / `docs` / `chore`，`scope` 可省。
- PR 描述按 [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md) 的各节填，没跑的检查标为未跑。
- 代码注释只写代码本身表达不出的外部协议约束、硬件或上游行为与非直白原因，不写逐句翻译或修复流水。
