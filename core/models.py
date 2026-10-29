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

SCHEMA_VERSION = 2

WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

#: 常用周期预设（显示名, 秒）
PERIOD_PRESETS = (
    ("1 分钟", 60),
    ("3 分钟", 180),
    ("5 分钟", 300),
    ("15 分钟", 900),
    ("30 分钟", 1800),
    ("60 分钟", 3600),
    ("2 小时", 7200),
    ("3 小时", 10800),
    ("4 小时", 14400),
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

#: 每套方案里最多叠加多少个周期
MAX_OVERLAYS = 10


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


def default_reminder_rules(period_seconds: int = 300) -> list[ReminderRule]:
    """默认提醒节点：60/30/10/3 秒。

    提醒阈值必须严格小于主周期长度，所以周期很短（1 分钟及以下）时会自动裁掉
    超限的节点；连一个都不剩就给一个「最后一秒」节点，保证角标不是哑巴。
    """
    limit = max(2, int(period_seconds))
    presets = [
        (60, "#FFFFFF", "#B3261E", True, 2.0, PRESET_PREFIX + "ding", 1),
        (30, "#FFFFFF", "#C2410C", True, 2.5, PRESET_PREFIX + "beep", 1),
        (10, "#FFFFFF", "#B91C1C", True, 4.0, PRESET_PREFIX + "tick", 1),
        (3, "#111111", "#FDE047", True, 6.0, PRESET_PREFIX + "tick", 1),
    ]
    usable = [p for p in presets if p[0] < limit]
    if not usable:
        usable = [(max(1, limit - 1), "#FFFFFF", "#B3261E", True, 2.0, PRESET_PREFIX + "ding", 1)]

    rules: list[ReminderRule] = []
    for threshold, fg, bg, flash, hz, src, repeat in usable:
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


def clamp_thresholds(scheme: "Scheme") -> int:
    """把超过主周期长度的阈值压到「主周期-1」，返回被改动的条数。

    用于配置迁移：v1 的节点是按当时的主周期配的，升级后主周期可能变小，
    不压一下会直接变成「配置错误」把角标打成 ERR。
    """
    limit = max(1, int(scheme.primary.period_seconds))
    changed = 0
    for timeframe in (scheme.primary, *scheme.overlays):
        for rule in timeframe.reminders:
            if rule.threshold_seconds >= limit:
                rule.threshold_seconds = limit - 1
                changed += 1
    return changed


@dataclass
class Timeframe:
    """一个周期的完整配置：周期值 + 自己的颜色 + 自己的提醒节点。

    主周期用 ``fg_color/bg_color`` 作为平时显示的颜色；
    叠加周期用同一组字段作为「接管样式」——进入接管窗口后角标换成这套颜色。
    """

    period_seconds: int = 300
    fg_color: str = "#FFFFFF"
    bg_color: str = "#1B1B1F"
    transparent_bg: bool = False  # 只对主周期有意义；叠加周期接管时一般都画底色
    reminders: list[ReminderRule] = field(default_factory=default_reminder_rules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "period_seconds": self.period_seconds,
            "fg_color": self.fg_color,
            "bg_color": self.bg_color,
            "transparent_bg": self.transparent_bg,
            "reminders": [r.to_dict() for r in self.reminders],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Timeframe":
        data = data or {}
        default = cls()
        reminders_raw = data.get("reminders")
        reminders = (
            [ReminderRule.from_dict(r) for r in reminders_raw]
            if isinstance(reminders_raw, list)
            else default_reminder_rules()
        )
        return cls(
            period_seconds=int(data.get("period_seconds", default.period_seconds)),
            fg_color=str(data.get("fg_color", default.fg_color)),
            bg_color=str(data.get("bg_color", default.bg_color)),
            transparent_bg=bool(data.get("transparent_bg", default.transparent_bg)),
            reminders=reminders,
        )

    @property
    def max_threshold(self) -> int:
        """本周期所有启用节点里最大的提前量（秒）。"""
        values = [int(r.threshold_seconds) for r in self.reminders if r.enabled]
        return max(values) if values else 0


@dataclass
class Scheme:
    """一套「周期方案」：一个主周期 + 若干叠加周期。

    切换主周期 = 切换到另一套 Scheme，叠加周期和各周期的设置整套跟着换。
    """

    primary: Timeframe = field(default_factory=Timeframe)
    overlays: list[Timeframe] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.to_dict(),
            "overlays": [o.to_dict() for o in self.overlays],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Scheme":
        data = data or {}
        return cls(
            primary=Timeframe.from_dict(data.get("primary") or {}),
            overlays=[Timeframe.from_dict(o) for o in data.get("overlays") or []],
        )

    @property
    def period_seconds(self) -> int:
        return int(self.primary.period_seconds)

    def sorted_overlays(self) -> list[Timeframe]:
        return sorted(self.overlays, key=lambda t: t.period_seconds)


def default_scheme(period_seconds: int = 300) -> Scheme:
    """新建方案：主周期用默认样式 + 与周期匹配的默认节点，没有叠加周期。"""
    period = int(period_seconds)
    return Scheme(primary=Timeframe(period_seconds=period, reminders=default_reminder_rules(period)))


@dataclass
class Profile:
    """一套交易时段配置：品种级（时段/星期/对齐）+ 多套周期方案。"""

    id: str = field(default_factory=lambda: new_id("profile"))
    name: str = "新配置"
    alignment: str = ALIGNMENT_SESSION
    sessions: list[TradingSession] = field(default_factory=list)
    weekdays: list[int] = field(default_factory=lambda: [0, 1, 2, 3, 4])
    schemes: list[Scheme] = field(default_factory=lambda: [default_scheme()])
    active_period: int = 300  # 当前方案的主周期秒数

    # ---- 方案访问 ----

    def find_scheme(self, period_seconds: int) -> Scheme | None:
        for scheme in self.schemes:
            if scheme.period_seconds == int(period_seconds):
                return scheme
        return None

    def activate(self, period_seconds: int) -> Scheme:
        """切到某个主周期；没有就新建一套默认方案（不复制现有方案）。"""
        period = int(period_seconds)
        scheme = self.find_scheme(period)
        if scheme is None:
            scheme = default_scheme(period)
            self.schemes.append(scheme)
        self.active_period = period
        return scheme

    @property
    def scheme(self) -> Scheme:
        scheme = self.find_scheme(self.active_period)
        if scheme is None:
            scheme = self.activate(self.active_period)
        return scheme

    @property
    def primary(self) -> Timeframe:
        return self.scheme.primary

    @property
    def period_seconds(self) -> int:
        """当前主周期的秒数（只读；要换周期请用 :meth:`activate`）。"""
        return int(self.active_period)

    @property
    def overlays(self) -> list[Timeframe]:
        return self.scheme.sorted_overlays()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "alignment": self.alignment,
            "sessions": [s.to_dict() for s in self.sessions],
            "weekdays": list(self.weekdays),
            "active_period": self.active_period,
            "schemes": [s.to_dict() for s in self.schemes],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, legacy_style: dict[str, Any] | None = None) -> "Profile":
        data = data or {}
        sessions = [TradingSession.from_dict(s) for s in data.get("sessions") or []]
        if not sessions:
            sessions = [TradingSession(570, 690), TradingSession(780, 900)]
        weekdays = [int(d) for d in data.get("weekdays", [0, 1, 2, 3, 4]) if 0 <= int(d) <= 6]

        schemes_raw = data.get("schemes")
        if isinstance(schemes_raw, list) and schemes_raw:
            schemes = [Scheme.from_dict(s) for s in schemes_raw]
            active = int(data.get("active_period", schemes[0].period_seconds))
        else:
            # v1 迁移：单个 period_seconds + reminders（颜色当时存在全局 appearance 里）
            period = int(data.get("period_seconds", 300))
            reminders_raw = data.get("reminders")
            reminders = (
                [ReminderRule.from_dict(r) for r in reminders_raw]
                if isinstance(reminders_raw, list)
                else default_reminder_rules()
            )
            style = dict(legacy_style or {})
            primary = Timeframe(period_seconds=period, reminders=reminders, **{
                k: v for k, v in style.items()
                if k in ("fg_color", "bg_color", "transparent_bg")
            })
            schemes = [Scheme(primary=primary)]
            active = period

        profile = cls(
            id=str(data.get("id") or new_id("profile")),
            name=str(data.get("name") or "未命名配置"),
            alignment=str(data.get("alignment", ALIGNMENT_SESSION)),
            sessions=sessions,
            weekdays=weekdays,
            schemes=schemes,
            active_period=active,
        )
        if profile.find_scheme(active) is None:
            profile.active_period = profile.schemes[0].period_seconds
        for scheme in profile.schemes:
            clamp_thresholds(scheme)
        return profile

    def copy(self, *, rename: str | None = None, new_id_flag: bool = True) -> "Profile":
        clone = Profile.from_dict(self.to_dict())
        if new_id_flag:
            clone.id = new_id("profile")
            for scheme in clone.schemes:
                for timeframe in [scheme.primary, *scheme.overlays]:
                    timeframe.reminders = [
                        ReminderRule.from_dict({**r.to_dict(), "id": new_id("rem")})
                        for r in timeframe.reminders
                    ]
        if rename is not None:
            clone.name = rename
        return clone


@dataclass
class Appearance:
    """角标的物理外观（跨品种、跨周期共享）。

    颜色不在这里——每个周期各有自己的颜色，见 :class:`Timeframe`。
    """

    font_family: str = "Bahnschrift"  # 笔画干净、数字辨识度高；缺失时自动回退
    font_size: int = 32
    font_weight: str = "normal"
    opacity: float = 0.90
    padding_x: int = 16
    padding_y: int = 4
    width: int = 0  # 0 = 按字体自动
    height: int = 0
    corner_radius: int = 10  # 0 = 直角（更锐利，无边缘杂色）
    idle_text: str = "--:--"  # 非交易时段只换文案，颜色沿用主周期的颜色
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
        raw_appearance = data.get("appearance") or {}
        # v1 的颜色存在全局 appearance 里；迁移时塞给各配置的主周期
        legacy_style = {
            "fg_color": raw_appearance.get("fg_color"),
            "bg_color": raw_appearance.get("bg_color"),
            "transparent_bg": raw_appearance.get("transparent_bg"),
        }
        legacy_style = {k: v for k, v in legacy_style.items() if v is not None}
        profiles = [Profile.from_dict(p, legacy_style=legacy_style) for p in data.get("profiles") or []]
        cfg = cls(
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
            active_profile_id=str(data.get("active_profile_id") or ""),
            profiles=profiles,
            appearance=Appearance.from_dict(raw_appearance),
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
        alignment=ALIGNMENT_SESSION,
        sessions=[TradingSession(570, 690), TradingSession(780, 900)],
        weekdays=[0, 1, 2, 3, 4],
        schemes=[default_scheme(300)],
        active_period=300,
    )
    futures = Profile(
        name="期货（日盘+夜盘）",
        alignment=ALIGNMENT_SESSION,
        sessions=[
            TradingSession(540, 615),   # 09:00–10:15
            TradingSession(630, 690),   # 10:30–11:30
            TradingSession(810, 900),   # 13:30–15:00
            TradingSession(1260, 1380), # 21:00–23:00
        ],
        weekdays=[0, 1, 2, 3, 4],
        schemes=[default_scheme(300)],
        active_period=300,
    )
    all_day = Profile(
        name="全天连续（整点对齐）",
        alignment=ALIGNMENT_CLOCK,
        sessions=[TradingSession(0, 1440)],
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        schemes=[default_scheme(900)],
        active_period=900,
    )
    return [ashare, futures, all_day]
