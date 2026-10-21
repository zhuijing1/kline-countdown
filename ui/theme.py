"""设置界面的通用小控件：颜色选择按钮、数字输入、颜色块预览。"""

from __future__ import annotations

import tkinter as tk
from tkinter import colorchooser, ttk


class ColorButton(ttk.Frame):
    """显示当前颜色的按钮，点击弹出系统取色器。"""

    def __init__(self, master, color: str, on_change, label: str = "", width: int = 6) -> None:
        super().__init__(master)
        self._on_change = on_change
        self._color = color
        self._label_widget = None
        self._enabled = True
        if label:
            self._label_widget = ttk.Label(self, text=label)
            self._label_widget.pack(side=tk.LEFT, padx=(0, 4))
        self.swatch = tk.Canvas(self, width=26, height=18, highlightthickness=1, highlightbackground="#888")
        self.swatch.pack(side=tk.LEFT)
        self.swatch.create_rectangle(1, 1, 25, 17, fill=color, outline="")
        self.swatch.bind("<Button-1>", self._pick)
        self._set_color(color)

    def _set_color(self, color: str) -> None:
        self._color = color
        try:
            self.swatch.itemconfigure(1, fill=color)
        except tk.TclError:
            self.swatch.delete("all")
            self.swatch.create_rectangle(1, 1, 25, 17, fill=color, outline="")
        self.swatch.configure(background=color)

    def _pick(self, _event) -> None:  # noqa: ANN001
        if not getattr(self, "_enabled", True):
            return
        result = colorchooser.askcolor(initialcolor=self._color, title="选择颜色")
        if result and result[1]:
            self._set_color(result[1])
            if self._on_change:
                self._on_change(result[1])

    @property
    def color(self) -> str:
        return self._color

    def set_enabled(self, enabled: bool) -> None:
        """置灰/恢复：不可用时点不出取色器，标签也调淡。"""
        self._enabled = bool(enabled)
        try:
            self.swatch.configure(cursor="hand2" if enabled else "arrow")
        except tk.TclError:
            pass
        if self._label_widget is not None:
            self._label_widget.configure(foreground="" if enabled else "#999")


class SpinBox(ttk.Frame):
    """带标签的数字输入框：label + Spinbox + 单位。"""

    def __init__(
        self,
        master,
        label: str,
        from_: int,
        to: int,
        value,
        on_change=None,
        unit: str = "",
        width: int = 8,
    ) -> None:
        super().__init__(master)
        ttk.Label(self, text=label).pack(side=tk.LEFT, padx=(0, 4))
        self.var = tk.StringVar(value=str(value))
        self.spin = ttk.Spinbox(
            self, from_=from_, to=to, textvariable=self.var, width=width,
            command=self._emit,
        )
        self.spin.pack(side=tk.LEFT, padx=(0, 4))
        self.spin.bind("<FocusOut>", lambda e: self._emit())
        self.spin.bind("<Return>", lambda e: self._emit())
        self.spin.bind("<MouseWheel>", lambda e: self._emit())
        if unit:
            ttk.Label(self, text=unit).pack(side=tk.LEFT)
        self._on_change = on_change

    def _emit(self) -> None:
        if self._on_change:
            try:
                self._on_change(int(float(self.var.get())))
            except (TypeError, ValueError):
                pass

    @property
    def value(self) -> int:
        try:
            return int(float(self.var.get()))
        except (TypeError, ValueError):
            return 0

    def set_value(self, value) -> None:
        self.var.set(str(value))


class ScaleRow(ttk.Frame):
    """带标签与百分比显示的滑杆。"""

    def __init__(self, master, label: str, from_: float, to: float, value: float, on_change=None, resolution: float = 0.05) -> None:
        super().__init__(master)
        ttk.Label(self, text=label, width=12, anchor="w").pack(side=tk.LEFT)
        self.scale = ttk.Scale(
            self, from_=from_, to=to, value=value, length=140,
            command=lambda _v: self._emit(),
        )
        self.scale.pack(side=tk.LEFT, padx=(0, 6))
        self.value_label = ttk.Label(self, text=f"{value:.2f}", width=6, anchor="w")
        self.value_label.pack(side=tk.LEFT)
        self._on_change = on_change
        self._resolution = resolution

    def _emit(self) -> None:
        value = round(float(self.scale.get()), 2)
        self.value_label.configure(text=f"{value:.2f}")
        if self._on_change:
            self._on_change(value)

    @property
    def value(self) -> float:
        return round(float(self.scale.get()), 2)

    def set_value(self, value: float) -> None:
        self.scale.set(value)
        self.value_label.configure(text=f"{value:.2f}")


