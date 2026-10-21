"""Win32 薄封装：DPI、鼠标穿透、单实例、多显示器坐标、系统托盘。

只在这里直接摸 ctypes，其余代码不接触 Win32 细节。
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import c_int, wintypes as w
from dataclasses import dataclass
from typing import Callable

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

# ---- 常量 ----
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

HWND_MESSAGE = -3
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 42
WM_NULL = 0x0000
WM_HOTKEY = 0x0312
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111

# 热键修饰符
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETVERSION = 0x00000004
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010
NIIF_INFO = 0x00000001
NIIF_WARNING = 0x00000002

TPM_LEFTALIGN = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
MF_GRAYED = 0x00000001
MF_POPUP = 0x00000010


IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SW_HIDE = 0
WM_SETICON = 0x0080
ICON_SMALL = 0
ICON_BIG = 1

# 逐像素 alpha（UpdateLayeredWindow）
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


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


gdi32 = ctypes.windll.gdi32


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", w.DWORD),
        ("Data2", w.WORD),
        ("Data3", w.WORD),
        ("Data4", w.BYTE * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uTimeout", w.UINT),
        ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", w.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", w.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", c_int),
        ("cbWndExtra", c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", w.HWND),
        ("message", w.UINT),
        ("wParam", w.WPARAM),
        ("lParam", w.LPARAM),
        ("time", w.DWORD),
        ("pt", POINT),
    ]


# SetWindowLongPtrW 在 64 位下可用
_GetWindowLong = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_SetWindowLong = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_GetWindowLong.argtypes = [w.HWND, c_int]
_GetWindowLong.restype = ctypes.c_ssize_t
_SetWindowLong.argtypes = [w.HWND, c_int, ctypes.c_ssize_t]
_SetWindowLong.restype = ctypes.c_ssize_t

# 显式声明 argtypes：否则 64 位下手动传句柄会被截断成 32 位
user32.CreateWindowExW.argtypes = [
    w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
    c_int, c_int, c_int, c_int,
    w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID,
]
user32.CreateWindowExW.restype = w.HWND
shell32.Shell_NotifyIconW.argtypes = [w.UINT, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = w.BOOL
user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT, c_int, c_int, w.UINT]
user32.LoadImageW.restype = w.HANDLE
user32.LoadIconW.argtypes = [w.HINSTANCE, w.LPCWSTR]
user32.LoadIconW.restype = w.HICON
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, c_int, c_int, c_int, w.HWND, ctypes.POINTER(w.RECT)]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.AppendMenuW.argtypes = [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR]
user32.GetParent.argtypes = [w.HWND]
user32.GetParent.restype = w.HWND
kernel32.CreateMutexW.argtypes = [w.LPVOID, w.BOOL, w.LPCWSTR]
kernel32.CreateMutexW.restype = w.HANDLE
user32.GetSystemMetrics.restype = c_int
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterHotKey.argtypes = [w.HWND, c_int, w.UINT, w.UINT]
user32.RegisterHotKey.restype = w.BOOL
user32.UnregisterHotKey.argtypes = [w.HWND, c_int]
user32.UnregisterHotKey.restype = w.BOOL
user32.SendMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t
user32.ShowWindow.argtypes = [w.HWND, c_int]
user32.ShowWindow.restype = w.BOOL
kernel32.GetConsoleWindow.restype = w.HWND
kernel32.GetConsoleProcessList.argtypes = [ctypes.POINTER(ctypes.c_uint32), ctypes.c_uint32]
kernel32.GetConsoleProcessList.restype = ctypes.c_uint32
user32.GetDC.argtypes = [w.HWND]
user32.GetDC.restype = w.HDC
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
user32.UpdateLayeredWindow.argtypes = [
    w.HWND, w.HDC, ctypes.POINTER(POINT), ctypes.POINTER(w.SIZE),
    w.HDC, ctypes.POINTER(POINT), w.DWORD, ctypes.POINTER(BLENDFUNCTION), w.DWORD,
]
user32.UpdateLayeredWindow.restype = w.BOOL
gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateDIBSection.argtypes = [
    w.HDC, ctypes.POINTER(BITMAPINFO), w.UINT, ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD,
]
gdi32.CreateDIBSection.restype = w.HBITMAP
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteDC.argtypes = [w.HDC]


# --------------------------------------------------------------------------
# DPI
# --------------------------------------------------------------------------


def enable_dpi_awareness() -> int:
    """开启 DPI 感知（优先 per-monitor V2），返回系统 DPI（用于 tk scaling）。

    注意：``SetProcessDpiAwarenessContext`` 的参数是 DPI_AWARENESS_CONTEXT 句柄，
    必须按指针宽度传递（``c_void_p(-4)``）。直接传 Python int 会被截断成 32 位，
    调用失败（ERROR_INVALID_PARAMETER），进程仍是 DPI 虚拟化的。
    """
    try:
        func = user32.SetProcessDpiAwarenessContext
        func.argtypes = [ctypes.c_void_p]
        func.restype = w.BOOL
        if func(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return _system_dpi()
    except (AttributeError, OSError):
        pass
    try:
        shcore = ctypes.windll.shcore
        shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
        shcore.SetProcessDpiAwareness.restype = ctypes.c_long
        if shcore.SetProcessDpiAwareness(2) == 0:  # PROCESS_PER_MONITOR_DPI_AWARE
            return _system_dpi()
    except (AttributeError, OSError):
        pass
    try:
        if user32.SetProcessDPIAware():
            return _system_dpi()
    except (AttributeError, OSError):
        pass
    return _system_dpi()


def _system_dpi() -> int:
    try:
        func = user32.GetDpiForSystem
        func.restype = w.UINT
        value = int(func())
        if value > 0:
            return value
    except (AttributeError, OSError):
        pass
    try:
        screen_dc = user32.GetDC(None)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(screen_dc, 88)  # LOGPIXELSX
        user32.ReleaseDC(None, screen_dc)
        return int(dpi or 96)
    except Exception:  # noqa: BLE001
        return 96


# --------------------------------------------------------------------------
# 窗口样式
# --------------------------------------------------------------------------


def set_click_through(hwnd: int, enabled: bool) -> bool:
    """开启/关闭鼠标穿透。

    注意：只操作 ``WS_EX_TRANSPARENT``，**不能**动 ``WS_EX_LAYERED``——
    后者是 Tk 实现 ``-alpha`` / ``-transparentcolor`` 的基础，
    清掉它会让整个窗口既不能透明也不能半透明。
    """
    hwnd = _wrapper(hwnd)
    style = _GetWindowLong(hwnd, GWL_EXSTYLE)
    if not style:
        return False
    if enabled:
        new_style = int(style) | WS_EX_TRANSPARENT
    else:
        new_style = int(style) & ~WS_EX_TRANSPARENT
    return bool(_SetWindowLong(hwnd, GWL_EXSTYLE, new_style))


def ensure_layered(hwnd: int, needed: bool = True) -> bool:
    """确保 ``WS_EX_LAYERED`` 在位（用了半透明或颜色键透明时必须为真）。"""
    if not needed:
        return True
    hwnd = _wrapper(hwnd)
    style = _GetWindowLong(hwnd, GWL_EXSTYLE)
    if not style:
        return False
    if int(style) & WS_EX_LAYERED:
        return True
    return bool(_SetWindowLong(hwnd, GWL_EXSTYLE, int(style) | WS_EX_LAYERED))


def set_tool_window(hwnd: int, enabled: bool = True) -> bool:
    """标记为工具窗口：不进任务栏、不出现在 Alt+Tab。

    角标是常驻的桌面挂件，不该在任务栏占一格；设置窗口才是「真正的窗口」。
    """
    hwnd = _wrapper(hwnd)
    style = _GetWindowLong(hwnd, GWL_EXSTYLE)
    if not style:
        return False
    if enabled:
        new_style = (int(style) | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
    else:
        new_style = int(style) & ~WS_EX_TOOLWINDOW
    return bool(_SetWindowLong(hwnd, GWL_EXSTYLE, new_style))


def update_layered_window(hwnd: int, x: int, y: int, width: int, height: int, bgra: bytes) -> bool:
    """用一张预乘 BGRA 位图刷新分层窗口（真正的逐像素 alpha）。

    这是替代「颜色键透明」的关键：颜色键只能整像素全透或全不透，
    文字抗锯齿的边缘像素会变成黑边；逐像素 alpha 才能让边缘真正半透明。

    ``bgra`` 必须是 width*height*4 字节，且已按 alpha 预乘。
    """
    hwnd = _wrapper(hwnd)
    screen = user32.GetDC(None)
    if not screen:
        return False
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = None
    previous = None
    try:
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height  # 自上而下
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0  # BI_RGB

        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(memory, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
        if not bitmap or not bits:
            return False
        ctypes.memmove(bits, bgra, len(bgra))
        previous = gdi32.SelectObject(memory, bitmap)

        size = w.SIZE(width, height)
        dst = POINT(x, y)
        src = POINT(0, 0)
        blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER / AC_SRC_ALPHA
        ok = user32.UpdateLayeredWindow(
            w.HWND(hwnd), screen, ctypes.byref(dst), ctypes.byref(size),
            memory, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA,
        )
        return bool(ok)
    finally:
        if previous:
            gdi32.SelectObject(memory, previous)
        if bitmap:
            gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)


def _wrapper(hwnd: int) -> int:
    """Tk 的 toplevel 外面还有一层包装窗口，样式要设在包装窗口上。"""
    parent = user32.GetParent(w.HWND(hwnd))
    return int(parent or hwnd)


def hide_console_window() -> bool:
    """隐藏本进程独占的控制台窗口（用 python.exe 启动时任务栏会多一个命令行图标）。

    只在「控制台是本次启动新建的」时候动手：从 cmd / PowerShell 里手动运行时，
    GetConsoleWindow() 拿到的是用户自己的终端窗口，隐藏它会把用户终端一起藏掉。
    用 GetConsoleProcessList 判断——列表里只有本进程，才说明控制台归我们独占。
    """
    import os

    if os.environ.get("KLCD_DEBUG"):
        return False
    try:
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return False
        pids = (ctypes.c_uint32 * 4)()
        count = kernel32.GetConsoleProcessList(pids, 4)
        if count != 1:  # 还有别的进程共用这个控制台 → 是用户的终端，别碰
            return False
        user32.ShowWindow(hwnd, SW_HIDE)
        return True
    except (AttributeError, OSError):
        return False


def set_window_icon(hwnd: int, icon_path: str) -> bool:
    """把窗口/任务栏图标换成指定 .ico（WM_SETICON）。"""
    try:
        big = user32.LoadImageW(None, icon_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
        small = user32.LoadImageW(None, icon_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
        if not big and not small:
            return False
        if big:
            user32.SendMessageW(w.HWND(hwnd), WM_SETICON, ICON_BIG, big)
        if small:
            user32.SendMessageW(w.HWND(hwnd), WM_SETICON, ICON_SMALL, small)
        return True
    except (AttributeError, OSError):
        return False


# --------------------------------------------------------------------------
# 多显示器
# --------------------------------------------------------------------------


def primary_screen() -> tuple[int, int]:
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def work_area() -> tuple[int, int, int, int]:
    """主屏工作区（不含任务栏）。"""
    rect = w.RECT()
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)  # SPI_GETWORKAREA
    return int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top)


# --------------------------------------------------------------------------
# 单实例
# --------------------------------------------------------------------------


class SingleInstance:
    """基于命名互斥体的单实例；重复启动会静默退出。"""

    def __init__(self, name: str = "Global\\KLineCountdownSingleInstance") -> None:
        self._handle = kernel32.CreateMutexW(None, True, name)
        error = kernel32.GetLastError()
        self.already_running = bool(self._handle) and error == 183  # ERROR_ALREADY_EXISTS

    def release(self) -> None:
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None


# --------------------------------------------------------------------------
# 托盘图标
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TrayMenuItem:
    id: int | None  # None 表示分隔符
    label: str
    checked: bool | None = None
    enabled: bool = True
    children: tuple["TrayMenuItem", ...] = ()  # 非空则渲染成子菜单（折叠）


TrayMenuProvider = Callable[[], list[TrayMenuItem]]


class TrayIcon:
    """在独立线程持有消息循环的系统托盘图标。

    - 左键双击 / 单击 -> ``on_activate``（显示/隐藏角标）
    - 右键 -> 由 ``menu_provider`` 提供菜单，选中的命令放入 ``commands`` 队列
    """

    def __init__(
        self,
        tooltip: str,
        icon_path: str,
        menu_provider: TrayMenuProvider,
        on_activate: Callable[[], None],
        icon_visible: bool = True,
        hotkeys: tuple[tuple[str, int, int], ...] = (),
    ) -> None:
        self.commands: "queue.Queue[tuple[str, int | None]]" = queue.Queue()
        self._menu_provider = menu_provider
        self._on_activate = on_activate
        self._tooltip = tooltip
        self._icon_path = icon_path
        self._hmenu: list[TrayMenuItem] = []
        self._cmd_base = 1
        self._hwnd = None
        self._hicon = None
        self._added = False
        self._closed = False
        self._thread: threading.Thread | None = None
        self._wndproc_ref = WNDPROC(self._wndproc)
        # 全局热键：(名称, 修饰符, 虚拟键码)。用于「鼠标穿透」等无法点击时的逃生通道。
        self._hotkeys = hotkeys
        self._hotkey_names: dict[int, str] = {}
        if icon_visible:
            self.start()

    # ---- 生命周期 ----

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="kcd-tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._closed = True
        try:
            if self._hwnd and self._added:
                self._added = False
                data = NOTIFYICONDATAW(cbSize=ctypes.sizeof(NOTIFYICONDATAW), hWnd=self._hwnd, uID=1)
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
            if self._hwnd:
                user32.PostMessageW(self._hwnd, 0x0012, 0, 0)  # WM_QUIT
        except Exception:  # noqa: BLE001
            pass

    def set_tooltip(self, text: str) -> None:
        self._tooltip = text[:126]
        if self._hwnd and self._added:
            data = NOTIFYICONDATAW(
                cbSize=ctypes.sizeof(NOTIFYICONDATAW),
                hWnd=self._hwnd,
                uID=1,
                uFlags=NIF_TIP | NIF_MESSAGE,
                uCallbackMessage=WM_TRAYICON,
                szTip=self._tooltip,
            )
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))

    def show_balloon(self, title: str, text: str, warning: bool = False) -> None:
        """托盘气泡提示（用于「鼠标穿透已开启」这类需要被看到的一次性提醒）。"""
        if not (self._hwnd and self._added):
            return
        data = NOTIFYICONDATAW(
            cbSize=ctypes.sizeof(NOTIFYICONDATAW),
            hWnd=self._hwnd,
            uID=1,
            uFlags=NIF_INFO,
            szInfo=text[:255],
            szInfoTitle=title[:63],
            dwInfoFlags=NIIF_WARNING if warning else NIIF_INFO,
        )
        try:
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(data))
        except Exception:  # noqa: BLE001
            pass

    # ---- 线程与窗口过程 ----

    def _run(self) -> None:
        try:
            hInstance = kernel32.GetModuleHandleW(None)
            class_name = "KCDTrayMessageWindow"
            wndclass = WNDCLASSW()
            wndclass.lpfnWndProc = self._wndproc_ref
            wndclass.hInstance = hInstance
            wndclass.lpszClassName = class_name
            if not user32.RegisterClassW(ctypes.byref(wndclass)):
                # 已注册过（重复启动）也可继续
                error = kernel32.GetLastError()
                if error != 1410:  # ERROR_CLASS_ALREADY_EXISTS
                    self.commands.put(("error", 0))
                    return
            self._hwnd = user32.CreateWindowExW(
                0, class_name, "KCD", 0, 0, 0, 0, 0, HWND_MESSAGE, None, hInstance, None
            )
            if not self._hwnd:
                self.commands.put(("error", 1))
                return
            self._icon_load()
            if not self._add_icon():
                self.commands.put(("error", 2))
                return
            self._register_hotkeys()
            msg = MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            self._unregister_hotkeys()
            if self._hicon:
                user32.DestroyIcon(w.HICON(self._hicon))
                self._hicon = None

    # ---- 全局热键 ----

    def _register_hotkeys(self) -> None:
        if not self._hotkeys or not self._hwnd:
            return
        for index, (name, modifiers, vk) in enumerate(self._hotkeys):
            hotkey_id = 0xB000 + index
            if user32.RegisterHotKey(self._hwnd, hotkey_id, modifiers | MOD_NOREPEAT, vk):
                self._hotkey_names[hotkey_id] = name
            else:
                self.commands.put(("hotkey_failed", 0))

    def _unregister_hotkeys(self) -> None:
        if not self._hwnd:
            return
        for hotkey_id in list(self._hotkey_names):
            try:
                user32.UnregisterHotKey(self._hwnd, hotkey_id)
            except Exception:  # noqa: BLE001
                pass
        self._hotkey_names.clear()

    def _wndproc(self, hwnd, message, wparam, lparam):  # noqa: ANN001
        if message == WM_HOTKEY:
            name = self._hotkey_names.get(int(wparam))
            if name:
                self.commands.put(("hotkey", name))  # type: ignore[arg-type]
            return 0
        if message == WM_TRAYICON:
            if lparam in (WM_RBUTTONUP, 0x0203):  # 右键 / 双击右键
                self._show_menu(hwnd)
            elif lparam in (WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                self.commands.put(("activate", None))
            return 0
        if message == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _icon_load(self) -> None:
        hicon = user32.LoadImageW(
            None, self._icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE
        )
        if not hicon:
            hicon = user32.LoadIconW(None, 32512)  # IDI_APPLICATION
        self._hicon = hicon

    def _add_icon(self) -> bool:
        data = NOTIFYICONDATAW(
            cbSize=ctypes.sizeof(NOTIFYICONDATAW),
            hWnd=self._hwnd,
            uID=1,
            uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP,
            uCallbackMessage=WM_TRAYICON,
            hIcon=self._hicon,
            szTip=self._tooltip,
        )
        if shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data)):
            data.uVersion = 0
            shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(data))
            self._added = True
            return True
        return False

    def _show_menu(self, hwnd: int) -> None:
        try:
            items = list(self._menu_provider() or [])
        except Exception:  # noqa: BLE001
            items = []
        if not items:
            return
        hmenu = user32.CreatePopupMenu()
        if not hmenu:
            return
        flags_map: dict[int, int] = {}
        submenus: list[int] = []
        counter = [self._cmd_base]

        def fill(menu, children) -> None:  # noqa: ANN001
            for item in children:
                # 先判断子菜单：容器项本身不带命令 id
                if item.children:
                    sub = user32.CreatePopupMenu()
                    if not sub:
                        continue
                    fill(sub, item.children)
                    user32.AppendMenuW(menu, MF_POPUP, sub, item.label)
                    submenus.append(sub)
                    continue
                if item.id is None:
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    continue
                counter[0] += 1
                command_id = counter[0]
                flags = 0
                if item.checked:
                    flags |= MF_CHECKED
                if not item.enabled:
                    flags |= MF_GRAYED
                user32.AppendMenuW(menu, flags, command_id, item.label)
                flags_map[command_id] = item.id

        try:
            fill(hmenu, items)
            point = POINT()
            user32.GetCursorPos(ctypes.byref(point))
            user32.SetForegroundWindow(hwnd)
            command_id = user32.TrackPopupMenu(
                hmenu, TPM_LEFTALIGN | TPM_RIGHTBUTTON | TPM_RETURNCMD, point.x, point.y, 0, hwnd, None
            )
            user32.PostMessageW(hwnd, WM_NULL, 0, 0)
            if command_id:
                self.commands.put(("command", int(flags_map.get(command_id, command_id))))
        finally:
            for sub in submenus:
                user32.DestroyMenu(sub)
            user32.DestroyMenu(hmenu)
