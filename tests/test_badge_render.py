"""角标渲染测试：逐像素 alpha、两种闪烁方式、无描边毛刺。

直接检查送进 ``UpdateLayeredWindow`` 的那张 BGRA 位图，
比截图稳定，也不依赖窗口是否可见。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import FLASH_PULSE, FLASH_TOGGLE  # noqa: E402
from core.textrender import render_badge  # noqa: E402
from ui.badge import BadgeVisual, readable_on  # noqa: E402

BASE = dict(
    font_family="Bahnschrift",
    font_size=28,
    font_weight="normal",
    padding_x=12,
    padding_y=6,
)


def render(
    *,
    fg: str = "#FFFFFF",
    bg: str = "#1B1B1F",
    transparent: bool = True,
    radius: int = 0,
    opacity: float = 1.0,
    text: str = "02:45",
    flash_on: bool = False,
    flash_color: str = "#FFD400",
    flash_style: str = FLASH_TOGGLE,
):
    """按 App 的取色规则（含闪烁）算出颜色，再渲染成 BGRA。"""
    from ui.badge import BadgeWindow

    visual = BadgeVisual(text, fg, bg, flash_on, flash_color, flash_style)
    real_fg, real_bg = BadgeWindow._colors(None, visual)
    return render_badge(
        text,
        **BASE,
        fg=real_fg,
        bg=real_bg,
        transparent=transparent,
        radius=radius,
        opacity=opacity,
    )


def pixel(data: bytes, width: int, x: int, y: int) -> tuple[int, int, int, int]:
    """返回 (B, G, R, A)。"""
    offset = (y * width + x) * 4
    return data[offset], data[offset + 1], data[offset + 2], data[offset + 3]


def find_opaque(data: bytes, width: int, height: int) -> tuple[int, int]:
    for y in range(height):
        for x in range(width):
            if pixel(data, width, x, y)[3] == 255:
                return x, y
    raise AssertionError("没有找到不透明像素")


def find_color(data: bytes, width: int, height: int, rgb: tuple[int, int, int]) -> tuple[int, int]:
    """找第一个颜色等于 rgb 且完全不透明的像素（用来定位文字）。"""
    for y in range(height):
        for x in range(width):
            b, g, r, a = pixel(data, width, x, y)
            if a == 255 and (r, g, b) == rgb:
                return x, y
    raise AssertionError(f"没有找到颜色 {rgb} 的像素")


class TestTextRender(unittest.TestCase):
    def test_transparent_leaves_corners_empty(self):
        width, height, data = render(transparent=True, fg="#FFD400", bg="#111111", radius=0, opacity=1.0)
        self.assertEqual(len(data), width * height * 4)
        self.assertEqual(pixel(data, width, 0, 0)[3], 0)          # 左上角全透明
        self.assertEqual(pixel(data, width, width - 1, height - 1)[3], 0)
        x, y = find_opaque(data, width, height)
        b, g, r, _ = pixel(data, width, x, y)
        self.assertEqual((r, g, b), (0xFF, 0xD4, 0x00))            # 数字是前景色

    def test_antialias_edges_have_partial_alpha(self):
        """边缘必须是「半透明的同色像素」，而不是混了底色的黑边。"""
        width, height, data = render(transparent=True, fg="#FFFFFF", bg="#000000", radius=0, opacity=1.0)
        partial = 0
        for y in range(height):
            for x in range(width):
                b, g, r, a = pixel(data, width, x, y)
                if 0 < a < 255:
                    partial += 1
                    # 预乘后的白色：三个分量都等于 alpha，说明没有掺黑
                    self.assertLessEqual(abs(r - a), 2)
                    self.assertLessEqual(abs(g - a), 2)
                    self.assertLessEqual(abs(b - a), 2)
        self.assertGreater(partial, 50, "应该有明显的抗锯齿边缘")

    def test_solid_background_fills_corners(self):
        width, height, data = render(transparent=False, fg="#FFFFFF", bg="#1B1B1F", radius=0, opacity=1.0)
        b, g, r, a = pixel(data, width, 0, 0)
        self.assertEqual(a, 255)
        self.assertEqual((r, g, b), (0x1B, 0x1B, 0x1F))

    def test_rounded_corner_is_clear(self):
        width, height, data = render(transparent=False, bg="#1B1B1F", radius=12, opacity=1.0)
        self.assertEqual(pixel(data, width, 0, 0)[3], 0)  # 圆角外是透明的
        self.assertEqual(pixel(data, width, width // 2, 0)[3], 255)  # 顶边中间不透明

    def test_opacity_baked_into_alpha(self):
        width, height, data = render(transparent=False, bg="#1B1B1F", radius=0, opacity=0.5)
        self.assertIn(pixel(data, width, 0, 0)[3], (127, 128))

    def test_pulse_flash_only_changes_text_color(self):
        width, height, dark = render(transparent=False, fg="#FFFFFF", bg="#B3261E", radius=0,
                                     opacity=1.0, flash_on=False, flash_color="#FFD400",
                                     flash_style=FLASH_PULSE)
        _, _, bright = render(transparent=False, fg="#FFFFFF", bg="#B3261E", radius=0,
                              opacity=1.0, flash_on=True, flash_color="#FFD400",
                              flash_style=FLASH_PULSE)
        # 底色像素不变
        self.assertEqual(pixel(dark, width, 0, 0), pixel(bright, width, 0, 0))
        # 文字像素变色：暗相是白色字，亮相是闪色字
        x, y = find_color(dark, width, height, (0xFF, 0xFF, 0xFF))
        self.assertEqual(pixel(bright, width, x, y)[:3], (0x00, 0xD4, 0xFF))

    def test_toggle_flash_changes_background(self):
        width, height, dark = render(transparent=False, bg="#B3261E", radius=0, opacity=1.0,
                                     flash_on=False, flash_color="#FFD400", flash_style=FLASH_TOGGLE)
        _, _, bright = render(transparent=False, bg="#B3261E", radius=0, opacity=1.0,
                              flash_on=True, flash_color="#FFD400", flash_style=FLASH_TOGGLE)
        self.assertNotEqual(pixel(dark, width, 0, 0), pixel(bright, width, 0, 0))
        self.assertEqual(pixel(bright, width, 0, 0)[:3], (0x00, 0xD4, 0xFF))  # BGRA 的闪色

    def test_readable_on_light_and_dark(self):
        self.assertEqual(readable_on("#FFFFFF"), "#111111")
        self.assertEqual(readable_on("#000000"), "#FFFFFF")
        self.assertEqual(readable_on("#FDE047"), "#111111")
        self.assertEqual(readable_on("#B3261E"), "#FFFFFF")


class TestBadgeVisual(unittest.TestCase):
    """不依赖 Tk 显示环境：直接验证闪烁取色规则。"""

    def test_pulse_keeps_background(self):
        from ui.badge import BadgeWindow

        fg, bg = BadgeWindow._colors(None, BadgeVisual(
            text="02:45", fg="#FFFFFF", bg="#B3261E", flash_on=True,
            flash_color="#FFD400", flash_style=FLASH_PULSE,
        ))
        self.assertEqual(fg, "#FFD400")
        self.assertEqual(bg, "#B3261E")

    def test_toggle_swaps_background(self):
        from ui.badge import BadgeWindow

        fg, bg = BadgeWindow._colors(None, BadgeVisual(
            text="02:45", fg="#FFFFFF", bg="#B3261E", flash_on=True,
            flash_color="#FFD400", flash_style=FLASH_TOGGLE,
        ))
        self.assertEqual(bg, "#FFD400")
        self.assertEqual(fg, readable_on("#FFD400"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
