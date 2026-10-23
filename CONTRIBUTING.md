# 参与贡献

这个仓库构建小米平板 8 Pro 的 Fedora 双系统镜像。改动最终会进到真机的启动链和根分区，所以每条改动都要能被离线检查复现。

## 开始之前

先读 `AGENTS.md`。它写明了每个事实的唯一出处、硬规则和提交格式。

需要的东西：

- Python 3.12 或更新版本
- `git`、`make`、`patch`、`dtc`、`binutils`、`util-linux`
- 一个 SunUEFI checkout，用来验证补丁能真实重放

## 跑检查

```sh
make test
```

必须全绿。`make test` 会跑全部离线检查，包括 GPT 计算、镜像与元数据校验、补丁登记结构和文档链接。文档链接检查同时校验 `#锚点`：改了标题就要同步改指向它的链接。

部分检查需要上游源码才能验证真实行为。不提供时会跳过或只检查结构，所以本地验证补丁要显式给出 checkout：

```sh
python3 scripts/checkout-sources.py --output build/test-sources --mode boot
export PIANO_TEST_SUNUEFI="$PWD/build/test-sources/sunuefi"
make test
```

`PIANO_TEST_SUNUEFI` 指向的目录必须带 `build.sh`。`tests/check_patches.py` 会在临时目录里按 `series.json` 的顺序重放补丁，并核对每个 `targets` 摘要。摘要不匹配说明上游变了，要人工 rebase 并更新摘要，不要改测试。

## 补丁

`patches/` 下的补丁针对 `sources.lock.json` 锁定的上游 commit。新增或修改补丁时：

- 补丁必须能在锁定的上游原文上干净应用，`make test` 会验证这一点。
- 每个补丁在 `series.json` 里登记 `file`、`summary` 和 `targets`。`targets` 是应用前的上游文件 sha256。
- 补丁不能覆盖上游已有的文件，也不能创建上游已经存在的路径。
- 上游更新后补丁失效时，重新 rebase 并更新摘要。不要删掉检查或跳过失败。

## 不要提交的内容

- 真实设备序列号。文档和测试里写 `<serial>` 或明显的假值。
- 内网地址。`192.168.77.0/30` 是设备 USB 的固定网段，可以写。其他内网地址一律换成文档保留段（`192.0.2.0/24`）。
- 个人绝对路径。默认值从脚本位置推导，或读产物清单。
- 固件、校准数据、私钥、`.env`、构建产物和日志。这些在 `.gitignore` 里。

## 提交

格式 `{type}: {description}`，`type` 取 `feat`、`fix`、`refactor`、`docs`、`chore`。一个提交一件事。

提交前自查：

```sh
git status --short
make test
```

## Pull Request

描述里写这四样：

1. 改了什么，为什么。一句话说清动机，不贴 diff。
2. 怎么验证的。贴命令与关键输出。没跑的检查标为未跑。
3. 影响面。是否改变镜像内容、分区布局、引导链或 `sources.lock.json`。
4. 回滚方式。如果改动涉及刷机、分区或安全相关路径，写清怎么退回。
