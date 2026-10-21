"""程序图标：无第三方依赖，运行时绘制。

提供两种产出：
* :func:`render_rgba` —— 自上而下 RGBA 位图，供 PNG / 窗口图标使用；
* :func:`build_ico_bytes` / :func:`write_icon_file` —— Windows ICO（托盘用）。

设计：深色圆底盘 + 白色表环 + 红色 120° 进度弧。
"""

from __future__ import annotations

import struct
from pathlib import Path

SUPERSAMPLE = 4  # 4x 超采样后盒式降采样，得到平滑边缘
ICON_SIZES = (16, 24, 32, 48)


def _sample(x: float, y: float) -> tuple[float, float, float, float]:
    """在归一化坐标 (0..1) 上取点的 RGBA（0..1 范围）。"""
    from math import atan2, pi

    cx, cy = 0.5, 0.5
    dx, dy = x - cx, y - cy
    dist = (dx * dx + dy * dy) ** 0.5

    outer, inner, ring = 0.47, 0.40, 0.36
    if dist > outer:
        return (0.0, 0.0, 0.0, 0.0)

    accent = (250 / 255, 77 / 255, 79 / 255, 1.0)
    bg = (27 / 255, 27 / 255, 31 / 255, 1.0)
    fg = (1.0, 1.0, 1.0, 1.0)

    angle = (atan2(dx, -dy) + 2 * pi) % (2 * pi)  # 12 点方向起算
    in_arc = ring <= dist <= inner and angle <= (120.0 / 180.0) * pi

    if ring <= dist <= outer:
        return accent if in_arc else fg
    if dist < ring:
        return accent if in_arc else bg
    return bg


def render_rgba(size: int, bottom_up: bool = False) -> bytes:
    """渲染 size×size 的 8 位 RGBA 位图。"""
    ss = SUPERSAMPLE
    dim = size * ss
    rows: list[bytearray] = []
    for oy in range(size):
        row = bytearray()
        for ox in range(size):
            sums = [0.0, 0.0, 0.0, 0.0]
            for j in range(ss):
                y = ((oy * ss + j) + 0.5) / dim
                for i in range(ss):
                    x = ((ox * ss + i) + 0.5) / dim
                    pixel = _sample(x, y)
                    for channel in range(4):
                        sums[channel] += pixel[channel]
            avg = [min(255, max(0, int(round(s * 255 / (ss * ss))))) for s in sums]
            row.extend(avg)
        rows.append(row)
    if bottom_up:
        rows.reverse()
    return b"".join(bytes(row) for row in rows)


def _bgra_rows(size: int) -> bytes:
    """ICO 需要 BGRA 且自下而上。"""
    rgba = bytearray(render_rgba(size))
    for offset in range(0, len(rgba), 4):
        rgba[offset], rgba[offset + 2] = rgba[offset + 2], rgba[offset]
    rows = [bytes(rgba[i: i + size * 4]) for i in range(0, len(rgba), size * 4)]
    rows.reverse()
    return b"".join(rows)


def _ico_image(size: int) -> tuple[bytes, bytes]:
    """返回 (ICONDIR 目录项, 位图数据)。"""
    pixels = _bgra_rows(size)
    and_mask = b"\x00" * (((size + 31) // 32) * 4 * size)
    header = struct.pack(
        "<IiiHHIIiiII",
        40,                # biSize
        size,              # biWidth
        size * 2,          # biHeight（含 AND 掩码高度）
        1,                 # biPlanes
        32,                # biBitCount
        0,                 # biCompression
        size * size * 4,   # biSizeImage
        0, 0, 0, 0,
    )
    blob = header + pixels + and_mask
    dimension = 0 if size >= 256 else size
    entry = struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(blob), 0)
    return entry, blob


def build_ico_bytes(sizes=ICON_SIZES) -> bytes:
    images = [_ico_image(size) for size in sizes]
    count = len(images)
    out = struct.pack("<HHH", 0, 1, count)  # ICONDIR: reserved, type=1, count
    offset = 6 + 16 * count
    for entry, blob in images:
        out += entry[:-4] + struct.pack("<I", offset)
        offset += len(blob)
    out += b"".join(blob for _entry, blob in images)
    return out


def write_icon_file(path: str | Path, sizes=ICON_SIZES) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_ico_bytes(sizes))
    return str(path)


def build_png_bytes(size: int = 200) -> bytes:
    """用 zlib 手写一个 RGBA PNG（PyInstaller 窗口图标友好）。"""
    import zlib

    raw = render_rgba(size)
    stride = size * 4

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8bit RGBA
    scanlines = b"".join(b"\x00" + raw[y * stride:(y + 1) * stride] for y in range(size))
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", header)
    png += chunk(b"IDAT", zlib.compress(scanlines, 9))
    png += chunk(b"IEND", b"")
    return png


def write_png_file(path: str | Path, size: int = 200) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_png_bytes(size))
    return str(path)


if __name__ == "__main__":  # pragma: no cover
    import tempfile

    target = Path(tempfile.gettempdir()) / "kcd-test.ico"
    write_icon_file(target)
    print(target, target.stat().st_size, "bytes")
