"""端到端集成测试：用「模拟时间轴」逐秒驱动引擎 + 提醒调度。

覆盖验收测试：
* A 标准时钟对齐（09:32:15 → 02:45，09:35:00 翻转到 05:00）
* B 下午盘 / 午休不跨界
* C 周期切换立即重算
* E 提醒按序触发、不重复、换 K 线重置、恢复不补响
* 跨日切换（收盘 → 次日开盘）
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engine import REASON_BREAK, STATUS_TRADING, compute_state  # noqa: E402
from core.models import Profile, ReminderRule, TradingSession  # noqa: E402
from core.reminders import ReminderScheduler  # noqa: E402

A_STOCK = Profile(
    id="p_ashare",
    name="A 股",
    period_seconds=300,
    sessions=[TradingSession(570, 690), TradingSession(780, 900)],
    weekdays=[0, 1, 2, 3, 4, 5, 6],
)

WEDNESDAY = datetime(2026, 10, 21)


def run_timeline(start: datetime, end: datetime, profile: Profile, step_seconds: float = 1.0):
    """按固定步长推进时间，收集每一步的状态与提醒事件。"""
    scheduler = ReminderScheduler()
    fires: list[tuple[datetime, int]] = []
    bars: list[tuple[datetime, datetime]] = []
    current = start
    previous_bar = None
    while current <= end:
        state = compute_state(current, profile)
        result = scheduler.update(state)
        if result.bar_changed and previous_bar is not None:
            bars.append(previous_bar)
        if state.bar_start is not None and state.status == STATUS_TRADING:
            previous_bar = (state.bar_start, state.bar_end)
        for event in result.events:
            fires.append((current, event.rule.threshold_seconds))
        current += timedelta(seconds=step_seconds)
    return fires, bars


class TestAcceptanceATimeline(unittest.TestCase):
    def test_countdown_walks_down_and_flips(self):
        start = WEDNESDAY.replace(hour=9, minute=32, second=15)
        end = WEDNESDAY.replace(hour=9, minute=35, second=0)
        fires, _bars = run_timeline(start, end, A_STOCK)

        # 起始：02:45
        first = compute_state(start, A_STOCK)
        self.assertEqual(first.remaining_seconds, 165)

        # 跨过 09:35:00 边界后变成 05:00
        flipped = compute_state(end, A_STOCK)
        self.assertEqual(flipped.bar_start, WEDNESDAY.replace(hour=9, minute=35))
        self.assertEqual(flipped.remaining_seconds, 300)

        # 每秒减一（语义上）；逐秒采样最后一步差值应为 1
        step_a = compute_state(WEDNESDAY.replace(hour=9, minute=33, second=0), A_STOCK)
        step_b = compute_state(WEDNESDAY.replace(hour=9, minute=33, second=1), A_STOCK)
        self.assertEqual(step_a.remaining_seconds - step_b.remaining_seconds, 1.0)

    def test_reminders_fire_in_order_exactly_once(self):
        rules = [
            ReminderRule(id="r60", threshold_seconds=60),
            ReminderRule(id="r30", threshold_seconds=30),
            ReminderRule(id="r10", threshold_seconds=10),
            ReminderRule(id="r3", threshold_seconds=3),
        ]
        profile = Profile(
            id="p",
            name="t",
            period_seconds=300,
            sessions=[TradingSession(570, 690)],
            reminders=rules,
        )
        start = WEDNESDAY.replace(hour=9, minute=30, second=0)
        end = WEDNESDAY.replace(hour=10, minute=0, second=0)
        fires, _bars = run_timeline(start, end, profile, step_seconds=0.5)

        thresholds = [threshold for _t, threshold in fires]
        # 08:30–10:00 共 6 根 5 分钟 K 线，每根四个节点各响一次
        self.assertEqual(thresholds, [60, 30, 10, 3] * 6)
        # 触发时刻与阈值一致（允许 0.5s 步长误差）
        for fire_time, threshold in fires:
            bar_end = compute_state(fire_time, profile).bar_end
            self.assertLessEqual(bar_end - fire_time, timedelta(seconds=threshold + 0.51))


class TestAcceptanceBTimeline(unittest.TestCase):
    def test_lunch_break_not_crossed(self):
        start = WEDNESDAY.replace(hour=11, minute=29, second=0)
        end = WEDNESDAY.replace(hour=13, minute=3, second=0)
        fires, _bars = run_timeline(start, end, A_STOCK)

        # 11:30 前最后一根只有 60 秒
        near_close = compute_state(WEDNESDAY.replace(hour=11, minute=29, second=30), A_STOCK)
        self.assertEqual(near_close.remaining_seconds, 30)
        self.assertEqual(near_close.bar_end, WEDNESDAY.replace(hour=11, minute=30))

        # 11:30:00 立刻非交易
        break_state = compute_state(WEDNESDAY.replace(hour=11, minute=30, second=0), A_STOCK)
        self.assertEqual(break_state.reason, REASON_BREAK)
        self.assertFalse(break_state.is_trading)

        # 13:00 新开始，剩余 03:00
        afternoon = compute_state(WEDNESDAY.replace(hour=13, minute=2, second=0), A_STOCK)
        self.assertEqual(afternoon.remaining_seconds, 180)

        # 午休 90 分钟内不产生任何 K 线边界提醒
        lunch_fires = [t for t, threshold in fires if WEDNESDAY.replace(hour=11, minute=30) <= t < WEDNESDAY.replace(hour=13, minute=0)]
        self.assertEqual(lunch_fires, [])


class TestAcceptanceCTimeline(unittest.TestCase):
    def test_period_switch_recomputes(self):
        switched = A_STOCK.copy()  # 拷贝，避免污染其他测试的共享对象
        switched.period_seconds = 900  # 模拟「切换到 15 分钟」
        state = compute_state(WEDNESDAY.replace(hour=10, minute=7, second=23), switched)
        self.assertEqual(state.remaining_seconds, 457)
        # 偏置：不得从 900 开始倒计时
        self.assertLess(state.remaining_seconds, 900)

    def test_full_15min_boundaries(self):
        profile = Profile(
            id="p", name="t", period_seconds=900,
            sessions=[TradingSession(570, 690), TradingSession(780, 900)],
        )
        # 15 分钟边界应与整点/半点对齐：09:30 / 09:45 / 10:00 / 10:15
        cases = [
            ((9, 30, 0), (9, 45, 0)),
            ((9, 44, 59), (9, 45, 0)),
            ((9, 45, 0), (10, 0, 0)),
            ((10, 14, 59), (10, 15, 0)),
            ((11, 15, 0), (11, 30, 0)),
            ((11, 29, 59), (11, 30, 0)),
        ]
        for (now_h, now_m, now_s), (end_h, end_m, end_s) in cases:
            now = WEDNESDAY.replace(hour=now_h, minute=now_m, second=now_s)
            state = compute_state(now, profile)
            self.assertEqual(state.status, STATUS_TRADING, f"{now.time()} 应在交易中")
            self.assertEqual(
                state.bar_end,
                WEDNESDAY.replace(hour=end_h, minute=end_m, second=end_s),
                f"{now.time()} 的周期边界",
            )


class TestAcceptanceEDtimeline(unittest.TestCase):
    def test_resume_does_not_replay(self):
        scheduler = ReminderScheduler()
        now = WEDNESDAY.replace(hour=9, minute=34, second=20)  # 剩余 40s，落在 60s 区间内
        state = compute_state(now, A_STOCK)

        # 正常情况：40s 会触发 60s 节点
        self.assertEqual([e.rule.threshold_seconds for e in scheduler.update(state).events], [60])

        # 模拟休眠唤醒：新调度器 + suppress，不应补响
        fresh = ReminderScheduler()
        fresh.suppress_current_bar()
        resumed = fresh.update(state)
        self.assertEqual(resumed.events, [])
        self.assertEqual(resumed.suppressed, 1)

    def test_bar_change_resets_and_new_bar_can_fire_again(self):
        scheduler = ReminderScheduler()
        bar1 = compute_state(WEDNESDAY.replace(hour=9, minute=34, second=30), A_STOCK)   # 剩 30s
        scheduler.update(bar1)  # 触发 30
        bar2 = compute_state(WEDNESDAY.replace(hour=9, minute=39, second=30), A_STOCK)   # 下一根剩 30s
        result = scheduler.update(bar2)
        self.assertTrue(result.bar_changed)
        self.assertEqual([e.rule.threshold_seconds for e in result.events], [30])

    def test_overnight_transition(self):
        monday_close = compute_state(datetime(2026, 10, 19, 15, 0, 0), A_STOCK)  # 周一收盘
        self.assertIsNotNone(monday_close.next_boundary)
        self.assertEqual((monday_close.next_boundary.date() - datetime(2026, 10, 19).date()).days, 1)
        tuesday_open = compute_state(datetime(2026, 10, 20, 9, 29, 59), A_STOCK)
        self.assertFalse(tuesday_open.is_trading)
        self.assertEqual(tuesday_open.reason, "pre_market")


if __name__ == "__main__":
    unittest.main(verbosity=2)
