"""开机自启动：写入 HKCU\\...\\Run，不污染系统目录。"""

from __future__ import annotations

import os
import sys

try:
    import winreg
except ImportError:  # pragma: no cover - 非 Windows
    winreg = None  # type: ignore[assignment]

APP_NAME = "KlineCountdown"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def launch_command() -> str:
    """当前进程的启动命令。"""
    executable = sys.executable
    if not executable:
        return ""
    script = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0] else ""
    if getattr(sys, "frozen", False) or not script:
        return f'"{executable}"'
    return f'"{executable}" "{script}"'


def set_autostart(enabled: bool) -> bool:
    """开启/关闭开机启动。返回是否成功。"""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def is_autostart_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _type = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except (FileNotFoundError, OSError):
        return False
