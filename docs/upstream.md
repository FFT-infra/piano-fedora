# 上游跟踪

构建来源由 `sources.lock.json` 固定。上游 main 的进展只用于审阅。更新文档不改变已构建镜像的来源，旧二进制也不会自动获得新实现。

## 当前 pin

构建消费的上游以 `sources.lock.json` 为准，当前 pin 的 commit 写在 `sources.lock.json` 里。其他消费源的 commit 也记在 `sources.lock.json`。研究与对照仓库登记在 `upstreams.json`，不参与构建。本页的引用链接按当前 pin 固定到具体提交。

### 对照基准

`sources.lock.json` 里有两条 pin 只进对照任务、不进产物：

- `vendor-kernel`：`android_kernel_xiaomi_piano` 的 DTS 源码，被 `scripts/compare-vendor-dt.py` 读取，用来核对 SunUEFI overlay 的目标节点在厂商硬件描述里是否存在。
- `vendor-blobs`：`android_vendor_xiaomi_piano` 的固件与传感器配置，被 `scripts/compare-vendor-blobs.py` 按 git blob 摘要比对。只读文件清单，不下载内容，不进镜像。

这两条属于消费上游，所以进 `upstream-watch` 的漂移报告，但白名单里没有它们：厂商树一更新就要人工看对照报告有没有新差异。任何出货路径引用 `vendor-kernel` 都算违规——它描述的是 Android 下游 DT，和启动用的 `board.dtb` 不是同一条血缘，见 `docs/device-tree.md`。

第一份固件对照（2026-11-08）覆盖厂商仓库提交的 148 个固件文件：5 个与出货树逐字节相同，含两个 Novatek 触控固件、Nanosic MCU 与 `gen80000_gmu.bin`；4 个不同，`gen80000_aqe/sqe`、`gen80000_zap.mbn`、`vpu35_4v.mbn`；其余两边各自独有，厂商侧以 `CAMERA_ICP` 为主。厂商仓库不是 vendor 分区的全集，所以"厂商有、我们没有"只说明这份清单里没有。报告同时核对 `runtime/matrix-owned-paths.txt` 的 99 条固件所有权路径：与出货树 99 个文件一一对应，没有缺失也没有未声明的。

`packages/xiaomi-piano-firmware` 的 `%prep` 现在核对上游自带的 `SHA256SUMS`（99 项）。在此之前 `Source0` 只订了提交号，tarball 内容被换掉也发现不了。

## 自动跟踪

`.github/workflows/upstream-watch.yml` 每周一 03:23 UTC 运行一次 `scripts/audit-upstreams.py --execute`。`scripts/upstream-watch.py` 处理执行结果，按以下规则分流：

- 锁定 ref 指向的提交与 `sources.lock.json` 的 pin 不同时，算作漂移。漂移汇总进一个 `upstream-drift` issue。下次运行若没有漂移，`scripts/upstream-watch.py` 关闭这个 issue。
- 白名单（`crosvm`、`librevm`、`uperf`、`piano-mesa`、`piano-sensors`、`piano-mipps-auth`）里的漂移在 issue 里附带一条改 pin 命令。workflow 本身不碰锁文件，也不开 PR：仓库设置不允许 Actions 创建 PR。人跑 `scripts/upstream-watch.py bump` 改 `sources.lock.json` 的 `commit`，`ref` 保持不动。这些项目属于纯用户态上游，`make test` 与 CI 能够覆盖检查；受影响产物仍要按本页末节重编。
- 内核、UEFI、相机/触控、固件和 debian-piano 只作报告。离线 CI 代替不了这些组件的真机验收。维护者按后文「每次跟进上游」的规则评审 pin。

漂移判断只看 pin，不看 `docs/upstreams.json` 的 `reference_commit`。`reference_commit` 是人工基线，只在 `--record-baseline` 时推进。拿 `reference_commit` 判断，会把没有位移的上游报成更新。

自动跟踪只覆盖 16 个消费上游。`docs/upstreams.json` 里的 83 个对照仓库不进 `upstream-watch`：它们没有 `lock_key`，`scripts/upstream-watch.py` 只处理带 `lock_key` 的行。这些仓库要人工复核，用下面两条命令：

```sh
python3 scripts/audit-upstreams.py --execute --output build/upstream-audit.json
python3 scripts/audit-upstreams.py --execute --record-baseline --output build/upstream-audit.json
```

第一条列出谁比基线新，第二条把核到的提交写成新基线。第二条会改 `docs/upstreams.json`，只在确认复核过之后跑。

登记表的价值在于「谁值得看」，不在于「谁被自动盯着」。把仓库目录整个抄进来会淹没真正要跟的那几行，所以未登记不等于遗漏。

## 每次跟进上游

