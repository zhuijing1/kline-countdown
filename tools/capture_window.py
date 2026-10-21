"""按窗口标题抓图（PrintWindow，不受遮挡影响）。

用法：
    python tools/capture_window.py "K线倒计时 · 设置" out.png

只依赖标准库：用 ctypes 调 GDI 抓位图，再用 zlib 手写 PNG。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as w
import struct
import sys
import zlib

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

# 必须先声明 DPI 感知：否则 GetWindowRect 返回的是被缩放后的逻辑坐标，
# 抓出来的图会被裁掉右下角。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
except (AttributeError, OSError):
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020

gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateCompatibleBitmap.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = w.HBITMAP
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.BitBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD]
gdi32.BitBlt.restype = w.BOOL
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteDC.argtypes = [w.HDC]
user32.GetWindowDC.argtypes = [w.HWND]
user32.GetWindowDC.restype = w.HDC
user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.PrintWindow.argtypes = [w.HWND, w.HDC, w.UINT]
user32.GetDC.argtypes = [w.HWND]
user32.GetDC.restype = w.HDC
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", w.DWORD),
        ("biWidth", w.LONG),
        ("biHeight", w.LONG),
        ("biPlanes", w.WORD),
        ("biBitCount", w.WORD),
        ("biCompression", w.DWORD),
        ("biSizeImage", w.DWORD),
        ("biXPelsPerMeter", w.LONG),
        ("biYPelsPerMeter", w.LONG),
        ("biClrUsed", w.DWORD),
        ("biClrImportant", w.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", w.DWORD * 3)]


gdi32.GetDIBits.argtypes = [w.HDC, w.HBITMAP, w.UINT, w.UINT, ctypes.c_void_p,
                            ctypes.POINTER(BITMAPINFO), w.UINT]
gdi32.GetDIBits.restype = ctypes.c_int


def find_window(title_part: str) -> int:
    found: list[int] = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def enum_proc(hwnd, _lparam):  # noqa: ANN001
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_part in buf.value:
            found.append(hwnd)
        return True

    user32.EnumWindows(enum_proc, 0)
    return found[0] if found else 0


def capture(hwnd: int, from_screen: bool = False) -> tuple[int, int, bytes]:
    rect = w.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top

    if from_screen:
        # 逐像素 alpha 的分层窗口用 PrintWindow 抓不到内容（DWM 合成），
        # 直接从屏幕 BitBlt 才是用户真正看到的画面。
        hdc = user32.GetDC(None)
    else:
        hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(mem, bmp)
    if from_screen:
        gdi32.BitBlt(mem, 0, 0, width, height, hdc, rect.left, rect.top, SRCCOPY)
    else:
        user32.PrintWindow(hwnd, mem, PW_RENDERFULLCONTENT)

    info = BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height  # 负数 = 自上而下
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0  # BI_RGB

    size = width * height * 4
    buf = ctypes.create_string_buffer(size)
    gdi32.GetDIBits(mem, bmp, 0, height, buf, ctypes.byref(info), 0)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    if from_screen:
        user32.ReleaseDC(None, hdc)
    else:
        user32.ReleaseDC(hwnd, hdc)
    return width, height, buf.raw


def write_png(path: str, width: int, height: int, bgra: bytes) -> None:
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # filter: None
        base = y * width * 4
        for x in range(width):
            i = base + x * 4
            rows += bytes((bgra[i + 2], bgra[i + 1], bgra[i]))  # BGRA -> RGB

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(rows), 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as handle:
        handle.write(png)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: capture_window.py <title-part> <out.png> [--screen]")
        return 2
    from_screen = "--screen" in sys.argv[3:]
    hwnd = find_window(sys.argv[1])
    if not hwnd:
        print("window not found:", sys.argv[1])
        return 1
    width, height, data = capture(hwnd, from_screen=from_screen)
    write_png(sys.argv[2], width, height, data)
    print(f"saved {sys.argv[2]} ({width}x{height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
