"""SQLite config.db path and schema (mirrors Uploadarr)."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

_CONFIG_SCHEMA = """
CREATE TABLE IF NOT EXISTS config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key_path TEXT NOT NULL UNIQUE,
    raw_value TEXT NOT NULL,
    value_type TEXT NOT NULL CHECK (
        value_type IN ('string', 'number', 'boolean', 'array', 'object')
    ),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS config_updated_at
AFTER UPDATE ON config
BEGIN
    UPDATE config SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
"""


# Test harness override (pytest monkeypatch); not operator configuration.
_DATA_DIR_OVERRIDE: Path | None = None


def set_data_dir_override(path: Path | str | None) -> None:
    """Pin the runtime data root for tests."""
    global _DATA_DIR_OVERRIDE
    _DATA_DIR_OVERRIDE = Path(path) if path is not None else None


def default_data_dir() -> Path:
    """Return the fixed runtime data root (container ``/data`` or test override)."""
    if _DATA_DIR_OVERRIDE is not None:
        return _DATA_DIR_OVERRIDE
    env = os.environ.get("MEDIAREFINERY_DATA_DIR")
    if env:
        return Path(env)
    return Path("/data")


def config_db_path(data_dir: Path | None = None) -> Path:
    """Return ``<data_dir>/databases/config.db``."""
    root = data_dir if data_dir is not None else default_data_dir()
    return root / "databases" / "config.db"


def open_config_db(data_dir: Path | None = None) -> sqlite3.Connection:
    """Open (and create) config.db with the standard schema."""
    path = config_db_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_CONFIG_SCHEMA)
    conn.commit()
    return conn
