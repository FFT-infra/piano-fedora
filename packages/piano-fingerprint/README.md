# piano-fingerprint

源码不随包维护。构建期从 SunUEFI（`sources.lock.json` 的 `sunuefi` pin）取
`linux/userspace/fingerprint`，与设备上跑的内核同一份客户端，本地不另立分支。

新的 TEE 传输只依赖两样东西：

| 源 | 取法 |
| --- | --- |
| QCBOR | 上游 `config/fingerprint.json` 的提交 `590a23daf65af068cee81555c597063150e23539` |
| linux/tee.h | 发行版 `kernel-headers`，TEE 对象 uAPI 在 7.2 与 7.3 完全相同 |

quic-teec 与 minkipc 已不在构建路径里。minkipc 只剩协议常量参考，上游也不再
为它打补丁。

产物：

- `/usr/sbin/piano-fingerprint-ctl`：静态链接的诊断与录入客户端，需要 root。
- `/usr/share/piano-fingerprint/piano-goodix-sources.tar`：`piano-libfprint`
  在目标发行版 ABI 下编译 Goodix 驱动用的后端源码归档，包含 QCBOR、TEE
  头文件与两份 BSD 许可。不再安装 `libpiano-goodix.a` 与 pkg-config。
