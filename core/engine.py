"""倒计时核心引擎（纯计算，无 UI、无副作用，可单元测试）。

对外只暴露两个函数：

* :func:`validate_profile` —— 配置合法性检查，返回人类可读的错误列表。
* :func:`compute_state`    —— 输入「当前时间 + 配置」，输出完整倒计时状态。

关键设计
--------
1. 每次调用都从「当前系统时间」重新推导，不累加、不依赖每秒减一，
   因此休眠唤醒、系统时间被修改、卡顿后都能立刻回到正确状态。
2. 交易时段之间是硬边界：周期只在单个时段内部切分，绝不跨午休/隔夜。
3. 时段剩余不足一个周期时，最后一根 K 线被截断到时段结束（规则统一、可预测）。
4. 多周期：主周期和所有叠加周期**共用同一套交易时段与对齐方式**，
   所以它们的 K 线边界必然同时落在时段起点上，叠加周期的结束时刻总是
   与某一根主周期 K 线的结束时刻重合——这是「接管」规则成立的前提。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from .alignment import get_alignment
from .models import (
    ALIGNMENT_LABELS,
    MAX_OVERLAYS,
    MAX_PERIOD_SECONDS,
    MIN_PERIOD_SECONDS,
    Profile,
    ReminderRule,
    Scheme,
    Timeframe,
    TradingSession,
)

STATUS_TRADING = "trading"
STATUS_IDLE = "idle"
STATUS_ERROR = "error"

REASON_TRADING = "trading"
REASON_PRE_MARKET = "pre_market"
REASON_BREAK = "break"
REASON_AFTER_CLOSE = "after_close"
REASON_NON_TRADING_DAY = "non_trading_day"
REASON_ERROR = "invalid_profile"

REASON_LABELS = {
    REASON_TRADING: "交易中",
    REASON_PRE_MARKET: "未开盘",
    REASON_BREAK: "休市间歇",
    REASON_AFTER_CLOSE: "已收盘",
    REASON_NON_TRADING_DAY: "非交易日",
    REASON_ERROR: "配置错误",
}

#: 向前搜索下一个交易日的最大天数
_SEARCH_DAYS = 21


@dataclass(frozen=True)
class BarState:
    """单个周期在某一时刻的倒计时状态。"""

    timeframe: Timeframe
    is_primary: bool
    remaining_seconds: float
    bar_start: datetime
    bar_end: datetime
    bar_index: int
    session_index: int
    session_start: datetime
    session_end: datetime

    @property
    def period_seconds(self) -> int:
        return int(self.timeframe.period_seconds)

    @property
    def bar_key(self) -> str:
        """唯一标识「这个周期的当前这根 K 线」，用于提醒去重。"""
        return f"{self.period_seconds}:{self.bar_start.isoformat()}:{self.bar_end.isoformat()}"

    @property
    def rules(self) -> tuple[ReminderRule, ...]:
        return tuple(self.timeframe.reminders)

    @property
    def zone_ids(self) -> tuple[str, ...]:
        """当前已进入区间的提醒节点 id（按阈值由宽到严）。"""
        return tuple(
            rule.id
            for rule in sorted(self.timeframe.reminders, key=lambda r: r.threshold_seconds, reverse=True)
            if rule.enabled and 0 < self.remaining_seconds <= rule.threshold_seconds
        )


@dataclass(frozen=True)
class CountdownState:
    """引擎输出。所有字段都是不可变值，便于在 UI 层安全缓存/比较。"""

    status: str
    reason: str
    profile_id: str
    profile_name: str
    alignment: str
    now: datetime
    primary: BarState | None = None
    overlays: tuple[BarState, ...] = ()
    next_boundary: datetime | None = None
    next_session_start: datetime | None = None
    session_index: int = -1
    errors: tuple[str, ...] = ()
    message: str = ""

    # ---- 状态 ----

    @property
    def is_trading(self) -> bool:
        return self.status == STATUS_TRADING

    @property
    def is_error(self) -> bool:
        return self.status == STATUS_ERROR

    @property
    def status_label(self) -> str:
        return REASON_LABELS.get(self.reason, self.reason)

    # ---- 主周期便捷访问（保持旧字段语义）----

    @property
    def period_seconds(self) -> int:
        return self.primary.period_seconds if self.primary else 0

    @property
    def remaining_seconds(self) -> float:
        return self.primary.remaining_seconds if self.primary else 0.0

    @property
    def bar_start(self) -> datetime | None:
        return self.primary.bar_start if self.primary else None

    @property
    def bar_end(self) -> datetime | None:
        return self.primary.bar_end if self.primary else None

    @property
    def bar_index(self) -> int:
        return self.primary.bar_index if self.primary else -1

    @property
    def rules(self) -> tuple[ReminderRule, ...]:
        return self.primary.rules if self.primary else ()

    @property
    def reminder_zone_ids(self) -> tuple[str, ...]:
        return self.primary.zone_ids if self.primary else ()

    @property
    def bar_key(self) -> str:
        """主周期的 K 线标识（提醒去重按周期分别记，见 :meth:`all_bars`）。"""
        if self.primary is None:
            return f"{self.profile_id}:idle:{self.reason}"
        return self.primary.bar_key

    # ---- 多周期 ----

    def all_bars(self) -> tuple[BarState, ...]:
        if self.primary is None:
            return ()
        return (self.primary, *self.overlays)

    @property
    def takeover(self) -> BarState | None:
        """当前接管角标的叠加周期；None 表示由主周期显示。

        规则：叠加周期的剩余时间 ≤ 主周期长度时，说明它进入了「最后一根主周期 K 线」，
        此时由其中**周期最大**的那个接管；其余周期一律静默（不出样式也不出声）。
        """
        if self.primary is None:
            return None
        limit = self.primary.period_seconds
        candidates = [bar for bar in self.overlays if 0 < bar.remaining_seconds <= limit]
        if not candidates:
            return None
        return max(candidates, key=lambda bar: bar.period_seconds)

    @property
    def style_bar(self) -> BarState | None:
        """决定角标颜色的周期（接管者优先）。"""
        return self.takeover or self.primary


# --------------------------------------------------------------------------
# 配置校验
# --------------------------------------------------------------------------


def _period_label(seconds: int) -> str:
    minutes, rest = divmod(int(seconds), 60)
    if rest:
        return f"{minutes} 分 {rest} 秒"
    return f"{minutes} 分钟"


def validate_scheme(scheme: Scheme) -> list[str]:
    """单个周期方案的合法性。"""
    errors: list[str] = []
    primary = scheme.primary
    period = int(primary.period_seconds)

    if period < MIN_PERIOD_SECONDS:
        errors.append(f"主周期过短：{period} 秒（最少 {MIN_PERIOD_SECONDS} 秒）")
    elif period > MAX_PERIOD_SECONDS:
        errors.append(f"主周期过长：{period} 秒（最多 {MAX_PERIOD_SECONDS // 3600} 小时）")

    if len(scheme.overlays) > MAX_OVERLAYS:
        errors.append(f"叠加周期过多：{len(scheme.overlays)} 个（最多 {MAX_OVERLAYS} 个）")

    seen = {period}
    for timeframe in scheme.overlays:
        other = int(timeframe.period_seconds)
        if other <= period:
            errors.append(f"叠加周期 {_period_label(other)} 必须大于主周期 {_period_label(period)}")
        if other in seen:
            errors.append(f"周期重复：{_period_label(other)}")
        seen.add(other)

    # 提醒阈值必须严格小于主周期长度（含主周期自己的节点）
    for timeframe in (primary, *scheme.overlays):
        for rule in timeframe.reminders:
            if not rule.enabled:
                continue
            if rule.threshold_seconds >= period:
                errors.append(
                    f"{_period_label(timeframe.period_seconds)} 周期的提醒节点 "
                    f"{rule.threshold_seconds} 秒超过主周期 {_period_label(period)}，须更小"
                )
    return errors


def validate_profile(profile: Profile) -> list[str]:
    """返回错误列表；空列表表示配置可用。"""
    errors: list[str] = []
    if profile is None:
        return ["配置不存在"]

    if not str(profile.name).strip():
        errors.append("配置名称不能为空")

    if profile.alignment not in ALIGNMENT_LABELS:
        errors.append(f"未知的周期对齐规则：{profile.alignment}")

    if not profile.weekdays:
        errors.append("至少要选择一个交易日（星期）")

    enabled = [s for s in profile.sessions if s.enabled]
    if not enabled:
        errors.append("至少要启用一个交易时段")

    for session in profile.sessions:
        if not (0 <= session.start_minute <= 1440) or not (0 <= session.end_minute <= 1440):
            errors.append(f"时段 {session.label} 时间超出 00:00–24:00")
        elif session.start_minute >= session.end_minute:
            errors.append(f"时段 {session.label} 开始时间不早于结束时间")

    # 重叠检查（只针对启用中的时段）
    ordered = sorted(enabled, key=lambda s: (s.start_minute, s.end_minute))
    for prev, cur in zip(ordered, ordered[1:]):
        if cur.start_minute < prev.end_minute:
            errors.append(f"时段重叠：{prev.label} 与 {cur.label}")
        elif cur.start_minute == prev.end_minute and prev.end_minute != 0:
            pass  # 首尾相接（如 09:30-11:30 + 11:30-15:00）视为合法

    for session in enabled:
        length = (session.end_minute - session.start_minute) * 60
        if length < 1:
            errors.append(f"时段 {session.label} 长度不足 1 分钟")

    if not profile.schemes:
        errors.append("至少要有一个周期方案")
    else:
        active = profile.scheme
        errors.extend(validate_scheme(active))
    return errors


# --------------------------------------------------------------------------
# 内部工具
# --------------------------------------------------------------------------


def _session_bounds(day: date, session: TradingSession) -> tuple[datetime, datetime]:
    base = datetime.combine(day, time(0, 0))
    return (
        base + timedelta(minutes=session.start_minute),
        base + timedelta(minutes=session.end_minute),
    )


def _day_sessions(profile: Profile, day: date) -> list[tuple[datetime, datetime, int, TradingSession]]:
    """当天有效的交易时段，按开始时间排序；含原始索引以便回查。"""
    if day.weekday() not in profile.weekdays:
        return []
    items: list[tuple[datetime, datetime, int, TradingSession]] = []
    for index, session in enumerate(sorted(profile.sessions, key=lambda s: s.start_minute)):
        if not session.enabled:
            continue
        start, end = _session_bounds(day, session)
        if end <= start:
            continue
        items.append((start, end, index, session))
    return items


def _next_open(profile: Profile, now: datetime) -> tuple[datetime | None, int]:
    """找到 now 之后最近一个交易时段的开始时刻。"""
    for offset in range(0, _SEARCH_DAYS + 1):
        day = now.date() + timedelta(days=offset)
        for start, _end, index, _session in _day_sessions(profile, day):
            if start > now:
                return start, index
    return None, -1


def _idle_state(
    *,
    now: datetime,
    profile: Profile,
    reason: str,
    message: str = "",
    next_session_start: datetime | None = None,
    session_index: int = -1,
) -> CountdownState:
    if next_session_start is None:
        next_session_start, session_index = _next_open(profile, now)
    return CountdownState(
        status=STATUS_IDLE,
        reason=reason,
        profile_id=profile.id,
        profile_name=profile.name,
        alignment=profile.alignment,
        now=now,
        next_boundary=next_session_start,
        next_session_start=next_session_start,
        session_index=session_index,
        message=message or REASON_LABELS.get(reason, reason),
    )


def _bar_state(
    timeframe: Timeframe,
    *,
    is_primary: bool,
    now: datetime,
    start: datetime,
    end: datetime,
    session_index: int,
    alignment_name: str,
) -> BarState:
    alignment = get_alignment(alignment_name)
    bounds = alignment.bar_bounds(now, start, end, int(timeframe.period_seconds))
    return BarState(
        timeframe=timeframe,
        is_primary=is_primary,
        remaining_seconds=max(0.0, (bounds.end - now).total_seconds()),
        bar_start=bounds.start,
        bar_end=bounds.end,
        bar_index=bounds.index,
        session_index=session_index,
        session_start=start,
        session_end=end,
    )


# --------------------------------------------------------------------------
# 主计算
# --------------------------------------------------------------------------


def compute_state(now: datetime, profile: Profile) -> CountdownState:
    """根据当前时间与配置推导倒计时状态（主周期 + 所有叠加周期）。"""
    errors = validate_profile(profile)
    if errors:
        return CountdownState(
            status=STATUS_ERROR,
            reason=REASON_ERROR,
            profile_id=getattr(profile, "id", ""),
            profile_name=getattr(profile, "name", "?"),
            alignment=getattr(profile, "alignment", ""),
            now=now,
            errors=tuple(errors),
            message=errors[0],
        )

    scheme = profile.scheme
    alignment = profile.alignment

    today_sessions = _day_sessions(profile, now.date())
    if not today_sessions:
        return _idle_state(now=now, profile=profile, reason=REASON_NON_TRADING_DAY)

    first_start = today_sessions[0][0]
    last_end = today_sessions[-1][1]

    for start, end, index, _session in today_sessions:
        if start <= now < end:
            primary = _bar_state(
                scheme.primary, is_primary=True, now=now,
                start=start, end=end, session_index=index, alignment_name=alignment,
            )
            overlays = tuple(
                _bar_state(
                    timeframe, is_primary=False, now=now,
                    start=start, end=end, session_index=index, alignment_name=alignment,
                )
                for timeframe in scheme.sorted_overlays()
            )
            # 若当前是时段最后一根 K 线，下一根 K 线在下一个交易时段开始
            next_open, _ = _next_open(profile, primary.bar_end)
            return CountdownState(
                status=STATUS_TRADING,
                reason=REASON_TRADING,
                profile_id=profile.id,
                profile_name=profile.name,
                alignment=alignment,
                now=now,
                primary=primary,
                overlays=overlays,
                next_boundary=primary.bar_end,
                next_session_start=next_open,
                session_index=index,
                message="交易中",
            )

        if now < start:
            # 当天还有时段没开始：之前时段已全部结束，因此这是休市间歇/开盘前
            reason = REASON_PRE_MARKET if start == first_start else REASON_BREAK
            return _idle_state(
                now=now,
                profile=profile,
                reason=reason,
                next_session_start=start,
                session_index=index,
            )

    # 已经过了当天最后一个时段
    if now >= last_end:
        return _idle_state(now=now, profile=profile, reason=REASON_AFTER_CLOSE)

    return _idle_state(now=now, profile=profile, reason=REASON_BREAK)


def describe_state(state: CountdownState) -> str:
    """给日志/测试用的单行描述。"""
    if state.status == STATUS_TRADING and state.bar_end:
        extra = ""
        if state.overlays:
            extra = f" 叠加{len(state.overlays)}个"
        if state.takeover is not None:
            extra += f" 接管{_period_label(state.takeover.period_seconds)}"
        return (
            f"[{state.profile_name}] 交易中 周期{state.period_seconds}s{extra} "
            f"K线 {state.bar_start:%H:%M:%S}→{state.bar_end:%H:%M:%S} "
            f"剩余 {state.remaining_seconds:.1f}s"
        )
    if state.status == STATUS_ERROR:
        return f"[{state.profile_name}] 配置错误: {'; '.join(state.errors)}"
    nxt = f"{state.next_boundary:%m-%d %H:%M}" if state.next_boundary else "无"
    return f"[{state.profile_name}] {state.status_label} 下次开盘 {nxt}"