1. 读取远端 main 的完整提交号。比对当前 pin 与远端的提交、文件和实际代码。发布说明只当索引用。
2. 启动、交接、内存、DTB、模块或硬件准备有变时，重读相关合同与消费者。检查补丁 series 的 base 和目标摘要。不要直接替换某一件旧产物。
3. 改 pin 之前核对所有消费路径、构建容器和依赖。重新生成受影响的内核、运行时、root 文件系统、initramfs、ESP 和 BOOT。清单同步更新。
4. 下载并核对实际产物，更新部署入口。构建、文件验证和实机行为分别记账。不能继承上游另一台设备的结果。

## 内存与面板边界

锁定上游的状态页只覆盖 16 GB、另一版 HyperOS、Debian/ext4 的设备。改装到 24 GB 的 Fedora/F2FS 机器不能照搬状态页的全部设备结论。

当前 Linux DTB 的 16 个非零 RAM 区间合计 16,470,685,696 字节。Raw Linux 交接修改面板 compatible、启动参数和 initrd 地址。Raw Linux 交接保留输入的内存描述。自动选面板不会自动发现 RAM。

上线后的只读更新计划要验两件事：Linux 描述的 RAM 是当次 Android FDT 的子集，固定保留区与 Linux RAM 相交的部分仍受保护。比较失败就拒绝更新。不要猜扩容后的地址，也不要放宽内存门槛。比较通过也不证明物理 DDR 所有权，也不证明完整 24 GB 可用。

### 两个原厂面板标识

原厂 ABL 用 `msm_drm.dsi_display0` 的值说明这台设备装的是哪块面板，UEFI 据此改写交接 DTB 的面板 `compatible`。

DTB 里的两个 profile 与原厂 UEFI 的两份面板描述文件一一对应。用原厂固件 `RawFiles/Panel_P81_*_dualdsi_dsc_vid.xml` 对比 `qcom,mdss-dsi-on-command` 可以验：

| 原厂标识 | 该 profile 的命令特征 | 对应原厂 XML |
| --- | --- | --- |
| `qcom,mdss_dsi_p81_42_02_0a_dualdsi_dsc_vid` | `c4 82 … c4 06` 块、`D1 20`、先 `B3` 后 `B2` | `Panel_P81_42_02_0a_...xml` |
| `qcom,mdss_dsi_p81_35_02_0b_dualdsi_dsc_vid` | `bc 77 07`、`D1 00`、先 `B2` 后 `B3` | `Panel_P81_35_02_0b_...xml` |

`35_02_0b` 那份 XML 的 `<PanelDescription>` 误写成 "P81 42 02 0a"，是原厂复制文件留下的错误，不能拿它判断归属；要看文件自己的命令块和 `PanelBuildIDReadBack`。

供应商归属由原厂触控驱动自己定：`nt36xxx.c` 的 `lcd_id_value` 注释写明 0 是 CSOT、1 是 BOE，同一处用这个值选 `piano_nova_{csot,boe}_thp_config.ini`。据此

| `lcd_id` | 供应商 | 面板 profile | 命令特征 |
| --- | --- | --- | --- |
| 0 | CSOT | `qcom,mdss_dsi_p81_42_02_0a_dualdsi_dsc_vid` | `c4 82 … c4 06` |
| 1 | BOE | `qcom,mdss_dsi_p81_35_02_0b_dualdsi_dsc_vid` | `bc 77 07` |

这与 `PianoFactoryPanel.h`、上游 `docs/devel/panel-selection.md` 的表一致，也与原厂 UEFI 的两份面板描述文件一一对应。本机 `lcd_id: 1`，所以是 BOE 模块，UEFI 读到的 `dsi_display0` 应当指向 `35_02_0b`。

2026-10-24 的文档曾把这一格写反（判成 `35_02_0b`=CSOT）。错在拿主线驱动的函数名当证据：驱动里 `piano_boe_*` 与 `piano_csot_*` 的名字来自移植本身，上游 `linux-sm8750-piano` 在 2026-10-22 把两者对调过一次，两次都没有改变任何字节。名字换来换去是循环论证，判据只能来自原厂侧。当前内核补丁里的函数名与上表相反，但发给面板的字节序列相同，功能上等价。

核对时注意两个 profile 在 DTB 里的写法不同：`35_02_0b` 用 `[...]` 字节数组，`42_02_0a` 用 `<0x39000040 0x2ff 0x27 …>` 打包字，dtc 打印时还会省略前导零。只认字节数组的解析器会漏掉后者。这个坑在 2026-10-24 实际踩过一次。

来源：[上游设备覆盖范围](https://github.com/slwyts/Project-SunUEFI/blob/065b192b863b6f99e12ba65d3cab27c0ddd6dca4/docs/status.md)、[面板选择合同](https://github.com/slwyts/Project-SunUEFI/blob/065b192b863b6f99e12ba65d3cab27c0ddd6dca4/docs/devel/panel-selection.md)、[实际 Raw Linux 交接](https://github.com/slwyts/Project-SunUEFI/blob/065b192b863b6f99e12ba65d3cab27c0ddd6dca4/uefi/core/PianoRawLinuxBoot.c)。
