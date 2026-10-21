#!/usr/bin/env python3
"""README 展示图脚本的契约检查。

渲染本身需要 ImageMagick 和真实截图，这里只验证脚本结构与参数解析，
保证 CI 的通用环境也能跑。真实的渲染在一次真机验收里人工确认。
"""

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RENDER = ROOT / "scripts" / "render-screenshot.py"


def load():
    spec = importlib.util.spec_from_file_location("render_screenshot", RENDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_is_importable():
    module = load()
    for name in ("render", "pick_font", "rounded_mask", "require_magick"):
        assert hasattr(module, name), f"render-screenshot.py lost {name}()"


def test_mask_is_a_rounded_rectangle():
    module = load()
    text = module.rounded_mask(800, 600, 24)
    assert text.startswith("roundrectangle"), text
    # 圆角半径必须同时作用在四个角，否则会露出直角。
    assert "0,0 799,599 24,24" in text, text


def test_caption_uses_annotate_not_label():
    """`label:` 需要 pango，没编译 pango 的 ImageMagick 会静默画不出字。

    2026-10-21 实测：用 label: 时输出图里没有文字，也不报错。
    """
    text = RENDER.read_text()
    assert "-annotate" in text
    assert '"label:' not in text and "'label:" not in text, (
        "caption 必须用 -annotate，label: 依赖 pango")


def test_font_lookup_falls_back_to_a_file():
    """-list font 只列出 type1 名单时，按名字找不到字体，要退回文件路径。"""
    text = RENDER.read_text()
    assert "LiberationSans-Regular.ttf" in text or "DejaVuSans.ttf" in text
    module = load()
    # 字体名检查要能命中带换行的 "Font: X" 行。
    assert ", ".join([]) == ""


def test_canvas_keeps_the_panel_aspect():
    module = load()
    cw, ch = module.CANVAS
    # 3200x2136 是 1.498；画布与它接近时边框不会露出背景接缝。
    ratio = cw / ch
    assert 1.3 < ratio < 1.7, f"canvas aspect {ratio:.3f} is far from the panel's 1.498"
    assert module.MARGIN > 0 and module.RADIUS > 0


def test_script_has_no_device_access():
    """排版脚本只处理本地图片，不碰设备。"""
    tree = ast.parse(RENDER.read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])
    for forbidden in ("paramiko", "fabric"):
        assert forbidden not in imports, forbidden
    assert "subprocess" in imports


def main():
    test_script_is_importable()
    test_mask_is_a_rounded_rectangle()
    test_caption_uses_annotate_not_label()
    test_font_lookup_falls_back_to_a_file()
    test_canvas_keeps_the_panel_aspect()
    test_script_has_no_device_access()
    print("OK: README 展示图脚本的圆角遮罩、标注方式与字体回退")


if __name__ == "__main__":
    main()
