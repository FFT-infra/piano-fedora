#!/usr/bin/env python3
"""把设备裸截图渲染成 README 用的展示图。

输入是一张平板的原始截图（3200x2136），输出是 1920x1080 的设备框展示图：
同一张图放大模糊后铺满画布作背景，中间放缩放的原始截图，四角圆角。

这个脚本只做排版，不碰设备，可以在任何有 ImageMagick 的机器上跑。
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 输出画布与留白。比例与 3200x2136 一致，边框不会露出背景的接缝。
CANVAS = (1920, 1280)
MARGIN = 96
RADIUS = 24


def run(args):
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{' '.join(str(a) for a in args)} failed: {result.stderr.strip()}")
    return result


def require_magick():
    for name in ("magick", "convert"):
        if shutil.which(name):
            return name
    raise RuntimeError("ImageMagick not found: install imagemagick or magick")


def rounded_mask(width, height, radius):
    """生成一张白底黑角的圆角遮罩，用于把截图裁成圆角矩形。"""
    return (
        f"roundrectangle 0,0 {width - 1},{height - 1} {radius},{radius}"
    )


def render(source: Path, dest: Path, canvas, margin, radius, caption=None):
    magick = require_magick()
    cw, ch = canvas
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        # 1) 背景：原图等比铺满画布后重模糊。用 40 的半径配合 sRGB，
        #    避免直接在线性空间里模糊导致整体发灰。
        background = tmp / "background.png"
        run([magick, str(source), "-auto-orient",
             "-resize", f"{cw}x{ch}^", "-gravity", "center",
             "-extent", f"{cw}x{ch}",
             "-colorspace", "sRGB", "-blur", "0x40",
             "-modulate", "100,80,70",
             str(background)])

        # 2) 前景：按画布留白等比缩放。留出底部一条给设备标签。
        caption_band = 72 if caption else 0
        fw = cw - margin * 2
        fh = ch - margin * 2 - caption_band
        foreground = tmp / "foreground.png"
        run([magick, str(source), "-auto-orient",
             "-resize", f"{fw}x{fh}", "-colorspace", "sRGB",
             str(foreground)])

        fg_w, fg_h = map(int, run([magick, "identify", "-format", "%w %h", str(foreground)]).stdout.split())

        # 3) 圆角遮罩，只作用在前景上。
        mask = tmp / "mask.png"
        run([magick, "-size", f"{fg_w}x{fg_h}", "xc:black",
             "-fill", "white", "-draw", rounded_mask(fg_w, fg_h, radius),
             str(mask)])
        rounded = tmp / "rounded.png"
        run([magick, str(foreground), str(mask),
             "-alpha", "off", "-compose", "CopyOpacity", "-composite",
             str(rounded)])

        # 4) 合成。前景居中；有 caption 时整体上移半个标签高度。
        compose_args = [magick, str(background), "(", str(rounded), ")"]
        if caption:
            compose_args += ["-gravity", "center", "-geometry", f"+0-{caption_band // 2}"]
        else:
            compose_args += ["-gravity", "center"]
        compose_args += ["-compose", "over", "-composite"]
        staged = tmp / "staged.png"
        run(compose_args + [str(staged)])

        if not caption:
            run([magick, str(staged), "-depth", "8", str(dest)])
            return

        # 5) 底部一行的设备标签。用 -annotate 直接画，不依赖 pango：
        #    `label:` 在没编译 pango 的 ImageMagick 上会静默画不出字。
        font = pick_font(magick)
        run([magick, str(staged),
             "-gravity", "south", "-fill", "rgba(255,255,255,0.92)",
             "-font", font, "-pointsize", "30",
             "-annotate", f"+0+{margin // 2}", caption,
             "-depth", "8", str(dest)])


def pick_font(magick):
    """挑一个系统里存在的无衬线字体，找不到就用字体文件的绝对路径。"""
    listing = run([magick, "-list", "font"]).stdout
    for candidate in ("Liberation-Sans", "DejaVu-Sans", "Noto-Sans", "Helvetica"):
        if f"Font: {candidate}\n" in listing or f"Font: {candidate} " in listing:
            return candidate
    # -list font 可能只有 type1 名单；退回按文件找。
    for path in ("/usr/share/fonts/liberation-sans/LiberationSans-Regular.ttf",
                 "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                 "/usr/share/fonts/dejavu/DejaVuSans.ttf"):
        if Path(path).is_file():
            return path
    raise RuntimeError("no sans-serif font found; install liberation-sans or dejavu-sans")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=Path, help="设备原始截图")
    ap.add_argument("dest", type=Path, help="输出的展示图")
    ap.add_argument("--width", type=int, default=CANVAS[0])
    ap.add_argument("--height", type=int, default=CANVAS[1])
    ap.add_argument("--margin", type=int, default=MARGIN)
    ap.add_argument("--radius", type=int, default=RADIUS)
    ap.add_argument("--caption", help="底部设备标签，例如 'Xiaomi Pad 8 Pro · 3200x2136'")
    args = ap.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"source not found: {args.source}")
    render(args.source, args.dest, (args.width, args.height),
           args.margin, args.radius, args.caption)
    print(f"{args.dest}: "
          f"{run([require_magick(), 'identify', '-format', '%wx%h', str(args.dest)]).stdout}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
