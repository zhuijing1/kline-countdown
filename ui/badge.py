"""桌面角标窗口：无边框、置顶、逐像素透明、可鼠标穿透。

渲染方式
--------
窗口仍然是 Tk 的（这样拖动、右键、双击都不用另写一套事件循环），
但**画面不走 Tk 画布**——Tk 只有「颜色键透明」，文字抗锯齿的边缘会被涂成黑边。
这里把窗口设成 ``WS_EX_LAYERED``，用 :func:`core.winapi.update_layered_window`
把一张预乘 BGRA 位图推给系统，得到真正的逐像素 alpha：

* 透明背景 = 数字以外全透明，边缘自然过渡，不需要描边；
* 有背景 = 圆角矩形 + 数字，整体透明度直接烘进 alpha 通道；
* 命中测试由 Windows 按 alpha 做，alpha=0 的区域自动鼠标穿透。

倒计时数字由引擎每 ~100ms 重算后传入，窗口自身不累积误差；
闪烁相位由 :func:`core.reminders.flash_phase` 推导。
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from typing import Callable

from core.models import FLASH_PULSE, Appearance
from core.textrender import GdiTextRenderer, render_badge
from core.winapi import (
    ensure_layered,
    primary_screen,
    set_click_through,
    set_tool_window,
    update_layered_window,
    work_area,
)


def _luminance(hex_color: str) -> float:
    color = hex_color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    if len(color) < 6:
        return 0.0
    try:
        r, g, b = (int(color[i: i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 0.0
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def readable_on(hex_color: str) -> str:
    """在给定背景上选可读的前景色。"""
    return "#111111" if _luminance(hex_color) > 0.45 else "#FFFFFF"


@dataclass(frozen=True)
class BadgeVisual:
    """一次刷新的完整外观状态（由 App 计算后交给角标渲染）。"""

    text: str
    fg: str
    bg: str
    transparent: bool = False
    flash_on: bool = False
    flash_color: str = "#FF0000"
    flash_style: str = "toggle"


class BadgeWindow:
    """角标本体。host 为 Tk 根窗口（无边框 toplevel），只负责输入，不负责绘制。"""

    def __init__(
        self,
        host: tk.Tk,
        appearance: Appearance,
        on_right_click: Callable[[int, int], None] | None = None,
        on_moved: Callable[[int, int], None] | None = None,
        on_double_click: Callable[[], None] | None = None,
    ) -> None:
        self.host = host
        self.appearance = appearance
        self.on_right_click = on_right_click
        self.on_moved = on_moved
        self.on_double_click = on_double_click

        self._drag_offset: tuple[int, int] | None = None
        self._visual: BadgeVisual | None = None
        self._size: tuple[int, int] = (140, 52)
        self._buffer: bytes | None = None
        self._buffer_key: tuple | None = None
        self._renderer = GdiTextRenderer()

        host.withdraw()
        host.title("K线倒计时角标")
        host.overrideredirect(True)
        host.configure(bg="#000000")

        # 画布只是输入面板，画面由分层窗口直接输出
        self.canvas = tk.Canvas(host, highlightthickness=0, borderwidth=0, bg="#000000")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        for sequence, handler in (
            ("<Button-1>", self._on_press),
            ("<B1-Motion>", self._on_motion),
            ("<ButtonRelease-1>", self._on_release),
            ("<Button-3>", self._on_right_click),
            ("<ButtonRelease-3>", lambda e: None),
            ("<Double-Button-1>", self._on_double_click),
        ):
            self.canvas.bind(sequence, handler)

        self.apply_appearance(appearance)

    # ---- 外观 ----

    def apply_appearance(self, appearance: Appearance) -> None:
        """应用外观。

        不再设置 ``-alpha`` / ``-transparentcolor``：那两个 API 会把窗口切回
        「颜色键」模式，覆盖掉我们推送的逐像素 alpha。透明度改为烘进位图。
        """
        self.appearance = appearance
        self.host.update_idletasks()
        hwnd = self.host.winfo_id()
        set_click_through(hwnd, bool(appearance.click_through))

        self.host.wm_attributes("-topmost", bool(appearance.always_on_top))
        # UpdateLayeredWindow 要求窗口带 WS_EX_LAYERED
        ensure_layered(hwnd, True)
        # 角标是桌面挂件：不进任务栏、不出现在 Alt+Tab
        set_tool_window(hwnd, True)

        self._buffer_key = None  # 外观变了，强制重画
        self._redraw()

    def _resolve_family(self, family: str) -> str:
        """字体缺失时按优先级回退，避免落到系统默认的丑字体。

        ``@宋体`` 这类以 ``@`` 开头的是竖排变体，GDI 会把文字转 90°，统一去掉前缀。
        """
        if family.startswith("@"):
            family = family[1:]
        try:
            available = set(tkfont.families(self.host))
        except tk.TclError:
            return family
        for candidate in (
            family,
            "Bahnschrift",
            "Segoe UI Variable Display",
            "Segoe UI",
            "Microsoft YaHei UI",
            "Consolas",
            "Arial",
        ):
            if candidate and candidate in available:
                return candidate
        return family

    # ---- 渲染 ----

    def render(self, visual: BadgeVisual) -> None:
        self._visual = visual
        self._redraw()

    def _colors(self, visual: BadgeVisual) -> tuple[str, str]:
        """按闪烁方式算出 (文字色, 底色)。"""
        fg, bg = visual.fg, visual.bg
        if visual.flash_on:
            if visual.flash_style == FLASH_PULSE:
                fg = visual.flash_color  # 背景不动，只换文字颜色
            else:
                bg = visual.flash_color
                fg = readable_on(visual.flash_color)
        return fg, bg

    def _redraw(self) -> None:
        visual = self._visual or BadgeVisual(text="00:00", fg="#FFFFFF", bg="#1B1B1F")
        appearance = self.appearance
        fg, bg = self._colors(visual)
        family = self._resolve_family(appearance.font_family)
        transparent = bool(visual.transparent)
        radius = 0 if transparent else max(0, int(appearance.corner_radius))

        key = (
            visual.text, fg, bg, transparent, radius, round(float(appearance.opacity), 3),
            int(appearance.padding_x), int(appearance.padding_y),
            int(appearance.width), int(appearance.height),
            family, int(appearance.font_size), str(appearance.font_weight),
        )
        if key != self._buffer_key or self._buffer is None:
            width, height, data = render_badge(
                visual.text,
                font_family=family,
                font_size=int(appearance.font_size),
                font_weight=str(appearance.font_weight),
                fg=fg,
                bg=bg,
                transparent=transparent,
                radius=radius,
                opacity=float(appearance.opacity),
                padding_x=int(appearance.padding_x),
                padding_y=int(appearance.padding_y),
                width=int(appearance.width),
                height=int(appearance.height),
                renderer=self._renderer,
            )
            self._buffer = data
            self._buffer_key = key
            self._size = (width, height)
            self._place(width, height)

        self._push()

    def _push(self) -> None:
        """把当前位图推给分层窗口（同时完成移动/缩放）。"""
        if self._buffer is None:
            return
        width, height = self._size
        update_layered_window(
            self.host.winfo_id(),
            int(self.appearance.x),
            int(self.appearance.y),
            width,
            height,
            self._buffer,
        )

    # ---- 位置与交互 ----

    def _screen_box(self) -> tuple[int, int, int, int]:
        """Tk 坐标系下的虚拟桌面 (x, y, w, h)。

        注意：不要直接用 Win32 的 GetSystemMetrics——在 DPI 虚拟化
        （进程非 DPI 感知，或缩放比例不同）时，Tk 的坐标空间与物理像素
        不一致，混用会把窗口放到屏幕外。
        """
        try:
            return (
                int(self.host.winfo_vrootx()),
                int(self.host.winfo_vrooty()),
                int(self.host.winfo_vrootwidth()),
                int(self.host.winfo_vrootheight()),
            )
        except tk.TclError:
            return 0, 0, int(self.host.winfo_screenwidth()), int(self.host.winfo_screenheight())

    def _dpi_scale(self) -> float:
        """物理像素 → Tk 坐标的换算系数（正常 DPI 感知进程为 1.0）。"""
        try:
            phys_w = int(primary_screen()[0])
            tk_w = int(self.host.winfo_screenwidth())
        except Exception:  # noqa: BLE001
            return 1.0
        if phys_w <= 0 or tk_w <= 0:
            return 1.0
        return tk_w / phys_w

    def _clamp(self, x: int, y: int, width: int, height: int) -> tuple[int, int]:
        vx, vy, vw, vh = self._screen_box()
        margin = 4
        x = max(vx + margin, min(int(x), vx + vw - width - margin))
        y = max(vy + margin, min(int(y), vy + vh - height - margin))
        return x, y

    def _default_bottom_right(self, width: int, height: int) -> tuple[int, int]:
        """任务栏上方、主屏右下角（换算到 Tk 坐标）。"""
        scale = self._dpi_scale()
        try:
            wx, wy, ww, wh = work_area()  # 物理像素
            right = (wx + ww) * scale
            bottom = (wy + wh) * scale
        except Exception:  # noqa: BLE001
            vx, vy, vw, vh = self._screen_box()
            right = vx + vw
            bottom = vy + vh - 48 * scale
        return int(right - width - 24), int(bottom - height - 24)

    def _place(self, width: int, height: int) -> None:
        x, y = self.appearance.x, self.appearance.y
        if x < 0 or y < 0:
            x, y = self._default_bottom_right(width, height)
        x, y = self._clamp(x, y, width, height)
        self.appearance.x = x
        self.appearance.y = y
        self.host.geometry(f"{width}x{height}+{x}+{y}")

    def move_to(self, x: int, y: int) -> None:
        width, height = self._size
        x, y = self._clamp(x, y, width, height)
        self.appearance.x = x
        self.appearance.y = y
        self.host.geometry(f"+{x}+{y}")
        self._push()

    def show(self) -> None:
        self.host.deiconify()
        self.host.lift()
        self.host.update_idletasks()
        self._push()

    def hide(self) -> None:
        self.host.withdraw()

    @property
    def visible(self) -> bool:
        return bool(self.host.winfo_ismapped())

    # ---- 鼠标事件 ----

    def _on_press(self, event) -> None:  # noqa: ANN001
        self._drag_offset = (event.x_root - self.appearance.x, event.y_root - self.appearance.y)

    def _on_motion(self, event) -> None:  # noqa: ANN001
        if not self._drag_offset or self.appearance.locked:
            return
        dx, dy = self._drag_offset
        self.move_to(event.x_root - dx, event.y_root - dy)

    def _on_release(self, event) -> None:  # noqa: ANN001
        if self._drag_offset and not self.appearance.locked and self.on_moved:
            self._drag_offset = None
            self.on_moved(self.appearance.x, self.appearance.y)

    def _on_right_click(self, event) -> None:  # noqa: ANN001
        if self.on_right_click:
            self.on_right_click(event.x_root, event.y_root)

    def _on_double_click(self, event) -> None:  # noqa: ANN001
        if self.on_double_click:
            self.on_double_click()
