"""多周期（主周期 + 叠加周期）的接管规则测试。

覆盖：
* 接管窗口的边界（剩余 ≤ 主周期长度）
* 接管期间低周期被静默（不出样式、不出声音）
* 多个叠加周期同时在场时取周期最大的
* 叠加周期必须大于主周期、阈值必须小于主周期
* 切换主周期 = 切换整套方案
* v1 配置迁移到 v2
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.engine import compute_state, validate_profile  # noqa: E402
from core.models import (  # noqa: E402
    MAX_OVERLAYS,
    AppConfig,
    Profile,
    ReminderRule,
    Timeframe,
    TradingSession,
    default_reminder_rules,
    default_scheme,
)
from core.reminders import ReminderScheduler  # noqa: E402

WED = datetime(2026, 10, 21, 0, 0)  # 星期三


def at(hm: str) -> datetime:
    hour, minute, second = (int(x) for x in hm.split(":"))
    return WED.replace(hour=hour, minute=minute, second=second)


def make_profile(primary: int = 300, overlays: list[int] | None = None) -> Profile:
    """A 股时段（09:30–11:30），主周期 + 若干叠加周期，都用 30 秒提醒便于观察。"""
    scheme = default_scheme(primary)
    scheme.primary.reminders = [
        ReminderRule(id=f"p{t}", threshold_seconds=t, fg_color="#00FF00", bg_color="#000000")
        for t in (120, 10)
        if t < primary
    ]
    for period in overlays or []:
        scheme.overlays.append(
            Timeframe(
                period_seconds=period,
                fg_color="#FFFFFF",
                bg_color="#B3261E",
                reminders=[ReminderRule(id=f"o{period}", threshold_seconds=30, fg_color="#FFFFFF",
                                        bg_color="#B3261E")],
            )
        )
    return Profile(
        id="p",
        name="A 股",
        sessions=[TradingSession(570, 690)],
        weekdays=[0, 1, 2, 3, 4, 5, 6],
        schemes=[scheme],
        active_period=primary,
    )


class TestTakeoverWindow(unittest.TestCase):
    def setUp(self):
        self.profile = make_profile(300, [1800])

    def test_enters_window_at_last_primary_bar(self):
        # 30 分钟 K 线 10:00–10:30，最后一根 5 分钟是 10:25–10:30
        before = compute_state(at("10:24:59"), self.profile)
        self.assertIsNone(before.takeover)
        self.assertEqual(before.style_bar.period_seconds, 300)

        inside = compute_state(at("10:25:00"), self.profile)
        self.assertIsNotNone(inside.takeover)
        self.assertEqual(inside.takeover.period_seconds, 1800)
        self.assertEqual(inside.style_bar.period_seconds, 1800)

    def test_window_ends_with_the_bar(self):
        # 10:30:00 两根 K 线同时结束，回到主周期
        state = compute_state(at("10:30:00"), self.profile)
        self.assertIsNone(state.takeover)
        self.assertEqual(state.style_bar.period_seconds, 300)

    def test_number_always_from_primary(self):
        state = compute_state(at("10:29:30"), self.profile)
        self.assertEqual(state.period_seconds, 300)
        self.assertEqual(state.remaining_seconds, 30)
        self.assertEqual(state.takeover.remaining_seconds, 30)

    def test_largest_overlay_wins(self):
        profile = make_profile(300, [1800, 3600])
        # 注意：时段起点对齐下，60 分钟线是 09:30–10:30 / 10:30–11:30，
        # 与 30 分钟线只在 :30 / 整点这些边界上同时结束。
        # 11:29 时两者都进入最后一根主周期 K 线 → 60 分钟赢。
        state = compute_state(at("11:29:00"), profile)
        self.assertEqual(state.takeover.period_seconds, 3600)

    def test_non_coinciding_boundary_uses_smaller_overlay(self):
        # 10:59 时只有 30 分钟线要结束（60 分钟线要到 11:30），所以 30 分钟接管
        profile = make_profile(300, [1800, 3600])
        state = compute_state(at("10:59:00"), profile)
        self.assertEqual(state.takeover.period_seconds, 1800)

    def test_overlay_smaller_than_primary_never_wins(self):
        # 主周期 30 分钟、叠加 5 分钟：5 分钟永远赢不了（校验也会报错）
        profile = make_profile(1800, [300])
        self.assertTrue(any("必须大于主周期" in e for e in validate_profile(profile)))


class TestSuppression(unittest.TestCase):
    def setUp(self):
        self.profile = make_profile(300, [1800])
        self.scheduler = ReminderScheduler()

    def _run(self, start: str, seconds: int):
        fired = []
        base = at(start)
        for i in range(seconds):
            state = compute_state(base + timedelta(seconds=i), self.profile)
            result = self.scheduler.update(state)
            for event in result.events:
                fired.append((event.rule.id, event.period_seconds))
        return fired

    def test_primary_rules_silenced_during_takeover(self):
        fired = self._run("10:25:00", 300)
        ids = [rule_id for rule_id, _ in fired]
        # 主周期的 120 秒节点（10:28:00）和 10 秒节点（10:29:50）都不该响
        self.assertNotIn("p120", ids)
        self.assertNotIn("p10", ids)
        # 30 分钟的 30 秒节点（10:29:30）应该响
        self.assertIn("o1800", ids)
        self.assertEqual([p for _, p in fired if _ == "o1800"], [1800])

    def test_primary_rules_work_outside_takeover(self):
        # 10:20–10:25 这根 5 分钟不是 30 分钟的最后一根，主周期节点照常响
        fired = self._run("10:24:00", 60)
        self.assertIn("p10", [rule_id for rule_id, _ in fired])

    def test_style_follows_takeover(self):
        self._run("10:25:00", 5)
        state = compute_state(at("10:26:00"), self.profile)
        result = self.scheduler.update(state)
        self.assertEqual(result.style_bar.period_seconds, 1800)
        # 还没到 30 秒节点 → 用接管色，没有具体节点样式
        self.assertIsNone(result.style_rule)

    def test_style_uses_overlay_rule_after_fire(self):
        self._run("10:29:20", 15)
        state = compute_state(at("10:29:31"), self.profile)
        result = self.scheduler.update(state)
        self.assertEqual(result.style_bar.period_seconds, 1800)
        self.assertIsNotNone(result.style_rule)
        self.assertEqual(result.style_rule.id, "o1800")


class TestValidation(unittest.TestCase):
    def test_threshold_must_be_less_than_primary(self):
        profile = make_profile(300, [1800])
        profile.scheme.overlays[0].reminders.append(
            ReminderRule(id="bad", threshold_seconds=300)
        )
        errors = validate_profile(profile)
        self.assertTrue(any("须更小" in e or "超过主周期" in e for e in errors))

    def test_duplicate_period_rejected(self):
        profile = make_profile(300, [1800, 1800])
        errors = validate_profile(profile)
        self.assertTrue(any("周期重复" in e for e in errors))

    def test_too_many_overlays(self):
        profile = make_profile(60, [])
        for index in range(MAX_OVERLAYS + 1):
            profile.scheme.overlays.append(Timeframe(period_seconds=120 + index * 60))
        errors = validate_profile(profile)
        self.assertTrue(any("叠加周期过多" in e for e in errors))


class TestSchemeSwitch(unittest.TestCase):
    def test_switch_keeps_other_scheme_intact(self):
        profile = make_profile(300, [1800])
        profile.scheme.overlays[0].reminders[0].threshold_seconds = 25

        # 切到 15 分钟：新方案是白纸，没有叠加周期
        profile.activate(900)
        self.assertEqual(profile.primary.period_seconds, 900)
        self.assertEqual(profile.overlays, [])
        self.assertEqual(len(profile.schemes), 2)

        # 切回 5 分钟：叠加周期和它的 25 秒节点都还在
        profile.activate(300)
        self.assertEqual(len(profile.overlays), 1)
        self.assertEqual(profile.overlays[0].period_seconds, 1800)
        self.assertEqual(profile.overlays[0].reminders[0].threshold_seconds, 25)

    def test_new_scheme_uses_defaults(self):
        profile = make_profile(300, [1800])
        profile.activate(60)
        primary = profile.primary
        self.assertEqual(primary.fg_color, Timeframe().fg_color)
        self.assertEqual(primary.bg_color, Timeframe().bg_color)
        # 1 分钟周期的默认节点必须都小于 60 秒
        self.assertTrue(all(r.threshold_seconds < 60 for r in primary.reminders))

    def test_period_property_is_read_only(self):
        profile = make_profile()
        with self.assertRaises(AttributeError):
            profile.period_seconds = 900  # type: ignore[misc]


class TestMigration(unittest.TestCase):
    def test_v1_config_wraps_into_one_scheme(self):
        raw = {
            "schema_version": 1,
            "appearance": {"fg_color": "#FF0000", "bg_color": "#001122", "transparent_bg": True,
                           "font_size": 40},
            "profiles": [{
                "id": "old",
                "name": "老配置",
                "period_seconds": 900,
                "sessions": [{"start_minute": 570, "end_minute": 690, "enabled": True}],
                "weekdays": [0, 1, 2, 3, 4],
                "reminders": [{"id": "r1", "threshold_seconds": 60}],
            }],
        }
        cfg = AppConfig.from_dict(raw)
        profile = cfg.profiles[0]
        self.assertEqual(profile.primary.period_seconds, 900)
        self.assertEqual(profile.primary.fg_color, "#FF0000")
        self.assertEqual(profile.primary.bg_color, "#001122")
        self.assertTrue(profile.primary.transparent_bg)
        self.assertEqual(len(profile.primary.reminders), 1)
        self.assertEqual(cfg.appearance.font_size, 40)

    def test_v1_thresholds_are_clamped(self):
        # 老配置把节点设成 60 秒、主周期却只有 60 秒 → 迁移时压到 59
        raw = {
            "schema_version": 1,
            "profiles": [{
                "id": "old", "name": "x", "period_seconds": 60,
                "sessions": [{"start_minute": 570, "end_minute": 690, "enabled": True}],
                "weekdays": [0, 1, 2, 3, 4],
                "reminders": [{"id": "r1", "threshold_seconds": 60}],
            }],
        }
        cfg = AppConfig.from_dict(raw)
        self.assertEqual(cfg.profiles[0].primary.reminders[0].threshold_seconds, 59)
        self.assertEqual(validate_profile(cfg.profiles[0]), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
