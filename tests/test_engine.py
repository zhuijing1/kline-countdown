"""周期计算引擎测试：对应需求文档「五、验收测试」A / B / C + 边界情况。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engine import (  # noqa: E402
    REASON_AFTER_CLOSE,
    REASON_BREAK,
    REASON_NON_TRADING_DAY,
    REASON_PRE_MARKET,
    REASON_TRADING,
    STATUS_ERROR,
    STATUS_IDLE,
    STATUS_TRADING,
    compute_state,
    validate_profile,
)
from core.models import (  # noqa: E402
    ALIGNMENT_CLOCK,
    ALIGNMENT_SESSION,
    Profile,
    TradingSession,
)

WED = datetime(2026, 10, 21)  # 星期三
SUN = datetime(2026, 10, 18)  # 星期日


def ashare(period=300, alignment=ALIGNMENT_SESSION, weekdays=(0, 1, 2, 3, 4)) -> Profile:
    return Profile(
        id="p_ashare",
        name="A 股",
        period_seconds=period,
        alignment=alignment,
        sessions=[TradingSession(9 * 60 + 30, 11 * 60 + 30), TradingSession(13 * 60, 15 * 60)],
        weekdays=list(weekdays),
    )


def custom(sessions, period=300, alignment=ALIGNMENT_SESSION, weekdays=(0, 1, 2, 3, 4, 5, 6)) -> Profile:
    return Profile(
        id="p_custom",
        name="自定义",
        period_seconds=period,
        alignment=alignment,
        sessions=[TradingSession(start, end, enabled=True) for start, end in sessions],
        weekdays=list(weekdays),
    )


class TestAcceptanceA(unittest.TestCase):
    """测试 A：标准时钟对齐（09:30–11:30，5 分钟）。"""

    def test_093215(self):
        state = compute_state(WED.replace(hour=9, minute=32, second=15), ashare())
        self.assertEqual(state.status, STATUS_TRADING)
        self.assertEqual(state.remaining_seconds, 165)  # 02:45
        self.assertEqual(state.bar_start, WED.replace(hour=9, minute=30))
        self.assertEqual(state.bar_end, WED.replace(hour=9, minute=35))

    def test_093459(self):
        state = compute_state(WED.replace(hour=9, minute=34, second=59), ashare())
        self.assertEqual(state.reason, REASON_TRADING)
        self.assertEqual(round(state.remaining_seconds), 1)

    def test_boundary_rolls_to_next_bar(self):
        state = compute_state(WED.replace(hour=9, minute=35, second=0), ashare())
        self.assertEqual(state.remaining_seconds, 300)
        self.assertEqual(state.bar_start, WED.replace(hour=9, minute=35))
        self.assertEqual(state.bar_end, WED.replace(hour=9, minute=40))

    def test_midnight_boundary_pre_open(self):
        state = compute_state(WED.replace(hour=8, minute=0), ashare())
        self.assertEqual((state.status, state.reason), (STATUS_IDLE, REASON_PRE_MARKET))
        self.assertEqual(state.next_boundary, WED.replace(hour=9, minute=30))


class TestAcceptanceB(unittest.TestCase):
    """测试 B：下午盘，不跨午休。"""

    def test_112900_no_cross_break(self):
        state = compute_state(WED.replace(hour=11, minute=29, second=0), ashare())
        self.assertEqual(state.status, STATUS_TRADING)
        self.assertEqual(state.remaining_seconds, 60)
        self.assertEqual(state.bar_end, WED.replace(hour=11, minute=30))

    def test_113000_is_break(self):
        state = compute_state(WED.replace(hour=11, minute=30, second=0), ashare())
        self.assertEqual((state.status, state.reason), (STATUS_IDLE, REASON_BREAK))
        self.assertEqual(state.next_boundary, WED.replace(hour=13, minute=0))

    def test_130000_first_afternoon_bar(self):
        state = compute_state(WED.replace(hour=13, minute=0, second=0), ashare())
        self.assertEqual(state.status, STATUS_TRADING)
        self.assertEqual(state.bar_start, WED.replace(hour=13, minute=0))
        self.assertEqual(state.bar_end, WED.replace(hour=13, minute=5))
        self.assertEqual(state.remaining_seconds, 300)

    def test_130200(self):
        state = compute_state(WED.replace(hour=13, minute=2, second=0), ashare())
        self.assertEqual(state.remaining_seconds, 180)

    def test_150000_closed(self):
        state = compute_state(WED.replace(hour=15, minute=0), ashare())
        self.assertEqual((state.status, state.reason), (STATUS_IDLE, REASON_AFTER_CLOSE))
        self.assertIsNotNone(state.next_boundary)
        # 明天（周四）仍是交易日，应从 09:30 开始
        self.assertEqual(state.next_boundary.strftime("%Y-%m-%d %H:%M"), "2026-10-22 09:30")


class TestAcceptanceC(unittest.TestCase):
    """测试 C：周期切换立即重算。"""

    def test_switch_to_15min(self):
        state = compute_state(WED.replace(hour=10, minute=7, second=23), ashare(period=900))
        self.assertEqual(state.status, STATUS_TRADING)
        self.assertEqual(round(state.remaining_seconds), 457)  # 07:37
        self.assertEqual(state.bar_start, WED.replace(hour=10, minute=0))
        self.assertEqual(state.bar_end, WED.replace(hour=10, minute=15))

    def test_switch_to_60min_session_alignment(self):
        state = compute_state(WED.replace(hour=10, minute=7, second=23), ashare(period=3600))
        # A 股 60 分钟线按时段起点：09:30–10:30–11:30
        self.assertEqual(state.bar_start, WED.replace(hour=9, minute=30))
        self.assertEqual(state.bar_end, WED.replace(hour=10, minute=30))
        self.assertEqual(round(state.remaining_seconds), 1357)

    def test_switch_to_60min_clock_alignment(self):
        state = compute_state(WED.replace(hour=10, minute=7, second=23), ashare(period=3600, alignment=ALIGNMENT_CLOCK))
        # 整点对齐：首根被截断为 09:30–10:00，之后 10:00–11:00
        self.assertEqual(state.bar_start, WED.replace(hour=10, minute=0))
        self.assertEqual(state.bar_end, WED.replace(hour=11, minute=0))


class TestAlignmentStrategy(unittest.TestCase):
    """两种对齐规则的差异与一致性。"""

    def test_5min_alignment_agree(self):
        for hhmm in [(9, 32, 15), (10, 7, 23), (14, 59, 0)]:
            now = WED.replace(hour=hhmm[0], minute=hhmm[1], second=hhmm[2])
            a = compute_state(now, ashare(period=300))
            b = compute_state(now, ashare(period=300, alignment=ALIGNMENT_CLOCK))
            self.assertEqual(a.bar_end, b.bar_end, f"{hhmm} 两种规则应一致")

    def test_two_hour_clock_alignment_after_midnight(self):
        state = compute_state(WED.replace(hour=0, minute=30), custom([(0, 1440)], period=7200, alignment=ALIGNMENT_CLOCK))
        self.assertEqual(state.bar_start, WED.replace(hour=0, minute=0))
        self.assertEqual(state.bar_end, WED.replace(hour=2, minute=0))

    def test_partial_session_truncated(self):
        # 09:00–10:10 用 45 分钟周期：09:00 / 09:45 / 10:30(截断到 10:10)
        state = compute_state(WED.replace(hour=10, minute=5), custom([(9 * 60, 10 * 60 + 10)], period=45 * 60))
        self.assertEqual(state.bar_start, WED.replace(hour=9, minute=45))
        self.assertEqual(state.bar_end, WED.replace(hour=10, minute=10))
        self.assertEqual(state.remaining_seconds, 300)

    def test_period_longer_than_session(self):
        state = compute_state(WED.replace(hour=9, minute=50), ashare(period=3 * 3600))
        self.assertEqual(state.bar_start, WED.replace(hour=9, minute=30))
        self.assertEqual(state.bar_end, WED.replace(hour=11, minute=30))


class TestWeekdayAndBreakRules(unittest.TestCase):
    def test_weekend_non_trading(self):
        state = compute_state(SUN.replace(hour=10, minute=0), ashare())
        self.assertEqual((state.status, state.reason), (STATUS_IDLE, REASON_NON_TRADING_DAY))
        next_day = state.next_boundary
        self.assertEqual(next_day.strftime("%Y-%m-%d %H:%M"), "2026-10-19 09:30")

    def test_multi_session_gap_reason(self):
        profile = custom([(570, 690), (780, 900), (1260, 1380)])
        state = compute_state(WED.replace(hour=15, minute=10), profile)
        self.assertEqual(state.reason, REASON_BREAK)
        self.assertEqual(state.next_boundary, WED.replace(hour=21, minute=0))

    def test_disabled_session_ignored(self):
        profile = Profile(
            id="p1",
            name="x",
            period_seconds=300,
            sessions=[TradingSession(570, 690), TradingSession(780, 900, enabled=False)],
        )
        state = compute_state(WED.replace(hour=14, minute=0), profile)
        self.assertEqual(state.reason, REASON_AFTER_CLOSE)


class TestValidation(unittest.TestCase):
    def test_overlap(self):
        profile = custom([(570, 690), (660, 780)])
        errors = validate_profile(profile)
        self.assertTrue(any("重叠" in e for e in errors))
        state = compute_state(WED.replace(hour=10), profile)
        self.assertEqual(state.status, STATUS_ERROR)

    def test_start_after_end(self):
        errors = validate_profile(custom([(600, 500)]))
        self.assertTrue(any("开始时间" in e for e in errors))

    def test_no_enabled_session(self):
        profile = Profile(id="p", name="x", period_seconds=60, sessions=[TradingSession(570, 690, enabled=False)])
        self.assertTrue(validate_profile(profile))
        self.assertEqual(compute_state(WED.replace(hour=10), profile).status, STATUS_ERROR)

    def test_bad_period(self):
        self.assertTrue(validate_profile(ashare(period=1)))
        self.assertTrue(validate_profile(ashare(period=100_000)))

    def test_adjacent_sessions_ok(self):
        self.assertEqual(validate_profile(custom([(570, 690), (690, 900)])), [])

    def test_24h_session_ok(self):
        self.assertEqual(validate_profile(custom([(0, 1440)])), [])

    def test_empty_weekdays(self):
        profile = ashare(weekdays=())
        self.assertTrue(validate_profile(profile))


if __name__ == "__main__":
    unittest.main(verbosity=2)
