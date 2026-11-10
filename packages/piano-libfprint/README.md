# piano-libfprint

Fedora 44/45 自带的 `libfprint-1.94.100-1` 与 SunUEFI `config/fingerprint.json`
pin 的是同一份源码：`v1.94.100` 是附注标签，解引用后为
`80a4b5ec612892c5c056c48dddaa561452cf37ec`。这个包拿官方 tarball，套上游
`patches/libfprint/1.94.100/0001-piano-goodix-uio.patch`（sha256
`6577da4849d748dfe472c9a2026419f91be175a98a926e3b98f0de5203ca1696`），在
`-Ddrivers=all` 下把 `piano_goodix` 驱动链到 `piano-fingerprint` 安装的
`libpiano-goodix.a`。

`0002-meson-dict-foreach.patch` 是本仓库的兼容补丁：Fedora 45 的 meson 1.12
不接受 `foreach` 单变量遍历 dict，而关掉 introspection 后上游的 else 分支一定
会被求值，构建在编译前就停了。

构建参数跟 SunUEFI 的 Arch 配方一致：关 gtk-doc、gtk-examples、
installed-tests、introspection、udev hwdb。包内额外只有
`/usr/lib/systemd/system/fprintd.service.d/20-piano.conf`，内容来自上游
`tools/build_piano_fingerprint.py` 的 `desktop_service()`。

替换发行版的 `libfprint` 用 `Provides` + `Obsoletes`，所以 `Release` 必须高于
Fedora 当前版本。Fedora 升级 libfprint 时，这里的 `Version`、tarball 与补丁要
一起对 pin。
