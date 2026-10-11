# 离线构建与设备验收

正式目标保持 Fedora 44/GNOME、F2FS 与 SunUEFI。启动载荷、内核模块、运行时 RPM、根镜像及 ESP 必须通过清单摘要相互绑定。源码构建、归档验证和设备验收分别记录。

## 上游合同

SunUEFI 的完整磁盘启动使用正常内核时钟、IOMMU 所有权与作用域校验。正式 initramfs 保留其模块种子和硬件准备程序，不补写 `piano-smmu-ready`、不绕过失败校验、不复制 legacy devmem 流程到正式入口。

Debian ABL 的 rootfs-init 使用另一套继承启动器状态的流程。独立 abl-storage 对照复用锁定 debian-piano 的原始 SMMU helper、DTBO 构建器和 mkbootimg。它只枚举存储、核对 Linux 分区并以 ro,norecovery 检查 Fedora 根目录，然后停在救援；不会读写挂载根分区、执行 systemd、创建正式硬件 ready 标记或替代 UEFI 部署。

DTBO 的 fragment 编号不能作为数量或兼容性证明。新编 DTBO 的主机检查不继承另一份二进制的设备结果。设备侧仍须核对 stock vendor_boot/base DT 与该 overlay 的适用性。

## 构建与回归

运行 `make test`。原生 ARM64 检查执行真实 BusyBox、ELF 依赖闭包和隔离 guest 中的根身份函数；缺少所需环境时明确标记跳过。Actions 的 host-check 同时使用 x86_64 和 ARM64 runner。

根探测固定调用 `/usr/bin/blkid`，避免 BusyBox 同名 applet 改变 util-linux 参数和输出含义。缺动态库、非法 cpio 路径、断链、不同启动入口或混用 ABL/SunUEFI 都必须拒绝。根分区仍以 GPT PARTNAME、文件系统 LABEL 与 TYPE 三者匹配，写挂载前再次核对。

正式构建：

```sh
python3 scripts/build-initramfs.py --rootfs build/device-root/rootfs \
  --kernel build/kernel --output build/initramfs
```

独立诊断：

```sh
python3 scripts/build-initramfs.py --rootfs build/device-root/rootfs \
  --kernel build/kernel --output build/initramfs-abl --boot-chain abl-storage \
  --abl-source build/sources/sunuefi/upstream/debian-piano-current
python3 scripts/build-abl-diagnostic.py --kernel build/kernel \
  --initramfs build/initramfs-abl \
  --source build/sources/sunuefi/upstream/debian-piano-current \
  --dtbo build/dtbo-abl-storage.img --output build/diagnostic-abl-storage
```

DTBO 由锁定上游的 scripts/build-dtbo.py 和 boot/dtbo-piano-power.dts 构建，dt-bindings 与 uapi 头文件使用 sources.lock.json 的内核 pin。仅供诊断的 header v4 空 ramdisk boot 与 legacy LZ4 init_boot 保持独立；正式 SunUEFI 仍使用上游 ESP 中的 Linux boot.img，不能互换。

## Actions 输入与部署包

build-image 可显式指定 kernel_run_id、runtime_run_id，仍必须通过两者 kernel manifest SHA256 相等检查。同一 job 构建正式 root/initramfs 与独立诊断包。build-bundle 用 root_image_run_id 固定本次集成镜像，内核从该镜像的 source-runs.json 取得。

部署包保留既有 SunUEFI/F2FS 产物，另附 diagnostic-abl-storage.tar.zst。独立诊断文件具有自己的 manifest 和 SHA256SUMS。上传后应下载并再次核对摘要及压缩流，不能以 Actions 绿灯替代文件内容检查。

单独的 build-initramfs 消费完整集成设备 root（root-image-8192），不从尚未包含硬件 helper 和设备运行时的基础 Fedora root 构建。

## 取证与图形服务

早期启动先尝试上游 ACM/NCM helper，再执行存储模块或 ABL 存储准备。它使用已绑定 UDC，不扫描寄存器、切 USB 角色或加载硬件模块。冷启动无可用 UDC 时保留物理控制台并记录失败，不虚报网络就绪。

串口入口为 /dev/ttyGS0，NCM 目标地址为 192.168.77.1/30，主机可使用同网段另一地址。initramfs 提供串口诊断，不声称内置 SSH server；不能只复制 Dropbear 而省略动态依赖。

在 initramfs 或 Fedora root 执行 piano-collect-boot，取得内核、根挂载、DRM 节点、实际绑定驱动、systemd job 和 GDM/显示服务 journal。命令只读，不启动或停止硬件服务。Fedora 的 gdm.service 具有与上游 Debian 显示管理器相同的 Requires/After 显示依赖；相机失败保留日志并限制重试，不通过永久 mask 改写失败。

## 已有分区的部署验收

已有 sunuefi_root/sunuefi_esp 的平板不得重新执行会清空 userdata 的首次分区 apply。先只读核对当前 GPT、原厂 BOOT、槽位、容量与恢复材料；合体 BOOT 必须绑定本机实际 APP generation。恢复工具不能恢复已擦除的 Android 用户文件。

主机验收通过后，设备侧依次确认 UEFI 读取 ESP 并交接、UFS、正确 F2FS 根、systemd、GDM、真实 GNOME 会话、冷启动与两系统往返切换。显示、触控、GPU、网络、音频和 Recovery 分别保留行为证据。黑屏背光、闪烁光标、Fastboot OKAY 或单个 active 服务不能单独证明这些项目通过。
