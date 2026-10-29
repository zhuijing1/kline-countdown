"""提醒调度测试：对应验收测试 E（去重、重置、恢复不补响）。"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engine import compute_state  # noqa: E402
from core.models import Profile, ReminderRule, TradingSession, default_scheme  # noqa: E402
from core.reminders import ReminderScheduler  # noqa: E402


def profile_with_rules(rules) -> Profile:
    scheme = default_scheme(300)
    scheme.primary.reminders = list(rules)
    return Profile(
        id="p",
        name="t",
        sessions=[TradingSession(9 * 60 + 30, 11 * 60 + 30)],
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        schemes=[scheme],
        active_period=300,
    )


def at(seconds: float) -> datetime:
    """本根 K 线（10:00–10:05）结束前 `seconds` 秒的时刻。"""
    return datetime(2026, 10, 21, 10, 5, 0) - timedelta(seconds=seconds)


def next_bar(seconds: float) -> datetime:
    """下一根 K 线（10:05–10:10）结束前 `seconds` 秒的时刻。"""
    return datetime(2026, 10, 21, 10, 10, 0) - timedelta(seconds=seconds)


class TestReminderDedup(unittest.TestCase):
    def setUp(self):
        self.rules = [
            ReminderRule(id="r60", threshold_seconds=60),
            ReminderRule(id="r30", threshold_seconds=30),
            ReminderRule(id="r10", threshold_seconds=10),
        ]
        self.profile = profile_with_rules(self.rules)
        self.scheduler = ReminderScheduler()

    def test_fire_once_per_threshold(self):
        state = compute_state(at(60), self.profile)
        result = self.scheduler.update(state)
        self.assertEqual([event.rule.id for event in result.events], ["r60"])
        # 反复刷新（每 100ms）不再重复触发
        for _ in range(20):
            follow = self.scheduler.update(compute_state(at(59.8), self.profile))
            self.assertEqual(follow.events, [])
            self.assertEqual([r.id for r in follow.active_rules], ["r60"])

    def test_next_threshold_fires_later(self):
        self.scheduler.update(compute_state(at(40), self.profile))
        result = self.scheduler.update(compute_state(at(30), self.profile))
        self.assertEqual([event.rule.id for event in result.events], ["r30"])

    def test_editing_threshold_makes_it_fire_again(self):
        """同一根 K 线里把节点的剩余秒数改小，应该按新阈值再响一次。

        去重键是「节点 id + 剩余秒数」，改了阈值就等于换了个新节点；
        否则用户调完参数会发现节点只闪不响。
        """
        self.scheduler.update(compute_state(at(60), self.profile))  # r60 已触发
        self.rules[0].threshold_seconds = 5  # 用户把它改成 5 秒
        result = self.scheduler.update(compute_state(at(5), self.profile))
        self.assertEqual([event.rule.id for event in result.events], ["r60"])
        # 再刷新不重复
        again = self.scheduler.update(compute_state(at(4.9), self.profile))
        self.assertEqual(again.events, [])

    def test_unchanged_threshold_does_not_refire(self):
        """只改颜色/声音之类，阈值没变，不应该重新响。"""
        self.scheduler.update(compute_state(at(60), self.profile))
        self.rules[0].fg_color = "#00FF00"
        result = self.scheduler.update(compute_state(at(58), self.profile))
        self.assertEqual(result.events, [])
        self.assertEqual([r.id for r in result.active_rules], ["r60"])

    def test_tightest_only_when_jump_into_zone(self):
        # 从 25 秒开始（启动即落入 60/30 两个区间）：只触发贴身节点，不连响
        result = self.scheduler.update(compute_state(at(25), self.profile))
        self.assertEqual([event.rule.id for event in result.events], ["r30"])
        result = self.scheduler.update(compute_state(at(24), self.profile))
        self.assertEqual(result.events, [])  # 宽松节点只被标记，不补响
        self.assertEqual([r.id for r in result.active_rules], ["r30", "r60"])

    def test_new_bar_resets(self):
        self.scheduler.update(compute_state(at(30), self.profile))  # 本根触发 r30
        result = self.scheduler.update(compute_state(next_bar(240), self.profile))
        self.assertTrue(result.bar_changed)  # 换到新 K 线
        self.assertEqual(result.events, [])
        # 新 K 线内 30 秒节点重新可用
        result = self.scheduler.update(compute_state(next_bar(30), self.profile))
        self.assertEqual([event.rule.id for event in result.events], ["r30"])

    def test_bar_start_boundary_no_retrigger(self):
        state = compute_state(datetime(2026, 10, 21, 10, 5, 0), self.profile)  # 新 K 线开始
        result = self.scheduler.update(state)
        self.assertTrue(result.bar_changed)
        self.assertEqual(result.events, [])

    def test_suppress_after_resume(self):
        self.scheduler.suppress_current_bar()
        result = self.scheduler.update(compute_state(at(20), self.profile))
        self.assertEqual(result.events, [])
        self.assertEqual(result.suppressed, 2)

    def test_idle_no_events(self):
        state = compute_state(datetime(2026, 10, 21, 12, 0, 0), self.profile)
        result = self.scheduler.update(state)
        self.assertEqual(result.events, [])
        self.assertEqual(result.active_rules, [])

    def test_disabled_rule_ignored(self):
        rules = [ReminderRule(id="r_off", threshold_seconds=30, enabled=False)]
        scheduler = ReminderScheduler()
        result = scheduler.update(compute_state(at(10), profile_with_rules(rules)))
        self.assertEqual(result.events, [])

    def test_bar_key_differs_per_bar(self):
        first = compute_state(at(60), self.profile).bar_key
        second = compute_state(datetime(2026, 10, 21, 10, 5, 0), self.profile).bar_key
        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestClockRollback(unittest.TestCase):
    """系统时间被回拨/调回上一根 K 线时，不得重复响铃。"""

    def setUp(self):
        self.profile = profile_with_rules([
            ReminderRule(id="r60", threshold_seconds=60),
            ReminderRule(id="r30", threshold_seconds=30),
        ])
        self.scheduler = ReminderScheduler()

    def test_rollback_within_same_bar_no_refire(self):
        self.scheduler.update(compute_state(at(30), self.profile))   # 触发 r30
        # 时间回拨到 45 秒剩余（同一根 K 线）
        result = self.scheduler.update(compute_state(at(45), self.profile))
        self.assertEqual(result.events, [])

    def test_rollback_to_previous_bar_no_refire(self):
        # 上一根 K 线触发过 r30
        self.scheduler.update(compute_state(at(30), self.profile))
        # 前进到下一根
        self.scheduler.update(compute_state(next_bar(240), self.profile))
        # 时钟被拨回上一根 K 线的 20 秒处：不应再次触发 r30
        result = self.scheduler.update(compute_state(at(20), self.profile))
        self.assertEqual([e.rule.id for e in result.events], [])

    def test_forward_into_new_bar_still_fires(self):
        self.scheduler.update(compute_state(at(30), self.profile))
        result = self.scheduler.update(compute_state(next_bar(30), self.profile))
        self.assertEqual([e.rule.id for e in result.events], ["r30"])
