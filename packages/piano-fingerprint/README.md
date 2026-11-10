# piano-fingerprint

`src/` 是 SunUEFI（`sources.lock.json` 的 `sunuefi` pin）里
`linux/userspace/fingerprint` 的逐字副本。上游改这几个文件时整份替换，
本地不另立分支。依赖同样按上游 `config/fingerprint.json` 的提交取用：

| 源 | 提交 |
| --- | --- |
| quic-teec | `736419e25a2036aac3292a10a93e394a90750ca3` |
| minkipc | `ce145804d9e994e0a7913f8d224d52e84bce6608` |
| QCBOR | `590a23daf65af068cee81555c597063150e23539` |

minkipc 的两个补丁来自上游 `patches/minkipc/0.1.0/`。产物：

- `/usr/sbin/piano-fingerprint-ctl`：静态链接的诊断与录入客户端，需要 root。
- `/usr/lib/piano-goodix/libpiano-goodix.a` 与 `piano-goodix.h`：给 libfprint 的
  Goodix 后端链接，安装后 pkg-config 名字是 `piano-goodix`。
