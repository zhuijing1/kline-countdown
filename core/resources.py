"""运行时资源：把内置生成的图标落到用户本地目录。

窗口 PNG 图标不能放在临时目录（磁盘清理会误删），因此统一放在
``%LOCALAPPDATA%\\KlineCountdown\\icons\\`` 下。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .icon import ICON_SIZES, write_icon_file, write_png_file


def _base_dir() -> Path:
    override = os.environ.get("KLCD_RESOURCES")
    if override:
        return Path(override)
    root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or str(Path.home())
    return Path(root) / "KlineCountdown" / "icons"


def _bundled_asset(name: str) -> Path | None:
    """打包后优先用随 exe 分发的图标，保证 exe / 任务栏 / 托盘三者完全一致。"""
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass) / "assets")
    roots.append(Path(__file__).resolve().parents[1] / "assets")
    for root in roots:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def resource_ico_path() -> str:
    bundled = _bundled_asset("app.ico")
    if bundled is not None:
        return str(bundled)
    path = _base_dir() / "app.ico"
    if not path.exists():
        write_icon_file(path, ICON_SIZES)
    return str(path)


def resource_png_path() -> str:
    bundled = _bundled_asset("app.png")
    if bundled is not None:
        return str(bundled)
    path = _base_dir() / "app.png"
    if not path.exists():
        write_png_file(path, 200)
    return str(path)


def ensure_resources() -> tuple[str, str]:
    return resource_ico_path(), resource_png_path()


if __name__ == "__main__":  # pragma: no cover
    print(ensure_resources())
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, resource_png_path(), "资源路径", 0)
