# Gunyah 源码摘录，未接入构建

这里保留前轮从 karim-manaouil/linux-next 的 gunyah-kvm 分支摘取的十个文件。
它们不是完整移植集，不能复制到正式内核后声称获得 VM 支持。

来源提交为 81e88bfca90c73cb2d754672b4b538e7e09edea8。完整提案有 34 个补丁，
包含 20 个 Gunyah 基础提交和 14 个 KVM 相关提交。旧说明里的“只有两个提交”
来自只查询两个路径的历史，不能描述整个分支。

当前摘录缺少 arch/arm64/kvm/gunyah.c、arch/arm64/gunyah/gunyah_hypercall.c、
驱动内部头文件，以及 KVM core、irqfd、arm64 Kconfig/Kbuild、UAPI 等改动。
目录没有此前说明中提到的 SHA256SUMS。没有完整依赖移植、目标内核编译、
设备树接口核对和真机客体测试证据，不登记为构建消费源。

作者要求配套修改过的 QEMU，记录的限制包括单 vCPU、virtio 尚未支持和
客体内存管理限制。“可直接用镜像现有 qemu/libvirt”的旧说明没有依据。

/dev/gunyah VMM 的公开源码并非不存在：Qualcomm 的 v16/v17 提案提供
VM Manager 和 UAPI。code002-2/librevm 当前 main 只有安装/获取脚本，发布
资产只有应用 deb；不足以提供本机当前内核所需的模块源码或匹配二进制。

Linux DT 中存在 Gunyah 节点，不证明任意 VM 管理接口可用，也不证明需要
“嵌套虚拟化”。此 KVM 提案是在 EL1 中将 KVM ABI 桥接到已有 Gunyah。

路线、来源和验收门槛统一见[虚拟化记录](../../docs/virtualization.md)。
