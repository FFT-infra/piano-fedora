# 公开分发前的个人化与许可缺口

流水线能把系统装上这台平板，但有几处把“这一台、这一个人”的东西固化进了构建期。自用不受影响。把 bundle、root-install 或 GitHub Release 当公开物发布之前，逐项处理。

仓库目前没有 GitHub Release，`deploy-bundle` 与 `root-install` 只以 workflow artifact 形式存在。

`release.yml` 的两个输入控制公开范围：`publish` 默认 `false`，跑完停在 draft；`prerelease` 默认 `false`，
转公开时决定是否标成 pre-release。转公开必须显式传参，不靠人记。

| 项 | 位置 | 现状 | 公开分发的问题 | 状态 |
| --- | --- | --- | --- | --- |
| SSH 管理公钥 | `scripts/installer/flash-linux.sh`、`scripts/install-f2fs.py` 的 `--ssh-key`；设备侧 `piano-first-boot-ssh-key.service` | 镜像里不带 `authorized_keys`。刷写脚本在 SHA256、来源与分区校验之后把公钥写进 ESP 的 `/piano-ssh-key.pub`，首启服务导入后删除。不传 `--ssh-key` 就只保留密码登录 | 已解决 | 完成 |
| 电极校准（厂商数据） | `packages/xiaomi-piano-thp/usr/share/piano/touch/piano-{csot,boe}.json` 与 `usr/share/piano/pen/calibration/piano-{csot,boe}.ini`；来源是仓库密钥 `TOUCH_CALIBRATION_TGZ_B64` 里的原厂 INI | 原厂 INI 本身不进 Git，但从它提取的触控校准表与笔校准数值已经提交（`source_sha256` 指回 INI），并随 RPM 分发 | 许可不明的厂商数据已经进入公开仓库与镜像；“INI 不进 Git”不等于数据没公开 | 已决定（2026-10-21）：承认已公开，`NOTICE` 写明来源与保留意见，发布照常。下一轮拆成独立的 `piano-calibration` 可选包，主镜像不带 |
| 已知默认密码与自动登录 | `scripts/finalize-fedora-rootfs.py:21`（口令常量）、`:676`（GDM 自动登录）、`:761`（manifest 里的登录证明） | `piano` 用户使用文档化默认密码 `piano123456`，GDM 自动登录，root 锁定 | 拿到同一镜像的任何人都能直接进桌面 | 已决定：默认密码作为产品默认值保留，文档继续写明；不强制改密、不生成随机密码；自动登录保持现状 |
| 真机序列号 | `docs/installation.md`、`tests/check_manual_flash_flow.py` | 已改为 `<serial>` 与 `SN-TEST-1` | 已解决，历史提交里仍有 | 完成（历史未重写） |
| 个人绝对路径 | `scripts/verify-new-image.sh` | 默认镜像路径从脚本位置推导，内核 release 读 `root-image-manifest.json` | 已解决 | 完成 |
| 内网拓扑 | `docs/architecture.md`、`docs/virtualization.md`、`scripts/probe-device.sh` | 公开文档用“桥接机”这类角色名，`probe-device.sh` 的示例地址用 `192.0.2.10`；`192.168.77.0/30` 是设备固定网段，保留 | 已解决 | 完成 |
| 旧的公钥历史 | 切换前的 dispatch 事件里带的 `ssh_public_key` 输入 | 旧公钥仍然留在历史事件里 | 公钥不是秘密，但它把一个人的身份和这台设备的管理权限挂在公开记录上 | 已换用安装期注入；旧公钥在设备侧作废 |

不需要处理：COPR 的 owner/project 来自仓库密钥 `COPR_LOGIN`/`COPR_TOKEN`，没有写死个人命名空间；镜像里没有 Wi-Fi 凭据；上游地址都是公开仓库。

SSH 公钥已改成安装期注入，“别人装了进不去”的问题解决了。电极校准按 2026-10-21 的决定处理：数据已在公开仓库与公开 COPR 里，`NOTICE` 写明来源，Release 不再因此阻挡。`release.yml` 仍然默认只建 draft，转公开要显式传 `publish=true`。
