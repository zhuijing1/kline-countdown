"""角标文字渲染：用 GDI 把文字画成「灰度覆盖图」，再合成出带 alpha 的 BGRA 位图。

为什么不用 Tk 画布
------------------
Tk 没有逐像素 alpha。透明只能靠「颜色键」——指定一个颜色当作全透明。
文字的抗锯齿边缘是「白 + 底色」混合出来的灰度像素，它们既不是纯白也不是键色，
于是被原样画到屏幕上，在浅色壁纸下就成了一层黑边 / 毛刺。

这里的做法：把文字画到一张纯黑底上（白色字），**灰度值就是覆盖率**，
再按覆盖率把前景色和背景色合成进 32 位 BGRA，交给 ``UpdateLayeredWindow``。
这样边缘是真正的半透明像素，和任何壁纸都自然融合，不需要描边。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w

from .winapi import BITMAPINFO, BITMAPINFOHEADER

gdi32 = ctypes.windll.gdi32
user32 = ctypes.windll.user32

TRANSPARENT = 1
ANTIALIASED_QUALITY = 4
DEFAULT_CHARSET = 1
FW_NORMAL = 400
FW_BOLD = 700
DT_CENTER = 0x00000001
DT_VCENTER = 0x00000004
DT_SINGLELINE = 0x00000020
DT_NOPREFIX = 0x00000800
BI_RGB = 0


gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateDIBSection.argtypes = [w.HDC, ctypes.POINTER(BITMAPINFO), w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD]
gdi32.CreateDIBSection.restype = w.HBITMAP
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.LPCWSTR,
]
gdi32.CreateFontW.restype = w.HFONT
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteDC.argtypes = [w.HDC]
gdi32.SetBkMode.argtypes = [w.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [w.HDC, w.COLORREF]
gdi32.GetTextExtentPoint32W.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int, ctypes.POINTER(w.SIZE)]
user32.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int, ctypes.POINTER(w.RECT), w.UINT]
user32.DrawTextW.restype = ctypes.c_int


def _parse_color(value: str) -> tuple[int, int, int]:
    color = (value or "").lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    if len(color) < 6:
        return 255, 255, 255
    try:
        return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
    except ValueError:
        return 255, 255, 255


class GdiTextRenderer:
    """把一行文字渲染成带 alpha 的 BGRA 位图（预乘）。"""

    def __init__(self) -> None:
        self._dc = gdi32.CreateCompatibleDC(None)
        self._font: int = 0
        self._font_key: tuple | None = None

    def close(self) -> None:
        self._drop_font()
        if self._dc:
            gdi32.DeleteDC(self._dc)
            self._dc = 0

    def _drop_font(self) -> None:
        if self._font:
            gdi32.DeleteObject(self._font)
            self._font = 0
            self._font_key = None

    def _use_font(self, family: str, size: int, weight: str) -> None:
        key = (family, int(size), weight)
        if key == self._font_key:
            return
        self._drop_font()
        self._font = gdi32.CreateFontW(
            -max(6, int(size)),  # 负值 = 字符高度（像素），不受 DPI 缩放影响
            0, 0, 0,
            FW_BOLD if str(weight).lower() == "bold" else FW_NORMAL,
            0, 0, 0,
            DEFAULT_CHARSET, 0, 0,
            ANTIALIASED_QUALITY,  # 灰度抗锯齿；ClearType 会带彩色边，不适合做 alpha
            0,
            family,
        )
        gdi32.SelectObject(self._dc, self._font)
        gdi32.SetBkMode(self._dc, TRANSPARENT)
        gdi32.SetTextColor(self._dc, 0x00FFFFFF)
        self._font_key = key

    def measure(self, text: str, family: str, size: int, weight: str) -> tuple[int, int]:
        self._use_font(family, size, weight)
        extent = w.SIZE()
        gdi32.GetTextExtentPoint32W(self._dc, text, len(text), ctypes.byref(extent))
        return max(1, int(extent.cx)), max(1, int(extent.cy))

    def _coverage(self, text: str, width: int, height: int) -> bytearray:
        """把文字画到纯黑底上，返回逐像素灰度（0–255）= 覆盖率。"""
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # 负数 = 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB

        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(self._dc, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits:
            return bytearray(width * height)
        previous = gdi32.SelectObject(self._dc, bitmap)
        try:
            ctypes.memset(bits, 0, width * height * 4)
            rect = w.RECT(0, 0, width, height)
            user32.DrawTextW(
                self._dc, text, len(text), ctypes.byref(rect),
                DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
            )
            raw = ctypes.string_at(bits, width * height * 4)
        finally:
            gdi32.SelectObject(self._dc, previous)
            gdi32.DeleteObject(bitmap)

        # 灰底白字：取任一分量即可（ANTIALIASED 出来是灰度）
        return bytearray(raw[0::4])


def render_badge(
    text: str,
    *,
    font_family: str,
    font_size: int,
    font_weight: str,
    fg: str,
    bg: str,
    transparent: bool,
    radius: int,
    opacity: float,
    padding_x: int,
    padding_y: int,
    width: int = 0,
    height: int = 0,
    renderer: GdiTextRenderer | None = None,
) -> tuple[int, int, bytes]:
    """返回 (宽, 高, 预乘 BGRA 字节串)。透明模式下底块不画，只留文字。"""
    own = renderer is None
    renderer = renderer or GdiTextRenderer()
    try:
        text_w, text_h = renderer.measure(text, font_family, font_size, font_weight)
        total_w = width if width > 0 else text_w + 2 * max(2, padding_x)
        total_h = height if height > 0 else text_h + 2 * max(1, padding_y)
        total_w = max(1, int(total_w))
        total_h = max(1, int(total_h))

        coverage = renderer._coverage(text, total_w, total_h)
        fr, fg_g, fb = _parse_color(fg)
        br, bg_g, bb = _parse_color(bg)
        alpha255 = int(max(0.0, min(1.0, float(opacity))) * 255)

        # 底块覆盖率（圆角矩形），透明模式下没有底块
        box = None if transparent else _rounded_coverage(total_w, total_h, max(0, int(radius)))

        # 256 级查找表：按文字覆盖率直接取预乘 BGRA，避免逐像素做浮点除法
        table = _blend_table(fr, fg_g, fb, br, bg_g, bb, alpha255, has_bg=box is not None)
        out = bytearray(table[0] * (total_w * total_h))  # 底块统一填充（透明模式下是全 0）

        for index, a_text in enumerate(coverage):
            if a_text:
                offset = index * 4
                out[offset: offset + 4] = table[a_text]

        # 圆角边缘：把 alpha 按覆盖率衰减（只有边框那一圈，量很小）。
        # 预乘数据直接整体乘系数即可，颜色不会变。
        if box is not None:
            for index, a_box in _border_pixels(total_w, total_h, max(0, int(radius))):
                offset = index * 4
                if a_box <= 0:
                    out[offset: offset + 4] = b"\x00\x00\x00\x00"
                elif a_box < 255:
                    k = a_box / 255.0
                    out[offset] = int(out[offset] * k)
                    out[offset + 1] = int(out[offset + 1] * k)
                    out[offset + 2] = int(out[offset + 2] * k)
                    out[offset + 3] = int(out[offset + 3] * k)
        return total_w, total_h, bytes(out)
    finally:
        if own:
            renderer.close()


def _blend_table(
    fr: int, fg_g: int, fb: int,
    br: int, bg_g: int, bb: int,
    alpha255: int, has_bg: bool,
) -> list[bytes]:
    """t = 文字覆盖率（0–255）→ 预乘 BGRA。"""
    table: list[bytes] = []
    for t in range(256):
        n = t / 255.0
        if has_bg:
            # 底块不透明，文字按覆盖率压在上面
            r = fr * n + br * (1 - n)
            g = fg_g * n + bg_g * (1 - n)
            b = fb * n + bb * (1 - n)
            a = alpha255
        else:
            r, g, b = fr, fg_g, fb
            a = int(t * alpha255 / 255)
        table.append(bytes((
            int(b * a / 255), int(g * a / 255), int(r * a / 255), a,
        )))
    return table


_BORDER_CACHE: dict[tuple[int, int, int], list[tuple[int, int]]] = {}


def _border_pixels(width: int, height: int, radius: int) -> list[tuple[int, int]]:
    """圆角边缘上覆盖率 < 255 的像素（缓存，形状不变就不用重算）。"""
    key = (width, height, radius)
    cached = _BORDER_CACHE.get(key)
    if cached is not None:
        return cached
    box = _rounded_coverage(width, height, radius)
    border = [(index, value) for index, value in enumerate(box) if value < 255]
    if len(_BORDER_CACHE) > 16:
        _BORDER_CACHE.clear()
    _BORDER_CACHE[key] = border
    return border


_ROUNDED_CACHE: dict[tuple[int, int, int], bytearray] = {}


def _rounded_coverage(width: int, height: int, radius: int) -> bytearray:
    """圆角矩形的覆盖率（1px 软边，避免锯齿）。

    用圆角矩形的有符号距离场：``d = |clamp(p - c, -(h - r))| - r``，
    d < 0 在内部，d > 0 在外部，边界处取 1px 过渡。

    形状只跟尺寸和半径有关，按 (宽, 高, 半径) 缓存——否则每帧两万多次开方太浪费。
    """
    if radius <= 0:
        return bytearray([255]) * (width * height)

    radius = min(radius, min(width, height) // 2)
    key = (width, height, radius)
    cached = _ROUNDED_CACHE.get(key)
    if cached is not None:
        return cached

    half_w, half_h = width / 2.0, height / 2.0
    inner_w, inner_h = half_w - radius, half_h - radius
    coverage = bytearray(width * height)
    for y in range(height):
        py = y + 0.5 - half_h
        dy = abs(py) - inner_h
        if dy < 0:
            dy = 0.0
        row = y * width
        for x in range(width):
            px = x + 0.5 - half_w
            dx = abs(px) - inner_w
            if dx < 0:
                dx = 0.0
            distance = (dx * dx + dy * dy) ** 0.5 - radius
            if distance <= -0.5:
                coverage[row + x] = 255
            elif distance >= 0.5:
                coverage[row + x] = 0
            else:
                coverage[row + x] = int((0.5 - distance) * 255)
    if len(_ROUNDED_CACHE) > 16:  # 尺寸调来调去时别无限涨
        _ROUNDED_CACHE.clear()
    _ROUNDED_CACHE[key] = coverage
    return coverage
