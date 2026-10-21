"""配置持久化：原子写入、损坏兜底、版本迁移。

存储位置：``%APPDATA%\\KlineCountdown\\config.json``
（可用环境变量 ``KLCD_CONFIG`` 覆盖，测试与便携模式用）。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from .models import SCHEMA_VERSION, AppConfig


def default_config_path() -> Path:
    override = os.environ.get("KLCD_CONFIG")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "KlineCountdown" / "config.json"


class ConfigStore:
    """线程安全的 JSON 配置读写。写入采用「临时文件 + 替换」避免半截文件。"""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path else default_config_path()
        self._lock = threading.RLock()
        self.last_load_notes: list[str] = []

    # ---- 迁移 ----

    def _migrate(self, data: dict, notes: list[str]) -> dict:
        version = int(data.get("schema_version", 1))
        if version > SCHEMA_VERSION:
            notes.append(f"配置版本 v{version} 高于当前程序支持的 v{SCHEMA_VERSION}，部分新特性将使用默认值")
            data["schema_version"] = SCHEMA_VERSION
            return data

        if version < 1:
            notes.append("配置缺少 schema_version，按 v1 处理")
        if version < SCHEMA_VERSION:
            notes.append(f"配置从 v{version} 迁移到 v{SCHEMA_VERSION}")
        data["schema_version"] = SCHEMA_VERSION
        return data

    # ---- 读写 ----

    def load(self) -> AppConfig:
        with self._lock:
            notes: list[str] = []
            if not self.path.exists():
                self.last_load_notes = ["配置文件不存在，使用默认配置"]
                return AppConfig.from_dict({"schema_version": SCHEMA_VERSION})
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
                backup = self.path.with_suffix(f".corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
                try:
                    backup.write_bytes(self.path.read_bytes())
                except OSError:
                    pass
                notes.append(f"配置文件损坏（{exc}），已备份到 {backup.name}，使用默认配置")
                self.last_load_notes = notes
                return AppConfig.from_dict({"schema_version": SCHEMA_VERSION})

            if not isinstance(raw, dict):
                notes.append("配置内容不是对象，使用默认配置")
                self.last_load_notes = notes
                return AppConfig.from_dict({"schema_version": SCHEMA_VERSION})

            raw = self._migrate(raw, notes)
            cfg = AppConfig.from_dict(raw)
            self.last_load_notes = notes
            return cfg

    def save(self, cfg: AppConfig) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2)
            tmp = self.path.with_suffix(f".tmp-{os.getpid()}-{threading.get_ident()}")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
