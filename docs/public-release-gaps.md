# 公开分发前的个人化与许可缺口

流水线能把系统装上这台平板，但有几处把"这一台、这一个人"的东西固化进了构建期。
内部自用不受影响；把 bundle、root-install 或 GitHub Release 当公开物发布之前，逐项处理。

当前状态：仓库没有 GitHub Release，`deploy-bundle` / `root-install` 只以 workflow artifact
形式存在。下面的问题还没有随任何公开发布物出去，但流水线本身已经这么做了。

| 项 | 位置 | 现状 | 公开分发的问题 | 处理方向 |
| --- | --- | --- | --- | --- |
| SSH 管理公钥 | `prepare-install.yml` 的 `ssh_public_key` 输入 → `scripts/prepare-root-capacity.py` 挂载根镜像写入 `authorized_keys` | 构建时把一把个人公钥写进 rootfs 的 root 与 piano 账户；公钥同时出现在公开仓库的 dispatch 事件里 | 镜像只有一个人的钥匙，别人装了进不去；等于把个人权限入口固化进发布物 | 公钥改成安装期参数，由刷写脚本在写分区时注入；镜像不带 `authorized_keys` |
| 电极校准（厂商数据） | `packages/xiaomi-piano-thp/usr/share/piano/touch/piano-{csot,boe}.json`；来源是仓库密钥 `TOUCH_CALIBRATION_TGZ_B64` 里的原厂 INI | 原厂 INI 本身不进 Git，但从它提取的校准表已经提交（`source_sha256` 指回 INI），并随 RPM 分发 | 许可不明的厂商数据已经进入公开仓库与镜像；"INI 不进 Git"不等于数据没公开 | 先确认再分发许可；不能确认就把校准拆成可选设备包，发布物不带，装机时由用户自己的原厂数据生成 |
| 已知默认密码与自动登录 | `scripts/finalize-fedora-rootfs.py:21`、`:676`、`:758` | `piano` 用户使用文档化默认密码 `piano123456`，GDM 自动登录，root 锁定 | 拿到同一镜像的任何人都能直接进桌面 | 默认密码作为产品默认值保留，文档继续写明；首次登录提示改密，不强制改密、不生成随机密码；自动登录保持现状 |
| 真机序列号 | `docs/installation.md:440`、`:450`；`tests/check_manual_flash_flow.py:68` | 文档示例和测试桩都写着这台设备的实际序列号 | 真实设备标识进了公开仓库；已经写进 Git 历史 | 改成 `<serial>` 与假序列号；历史是否重写单独决定 |
| 个人绝对路径 | `scripts/verify-new-image.sh:6` | 默认镜像路径写死 `/home/<user>/...`，默认内核版本写死 `<release>` | 对别人不可用，暴露本机布局，内核字符串还会过期 | 默认值改成仓库内相对路径，内核版本从产物清单读 |
| 内网拓扑 | `docs/architecture.md:28`、`docs/virtualization.md:107`、`local-notes/acceptance-20261015.md:264`、`scripts/probe-device.sh:7` | 桥接机、验收时的 DHCP 地址、虚拟机地址 | 暴露内网布局 | 公开文档用占位符；`192.168.77.0/30` 是设备固定网段，保留 |
| 旧的公钥历史 | 历史 dispatch 事件（例如 run 38724829866 的 `ssh_public_key`） | 之前的公钥已经出现在公开事件里 | 公钥不是秘密，但它把一个人的身份和这台设备的管理权限挂在公开记录上 | 换用专用密钥（已做），旧公钥作废并记录 |

不需要处理的：COPR 的 owner/project 来自仓库密钥 `COPR_LOGIN`/`COPR_TOKEN`，没有写死个人命名空间；
镜像里没有 Wi-Fi 凭据；上游地址都是公开仓库。

处理顺序：先改第一项，它是唯一"别人装了用不了"的问题；第二项要一个许可决定；第三项是产品决定；
其余是机械替换，可以随时做。前两项没有结论之前，不要把 bundle 挂成公开 Release。
