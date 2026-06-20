"""Dot-path config repository for config.db."""

from __future__ import annotations

import json
import sqlite3
from typing import Any


class ConfigDBRepository:
    """Read/write nested configuration in SQLite."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get_all(self) -> dict[str, Any]:
        """Return all configuration entries as a flat dot-path map."""
        cur = self._conn.cursor()
        cur.execute("SELECT key_path, raw_value, value_type FROM config")
        return {
            row["key_path"]: self._decode(row["raw_value"], row["value_type"])
            for row in cur.fetchall()
        }

    def get_nested(self) -> dict[str, Any]:
        """Return all configuration entries as a nested dictionary."""
        return self._unflatten(self.get_all())

    def get(self, key_path: str, default: Any = None) -> Any:
        """Return the value at ``key_path``, or ``default`` when missing."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT raw_value, value_type FROM config WHERE key_path = ?",
            (key_path,),
        )
        row = cur.fetchone()
        if row is None:
            return default
        return self._decode(row["raw_value"], row["value_type"])

    def upsert(self, key_path: str, value: Any) -> None:
        """Insert or update a single configuration entry."""
        value_type = self._detect_type(value)
        raw = self._encode(value)
        self._conn.execute(
            """
            INSERT INTO config (key_path, raw_value, value_type)
            VALUES (?, ?, ?)
            ON CONFLICT(key_path) DO UPDATE SET
                raw_value = excluded.raw_value,
                value_type = excluded.value_type,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key_path, raw, value_type),
        )
        self._conn.commit()

    def bulk_upsert(self, nested: dict[str, Any]) -> int:
        """Insert or update all entries from a nested configuration dict."""
        flat = self._flatten(nested)
        for key_path, value in flat.items():
            self.upsert(key_path, value)
        return len(flat)

    @staticmethod
    def _detect_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return "number"
        if isinstance(value, list):
            return "array"
        if isinstance(value, dict):
            return "object"
        return "string"

    @staticmethod
    def _encode(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, (list, dict)):
            return json.dumps(value)
        return str(value)

    @staticmethod
    def _decode(raw: str, value_type: str) -> Any:
        if value_type == "boolean":
            return raw.lower() == "true"
        if value_type == "number":
            return float(raw) if "." in raw else int(raw)
        if value_type == "array":
            return json.loads(raw)
        if value_type == "object":
            return json.loads(raw)
        return raw

    @classmethod
    def _flatten(cls, data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict) and value:
                out.update(cls._flatten(value, path))
            else:
                out[path] = value
        return out

    @classmethod
    def _unflatten(cls, flat: dict[str, Any]) -> dict[str, Any]:
        root: dict[str, Any] = {}
        for key_path, value in flat.items():
            parts = key_path.split(".")
            cur = root
            for part in parts[:-1]:
                nxt = cur.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    cur[part] = nxt
                cur = nxt
            cur[parts[-1]] = value
        return root
