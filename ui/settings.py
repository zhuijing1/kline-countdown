"""设置窗口：交易时段管理 / 外观 / 提醒。

所有修改通过 ``on_change(cfg)`` 立刻外发，由 App 落盘并应用；
窗口只是配置的编辑器，不持有业务逻辑。"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from core.engine import validate_profile
from core.models import (
    ALIGNMENT_LABELS,
    ALIGNMENT_SESSION,
    FLASH_STYLES,
    FLASH_STYLE_LABELS,
    PERIOD_PRESETS,
    Appearance,
    AppConfig,
    Profile,
    ReminderRule,
    TradingSession,
    format_hm,
    parse_hm,
    sound_source_label,
)
from .theme import (
    ColorButton,
    IntScaleRow,
    ScaleRow,
    SpinBox,
    center_on_screen,
    check_row,
    choose_sound_source,
    entry_row,
)

TABS = ("profiles", "appearance", "reminders")


def edit_session_dialog(parent, session: TradingSession | None = None) -> TradingSession | None:
    """新增/编辑单个交易时段的模态对话框。返回 None 表示取消。"""
    session = session or TradingSession(570, 690)
    dialog = tk.Toplevel(parent)
    dialog.title("交易时段")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)

    result: list[TradingSession | None] = [None]

    ttk.Label(dialog, text="开始时间（HH:MM）").pack(anchor="w", padx=16, pady=(16, 2))
    start_var = tk.StringVar(value=format_hm(session.start_minute))
    ttk.Entry(dialog, textvariable=start_var, width=14).pack(anchor="w", padx=16)

    ttk.Label(dialog, text="结束时间（HH:MM，24:00 表示午夜）").pack(anchor="w", padx=16, pady=(12, 2))
    end_var = tk.StringVar(value=format_hm(session.end_minute))
    ttk.Entry(dialog, textvariable=end_var, width=14).pack(anchor="w", padx=16)

    enabled = check_row(dialog, "启用该时段", session.enabled)
    enabled.pack(anchor="w", padx=16, pady=(12, 4))

    error = ttk.Label(dialog, text="", foreground="#C0392B")
    error.pack(anchor="w", padx=16)

    buttons = ttk.Frame(dialog)
    buttons.pack(fill=tk.X, padx=16, pady=12)

    def commit() -> None:
        try:
            start = parse_hm(start_var.get())
            end = parse_hm(end_var.get())
            if start >= end:
                raise ValueError("开始时间必须早于结束时间")
        except ValueError as exc:
            error.configure(text=str(exc))
            return
        result[0] = TradingSession(start, end, enabled.var.get())  # type: ignore[attr-defined]
        dialog.destroy()

    ttk.Button(buttons, text="确定", command=commit).pack(side=tk.RIGHT, padx=4)
    ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
    dialog.bind("<Return>", lambda e: commit())
    center_on_screen(dialog, 300, 260)
    parent.wait_window(dialog)
    return result[0]


class ProfilesTab(ttk.Frame):
    """交易时段配置管理：多配置、多时段、周期、对齐、星期。"""

    def __init__(self, master, cfg: AppConfig, on_change: Callable[[AppConfig], None], on_active: Callable[[Profile], None]) -> None:
        super().__init__(master)
        self.cfg = cfg
        self.on_change = on_change
        self.on_active = on_active
        self._loading = False

        ttk.Label(self, text="交易时段配置", font=(None, 11, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        body.columnconfigure(0, weight=0, minsize=196)  # 左栏固定窄，但要放得下最长的配置名
        body.columnconfigure(1, weight=1)               # 右侧详情占满剩余
        body.rowconfigure(0, weight=1)

        # ---- 左：配置列表（按钮竖排，节省横向空间） ----
        left = ttk.Labelframe(body, text=" 配置列表 ")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        list_wrap = ttk.Frame(left)
        list_wrap.grid(row=0, column=0, sticky="nsew", padx=4, pady=(4, 2))
        list_wrap.columnconfigure(0, weight=1)
        list_wrap.rowconfigure(0, weight=1)
        scroll = ttk.Scrollbar(list_wrap, orient=tk.VERTICAL)
        self.listbox = tk.Listbox(
            list_wrap, yscrollcommand=scroll.set, exportselection=False, width=18, activestyle="dotbox"
        )
        scroll.configure(command=self.listbox.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.listbox.grid(row=0, column=0, sticky="nsew")
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-Button-1>", self._make_active)

        actions = ttk.Frame(left)
        actions.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 6))
        actions.columnconfigure(0, weight=1)
        for row_index, (text, command) in enumerate(
            (
                ("设为当前", self._make_active),
                ("新建配置", self._new_profile),
                ("复制配置", self._copy_profile),
                ("删除配置", self._delete_profile),
            )
        ):
            ttk.Button(actions, text=text, command=command).grid(
                row=row_index, column=0, sticky="ew", pady=2
            )

        # ---- 右：配置编辑 ----
        right = ttk.Labelframe(body, text=" 配置详情 ")
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 6))
        right.columnconfigure(0, weight=1)

        self.name_entry = entry_row(right, "名称", "")
        self.name_entry.var.trace_add("write", lambda *_: self._commit_name())  # type: ignore[attr-defined]
        self.name_entry.pack(fill=tk.X, padx=8, pady=(10, 4))

        period = ttk.Labelframe(right, text=" K 线周期 ")
        period.pack(fill=tk.X, padx=8, pady=6)
        row = ttk.Frame(period)
        row.pack(fill=tk.X, padx=8, pady=6)
        self.period_values = [seconds for _name, seconds in PERIOD_PRESETS]
        self.period_combo = ttk.Combobox(
            row,
            state="readonly",
            width=12,
            values=[name for name, _seconds in PERIOD_PRESETS] + ["自定义"],
        )
        self.period_combo.pack(side=tk.LEFT, padx=(0, 8))
        self.period_combo.bind("<<ComboboxSelected>>", self._on_period_select)
        ttk.Label(row, text="自定义：").pack(side=tk.LEFT)
        self.custom_min = SpinBox(row, "分", 0, 1440, 0, self._on_period_custom, width=5)
        self.custom_min.pack(side=tk.LEFT, padx=2)
        self.custom_sec = SpinBox(row, "秒", 0, 59, 0, self._on_period_custom, width=5)
        self.custom_sec.pack(side=tk.LEFT, padx=2)

        align = ttk.Frame(period)
        align.pack(fill=tk.X, padx=8, pady=(0, 6))
        ttk.Label(align, text="对齐规则：").pack(side=tk.LEFT)
        self.align_combo = ttk.Combobox(row := align, state="readonly", width=20, values=list(ALIGNMENT_LABELS.values()))
        self.align_combo.pack(side=tk.LEFT)
        self.align_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_align())

        # 说明文字占满右侧整行再折行（跟窗口一起伸缩）
        self.align_hint = ttk.Label(
            right,
            text="「交易时段起点对齐」与国内多数行情软件一致（60 分钟线为 09:30/10:30/11:30）；"
                 "「整点时钟对齐」按 00:00/00:15/00:30 切分，适合连续交易市场。",
            wraplength=620,
            foreground="#666",
        )
        self.align_hint.pack(anchor="w", fill=tk.X, padx=10, pady=(0, 4))
        right.bind("<Configure>", lambda e: self.align_hint.configure(wraplength=max(320, e.width - 40)))

        # 交易日（星期）
        week = ttk.Labelframe(right, text=" 适用星期 ")
        week.pack(fill=tk.X, padx=8, pady=6)
        self.week_vars: list[tk.BooleanVar] = []
        self.week_row = ttk.Frame(week)
        self.week_row.pack(fill=tk.X, padx=6, pady=6)
        for day in range(7):
            var = tk.BooleanVar(value=day < 5)
            box = ttk.Checkbutton(self.week_row, text="一二三四五六日"[day], variable=var, command=self._commit_weekdays)
            box.pack(side=tk.LEFT)
            self.week_vars.append(var)

        # 时段表
        table = ttk.Labelframe(right, text=" 交易时段 ")
        table.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        columns = ("start", "end", "enabled")
        self.session_tree = ttk.Treeview(table, columns=columns, show="headings", height=4)
        self.session_tree.heading("start", text="开始")
        self.session_tree.heading("end", text="结束")
        self.session_tree.heading("enabled", text="启用")
        self.session_tree.column("start", width=90, anchor="center")
        self.session_tree.column("end", width=90, anchor="center")
        self.session_tree.column("enabled", width=60, anchor="center")
        self.session_tree.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.session_tree.bind("<Double-Button-1>", lambda _e: self._edit_session())
        buttons = ttk.Frame(table)
        buttons.pack(fill=tk.X, padx=6, pady=(0, 6))
        ttk.Button(buttons, text="添加", command=self._add_session).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text="编辑", command=self._edit_session).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text="删除", command=self._delete_session).pack(side=tk.LEFT, padx=2)

        self.validation = ttk.Label(right, text="", wraplength=320)
        self.validation.pack(anchor="w", padx=10, pady=(4, 8))

        self.refresh_list()

    # ---- 列表刷新 ----

    def refresh_list(self) -> None:
        self.listbox.delete(0, tk.END)
        for profile in self.cfg.profiles:
            marker = "● " if profile.id == self.cfg.active_profile_id else "   "
            self.listbox.insert(tk.END, marker + profile.name)
        self._load_profile(self.cfg.active_profile())

    def _on_select(self, _event) -> None:  # noqa: ANN001
        if self._loading:
            return
        selection = self.listbox.curselection()
        if not selection:
            return
        self._load_profile(self.cfg.profiles[selection[0]])

    def _load_profile(self, profile: Profile) -> None:
        self._loading = True
        try:
            index = self.cfg.profiles.index(profile)
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(index)
            self.name_entry.var.set(profile.name)  # type: ignore[attr-defined]

            preset_seconds = {seconds for _name, seconds in PERIOD_PRESETS}
            if profile.period_seconds in preset_seconds:
                label = next(name for name, seconds in PERIOD_PRESETS if seconds == profile.period_seconds)
                self.period_combo.set(label)
                minutes, seconds = divmod(profile.period_seconds, 60)
            else:
                self.period_combo.set("自定义")
                minutes, seconds = divmod(profile.period_seconds, 60)
            self.custom_min.set_value(minutes)
            self.custom_sec.set_value(seconds)
            self.align_combo.set(ALIGNMENT_LABELS.get(profile.alignment, ALIGNMENT_LABELS[ALIGNMENT_SESSION]))

            for day in range(7):
                self.week_vars[day].set(day in profile.weekdays)

            self.session_tree.delete(*self.session_tree.get_children())
            for position, session in enumerate(profile.sessions):
                self.session_tree.insert(
                    "", tk.END, iid=str(position),
                    values=(format_hm(session.start_minute), format_hm(session.end_minute), "启用" if session.enabled else "停用"),
                )
            self._show_validation(profile)
        finally:
            self._loading = False

    def _show_validation(self, profile: Profile) -> None:
        errors = validate_profile(profile)
        if errors:
            self.validation.configure(text="⚠ " + "；".join(errors), foreground="#C0392B")
        else:
            self.validation.configure(text="✔ 配置有效", foreground="#1E8449")

    # ---- 编辑回调 ----

    def _profile(self) -> Profile | None:
        selection = self.listbox.curselection()
        index = selection[0] if selection else 0
        if not self.cfg.profiles:
            return None
        try:
            return self.cfg.profiles[index]
        except IndexError:
            return self.cfg.profiles[0]

    def _commit_name(self) -> None:
        if self._loading:
            return
        profile = self._profile()
        if not profile:
            return
        name = self.name_entry.var.get().strip()  # type: ignore[attr-defined]
        if not name:
            return
        profile.name = name
        self.refresh_list_keep_selection()
        self.on_change(self.cfg)

    def refresh_list_keep_selection(self) -> None:
        profile = self._profile()
        self.refresh_list()
        if profile:
            index = self.cfg.profiles.index(profile)
            self.listbox.selection_set(index)

    def _on_period_select(self, _event) -> None:  # noqa: ANN001
        if self._loading:
            return
        profile = self._profile()
        if not profile:
            return
        label = self.period_combo.get()
        for name, seconds in PERIOD_PRESETS:
            if name == label:
                profile.period_seconds = seconds
                minutes, secs = divmod(seconds, 60)
                self.custom_min.set_value(minutes)
                self.custom_sec.set_value(secs)
                self.on_change(self.cfg)
                self._show_validation(profile)
                return

    def _on_period_custom(self, _value=None) -> None:  # noqa: ANN001
        if self._loading:
            return
        profile = self._profile()
        if not profile:
            return
        total = self.custom_min.value * 60 + self.custom_sec.value
        if total <= 0:
            return
        self.period_combo.set("自定义")
        profile.period_seconds = total
        self.on_change(self.cfg)
        self._show_validation(profile)

    def _on_align(self) -> None:
        if self._loading:
            return
        profile = self._profile()
        if not profile:
            return
        label = self.align_combo.get()
        for key, text in ALIGNMENT_LABELS.items():
            if text == label:
                profile.alignment = key
                self.on_change(self.cfg)
                self._show_validation(profile)
                return

    def _commit_weekdays(self) -> None:
        if self._loading:
            return
        profile = self._profile()
        if not profile:
            return
        profile.weekdays = [day for day, var in enumerate(self.week_vars) if var.get()]
        self.on_change(self.cfg)
        self._show_validation(profile)

    # ---- 时段表 ----

    def _add_session(self) -> None:
        profile = self._profile()
        if not profile:
            return
        session = edit_session_dialog(self)
        if session:
            profile.sessions.append(session)
            profile.sessions.sort(key=lambda s: s.start_minute)
            self.on_change(self.cfg)
            self.refresh_list_keep_selection()

    def _edit_session(self) -> None:
        profile = self._profile()
        if not profile:
            return
        selection = self.session_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        session = edit_session_dialog(self, profile.sessions[index])
        if session:
            profile.sessions[index] = session
            profile.sessions.sort(key=lambda s: s.start_minute)
            self.on_change(self.cfg)
            self.refresh_list_keep_selection()

    def _delete_session(self) -> None:
        profile = self._profile()
        if not profile:
            return
        selection = self.session_tree.selection()
        if not selection:
            return
        index = int(selection[0])
        del profile.sessions[index]
        self.on_change(self.cfg)
        self.refresh_list_keep_selection()

    # ---- 配置增删 ----

    def _new_profile(self) -> None:
        profile = Profile(
            name="新配置",
            period_seconds=300,
            sessions=[TradingSession(570, 690)],
        )
        self.cfg.profiles.append(profile)
        self.cfg.active_profile_id = profile.id
        self.on_change(self.cfg)
        self.on_active(profile)
        self.refresh_list()

    def _copy_profile(self) -> None:
        profile = self._profile()
        if not profile:
            return
        clone = profile.copy(rename=f"{profile.name} 副本")
        self.cfg.profiles.append(clone)
        self.cfg.active_profile_id = clone.id
        self.on_change(self.cfg)
        self.on_active(clone)
        self.refresh_list()

    def _delete_profile(self) -> None:
        if len(self.cfg.profiles) <= 1:
            messagebox.showinfo("无法删除", "至少需要保留一个交易时段配置。", parent=self)
            return
        profile = self._profile()
        if not profile:
            return
        if not messagebox.askyesno("删除配置", f"确定删除「{profile.name}」？", parent=self):
            return
        self.cfg.profiles.remove(profile)
        if self.cfg.active_profile_id == profile.id:
            self.cfg.active_profile_id = self.cfg.profiles[0].id
            self.on_active(self.cfg.profiles[0])
        self.on_change(self.cfg)
        self.refresh_list()

    def _make_active(self, _event=None) -> None:  # noqa: ANN001
        profile = self._profile()
        if not profile:
            return
        self.cfg.active_profile_id = profile.id
        self.on_change(self.cfg)
        self.on_active(profile)
        self.refresh_list()


class AppearanceTab(ttk.Frame):
    """外观：字体、颜色、尺寸、透明度、定位行为。修改即时生效。"""

    def __init__(self, master, appearance: Appearance, on_change: Callable[[Appearance], None]) -> None:
        super().__init__(master)
        self.appearance = appearance
        self.on_change = on_change
        self._loading = False
        try:
            import tkinter.font as tkfont

            # "@" 开头的是竖排变体（@宋体/@黑体…），选了会把文字转 90°，直接排除
            families = sorted({f for f in tkfont.families() if not f.startswith("@")})
        except Exception:  # noqa: BLE001
            families = ["Consolas", "Arial", "Microsoft YaHei", "Segoe UI"]
        preferred = [f for f in appearance.font_family.split("|") if f in families]
        values = (preferred + families) if preferred else families

        ttk.Label(self, text="角标外观", font=(None, 11, "bold")).pack(anchor="w", padx=8, pady=(8, 4))
        form = ttk.Frame(self)
        form.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

        row = ttk.Frame(form)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="字体", width=12, anchor="w").pack(side=tk.LEFT)
        self.font_combo = ttk.Combobox(row, values=values, width=20)
        self.font_combo.set(appearance.font_family if appearance.font_family in families else "Consolas")
        self.font_combo.bind("<<ComboboxSelected>>", self._on_font)
        self.font_combo.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(row, text="字重").pack(side=tk.LEFT)
        self.weight_combo = ttk.Combobox(row, state="readonly", values=["normal", "bold"], width=8)
        self.weight_combo.set(appearance.font_weight)
        self.weight_combo.bind("<<ComboboxSelected>>", self._on_weight)
        self.weight_combo.pack(side=tk.LEFT)

        row = ttk.Frame(form)
        row.pack(fill=tk.X, pady=(6, 3))
        self.size_scale = IntScaleRow(row, "字号", 10, 96, appearance.font_size, self._on_size, length=260)
        self.size_scale.pack(side=tk.LEFT)
        ttk.Label(row, text="← 拖动即可实时预览").pack(side=tk.LEFT, padx=6)

        row = ttk.Frame(form)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="颜色", width=12, anchor="w").pack(side=tk.LEFT)
        ColorButton(row, appearance.fg_color, self._on_fg, label="数字").pack(side=tk.LEFT, padx=(0, 10))
        self.bg_color_button = ColorButton(row, appearance.bg_color, self._on_bg, label="背景")
        self.bg_color_button.pack(side=tk.LEFT, padx=(0, 14))

        row = ttk.Frame(form)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="背景", width=12, anchor="w").pack(side=tk.LEFT)
        self.transparent_var = tk.BooleanVar(value=bool(appearance.transparent_bg))
        ttk.Checkbutton(
            row, text="透明背景（只显示数字，不画色块）", variable=self.transparent_var,
            command=self._on_transparent,
        ).pack(side=tk.LEFT)

        ttk.Label(
            form,
            text="透明背景时数字边缘是真正的半透明过渡，不需要描边；浅色壁纸上建议把数字换成深色。",
            wraplength=640,
            foreground="#666",
        ).pack(anchor="w", pady=(0, 2))
        self._sync_transparent_widgets()

        row = ttk.Frame(form)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="透明度", width=12, anchor="w").pack(side=tk.LEFT)
        self.opacity_scale = ScaleRow(row, "", 0.1, 1.0, appearance.opacity, self._on_opacity)
        self.opacity_scale.pack(side=tk.LEFT)

        row = ttk.Frame(form)
        row.pack(fill=tk.X, pady=3)
        ttk.Label(row, text="空闲文案", width=12, anchor="w").pack(side=tk.LEFT)
        idle = entry_row(row, "", appearance.idle_text, self._on_idle_text, width=16)
        idle.pack(side=tk.LEFT)
        ttk.Label(row, text="非交易时段显示，颜色与交易时段相同").pack(side=tk.LEFT, padx=8)

        # 布局细节统一收进「高级」，日常只需要拖字号滑块
        advanced = ttk.Labelframe(form, text=" 高级（一般不用改） ")
        advanced.pack(fill=tk.X, pady=6)

        row = ttk.Frame(advanced)
        row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row, text="内边距", width=12, anchor="w").pack(side=tk.LEFT)
        self.pad_x = SpinBox(row, "水平", 0, 80, appearance.padding_x, self._on_padding)
        self.pad_x.pack(side=tk.LEFT, padx=2)
        self.pad_y = SpinBox(row, "垂直", 0, 80, appearance.padding_y, self._on_padding)
        self.pad_y.pack(side=tk.LEFT, padx=2)
        ttk.Label(row, text="数字与色块边缘的距离").pack(side=tk.LEFT, padx=6)

        row = ttk.Frame(advanced)
        row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row, text="固定尺寸", width=12, anchor="w").pack(side=tk.LEFT)
        self.width_spin = SpinBox(row, "宽", 0, 1200, appearance.width, self._on_size_mode_change)
        self.width_spin.pack(side=tk.LEFT, padx=2)
        self.height_spin = SpinBox(row, "高", 0, 800, appearance.height, self._on_size_mode_change)
        self.height_spin.pack(side=tk.LEFT, padx=2)
        ttk.Label(row, text="0 = 按字号自动").pack(side=tk.LEFT, padx=6)

        row = ttk.Frame(advanced)
        row.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(row, text="圆角", width=12, anchor="w").pack(side=tk.LEFT)
        self.radius_spin = SpinBox(row, "半径", 0, 40, appearance.corner_radius, self._on_radius)
        self.radius_spin.pack(side=tk.LEFT, padx=2)
        ttk.Label(row, text="0 = 直角（文字边缘更干净）").pack(side=tk.LEFT, padx=6)

        flags = ttk.Labelframe(form, text=" 窗口行为 ")
        flags.pack(fill=tk.X, pady=6)
        row = ttk.Frame(flags)
        row.pack(fill=tk.X, padx=6, pady=6)
        self._flag_vars: dict[str, tk.BooleanVar] = {}
        for label, getter, field in (
            ("始终置顶", lambda: appearance.always_on_top, "always_on_top"),
            ("锁定位置（禁止拖动）", lambda: appearance.locked, "locked"),
            ("鼠标穿透（点不中）", lambda: appearance.click_through, "click_through"),
        ):
            var = tk.BooleanVar(value=bool(getter()))
            box = ttk.Checkbutton(
                row, text=label, variable=var,
                command=lambda f=field, v=var: self._on_flag(f, v),
            )
            box.pack(side=tk.LEFT, padx=(0, 14))
            self._flag_vars[field] = var

        ttk.Label(
            form,
            text="开启「鼠标穿透」后角标不再响应鼠标；用托盘图标右键菜单，或按 Ctrl+Alt+K 可随时解除。",
            wraplength=520,
            foreground="#666",
        ).pack(anchor="w", pady=(0, 4))

        row = ttk.Frame(form)
        row.pack(fill=tk.X, pady=4)
        ttk.Button(row, text="重置到屏幕右下角", command=self._reset_position).pack(side=tk.LEFT, padx=2)
        ttk.Button(row, text="恢复默认外观", command=self._reset_defaults).pack(side=tk.LEFT, padx=2)

    # ---- 回调 ----

    def _emit(self) -> None:
        if not self._loading:
            self.on_change(self.appearance)

    def _on_font(self, _event) -> None:  # noqa: ANN001
        self.appearance.font_family = self.font_combo.get()
        self._emit()

    def _on_weight(self, _event) -> None:  # noqa: ANN001
        self.appearance.font_weight = self.weight_combo.get()
        self._emit()

    def _on_size(self, value) -> None:  # noqa: ANN001
        self.appearance.font_size = int(value)
        self._emit()

    def _on_fg(self, color) -> None:
        self.appearance.fg_color = color
        self._emit()

    def _on_bg(self, color) -> None:
        self.appearance.bg_color = color
        self._emit()

    def _on_opacity(self, value) -> None:
        self.appearance.opacity = min(1.0, max(0.1, round(value, 2)))
        self._emit()

    def _on_padding(self, _value=None) -> None:  # noqa: ANN001
        self.appearance.padding_x = self.pad_x.value
        self.appearance.padding_y = self.pad_y.value
        self._emit()

    def _on_size_mode_change(self, _event=None) -> None:  # noqa: ANN001
        self.appearance.width = self.width_spin.value
        self.appearance.height = self.height_spin.value
        self._emit()

    def _on_radius(self, value) -> None:  # noqa: ANN001
        self.appearance.corner_radius = int(value)
        self._emit()

    def _on_transparent(self) -> None:
        self.appearance.transparent_bg = bool(self.transparent_var.get())
        self._sync_transparent_widgets()
        self._emit()

    def _sync_transparent_widgets(self) -> None:
        """透明模式下没有底块，背景色用不上，置灰避免误导。"""
        transparent = bool(self.appearance.transparent_bg)
        try:
            self.bg_color_button.set_enabled(not transparent)
        except AttributeError:
            pass

    def _on_idle_text(self, text) -> None:
        self.appearance.idle_text = text
        self._emit()

    def _on_flag(self, field: str, var: tk.BooleanVar) -> None:
        """窗口行为复选框。开启鼠标穿透前必须确认——否则用户会点不中角标。"""
        if self._loading:
            return
        value = bool(var.get())
        if field == "click_through" and value:
            confirmed = messagebox.askyesno(
                "开启鼠标穿透",
                "开启后角标将不再响应鼠标点击（包括右键菜单）。\n\n"
                "解除方式（任选其一）：\n"
                "  1. 右键托盘图标 → 鼠标穿透\n"
                "  2. 按 Ctrl+Alt+K\n"
                "  3. 退出程序后修改配置文件\n\n"
                "确定开启吗？",
                parent=self,
            )
            if not confirmed:
                var.set(False)
                return
        setattr(self.appearance, field, value)
        self._emit()

    def _reset_position(self) -> None:
        self.appearance.x = -1
        self.appearance.y = -1
        self._emit()

    def _reset_defaults(self) -> None:
        if not messagebox.askyesno("恢复默认", "确定恢复默认外观设置？", parent=self):
            return
        fresh = Appearance()
        self.appearance.__dict__.update(fresh.__dict__)
        self.refresh_widgets()
        self._emit()

    def refresh_widgets(self) -> None:
        self._loading = True
        try:
            self.size_scale.set_value(self.appearance.font_size)
            self.opacity_scale.set_value(self.appearance.opacity)
            self.pad_x.set_value(self.appearance.padding_x)
            self.pad_y.set_value(self.appearance.padding_y)
            self.width_spin.set_value(self.appearance.width)
            self.height_spin.set_value(self.appearance.height)
            self.radius_spin.set_value(self.appearance.corner_radius)
            self.weight_combo.set(self.appearance.font_weight)
            self.transparent_var.set(bool(self.appearance.transparent_bg))
            self._sync_transparent_widgets()
            for field, var in getattr(self, "_flag_vars", {}).items():
                var.set(bool(getattr(self.appearance, field, False)))
        finally:
            self._loading = False


class ReminderTab(ttk.Frame):
    """提醒规则：多个节点、颜色/闪烁/声音，全局静音，周期结束提示音。"""

    def __init__(
        self,
        master,
        profile: Profile,
        global_mute: bool,
        on_change: Callable[[Profile], None],
        on_global_mute: Callable[[bool], None],
        on_preview: Callable[[str, float, int, float], None],
    ) -> None:
        super().__init__(master)
        self.profile = profile
        self.on_change = on_change
        self.on_global_mute = on_global_mute
        self.on_preview = on_preview

        ttk.Label(self, text="提醒节点（每根 K 线各自触发一次）", font=(None, 11, "bold")).pack(anchor="w", padx=8, pady=(8, 4))

        self._mute_box = check_row(self, "一键关闭所有声音与闪烁提醒", global_mute, self._on_mute)
        self._mute_box.pack(anchor="w", padx=12, pady=(0, 4))
        ttk.Label(
            self,
            text="规则按剩余秒数由宽到严触发；同一根 K 线的同一节点只响一次，进入下一根 K 线后重置。",
            wraplength=520,
            foreground="#666",
        ).pack(anchor="w", padx=12)

        columns = ("enabled", "seconds", "title", "flash", "sound")
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=8)
        self.tree.heading("enabled", text="启用")
        self.tree.heading("seconds", text="剩余(秒)")
        self.tree.heading("title", text="样式预览")
        self.tree.heading("flash", text="闪烁")
        self.tree.heading("sound", text="声音")
        widths = {"enabled": 60, "seconds": 80, "title": 210, "flash": 150, "sound": 60}
        for col in columns:
            self.tree.column(col, width=widths[col], anchor="center")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=12, pady=8)
        self.tree.bind("<Double-Button-1>", lambda _e: self._edit_rule())

        buttons = ttk.Frame(self)
        buttons.pack(fill=tk.X, padx=12, pady=(0, 8))
        ttk.Button(buttons, text="添加节点", command=self._add_rule).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text="编辑", command=self._edit_rule).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text="删除", command=self._delete_rule).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text="上移", command=lambda: self._move_rule(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text="下移", command=lambda: self._move_rule(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text="全部试听", command=self._preview_all).pack(side=tk.RIGHT, padx=2)

        self.status = ttk.Label(self, text="", foreground="#C0392B")
        self.status.pack(anchor="w", padx=12, pady=(0, 8))

        self._refresh_table()

    # ---- 表刷新 ----

    def _refresh_table(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for rule in sorted(self.profile.reminders, key=lambda r: r.threshold_seconds, reverse=True):
            preview = f"{rule.threshold_seconds}s  12:34  ▮{rule.fg_color}▮{rule.bg_color}"
            flash = f"{rule.flash_hz:g}Hz {FLASH_STYLE_LABELS.get(rule.flash_style, rule.flash_style)}" if rule.flash else "否"
            self.tree.insert(
                "", tk.END,
                iid=rule.id,
                values=("是" if rule.enabled else "否", rule.threshold_seconds, preview, flash,
                       "是" if rule.sound else "否"),
            )

    # ---- 规则编辑 ----

    def _selected_rule(self) -> ReminderRule | None:
        selection = self.tree.selection()
        if not selection:
            return None
        rule_id = selection[0]
        for rule in self.profile.reminders:
            if rule.id == rule_id:
                return rule
        return None

    def _add_rule(self) -> None:
        rule = ReminderRule()
        self.profile.reminders.append(rule)
        self.on_change(self.profile)
        self._refresh_table()
        self.tree.selection_set(rule.id)

    def _edit_rule(self) -> None:
        rule = self._selected_rule()
        if rule:
            edit_reminder_dialog(self, rule, self.profile, self.on_change, self.on_preview)
            self._refresh_table()

    def _delete_rule(self) -> None:
        rule = self._selected_rule()
        if not rule:
            return
        self.profile.reminders = [r for r in self.profile.reminders if r.id != rule.id]
        self.on_change(self.profile)
        self._refresh_table()

    def _move_rule(self, delta: int) -> None:
        rule = self._selected_rule()
        if not rule:
            return
        rules = self.profile.reminders
        index = next(i for i, r in enumerate(rules) if r.id == rule.id)
        target = index + delta
        if not (0 <= target < len(rules)):
            return
        rules[index], rules[target] = rules[target], rules[index]
        self.on_change(self.profile)
        self._refresh_table()

    def _preview_all(self) -> None:
        for rule in self.profile.reminders:
            if rule.enabled and rule.sound:
                self.on_preview(rule.sound_source, rule.volume, rule.sound_repeat, rule.sound_interval)
                return
        self.status.configure(text="没有启用声音的提醒节点。")

    # ---- 一键静音 ----

    def _on_mute(self) -> None:
        self.on_global_mute(bool(self._mute_box.var.get()))  # type: ignore[attr-defined]


def edit_reminder_dialog(
    parent,
    rule: ReminderRule,
    profile: Profile,
    on_change: Callable[[Profile], None],
    on_preview: Callable[[str, float, int, float], None],
) -> None:
    """编辑单个提醒节点。"""
    dialog = tk.Toplevel(parent)
    dialog.title(f"提醒节点：剩余 {rule.threshold_seconds} 秒")
    dialog.transient(parent)
    dialog.grab_set()
    dialog.resizable(False, False)

    enabled = check_row(dialog, "启用该节点", rule.enabled)
    enabled.pack(anchor="w", padx=16, pady=(14, 4))

    row = ttk.Frame(dialog)
    row.pack(fill=tk.X, padx=16, pady=4)
    ttk.Label(row, text="剩余时间 ≥").pack(side=tk.LEFT)
    seconds = SpinBox(row, "", 0, 86400, rule.threshold_seconds, None)
    seconds.pack(side=tk.LEFT, padx=4)
    ttk.Label(row, text="秒（进入该节点时触发）").pack(side=tk.LEFT)

    colors = ttk.Labelframe(dialog, text=" 样式 ")
    colors.pack(fill=tk.X, padx=16, pady=6)
    row = ttk.Frame(colors)
    row.pack(fill=tk.X, padx=8, pady=6)
    fg = ColorButton(row, rule.fg_color, lambda color: setattr(fg, "_color", color), label="数字色")
    fg.pack(side=tk.LEFT, padx=(0, 10))
    bg = ColorButton(row, rule.bg_color, lambda color: setattr(bg, "_color", color), label="背景色")
    bg.pack(side=tk.LEFT, padx=(0, 10))
    flash_color = ColorButton(row, rule.flash_color, lambda color: setattr(flash_color, "_color", color), label="闪烁色")
    flash_color.pack(side=tk.LEFT)

    flash_row = ttk.Frame(colors)
    flash_row.pack(fill=tk.X, padx=8, pady=(0, 4))
    flash = check_row(flash_row, "闪烁", rule.flash)
    flash.pack(side=tk.LEFT, padx=(0, 10))
    ttk.Label(flash_row, text="方式").pack(side=tk.LEFT)
    style = ttk.Combobox(flash_row, state="readonly", values=[FLASH_STYLE_LABELS[s] for s in FLASH_STYLES], width=14)
    style.set(FLASH_STYLE_LABELS.get(rule.flash_style, FLASH_STYLE_LABELS[FLASH_STYLES[0]]))
    style.pack(side=tk.LEFT, padx=4)
    hz = SpinBox(flash_row, "频率", 1, 30, int(rule.flash_hz), None, unit="Hz")
    hz.pack(side=tk.LEFT, padx=6)
    duration = SpinBox(flash_row, "持续", 0, 3600, int(rule.flash_duration), None, unit="秒")
    duration.pack(side=tk.LEFT, padx=6)

    ttk.Label(colors, text="持续填 0 = 一直闪到本根 K 线结束。", foreground="#666").pack(
        anchor="w", padx=8, pady=(0, 8)
    )

    sound_box = ttk.Labelframe(dialog, text=" 声音 ")
    sound_box.pack(fill=tk.X, padx=16, pady=6)
    row = ttk.Frame(sound_box)
    row.pack(fill=tk.X, padx=8, pady=6)
    sound = check_row(row, "播放声音", rule.sound)
    sound.pack(side=tk.LEFT, padx=(0, 12))
    ttk.Label(row, text="音源").pack(side=tk.LEFT)
    source_label = ttk.Label(row, text=sound_source_label(rule.sound_source), width=26, anchor="w")
    source_label.pack(side=tk.LEFT)

    def choose() -> None:
        def apply(chosen: str) -> None:
            rule.sound_source = chosen
            source_label.configure(text=sound_source_label(chosen))

        choose_sound_source(dialog, rule.sound_source, apply)

    ttk.Button(row, text="选择音源", command=choose).pack(side=tk.LEFT, padx=4)
    ttk.Button(row, text="试听", command=lambda: on_preview(rule.sound_source, volume.value, repeat.value, interval.value)).pack(side=tk.LEFT)

    row = ttk.Frame(sound_box)
    row.pack(fill=tk.X, padx=8, pady=(0, 8))
    volume = ScaleRow(row, "音量", 0.0, 1.0, rule.volume, None)
    volume.pack(side=tk.LEFT)

    # 重复 / 间隔 单独一行：挤在音量那行会被窗口右边界裁掉
    row = ttk.Frame(sound_box)
    row.pack(fill=tk.X, padx=8, pady=(0, 4))
    repeat = SpinBox(row, "重复", 1, 20, rule.sound_repeat, None, unit="次")
    repeat.pack(side=tk.LEFT, padx=(0, 16))
    interval = ScaleRow(row, "每次间隔", 0.0, 5.0, rule.sound_interval, None, resolution=0.1)
    interval.pack(side=tk.LEFT)
    ttk.Label(row, text="秒").pack(side=tk.LEFT)

    ttk.Label(
        sound_box,
        text="重复 3 次 + 每次间隔 0.5 秒 = 连着响 3 下，每下之间停 0.5 秒。",
        foreground="#666",
    ).pack(anchor="w", padx=8, pady=(0, 8))

    buttons = ttk.Frame(dialog)
    buttons.pack(fill=tk.X, padx=16, pady=10)

    def commit() -> None:
        rule.enabled = enabled.var.get()  # type: ignore[attr-defined]
        rule.threshold_seconds = max(0, seconds.value)
        rule.flash = flash.var.get()  # type: ignore[attr-defined]
        selected = {FLASH_STYLE_LABELS[key]: key for key in FLASH_STYLES}.get(style.get(), rule.flash_style)
        rule.flash_style = selected
        rule.flash_hz = max(1, hz.value)
        rule.flash_duration = max(0, duration.value)
        rule.fg_color = fg._color  # noqa: SLF001
        rule.bg_color = bg._color  # noqa: SLF001
        rule.flash_color = flash_color._color  # noqa: SLF001
        rule.sound = sound.var.get()  # type: ignore[attr-defined]
        rule.volume = volume.value
        rule.sound_repeat = max(1, repeat.value)
        rule.sound_interval = interval.value
        on_change(profile)
        dialog.destroy()

    ttk.Button(buttons, text="保存", command=commit).pack(side=tk.RIGHT, padx=4)
    ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
    dialog.bind("<Return>", lambda e: commit())
    center_on_screen(dialog, 660, 470)
    parent.wait_window(dialog)


class SettingsWindow:
    """三标签设置窗口的容器。"""

    def __init__(
        self,
        host: tk.Tk,
        cfg: AppConfig,
        on_change: Callable[[AppConfig], None],
        on_active_profile: Callable[[Profile], None],
        on_preview: Callable[[str, float, int, float], None],
        tab: str = "profiles",
    ) -> None:
        self.cfg = cfg
        self.on_change = on_change
        self.window = tk.Toplevel(host)
        self.window.title("K线倒计时 · 设置")
        self.window.minsize(840, 740)
        self.window.protocol("WM_DELETE_WINDOW", self._close)
        # 任务栏/标题栏图标与托盘保持一致（同一个 .ico）
        try:
            from core.resources import resource_ico_path
            from core.winapi import set_window_icon

            ico = resource_ico_path()
            self.window.iconbitmap(ico)
            self.window.update_idletasks()
            set_window_icon(self.window.winfo_id(), ico)
        except Exception:  # noqa: BLE001
            pass

        notebook = ttk.Notebook(self.window)
        notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.profiles_tab = ProfilesTab(notebook, cfg, self._commit, on_active_profile)
        self.profiles_tab.pack(fill=tk.BOTH, expand=True)
        notebook.add(self.profiles_tab, text=" 交易时段 ")

        self.appearance_tab = AppearanceTab(notebook, cfg.appearance, self._commit_appearance)
        notebook.add(self.appearance_tab, text=" 外观 ")

        self.reminder_tab = ReminderTab(
            notebook, cfg.active_profile(), cfg.global_mute, self._commit_profile, self._commit_global_mute, on_preview
        )
        notebook.add(self.reminder_tab, text=" 提醒 ")
        self.on_preview = on_preview

        wanted = TABS.index(tab) if tab in TABS else 0
        notebook.select(wanted)
        center_on_screen(self.window, 920, 790)
        self.notebook = notebook

    def on_active_profile_changed(self, profile: Profile) -> None:
        """切换当前配置后，提醒页改为编辑新配置（互不覆盖）。"""
        try:
            self.reminder_tab.destroy()
        except Exception:  # noqa: BLE001
            pass
        self.reminder_tab = ReminderTab(
            self.notebook,
            profile,
            self.cfg.global_mute,
            self._commit_profile,
            self._commit_global_mute,
            self.on_preview,
        )
        try:
            self.notebook.forget(self.reminder_tab)
        except tk.TclError:
            pass
        self.notebook.add(self.reminder_tab, text=" 提醒 ")

    def _commit(self, cfg: AppConfig) -> None:
        self.on_change(cfg)

    def _commit_appearance(self, appearance: Appearance) -> None:
        self.cfg.appearance = appearance
        self._commit(self.cfg)

    def _commit_profile(self, profile: Profile) -> None:
        self._commit(self.cfg)

    def _commit_global_mute(self, muted: bool) -> None:
        self.cfg.global_mute = muted
        self._commit(self.cfg)

    def set_tab(self, tab: str) -> None:
        if tab in TABS:
            self.notebook.select(TABS.index(tab))

    def _close(self) -> None:
        # 关闭设置窗口后程序继续在后台运行（托盘仍在）
        self.window.destroy()
