"""主程序入口：K 线周期倒计时角标。

职责即「胶水层」：
* 起 Tk（DPI 感知 + 单实例 + 窗口图标）
* 每 ~100ms 从系统时钟重算状态（引擎纯函数，不累积误差）
* 把状态翻译成角标视觉（文本 + 颜色 + 闪烁相位）
* 托盘、右键菜单、设置窗口、声音、开机自启动
"""

from __future__ import annotations

import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audio import AudioPlayer  # noqa: E402
from core.autostart import is_autostart_enabled, set_autostart  # noqa: E402
from core.config_store import ConfigStore  # noqa: E402
from core.engine import compute_state  # noqa: E402
from core.models import (  # noqa: E402
    AppConfig,
    Profile,
    format_duration,
)
from core.reminders import flash_phase  # noqa: E402
from core.resources import ensure_resources  # noqa: E402
from core.winapi import (  # noqa: E402
    MOD_ALT,
    MOD_CONTROL,
    SingleInstance,
    TrayIcon,
    TrayMenuItem,
    enable_dpi_awareness,
    hide_console_window,
)
from ui.badge import BadgeVisual, BadgeWindow  # noqa: E402
from ui.settings import SettingsWindow  # noqa: E402

TICK_MS = 100  # 计算与刷新周期。

# 右键菜单里的周期项：(秒, 显示名)。顺序即菜单顺序。
PERIOD_MENU = (
    (60, "1 分钟"),
    (180, "3 分钟"),
    (300, "5 分钟"),
    (900, "15 分钟"),
    (1800, "30 分钟"),
    (3600, "60 分钟"),
)


