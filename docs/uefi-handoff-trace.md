# UEFI 交接阶段取证（诊断分支）

本分支沿用锁定的 Project-SunUEFI 正式 RawLinux 路径。补丁只记录阶段并增加固定范围的原始日志导出，不改变原厂分区保护、资源所有权校验、ExitBootServices 或裸入口汇编。

补充只读接口：`oem inherited-fdt` 导出 Core 在原始有效 lease 内缓存、仍由 running owner 持有的继承 FDT。导出仅接受原始指针和字节数，检查 header、源前后 SHA256 与 staged copy SHA256，不接受主机指定地址，不改 FDT。新快照可查询 `SunUEFI:snapshot-kind`、`SunUEFI:snapshot-size`、`SunUEFI:snapshot-crc32`；主机必须对收到的字节校验类型、长度和 CRC，再保存为证据。普通 ramlog 或截图会清除这些额外诊断元数据。

每个 `PIANO_RAW_TRACE` 行带启动会话 nonce、阶段、EFI 状态和详情。记录涵盖 owner retirement（失败时细分子状态）、载荷准备、late handoff 授权、memory map、EBS 返回及跳转前。EBS 成功后的记录只调用不依赖 Boot Services 的格式化及既有 RAM 日志写入函数；在 MMU 关闭前结束。`branch-before` 只表示来到分支前，不表示 Linux 已执行。

新增命令：

```sh
fastboot -s SunUEFI-piano oem ramlog-raw
fastboot -s SunUEFI-piano get_staged console-raw.bin
```

命令保留普通 ramlog 的 Boot Services 存活检查，仅当 native memory map 中存在唯一、精确匹配的 `Piano_Ramoops` 保留区时，导出其前 2 MiB。地址固定，主机不能指定地址或长度。不校验或修复环头，不裁剪当前会话，不初始化 RAM，也不写设备存储。它返回的是原始字节，不能假定全是有效日志。

设备流程：第一次临时启动诊断 UEFI 后，先抓 raw 基线并确认环头可被现有 logger 使用，再通过 RAM 通道启动配套载荷；如果挂住，只需一次手动回到原厂 fastboot。随后电脑可以再次临时启动诊断 UEFI并读 raw 区，将上次会话 nonce 的阶段记录与当前启动区分。跨重启数据保留仍需实机验证；找不到记录不能证明某阶段未执行。

主机验证包括实际 C 函数的 12 个原始导出边界/清理用例，以及实际 RawLinuxEnter/Trace 函数的七条 EBS 成功或失败路径。成功场景令 `gBS=NULL`，验证后续 trace 与 branch-before 不访问 Boot Services。它们不能替代固件构建和实机验证。

本机完整 `make test` 在已有 F2FS 元数据测试中遇到共享文件系统 symlink xattr 的 `ENODATA`，发生在新增补丁检查之前；相关诊断测试均通过，完整检查另在连接设备的主机执行并记录。该环境错误没有通过放宽元数据校验处理。

连接主机上的完整检查已跑完，F2FS 元数据测试通过；静态 AArch64 BusyBox 与完整 SunUEFI fixture 不可用的部分由现有测试明确标为 SKIP，不能视作通过。实际 AArch64 归档的 ELF、模块身份和依赖闭包另已在本机现成构建产物上验证。
