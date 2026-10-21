"""提醒调度（纯状态机，可单元测试）。

职责：把「引擎算出的剩余时间」翻译成「这一次刷新该不该响/该不该变色」。
关键约束（来自需求）：

* 同一根 K 线的同一个提醒节点，只触发一次；
* 去重键是「节点 id + 剩余秒数」：把节点的剩余秒数改了，等于换了个新节点，
  会在新阈值处重新触发一次（否则调完参数就再也不响了）；
* 刷新界面、切周期都不会重置已触发记录（只有换 K 线才重置）；
* 休眠唤醒 / 长时间卡顿后恢复，不补响历史节点，避免一次叫醒四声。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine import CountdownState
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


@dataclass
class TickResult:
    bar_changed: bool = False
    events: list[ReminderEvent] = field(default_factory=list)
    active_rules: list[ReminderRule] = field(default_factory=list)
    suppressed: int = 0

    @property
    def primary_rule(self) -> ReminderRule | None:
        """最紧急（阈值最小）的生效节点，用于决定角标样式。"""
        return self.active_rules[0] if self.active_rules else None


class ReminderScheduler:
    """按 K 线维度去重的提醒状态机。

    ``_fired_by_bar`` 保留最近若干根 K 线的已触发记录：
    系统时钟被回拨、或用户把时间调回上一根 K 线时，也不会重复响铃。
    """

    MAX_BAR_HISTORY = 12

    def __init__(self) -> None:
        self._bar_key: str | None = None
        self._fired_by_bar: dict[str, set[tuple[str, int]]] = {}
        self._suppress_next: bool = False
        self.last_remaining: float | None = None

    @property
    def _fired(self) -> set[tuple[str, int]]:
        """当前 K 线的已触发集合（自动创建）。"""
        if self._bar_key is None:
            return set()
        return self._fired_by_bar.setdefault(self._bar_key, set())

    # ---- 生命周期 ----

    def reset(self) -> None:
        self._bar_key = None
        self._fired_by_bar.clear()
        self._suppress_next = False
        self.last_remaining = None

    def suppress_current_bar(self) -> None:
        """休眠唤醒/恢复运行后调用：当前这根 K 线不再补响任何节点。"""
        self._suppress_next = True

    # ---- 每次刷新调用 ----

    def update(self, state: CountdownState) -> TickResult:
        result = TickResult()
        bar_key = state.bar_key

        if bar_key != self._bar_key:
            self._bar_key = bar_key
            result.bar_changed = True
            self._fired_by_bar.setdefault(bar_key, set())
            # 只保留最近若干根，避免长期运行内存增长
            if len(self._fired_by_bar) > self.MAX_BAR_HISTORY:
                for stale in list(self._fired_by_bar)[: -self.MAX_BAR_HISTORY]:
                    if stale != bar_key:
                        self._fired_by_bar.pop(stale, None)
            if self._suppress_next:
                # 刚恢复运行：吃掉当前区间内的节点，不补响
                self._suppress_next = False
                for rule in state.rules:
                    if rule.enabled and 0 < state.remaining_seconds <= rule.threshold_seconds:
                        self._fired.add(_rule_key(rule))
                        result.suppressed += 1

        self.last_remaining = state.remaining_seconds
        if not state.is_trading:
            result.active_rules = []
            return result

        rules = [r for r in state.rules if r.enabled and r.threshold_seconds > 0]
        remaining = state.remaining_seconds

        eligible = [
            r for r in rules if _rule_key(r) not in self._fired and 0 < remaining <= r.threshold_seconds
        ]
        # 生效区间按阈值从小到大（最紧急在前），供 UI 取样式
        active = sorted(
            [r for r in rules if 0 < remaining <= r.threshold_seconds],
            key=lambda r: r.threshold_seconds,
        )
        result.active_rules = active

        if eligible:
            # 一次刷新最多只响一个节点：取「最贴身的那个」，
            # 其余更宽松的节点标记为已处理，避免启动/唤醒瞬间连响。
            chosen = min(eligible, key=lambda r: r.threshold_seconds)
            for rule in eligible:
                self._fired.add(_rule_key(rule))
            result.events.append(
                ReminderEvent(rule=chosen, bar_key=bar_key, remaining_seconds=remaining)
            )
        return result


def flash_phase(elapsed: float, hz: float) -> bool:
    """闪烁相位：True 表示处于高亮半周期。纯时间函数，不累积误差。"""
    if hz <= 0:
        return True
    period = 1.0 / hz
    return (elapsed % period) < (period / 2.0)