class App:
    def __init__(self) -> None:
        self.store = ConfigStore()
        self.cfg: AppConfig = self.store.load()
        self.audio = AudioPlayer()
        self.scheduler = self._make_scheduler()
        self.last_state = None
        self.last_tick_monotonic = time.monotonic()
        self.settings: SettingsWindow | None = None
        self._last_flash = None
        self._flash_started_at: float | None = None
        self._tooltip_text: str = ""
        self._tooltip_updated: float = 0.0

    # ---------------- 生命周期 ----------------

    def _make_scheduler(self):
        from core.reminders import ReminderScheduler

        return ReminderScheduler()

    def run(self) -> None:
        hide_console_window()  # 用 python.exe 启动时去掉任务栏里的命令行图标
        dpi = enable_dpi_awareness()
        root = tk.Tk()
        try:
            root.tk.call("tk", "scaling", dpi / 72.0)
        except tk.TclError:
            pass
        ico, png = ensure_resources()
        try:
            root.iconbitmap(default=ico)  # 任务栏/标题栏图标与托盘保持一致
        except tk.TclError:
            pass
        try:
            root.iconphoto(True, tk.PhotoImage(file=png, master=root))
        except tk.TclError:
            pass

        self.root = root
        self.badge = BadgeWindow(
            root,
            self.cfg.appearance,
            on_right_click=self._show_badge_menu,
            on_moved=self._on_badge_moved,
            on_double_click=self._toggle_visibility,
        )
        self.badge.apply_appearance(self.cfg.appearance)

        self.tray = TrayIcon(
            tooltip="K线倒计时",
            icon_path=ico,
            menu_provider=self._tray_menu_items,
            on_activate=self._toggle_visibility,
            hotkeys=(
                ("toggle_click_through", MOD_CONTROL | MOD_ALT, 0x4B),  # Ctrl+Alt+K
                ("toggle_visible", MOD_CONTROL | MOD_ALT, 0x48),        # Ctrl+Alt+H
            ),
        )

        if self.cfg.autostart != is_autostart_enabled():
            set_autostart(self.cfg.autostart)

        if self.cfg.appearance.click_through:
            # 启动即处于穿透状态时，明确告诉用户怎么解除，避免「点不中也不知道怎么办」
            self.root.after(
                1200,
                lambda: self.tray.show_balloon(
                    "鼠标穿透已开启",
                    "角标当前不响应鼠标。按 Ctrl+Alt+K 解除，或用托盘图标右键菜单关闭。",
                    warning=True,
                ),
            )

        if not self.cfg.hidden:
            self.badge.show()
        else:
            self.badge.hide()

        self.root.after(200, self._poll_tray)
        self.root.after(TICK_MS, self._tick)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.mainloop()

    def _quit(self) -> None:
        try:
            self.tray.stop()
            self.audio.shutdown()
            self.store.save(self.cfg)
        finally:
            self.root.destroy()

    # ---------------- 托盘与菜单 ----------------

    CMD_PROFILE_BASE = 1000
    CMD_PERIOD_BASE = 2000
    # 托盘菜单里的行为开关（与角标右键菜单共用一套处理逻辑）
    CMD_TOGGLE_CLICK_THROUGH = 9001
    CMD_TOGGLE_LOCK = 9002
    CMD_TOGGLE_VISIBLE = 9003
    CMD_OPEN_SETTINGS = 9004
    CMD_QUIT = 9005

    def _tray_menu_items(self) -> list[TrayMenuItem]:
        """托盘右键菜单：周期与配置折叠成子菜单，去掉与子菜单重复的「当前…」行。"""
        active = self.cfg.active_profile()
        period_children = []
        for seconds, label in PERIOD_MENU:
            period_children.append(
                TrayMenuItem(
                    id=self.CMD_PERIOD_BASE + seconds // 60,
                    label=label,
                    checked=active.period_seconds == seconds,
                )
            )
        profile_children = []
        for profile in self.cfg.profiles:
            profile_children.append(
                TrayMenuItem(
                    id=self.CMD_PROFILE_BASE + self.cfg.profiles.index(profile),
                    label=profile.name,
                    checked=profile.id == self.cfg.active_profile_id,
                )
            )
        return [
            TrayMenuItem(id=None, label="周期", children=tuple(period_children)),
            TrayMenuItem(id=None, label="配置", children=tuple(profile_children)),
            TrayMenuItem(id=None, label=""),
            TrayMenuItem(
                id=self.CMD_TOGGLE_CLICK_THROUGH,
                label="鼠标穿透（Ctrl+Alt+K）",
                checked=self.cfg.appearance.click_through,
            ),
            TrayMenuItem(id=self.CMD_TOGGLE_LOCK, label="锁定位置", checked=self.cfg.appearance.locked),
            TrayMenuItem(id=self.CMD_TOGGLE_VISIBLE, label="隐藏角标" if self.badge.visible else "显示角标"),
            TrayMenuItem(id=None, label=""),
            TrayMenuItem(id=self.CMD_OPEN_SETTINGS, label="设置…"),
            TrayMenuItem(id=self.CMD_QUIT, label="退出"),
        ]

    def _poll_tray(self) -> None:
        while True:
            try:
                kind, value = self.tray.commands.get_nowait()
            except Exception:  # noqa: BLE001
                break
            if kind == "activate":
                self._toggle_visibility()
            elif kind == "hotkey":
                self._handle_hotkey(str(value))
            elif kind == "command" and value is not None:
                self._handle_command(int(value))
        self.root.after(150, self._poll_tray)

    def _handle_hotkey(self, name: str) -> None:
        """全局热键：即使角标已开启鼠标穿透，也能用键盘救回来。"""
        if name == "toggle_click_through":
            self._toggle_click_through()
        elif name == "toggle_visible":
            self._toggle_visibility()

    def _handle_command(self, command_id: int) -> None:
        if command_id == self.CMD_TOGGLE_CLICK_THROUGH:
            self._toggle_click_through()
            return
        if command_id == self.CMD_TOGGLE_LOCK:
            self._toggle_lock()
            return
        if command_id == self.CMD_TOGGLE_VISIBLE:
            self._toggle_visibility()
            return
        if command_id == self.CMD_OPEN_SETTINGS:
            self._open_settings("profiles")
            return
        if command_id == self.CMD_QUIT:
            self._quit()
            return
        profile = self.cfg.active_profile()
        if self.CMD_PROFILE_BASE <= command_id < self.CMD_PROFILE_BASE + len(self.cfg.profiles):
            index = command_id - self.CMD_PROFILE_BASE
            self._switch_profile(self.cfg.profiles[index])
            return
        if self.CMD_PERIOD_BASE <= command_id < self.CMD_PERIOD_BASE + 100:
            minutes = command_id - self.CMD_PERIOD_BASE
            if minutes and minutes * 60 != profile.period_seconds:
                profile.period_seconds = minutes * 60
                self._commit()
            return

    def _switch_profile(self, profile: Profile) -> None:
        if profile.id == self.cfg.active_profile_id:
            return
        self.cfg.active_profile_id = profile.id
        self.scheduler.reset()
        self._commit()
        if self.settings:
            self.settings.on_active_profile_changed(profile)

    def _show_badge_menu(self, x_root: int, y_root: int) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        active = self.cfg.active_profile()
        appearance = self.cfg.appearance

        # 周期：当前项前面打勾（不另起一行重复「当前周期」）
        menu.add_command(label="周期", state=tk.DISABLED)
        for seconds, label in PERIOD_MENU:
            mark = "✓ " if active.period_seconds == seconds else "    "
            menu.add_command(label=mark + label, command=lambda s=seconds: self._set_period(s))
        menu.add_separator()

        # 配置：同样用对勾标记当前配置
        menu.add_command(label="配置", state=tk.DISABLED)
        for profile in self.cfg.profiles:
            mark = "✓ " if profile.id == self.cfg.active_profile_id else "    "
            menu.add_command(label=mark + profile.name, command=lambda p=profile: self._switch_profile(p))
        menu.add_separator()

        menu.add_command(label="设置…", command=lambda: self._open_settings("profiles"))
        menu.add_separator()
        # 勾选类菜单项必须持有 Tk 变量引用：否则 BooleanVar 被 GC 后
        # Tcl 变量一并删除，勾选标记就显示不出来。
        self._menu_vars = []
        lock_var = tk.BooleanVar(value=bool(appearance.locked))
        through_var = tk.BooleanVar(value=bool(appearance.click_through))
        self._menu_vars.extend([lock_var, through_var])

        menu.add_checkbutton(label="锁定位置", variable=lock_var, command=self._toggle_lock)
        menu.add_checkbutton(label="鼠标穿透（Ctrl+Alt+K 可解除）", variable=through_var, command=self._toggle_click_through)
        menu.add_command(label="隐藏角标" if self.badge.visible else "显示角标", command=self._toggle_visibility)
        menu.add_separator()
        menu.add_command(label="开机启动" if not self.cfg.autostart else "取消开机启动", command=self._toggle_autostart)
        menu.add_command(label="退出程序", command=self._quit_from_menu)

        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _set_period(self, seconds: int) -> None:
        profile = self.cfg.active_profile()
        if profile.period_seconds == seconds:
            return
        profile.period_seconds = seconds
        self._commit()

    def _toggle_lock(self) -> None:
        self.cfg.appearance.locked = not self.cfg.appearance.locked
        self._commit()

    def _toggle_click_through(self) -> None:
        self.cfg.appearance.click_through = not self.cfg.appearance.click_through
        self._commit()

    def _toggle_visibility(self) -> None:
        if self.badge.visible:
            self.badge.hide()
            self.cfg.hidden = True
        else:
            self.badge.show()
            self.cfg.hidden = False
        self._commit()

    def _toggle_autostart(self) -> None:
        self.cfg.autostart = not self.cfg.autostart
        set_autostart(self.cfg.autostart)
        self._commit()

    def _quit_from_menu(self) -> None:
        self._quit()

    def _open_settings(self, tab: str) -> None:
        if self.settings and self.settings.window.winfo_exists():
            self.settings.window.lift()
            self.settings.set_tab(tab)
            return
        self.settings = SettingsWindow(
            self.root,
            self.cfg,
            on_change=self._on_settings_change,
            on_active_profile=self._switch_profile,
            on_preview=self._preview_sound,
            tab=tab,
        )

    # ---------------- 设置回调 ----------------

    def _on_settings_change(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self._commit()

    def _preview_sound(self, source: str, volume: float, repeat: int, interval: float) -> None:
        self.audio.play(source, volume, repeat, interval)

    def _commit(self) -> None:
        """持久化 + 立即应用。"""
        self.store.save(self.cfg)
        self.badge.apply_appearance(self.cfg.appearance)
        self._tooltip_text = ""  # 交给下一次 tick 刷新托盘提示

    def _on_badge_moved(self, x: int, y: int) -> None:
        self.cfg.appearance.x = x
        self.cfg.appearance.y = y
        self._commit()

    # ---------------- 主循环 ----------------

    def _tick(self) -> None:
        now = datetime.now()
        state = compute_state(now, self.cfg.active_profile())
        self._render(state, now)
        self._update_tooltip(state)
        self.last_state = state

        gap = time.monotonic() - self.last_tick_monotonic
        self.last_tick_monotonic = time.monotonic()
        if gap > 2.0:  # 休眠/唤醒或长时间卡顿：不补响历史节点
            self.scheduler.suppress_current_bar()

        delay = TICK_MS
        next_boundary = state.next_boundary
        if next_boundary is not None:
            seconds_to_boundary = (next_boundary - now).total_seconds()
            if 0 <= seconds_to_boundary < 1.5:
                delay = 40  # 临近边界时提高刷新密度
        self.root.after(delay, self._tick)

    def _update_tooltip(self, state) -> None:
        """托盘提示：配置名 + 状态 + 剩余时间（每秒最多更新一次）。"""
        if state.is_trading:
            text = f"{state.profile_name} · 剩余 {format_duration(state.remaining_seconds)}"
        elif state.is_error:
            text = f"{state.profile_name} · 配置错误"
        else:
            text = f"{state.profile_name} · {state.status_label}"
        if text == self._tooltip_text:
            return
        now_tick = time.monotonic()
        if now_tick - self._tooltip_updated < 1.0 and not state.is_error:
            return
        self._tooltip_text = text
        self._tooltip_updated = now_tick
        try:
            self.tray.set_tooltip(f"K线倒计时 · {text}")
        except Exception:  # noqa: BLE001
            pass

    def _render(self, state, now: datetime) -> None:
        result = self.scheduler.update(state)

        if self.cfg.global_mute:
            self.audio.stop()
            active_rule = None  # 一键静音同时关闭闪烁
        else:
            for event in result.events:
                rule = event.rule
                if rule.sound:
                    self.audio.play(rule.sound_source, rule.volume, rule.sound_repeat, rule.sound_interval)
            active_rule = result.primary_rule

        appearance = self.cfg.appearance

        if not state.is_trading:
            text = appearance.idle_text if not state.is_error else "ERR"
            self._flash_started_at = None
            # 空闲态与交易态用同一套配色，避免两个状态来回跳色
            self.badge.render(
                BadgeVisual(text=text, fg=appearance.fg_color, bg=appearance.bg_color)
            )
            return

        text = format_duration(state.remaining_seconds)
        if active_rule is None:
            self._flash_started_at = None
            self.badge.render(BadgeVisual(text=text, fg=appearance.fg_color, bg=appearance.bg_color))
            return

        if self._flash_started_at is None:
            self._flash_started_at = now.timestamp()
        elapsed = now.timestamp() - self._flash_started_at
        if active_rule.flash_duration > 0 and elapsed > active_rule.flash_duration:
            flash_on = False
        else:
            flash_on = flash_phase(elapsed, active_rule.flash_hz)

        self.badge.render(
            BadgeVisual(
                text=text,
                fg=active_rule.fg_color,
                bg=active_rule.bg_color,
                flash_on=flash_on and bool(active_rule.flash),
                flash_color=active_rule.flash_color,
                flash_style=active_rule.flash_style,
            )
        )


def main() -> int:
    single = SingleInstance()
    if single.already_running:
        return 0  # 已在运行，静默退出
    app = App()
    try:
        app.run()
    finally:
        single.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