def check_row(master, text: str, value: bool, on_change=None) -> ttk.Frame:
    """返回 frame；读选用 frame.var.get()。"""
    frame = ttk.Frame(master)
    var = tk.BooleanVar(value=value)
    ttk.Checkbutton(frame, text=text, variable=var, command=on_change).pack(side=tk.LEFT)
    frame.var = var  # type: ignore[attr-defined]
    return frame


def entry_row(master, label: str, value: str, on_change=None, width: int = 18) -> ttk.Frame:
    """返回 frame；读选用 frame.var.get()。"""
    frame = ttk.Frame(master)
    ttk.Label(frame, text=label, width=12, anchor="w").pack(side=tk.LEFT)
    var = tk.StringVar(value=value)
    entry = ttk.Entry(frame, textvariable=var, width=width)
    entry.pack(side=tk.LEFT)
    entry.bind("<FocusOut>", lambda e: on_change(var.get()) if on_change else None)
    entry.bind("<Return>", lambda e: on_change(var.get()) if on_change else None)
    frame.var = var  # type: ignore[attr-defined]
    return frame


def choose_sound_source(parent, current: str, on_choose) -> None:
    """预设/内建/自定义音源对话框。"""
    from tkinter import filedialog

    presets = [
        ("内置：叮（短促）", "preset:ding"),
        ("内置：嘀（清亮）", "preset:beep"),
        ("内置：嗒（极短）", "preset:tick"),
        ("内置：警报（双响）", "preset:alarm"),
        ("从文件选择……", "__browse__"),
    ]
    dialog = tk.Toplevel(parent)
    dialog.title("选择提示音")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)
    listbox = tk.Listbox(dialog, width=44, height=len(presets) + 2, exportselection=False)
    for label, value in presets:
        listbox.insert(tk.END, label)
    listbox.pack(padx=12, pady=12)
    if current.startswith("file:"):
        listbox.insert(tk.END, f"当前文件：{current[5:]}")

    def commit(index=None):  # noqa: ANN001
        idx = listbox.curselection()[0] if listbox.curselection() else 0
        if idx >= len(presets):
            dialog.destroy()
            return
        _label, value = presets[idx]
        dialog.destroy()
        if value == "__browse__":
            path = filedialog.askopenfilename(
                parent=parent,
                title="选择音频文件",
                filetypes=[("音频", "*.wav *.mp3 *.wma *.aiff"), ("所有文件", "*.*")],
            )
            if path:
                on_choose(f"file:{path}")
        else:
            on_choose(value)

    ttk.Button(dialog, text="确定", command=commit).pack(pady=(0, 12))
    listbox.bind("<Double-Button-1>", lambda e: commit())
    dialog.bind("<Return>", lambda e: commit())
    center_on_screen(dialog)


def center_on_screen(window: tk.Toplevel, width: int | None = None, height: int | None = None) -> None:
    window.update_idletasks()
    width = width or window.winfo_width()
    height = height or window.winfo_height()
    screen_w = window.winfo_screenwidth()
    screen_h = window.winfo_screenheight()
    window.geometry(f"{width}x{height}+{(screen_w - width) // 2}+{(screen_h - height) // 2}")


class IntScaleRow(ttk.Frame):
    """整数滑块：标签 + 滑杆 + 数值。适合字号这类"拖着看效果"的参数。"""

    def __init__(self, master, label: str, from_: int, to: int, value: int, on_change=None, length: int = 220) -> None:
        super().__init__(master)
        if label:
            ttk.Label(self, text=label, width=12, anchor="w").pack(side=tk.LEFT)
        self.scale = ttk.Scale(
            self, from_=from_, to=to, value=value, length=length,
            command=lambda _v: self._emit(),
        )
        self.scale.pack(side=tk.LEFT, padx=(0, 8))
        self.value_label = ttk.Label(self, text=str(int(value)), width=4, anchor="w")
        self.value_label.pack(side=tk.LEFT)
        self._on_change = on_change

    def _emit(self) -> None:
        value = int(round(float(self.scale.get())))
        self.value_label.configure(text=str(value))
        if self._on_change:
            self._on_change(value)

    @property
    def value(self) -> int:
        return int(round(float(self.scale.get())))

    def set_value(self, value: int) -> None:
        self.scale.set(value)
        self.value_label.configure(text=str(int(value)))
