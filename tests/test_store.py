"""配置持久化 + 内置图标测试：对应验收测试 D（持久化与隔离）。"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config_store import ConfigStore, default_config_path  # noqa: E402
from core.icon import build_ico_bytes, write_icon_file  # noqa: E402
from core.models import (  # noqa: E402
    AppConfig,
    Appearance,
    ReminderRule,
    SCHEMA_VERSION,
    TradingSession,
    default_profiles,
    format_duration,
    parse_hm,
)


class TempConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.addCleanup(self.tmp.cleanup)

    def store(self) -> ConfigStore:
        return ConfigStore(self.path)


class TestPersistence(TempConfigTestCase):
    def test_default_profiles_created(self):
        cfg = self.store().load()
        self.assertEqual(len(cfg.profiles), 3)
        self.assertEqual(cfg.active_profile().name, "A 股")
        self.assertEqual(len(cfg.active_profile().sessions), 2)

    def test_roundtrip(self):
        store = self.store()
        cfg = store.load()
        cfg.profiles[0].name = "我的A股"
        cfg.profiles[0].period_seconds = 180
        cfg.appearance.font_size = 42
        cfg.global_mute = True
        store.save(cfg)

        reloaded = self.store().load()
        self.assertEqual(reloaded.profiles[0].name, "我的A股")
        self.assertEqual(reloaded.profiles[0].period_seconds, 180)
        self.assertEqual(reloaded.appearance.font_size, 42)
        self.assertTrue(reloaded.global_mute)
        self.assertEqual(reloaded.schema_version, SCHEMA_VERSION)

    def test_profiles_independent(self):
        """测试 D：不同配置的时段/周期/提醒互不覆盖。"""
        store = self.store()
        cfg = store.load()
        ashare = cfg.profiles[0]
        ashare.reminders = [ReminderRule(id="r_ashare", threshold_seconds=42)]
        futures = cfg.profiles[1]
        futures.reminders = [ReminderRule(id="r_fut", threshold_seconds=17)]
        futures.period_seconds = 600
        store.save(cfg)

        reloaded = self.store().load()
        by_id = {p.id: p for p in reloaded.profiles}
        self.assertEqual(by_id[ashare.id].reminders[0].threshold_seconds, 42)
        self.assertEqual(by_id[futures.id].reminders[0].threshold_seconds, 17)
        self.assertEqual(by_id[futures.id].period_seconds, 600)
        self.assertEqual(by_id[ashare.id].period_seconds, 300)

    def test_corrupt_file_falls_back(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ not json", encoding="utf-8")
        store = self.store()
        cfg = store.load()
        self.assertEqual(len(cfg.profiles), 3)
        self.assertTrue(any("损坏" in note for note in store.last_load_notes))

    def test_missing_file_creates_default(self):
        cfg = self.store().load()
        self.assertEqual(cfg.schema_version, SCHEMA_VERSION)

    def test_default_path_uses_appdata(self):
        self.assertTrue(default_config_path().name == "config.json")

    def test_env_override(self):
        os.environ["KLCD_CONFIG"] = str(self.path)
        try:
            self.assertEqual(default_config_path(), self.path)
        finally:
            os.environ.pop("KLCD_CONFIG", None)

    def test_profile_copy_new_ids(self):
        clone = default_profiles()[0].copy(rename="副本")
        self.assertNotEqual(clone.id, default_profiles()[0].id)
        self.assertEqual(clone.name, "副本")
        self.assertEqual(len(clone.reminders), len(default_profiles()[0].reminders))
        self.assertNotEqual(clone.reminders[0].id, default_profiles()[0].reminders[0].id)


class TestHelpers(unittest.TestCase):
    def test_format_duration(self):
        self.assertEqual(format_duration(165), "02:45")
        self.assertEqual(format_duration(1), "00:01")
        self.assertEqual(format_duration(3661), "1:01:01")
        self.assertEqual(format_duration(0), "00:00")
        self.assertEqual(format_duration(-5), "00:00")

    def test_parse_hm(self):
        self.assertEqual(parse_hm("09:30"), 570)
        self.assertEqual(parse_hm("9:30"), 570)
        self.assertEqual(parse_hm("24:00"), 1440)
        self.assertEqual(parse_hm("09：30"), 570)  # 全角冒号
        for bad in ["", "25:00", "09:60", "abc", "9", "24:01"]:
            with self.assertRaises(ValueError, msg=bad):
                parse_hm(bad)

    def test_appearance_from_dict_tolerant(self):
        appearance = Appearance.from_dict({"font_size": "x", "opacity": 0.5, "unknown": 1})
        self.assertEqual(appearance.font_size, Appearance().font_size)  # 非法值回落默认
        self.assertEqual(appearance.opacity, 0.5)

    def test_session_label(self):
        self.assertEqual(TradingSession(570, 690).label, "09:30–11:30")
        self.assertEqual(TradingSession(0, 1440).label, "00:00–24:00")


class TestIcon(unittest.TestCase):
    def test_build_ico_bytes(self):
        blob = build_ico_bytes()
        self.assertIsInstance(blob, bytes)
        self.assertGreater(len(blob), 1000)
        # ICONDIR 头：reserved=0, type=1(icon), count=4
        self.assertEqual(blob[:6], b"\x00\x00\x01\x00\x04\x00")
        first_offset = int.from_bytes(blob[18:22], "little")
        self.assertEqual(first_offset, 70)  # 6 字节 ICONDIR + 4×16 字节目录项

    def test_write_icon_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "app.ico"
            path = write_icon_file(target)
            self.assertTrue(Path(path).is_file())
            self.assertGreater(Path(path).stat().st_size, 1000)

    def test_resource_png_exists(self):
        """附带 200x200 PNG 用作窗口标题图标。"""
        from core.resources import resource_png_path

        self.assertTrue(Path(resource_png_path()).exists())


class TestAppConfigMigration(unittest.TestCase):
    def test_from_dict_minimal(self):
        cfg = AppConfig.from_dict({})
        self.assertTrue(cfg.profiles)
        self.assertEqual(cfg.active_profile_id, cfg.profiles[0].id)

    def test_from_dict_active_missing(self):
        cfg = AppConfig.from_dict(
            {"profiles": [p.to_dict() for p in default_profiles()], "active_profile_id": "nope"}
        )
        self.assertNotEqual(cfg.active_profile_id, "nope")

    def test_json_serializable(self):
        config = AppConfig.from_dict({"profiles": [p.to_dict() for p in default_profiles()]})
        payload = json.dumps(config.to_dict(), ensure_ascii=False)
        self.assertIn("profiles", payload)
        self.assertIn("appearance", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
