"""提醒调度（纯状态机，可单元测试）。

职责：把「引擎算出的剩余时间」翻译成「这一次刷新该不该响 / 该用什么样式」。

多周期规则
----------
* 每个周期各自去重：去重键是「周期 + 节点 id + 剩余秒数」，
  同一根 30 分钟 K 线和同一根 5 分钟 K 线互不干扰；
* 接管期间（:attr:`CountdownState.takeover` 不为 None），只有接管周期的提醒生效，
  其余周期——包括主周期——的节点被**静默**：不出样式、不出声音，并且直接记为已触发，
  避免接管结束后补响；
* 改了节点的剩余秒数等于换了个新节点，会在新阈值处重新触发（调参数测试时不会「只闪不响」）；
* 休眠唤醒 / 长时间卡顿后恢复不补响历史节点。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import BarState, CountdownState
from .models import ReminderRule


def _rule_key(rule: ReminderRule) -> tuple[str, int]:
    """去重键：同一节点改了剩余秒数就算另一个节点。"""
    return (rule.id, int(rule.threshold_seconds))


@dataclass(frozen=True)
class ReminderEvent:
    """一次提醒触发。"""

    rule: ReminderRule
    bar_key: str
    remaining_seconds: float
    period_seconds: int = 0


@dataclass
class TickResult:
    bar_changed: bool = False
    events: list[ReminderEvent] = field(default_factory=list)
    active_rules: list[ReminderRule] = field(default_factory=list)  # 当前生效区间内的节点
    style_bar: BarState | None = None       # 决定角标颜色的周期（接管者优先）
    style_rule: ReminderRule | None = None  # 该周期当前生效的节点（None = 用周期自己的颜色）
    suppressed: int = 0

    @property
    def primary_rule(self) -> ReminderRule | None:
        """兼容旧名字：当前应显示样式的规则。"""
        return self.style_rule


class ReminderScheduler:
    """按「周期 + K 线」维度去重的提醒状态机。"""

    MAX_BAR_HISTORY = 64

    def __init__(self) -> None:
        self._current: dict[int, str] = {}                  # 周期 -> 当前 bar_key
        self._fired: dict[str, set[tuple[str, int]]] = {}   # bar_key -> 已触发节点
        self._suppress_next: bool = False

    # ---- 生命周期 ----

    def reset(self) -> None:
        self._current.clear()
        self._fired.clear()
        self._suppress_next = False

    def suppress_current_bar(self) -> None:
        """休眠唤醒/恢复运行后调用：当前这几根 K 线不再补响任何节点。"""
        self._suppress_next = True

    def _prune(self) -> None:
        if len(self._fired) <= self.MAX_BAR_HISTORY:
            return
        keep = set(self._current.values())
        for key in list(self._fired):
            if key not in keep and len(self._fired) > self.MAX_BAR_HISTORY:
                self._fired.pop(key, None)

    # ---- 每次刷新调用 ----

    def update(self, state: CountdownState) -> TickResult:
        result = TickResult()
        if not state.is_trading or state.primary is None:
            return result

        bars = state.all_bars()
        takeover = state.takeover

        # 1) 换 K 线：每个周期各自检测，各自重置
        for bar in bars:
            if self._current.get(bar.period_seconds) != bar.bar_key:
                self._current[bar.period_seconds] = bar.bar_key
                result.bar_changed = True
                if self._suppress_next:
                    marks = [
                        _rule_key(rule)
                        for rule in bar.timeframe.reminders
                        if rule.enabled and 0 < bar.remaining_seconds <= rule.threshold_seconds
                    ]
                    self._fired.setdefault(bar.bar_key, set()).update(marks)
                    result.suppressed += len(marks)
        if result.bar_changed:
            self._suppress_next = False
            self._prune()

        # 2) 逐周期判定
        for bar in bars:
            fired = self._fired.setdefault(bar.bar_key, set())
            rules = [r for r in bar.timeframe.reminders if r.enabled and r.threshold_seconds > 0]
            active = sorted(
                [r for r in rules if 0 < bar.remaining_seconds <= r.threshold_seconds],
                key=lambda r: r.threshold_seconds,
            )
            eligible = [r for r in active if _rule_key(r) not in fired]

            # 接管期间：非接管周期的提醒全部静默（样式和声音都不出）
            if takeover is not None and bar is not takeover:
                for rule in eligible:
                    fired.add(_rule_key(rule))
                    result.suppressed += 1
                continue

            if eligible:
                # 一次刷新最多只响一个节点：取「最贴身的那个」
                chosen = min(eligible, key=lambda r: r.threshold_seconds)
                for rule in eligible:
                    fired.add(_rule_key(rule))
                result.events.append(
                    ReminderEvent(
                        rule=chosen,
                        bar_key=bar.bar_key,
                        remaining_seconds=bar.remaining_seconds,
                        period_seconds=bar.period_seconds,
                    )
                )

            if bar is state.style_bar:
                result.style_bar = bar
                result.active_rules = active
                result.style_rule = min(active, key=lambda r: r.threshold_seconds) if active else None

        return result


def flash_phase(elapsed: float, hz: float) -> bool:
    """闪烁相位：True 表示处于高亮半周期。纯时间函数，不累积误差。"""
    if hz <= 0:
        return True
    period = 1.0 / hz
    return (elapsed % period) < (period / 2.0)
