"""数据模型层。

只依赖标准库，不含任何 UI / Win32 代码，可被测试直接导入。

设计约定
--------
* 时间一律使用「本地时间 naive datetime」，与系统时钟同源。
* 交易时段用「距零点分钟数」表示，`end_minute` 允许取 1440（24:00）。
* 所有模型都提供 to_dict()/from_dict()，缺字段自动回落默认值，
  便于配置结构随版本演进时做向后兼容迁移。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 1

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

#: 常用周期预设（显示名, 秒）
PERIOD_PRESETS = (
    ("1 分钟", 60),
    ("3 分钟", 180),
    ("5 分钟", 300),
    ("15 分钟", 900),
    ("30 分钟", 1800),
    ("60 分钟", 3600),
)

ALIGNMENT_SESSION = "session"
ALIGNMENT_CLOCK = "clock"
ALIGNMENT_LABELS = {
    ALIGNMENT_SESSION: "交易时段起点对齐",
    ALIGNMENT_CLOCK: "整点时钟对齐",
}

FLASH_TOGGLE = "toggle"
FLASH_PULSE = "pulse"
FLASH_STYLES = (FLASH_TOGGLE, FLASH_PULSE)
FLASH_STYLE_LABELS = {FLASH_TOGGLE: "整块闪烁（背景变色）", FLASH_PULSE: "仅文字闪烁"}

#: 内置提示音预设
SOUND_PRESETS = (
    ("叮（短促）", "ding"),
    ("嘀（清亮）", "beep"),
    ("嗒（极短）", "tick"),
    ("警报（双响）", "alarm"),
)
SOUND_PRESET_KEYS = tuple(key for _, key in SOUND_PRESETS)
PRESET_PREFIX = "preset:"
FILE_PREFIX = "file:"

MAX_PERIOD_SECONDS = 24 * 3600
MIN_PERIOD_SECONDS = 5


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def format_hm(minutes: int) -> str:
    """分钟数 -> "HH:MM"（1440 显示为 24:00）。"""
    minutes = int(minutes)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def parse_hm(text: str) -> int:
    """ "HH:MM" -> 分钟数。非法输入抛 ValueError。"""
    raw = str(text).strip().replace("：", ":")
    if not raw:
        raise ValueError("时间不能为空")
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(f"时间格式应为 HH:MM，收到 {text!r}")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError(f"时间格式应为 HH:MM，收到 {text!r}") from exc
    if not (0 <= hour <= 24) or not (0 <= minute <= 59):
        raise ValueError(f"时间超出范围：{text}")
    if hour == 24 and minute != 0:
        raise ValueError("24:00 之后不能再有分钟")
    return hour * 60 + minute


def format_duration(seconds: float) -> str:
    """秒数 -> 角标显示文本。>=1 小时用 H:MM:SS，否则 MM:SS。"""
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def sound_source_label(source: str) -> str:
    if not source:
        return "（无）"
    if source.startswith(PRESET_PREFIX):
        key = source[len(PRESET_PREFIX):]
        for label, preset_key in SOUND_PRESETS:
            if preset_key == key:
                return f"内置：{label}"
        return f"内置：{key}"
    if source.startswith(FILE_PREFIX):
        return source[len(FILE_PREFIX):]
    return source


@dataclass
class TradingSession:
    """一个交易时段，start/end 为距零点分钟数，end 可等于 1440。"""

    start_minute: int = 570
    end_minute: int = 690
    enabled: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TradingSession":
        data = data or {}
        return cls(
            start_minute=int(data.get("start_minute", 570)),
            end_minute=int(data.get("end_minute", 690)),
            enabled=bool(data.get("enabled", True)),
        )

    @property
    def label(self) -> str:
        return f"{format_hm(self.start_minute)}–{format_hm(self.end_minute)}"


@dataclass
class ReminderRule:
    """单个提醒节点：在剩余 N 秒时改变角标样式 / 播放声音。"""

    id: str = field(default_factory=lambda: new_id("rem"))
    threshold_seconds: int = 60
    enabled: bool = True
    fg_color: str = "#FFFFFF"
    bg_color: str = "#B3261E"
    flash: bool = True
    flash_style: str = FLASH_PULSE  # 默认只闪文字，不整块变色
    flash_hz: float = 2.0
    flash_duration: float = 0.0  # 0 = 持续到本根 K 线结束
    flash_color: str = "#FFD400"
    sound: bool = True
    sound_source: str = PRESET_PREFIX + "ding"
    sound_repeat: int = 1
    sound_interval: float = 0.6
    volume: float = 0.8

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReminderRule":
        data = data or {}
        default = cls()
        return cls(
            id=str(data.get("id") or new_id("rem")),
            threshold_seconds=int(data.get("threshold_seconds", default.threshold_seconds)),
            enabled=bool(data.get("enabled", True)),
            fg_color=str(data.get("fg_color", default.fg_color)),
            bg_color=str(data.get("bg_color", default.bg_color)),
            flash=bool(data.get("flash", default.flash)),
            flash_style=str(data.get("flash_style", default.flash_style)),
            flash_hz=float(data.get("flash_hz", default.flash_hz)),
            flash_duration=float(data.get("flash_duration", default.flash_duration)),
            flash_color=str(data.get("flash_color", default.flash_color)),
            sound=bool(data.get("sound", default.sound)),
            sound_source=str(data.get("sound_source", default.sound_source)),
            sound_repeat=max(0, int(data.get("sound_repeat", default.sound_repeat))),
            sound_interval=max(0.0, float(data.get("sound_interval", default.sound_interval))),
            volume=min(1.0, max(0.0, float(data.get("volume", default.volume)))),
        )


def default_reminder_rules() -> list[ReminderRule]:
    """默认提醒节点：60/30/10/3 秒。"""
    rules: list[ReminderRule] = []
    presets = [
        (60, "#FFFFFF", "#B3261E", True, 2.0, PRESET_PREFIX + "ding", 1),
        (30, "#FFFFFF", "#C2410C", True, 2.5, PRESET_PREFIX + "beep", 1),
        (10, "#FFFFFF", "#B91C1C", True, 4.0, PRESET_PREFIX + "tick", 1),
        (3, "#111111", "#FDE047", True, 6.0, PRESET_PREFIX + "tick", 1),
    ]
    for threshold, fg, bg, flash, hz, src, repeat in presets:
        rules.append(
            ReminderRule(
                threshold_seconds=threshold,
                fg_color=fg,
                bg_color=bg,
                flash=flash,
                flash_hz=hz,
                sound_source=src,
                sound_repeat=repeat,
            )
        )
    return rules


@dataclass
class Profile:
    """一套完整的交易时段配置：时段 + 周期 + 对齐规则 + 提醒。"""

    id: str = field(default_factory=lambda: new_id("profile"))
    name: str = "新配置"
    period_seconds: int = 300
    alignment: str = ALIGNMENT_SESSION
    sessions: list[TradingSession] = field(default_factory=list)
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    reminders: list[ReminderRule] = field(default_factory=default_reminder_rules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "period_seconds": self.period_seconds,
            "alignment": self.alignment,
            "sessions": [s.to_dict() for s in self.sessions],
            "weekdays": list(self.weekdays),
            "reminders": [r.to_dict() for r in self.reminders],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Profile":
        data = data or {}
        sessions = [TradingSession.from_dict(s) for s in data.get("sessions") or []]
        if not sessions:
            sessions = [TradingSession(570, 690), TradingSession(780, 900)]
        reminders_raw = data.get("reminders")
        reminders = (
            [ReminderRule.from_dict(r) for r in reminders_raw]
            if isinstance(reminders_raw, list)
            else default_reminder_rules()
        )
        weekdays = [int(d) for d in data.get("weekdays", [0, 1, 2, 3, 4]) if 0 <= int(d) <= 6]
        return cls(
            id=str(data.get("id") or new_id("profile")),
            name=str(data.get("name") or "未命名配置"),
            period_seconds=int(data.get("period_seconds", 300)),
            alignment=str(data.get("alignment", ALIGNMENT_SESSION)),
            sessions=sessions,
            weekdays=weekdays,
            reminders=reminders,
        )

    def copy(self, *, rename: str | None = None, new_id_flag: bool = True) -> "Profile":
        clone = Profile.from_dict(self.to_dict())
        if new_id_flag:
            clone.id = new_id("profile")
            clone.reminders = [ReminderRule.from_dict({**r.to_dict(), "id": new_id("rem")}) for r in self.reminders]
        if rename is not None:
            clone.name = rename
        return clone


@dataclass
class Appearance:
    """角标外观。修改后立即生效并持久化。"""

    font_family: str = "Bahnschrift"  # 笔画干净、数字辨识度高；缺失时自动回退
    font_size: int = 32
    font_weight: str = "normal"
    fg_color: str = "#FFFFFF"
    bg_color: str = "#1B1B1F"
    transparent_bg: bool = False  # True = 不画底色，只留数字（逐像素 alpha，无需描边）
    opacity: float = 0.90
    padding_x: int = 16
    padding_y: int = 4
    width: int = 0  # 0 = 按字体自动
    height: int = 0
    corner_radius: int = 10  # 0 = 直角（更锐利，无边缘杂色）
    idle_text: str = "--:--"  # 非交易时段只换文案，颜色沿用上面的数字/背景色
    always_on_top: bool = True
    click_through: bool = False
    locked: bool = False
    x: int = -1  # -1 = 首次启动自动放到右下角
    y: int = -1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Appearance":
        data = data or {}
        default = cls()
        result = cls()
        for key in default.to_dict():
            if key not in data:
                continue
            current = getattr(default, key)
            value = data[key]
            try:
                if isinstance(current, bool):
                    setattr(result, key, bool(value))
                elif isinstance(current, int):
                    setattr(result, key, int(value))
                elif isinstance(current, float):
                    setattr(result, key, float(value))
                else:
                    setattr(result, key, str(value))
            except (TypeError, ValueError):
                continue
        return result


@dataclass
class AppConfig:
    """顶层配置：外观（全局）+ 多个交易时段配置（各自独立周期与提醒）。"""

    schema_version: int = SCHEMA_VERSION
    active_profile_id: str = ""
    profiles: list[Profile] = field(default_factory=list)
    appearance: Appearance = field(default_factory=Appearance)
    global_mute: bool = False
    autostart: bool = False
    hidden: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "active_profile_id": self.active_profile_id,
            "profiles": [p.to_dict() for p in self.profiles],
            "appearance": self.appearance.to_dict(),
            "global_mute": self.global_mute,
            "autostart": self.autostart,
            "hidden": self.hidden,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        data = data or {}
        profiles = [Profile.from_dict(p) for p in data.get("profiles") or []]
        cfg = cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            active_profile_id=str(data.get("active_profile_id") or ""),
            profiles=profiles,
            appearance=Appearance.from_dict(data.get("appearance") or {}),
            global_mute=bool(data.get("global_mute", False)),
            autostart=bool(data.get("autostart", False)),
            hidden=bool(data.get("hidden", False)),
        )
        cfg.ensure_valid()
        return cfg

    # ---- 便捷访问 ----

    def ensure_valid(self) -> None:
        if not self.profiles:
            self.profiles = default_profiles()
        ids = {p.id for p in self.profiles}
        if self.active_profile_id not in ids:
            self.active_profile_id = self.profiles[0].id

    def active_profile(self) -> Profile:
        self.ensure_valid()
        for profile in self.profiles:
            if profile.id == self.active_profile_id:
                return profile
        return self.profiles[0]


def default_profiles() -> list[Profile]:
    """首次启动的三套内置配置。用户可自由修改/删除。"""
    ashare = Profile(
        name="A 股",
        period_seconds=300,
        alignment=ALIGNMENT_SESSION,
        sessions=[TradingSession(570, 690), TradingSession(780, 900)],
        weekdays=[0, 1, 2, 3, 4],
        reminders=default_reminder_rules(),
    )
    futures = Profile(
        name="期货（日盘+夜盘）",
        period_seconds=300,
        alignment=ALIGNMENT_SESSION,
        sessions=[
            TradingSession(540, 615),   # 09:00–10:15
            TradingSession(630, 690),   # 10:30–11:30
            TradingSession(810, 900),   # 13:30–15:00
            TradingSession(1260, 1380), # 21:00–23:00
        ],
        weekdays=[0, 1, 2, 3, 4],
        reminders=default_reminder_rules(),
    )
    all_day = Profile(
        name="全天连续（整点对齐）",
        period_seconds=900,
        alignment=ALIGNMENT_CLOCK,
        sessions=[TradingSession(0, 1440)],
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        reminders=default_reminder_rules(),
    )
    return [ashare, futures, all_day]
