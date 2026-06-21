"""Service state store: multi-tenant SQLite schema + per-user scoped accessor.

This module is intentionally separate from :mod:`mediarefinery.state` (the
older single-user pipeline state). The service starts with a fresh
``state.db``; there is no in-place migration from older state files.

The schema adds service-only tables (``users``, ``sessions``,
``user_api_keys``, ``audit_log``, ``model_registry``) and re-issues the
core pipeline tables with a non-nullable ``user_id`` column so the
multi-tenant isolation invariant is enforced at the database layer:
every row that belongs to a tenant carries that tenant's id, and every
read goes through :meth:`StateStore.with_user`, which transparently
scopes queries by ``user_id``.

Encryption-at-rest for ``sessions.encrypted_immich_token`` and
``user_api_keys.encrypted_key`` is handled by ``service.security`` in
this module stores opaque ``BLOB`` payloads and does not import the
cryptography package.
"""

from __future__ import annotations

import hashlib
import json as _json
import re
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from ..analysis import analysis_summary
from ._sql import build_sql, sql_placeholders

SERVICE_SCHEMA_VERSION = 6

SERVICE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT,
    is_admin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    encrypted_immich_token BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TEXT NOT NULL,
    last_revalidated_at TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS user_api_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    label TEXT,
    encrypted_key BLOB NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_user_api_keys_user ON user_api_keys(user_id);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    action TEXT NOT NULL,
    target_asset_id TEXT,
    run_id INTEGER,
    before_state TEXT,
    after_state TEXT,
    details_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_user_at ON audit_log(user_id, at);

CREATE TABLE IF NOT EXISTS model_registry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    license TEXT,
    kind TEXT NOT NULL DEFAULT 'classifier',
    active_slot TEXT NOT NULL DEFAULT 'classifier',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    active INTEGER NOT NULL DEFAULT 0,
    UNIQUE(name, version, sha256)
);

CREATE TABLE IF NOT EXISTS assets (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    immich_checksum_or_version TEXT,
    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_processed TEXT,
    PRIMARY KEY (user_id, asset_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at TEXT,
    status TEXT NOT NULL,
    dry_run INTEGER NOT NULL,
    command TEXT,
    summary_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_user ON runs(user_id);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL,
    action_name TEXT NOT NULL,
    dry_run INTEGER NOT NULL,
    would_apply INTEGER NOT NULL,
    success INTEGER,
    error_code TEXT,
    ran_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_actions_user_run ON actions(user_id, run_id);

CREATE TABLE IF NOT EXISTS user_config (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    categories_json TEXT NOT NULL DEFAULT '{}',
    policies_json TEXT NOT NULL DEFAULT '{}',
    last_seen_model_sha256 TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS service_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES runs(id) ON DELETE CASCADE,
    asset_id TEXT,
    stage TEXT NOT NULL,
    message_code TEXT NOT NULL,
    message TEXT,
    details_json TEXT,
    at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_errors_user ON errors(user_id);

CREATE TABLE IF NOT EXISTS asset_overrides (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL,
    category_id TEXT,
    reason TEXT NOT NULL DEFAULT 'manual',
    set_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, asset_id)
);
CREATE INDEX IF NOT EXISTS idx_asset_overrides_user ON asset_overrides(user_id);

CREATE TABLE IF NOT EXISTS asset_analysis (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    model_sha256 TEXT,
    primary_category_id TEXT,
    media_kind TEXT,
    safety_label TEXT,
    safety_confidence REAL,
    review_needed INTEGER NOT NULL DEFAULT 0,
    document_type TEXT,
    duplicate_key TEXT,
    event_key TEXT,
    ocr_text TEXT,
    review_queues_json TEXT NOT NULL DEFAULT '[]',
    analysis_json TEXT NOT NULL,
    PRIMARY KEY (user_id, asset_id),
    FOREIGN KEY (user_id, asset_id) REFERENCES assets(user_id, asset_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_analysis_user_safety
    ON asset_analysis(user_id, safety_label);
CREATE INDEX IF NOT EXISTS idx_asset_analysis_user_media
    ON asset_analysis(user_id, media_kind);
CREATE INDEX IF NOT EXISTS idx_asset_analysis_user_duplicate
    ON asset_analysis(user_id, duplicate_key);
CREATE INDEX IF NOT EXISTS idx_asset_analysis_user_event
    ON asset_analysis(user_id, event_key);

CREATE TABLE IF NOT EXISTS event_groups (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    auto_key TEXT,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'auto',
    sort_at TEXT,
    source_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, event_id),
    UNIQUE(user_id, auto_key)
);
CREATE INDEX IF NOT EXISTS idx_event_groups_user_status
    ON event_groups(user_id, status);

CREATE TABLE IF NOT EXISTS asset_event_memberships (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    asset_id TEXT NOT NULL,
    event_id TEXT,
    auto_event_key TEXT,
    assignment_source TEXT NOT NULL DEFAULT 'auto',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, asset_id),
    FOREIGN KEY (user_id, asset_id) REFERENCES assets(user_id, asset_id)
        ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_asset_event_memberships_user_event
    ON asset_event_memberships(user_id, event_id);

CREATE TABLE IF NOT EXISTS user_auto_scan (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    interval_minutes INTEGER NOT NULL DEFAULT 30,
    last_seen_taken_at TEXT,
    last_run_at TEXT,
    last_status TEXT,
    last_error_code TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_auto_scan_enabled ON user_auto_scan(enabled);

PRAGMA user_version = 6;
"""

USER_ID_RE = re.compile(r"[A-Za-z0-9_.:@-]{1,128}")


def _validate_user_id(user_id: str) -> str:
    if not isinstance(user_id, str) or not USER_ID_RE.fullmatch(user_id):
        raise ValueError("user_id must match [A-Za-z0-9_.:@-]{1,128}")
    return user_id


@dataclass(frozen=True)
class UserRecord:
    """Represent UserRecord.

    Attributes
    ----------
    user_id : str
    email : str
    name : str | None
    is_admin : bool
    """

    user_id: str
    email: str
    name: str | None
    is_admin: bool


class StateStore:
    """Connection-owning state store for the service SQLite database.

    Use :meth:`with_user` to read or write tenant-scoped data; never touch
    ``self._conn`` directly from outside this module.
    """

    def __init__(self, sqlite_path: str | Path):
        """Initialize the instance.

        Parameters
        ----------
        sqlite_path : str | Path
        """
        self.path = Path(sqlite_path) if str(sqlite_path) != ":memory:" else sqlite_path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn_target = str(self.path)
        else:
            conn_target = ":memory:"
        # FastAPI runs sync routes in a threadpool; the connection must
        # be reachable from request threads. SQLite serialises writers
        # internally, which is fine for the single-replica service model.
        self._conn = sqlite3.connect(conn_target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def initialize(self) -> None:
        # Forward migration is encoded in SERVICE_SCHEMA_SQL itself: every
        # CREATE uses IF NOT EXISTS, and the trailing PRAGMA bumps
        # user_version.
        """Initialize.

        Returns
        -------
        None
        """
        self._conn.executescript(SERVICE_SCHEMA_SQL)
        self._ensure_model_registry_v5_columns()
        version = self.schema_version()
        if version != SERVICE_SCHEMA_VERSION:
            raise RuntimeError(
                f"service state schema version {version} does not match "
                f"{SERVICE_SCHEMA_VERSION}"
            )
        self._conn.commit()

    def schema_version(self) -> int:
        """Return the schema version.

        Returns
        -------
        int
        """
        row = self._conn.execute("PRAGMA user_version").fetchone()
        return int(row[0])

    def upsert_user(
        self,
        *,
        user_id: str,
        email: str,
        name: str | None = None,
        is_admin: bool = False,
    ) -> UserRecord:
        """Upsert user.

        Parameters
        ----------
        user_id : str
        email : str
        name : str | None, optional
        is_admin : bool, optional

        Returns
        -------
        UserRecord
        """
        user_id = _validate_user_id(user_id)
        self._conn.execute(
            """
            INSERT INTO users(user_id, email, name, is_admin)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                email = excluded.email,
                name = excluded.name,
                is_admin = excluded.is_admin,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (user_id, email, name, int(is_admin)),
        )
        self._conn.commit()
        return UserRecord(user_id=user_id, email=email, name=name, is_admin=is_admin)

    def admin_count(self) -> int:
        """Admin count.

        Returns
        -------
        int
        """
        cursor = self._conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE is_admin = 1"
        )
        return int(cursor.fetchone()["c"])

    def promote_to_admin(self, user_id: str) -> None:
        """Promote to admin.

        Parameters
        ----------
        user_id : str

        Returns
        -------
        None
        """
        _validate_user_id(user_id)
        self._conn.execute(
            "UPDATE users SET is_admin = 1 WHERE user_id = ?", (user_id,)
        )
        self._conn.commit()

    def get_setting(self, key: str) -> object | None:
        """Return setting.

        Parameters
        ----------
        key : str

        Returns
        -------
        object | None
        """
        cursor = self._conn.execute(
            "SELECT value_json FROM service_settings WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        import json as _json

        return cast("object | None", _json.loads(row["value_json"]))

    def set_setting(self, key: str, value: object) -> None:
        """Set setting.

        Parameters
        ----------
        key : str
        value : object

        Returns
        -------
        None
        """
        import json as _json

        self._conn.execute(
            """
            INSERT INTO service_settings(key, value_json)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, _json.dumps(value, sort_keys=True)),
        )
        self._conn.commit()

    def active_model_sha256(self) -> str | None:
        """Active model sha256.

        Returns
        -------
        str | None
        """
        cursor = self._conn.execute(
            "SELECT sha256 FROM model_registry "
            "WHERE active = 1 AND active_slot IN ('primary_safety', 'classifier') "
            "ORDER BY CASE active_slot WHEN 'primary_safety' THEN 0 ELSE 1 END "
            "LIMIT 1"
        )
        row = cursor.fetchone()
        return None if row is None else str(row["sha256"])

    def active_adult_subtype_model(self) -> dict[str, Any] | None:
        """Active adult subtype model.

        Returns
        -------
        dict[str, Any] | None
        """
        return self._active_model_for_slot("adult_subtype")

    def active_ocr_model(self) -> dict[str, Any] | None:
        """Active ocr model.

        Returns
        -------
        dict[str, Any] | None
        """
        return self._active_model_for_slot("ocr")

    def _active_model_for_slot(self, active_slot: str) -> dict[str, Any] | None:
        cursor = self._conn.execute(
            "SELECT id, name, version, sha256, license, kind, active_slot, "
            "metadata_json FROM model_registry "
            "WHERE active = 1 AND active_slot = ? LIMIT 1",
            (active_slot,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        import json as _json

        try:
            metadata = _json.loads(row["metadata_json"] or "{}")
        except _json.JSONDecodeError:
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "version": str(row["version"]),
            "sha256": str(row["sha256"]),
            "license": None if row["license"] is None else str(row["license"]),
            "kind": str(row["kind"]),
            "active_slot": str(row["active_slot"]),
            "metadata": metadata,
        }

    def list_users(self) -> list[UserRecord]:
        """List users.

        Returns
        -------
        list[UserRecord]
        """
        cursor = self._conn.execute(
            "SELECT user_id, email, name, is_admin FROM users ORDER BY user_id"
        )
        return [
            UserRecord(
                user_id=row["user_id"],
                email=row["email"],
                name=row["name"],
                is_admin=bool(row["is_admin"]),
            )
            for row in cursor.fetchall()
        ]

    def list_enabled_auto_scan(self) -> list[sqlite3.Row]:
        """Return rows for every user with auto-scan currently enabled.

        Used only by the auto-scan coordinator — every per-user
        operation that follows is gated by ``with_user(user_id)`` so
        the cross-tenant isolation invariant holds.
        """
        cursor = self._conn.execute(
            "SELECT user_id, interval_minutes, last_run_at, last_seen_taken_at "
            "FROM user_auto_scan WHERE enabled = 1"
        )
        return list(cursor.fetchall())

    def with_user(self, user_id: str) -> UserScopedState:
        """Return a tenant-scoped accessor.

        The returned object refuses to act on rows belonging to other
        ``user_id`` values.
        """
        return UserScopedState(self._conn, _validate_user_id(user_id))

    def close(self) -> None:
        """Close.

        Returns
        -------
        None
        """
        self._conn.close()

    def _ensure_model_registry_v5_columns(self) -> None:
        rows = self._conn.execute("PRAGMA table_info(model_registry)").fetchall()
        columns = {str(row["name"]) for row in rows}
        additions = {
            "kind": "ALTER TABLE model_registry "
            "ADD COLUMN kind TEXT NOT NULL DEFAULT 'classifier'",
            "active_slot": "ALTER TABLE model_registry "
            "ADD COLUMN active_slot TEXT NOT NULL DEFAULT 'classifier'",
            "metadata_json": "ALTER TABLE model_registry "
            "ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'",
        }
        for column, sql in additions.items():
            if column not in columns:
                self._conn.execute(sql)
        self._conn.commit()

    def __enter__(self) -> StateStore:
        """Enter the context manager.

        Returns
        -------
        StateStore
        """
        self.initialize()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        """Exit the context manager.

        Parameters
        ----------
        exc_type : object
        exc : object
        tb : object

        Returns
        -------
        None
        """
        self.close()


class UserScopedState:
    """Per-tenant view over a :class:`StateStore` connection.

    Every method either filters by ``user_id`` on read or stamps
    ``user_id`` on write. There is no unscoped read API exposed here.
    """

    def __init__(self, conn: sqlite3.Connection, user_id: str) -> None:
        """Initialize the instance.

        Parameters
        ----------
        conn : sqlite3.Connection
        user_id : str

        Returns
        -------
        None
        """
        self._conn = conn
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        """User id.

        Returns
        -------
        str
        """
        return self._user_id

    # -- assets ------------------------------------------------------

    def upsert_asset(
        self,
        *,
        asset_id: str,
        media_type: str,
        checksum: str | None = None,
    ) -> None:
        """Upsert asset.

        Parameters
        ----------
        asset_id : str
        media_type : str
        checksum : str | None, optional

        Returns
        -------
        None
        """
        self._conn.execute(
            """
            INSERT INTO assets(user_id, asset_id, media_type, immich_checksum_or_version)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, asset_id) DO UPDATE SET
                media_type = excluded.media_type,
                immich_checksum_or_version = excluded.immich_checksum_or_version
            """,
            (self._user_id, asset_id, media_type, checksum),
        )
        self._conn.commit()

    def list_assets(self) -> list[sqlite3.Row]:
        """List assets.

        Returns
        -------
        list[sqlite3.Row]
        """
        cursor = self._conn.execute(
            "SELECT * FROM assets WHERE user_id = ? ORDER BY asset_id",
            (self._user_id,),
        )
        return list(cursor.fetchall())

    def record_asset_analysis(
        self,
        *,
        asset_id: str,
        analysis: Mapping[str, Any],
    ) -> None:
        """Persist the latest additive analysis signals for one asset."""
        self._assert_owns_asset(asset_id)
        import json as _json

        summary = analysis_summary(analysis)
        ocr = analysis.get("ocr") if isinstance(analysis.get("ocr"), Mapping) else {}
        ocr_text = None
        if isinstance(ocr, Mapping) and isinstance(ocr.get("text"), str):
            ocr_text = str(ocr["text"])
        self._conn.execute(
            """
            INSERT INTO asset_analysis(
                user_id, asset_id, model_sha256, primary_category_id,
                media_kind, safety_label, safety_confidence, review_needed,
                document_type, duplicate_key, event_key, ocr_text,
                review_queues_json, analysis_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, asset_id) DO UPDATE SET
                analyzed_at = CURRENT_TIMESTAMP,
                model_sha256 = excluded.model_sha256,
                primary_category_id = excluded.primary_category_id,
                media_kind = excluded.media_kind,
                safety_label = excluded.safety_label,
                safety_confidence = excluded.safety_confidence,
                review_needed = excluded.review_needed,
                document_type = excluded.document_type,
                duplicate_key = excluded.duplicate_key,
                event_key = excluded.event_key,
                ocr_text = excluded.ocr_text,
                review_queues_json = excluded.review_queues_json,
                analysis_json = excluded.analysis_json
            """,
            (
                self._user_id,
                asset_id,
                analysis.get("model_sha256"),
                summary["primary_category_id"],
                summary["media_kind"],
                summary["safety_label"],
                summary["safety_confidence"],
                int(bool(summary["review_needed"])),
                summary["document_type"],
                summary["duplicate_key"],
                summary["event_key"],
                ocr_text,
                _json.dumps(summary["review_queues"], sort_keys=True),
                _json.dumps(dict(analysis), sort_keys=True),
            ),
        )
        self._sync_asset_event_membership(
            asset_id=asset_id,
            analysis=analysis,
            preserve_manual=True,
        )
        self._conn.commit()

    def get_asset_analysis(self, asset_id: str) -> dict[str, Any] | None:
        """Return asset analysis.

        Parameters
        ----------
        asset_id : str

        Returns
        -------
        dict[str, Any] | None
        """
        import json as _json

        cursor = self._conn.execute(
            "SELECT analysis_json FROM asset_analysis "
            "WHERE user_id = ? AND asset_id = ?",
            (self._user_id, asset_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        data = _json.loads(row["analysis_json"])
        return data if isinstance(data, dict) else None

    # -- event groups ------------------------------------------------

    def list_event_groups(self) -> list[dict[str, Any]]:
        """List event groups.

        Returns
        -------
        list[dict[str, Any]]
        """
        rows = self._conn.execute(
            """
            SELECT eg.event_id AS event_id,
                   eg.auto_key AS auto_key,
                   eg.title AS title,
                   eg.status AS status,
                   eg.sort_at AS sort_at,
                   eg.source_json AS source_json,
                   eg.created_at AS created_at,
                   eg.updated_at AS updated_at,
                   COUNT(em.asset_id) AS asset_count
              FROM event_groups eg
              LEFT JOIN asset_event_memberships em
                ON em.user_id = eg.user_id
               AND em.event_id = eg.event_id
               AND em.assignment_source != 'removed'
             WHERE eg.user_id = ?
               AND eg.status NOT IN ('merged', 'reset')
             GROUP BY eg.event_id, eg.auto_key, eg.title, eg.status,
                      eg.sort_at, eg.source_json, eg.created_at, eg.updated_at
            HAVING asset_count > 0
             ORDER BY COALESCE(eg.sort_at, eg.updated_at) DESC,
                      lower(eg.title) ASC,
                      eg.event_id ASC
            """,
            (self._user_id,),
        ).fetchall()
        return [_event_group_from_row(row) for row in rows]

    def get_event_group(self, event_id: str) -> dict[str, Any] | None:
        """Return event group.

        Parameters
        ----------
        event_id : str

        Returns
        -------
        dict[str, Any] | None
        """
        rows = self._conn.execute(
            """
            SELECT eg.event_id AS event_id,
                   eg.auto_key AS auto_key,
                   eg.title AS title,
                   eg.status AS status,
                   eg.sort_at AS sort_at,
                   eg.source_json AS source_json,
                   eg.created_at AS created_at,
                   eg.updated_at AS updated_at,
                   COUNT(em.asset_id) AS asset_count
              FROM event_groups eg
              LEFT JOIN asset_event_memberships em
                ON em.user_id = eg.user_id
               AND em.event_id = eg.event_id
               AND em.assignment_source != 'removed'
             WHERE eg.user_id = ?
               AND eg.event_id = ?
               AND eg.status NOT IN ('merged', 'reset')
             GROUP BY eg.event_id, eg.auto_key, eg.title, eg.status,
                      eg.sort_at, eg.source_json, eg.created_at, eg.updated_at
            """,
            (self._user_id, event_id),
        ).fetchall()
        if not rows or int(rows[0]["asset_count"]) <= 0:
            return None
        return _event_group_from_row(rows[0])

    def list_event_assets_paginated(
        self,
        *,
        event_id: str,
        cursor: str | None,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List event assets paginated.

        Parameters
        ----------
        event_id : str
        cursor : str | None
        page_size : int

        Returns
        -------
        tuple[list[dict[str, Any]], str | None]
        """
        self._assert_visible_event(event_id)
        return self.list_user_assets_paginated(
            cursor=cursor,
            page_size=page_size,
            event_id=event_id,
        )

    def rename_event_group(self, *, event_id: str, title: str) -> dict[str, Any]:
        """Rename event group.

        Parameters
        ----------
        event_id : str
        title : str

        Returns
        -------
        dict[str, Any]
        """
        row = self._assert_visible_event(event_id)
        clean_title = _normalize_event_title(title)
        before = _event_group_record_from_row(row)
        self._conn.execute(
            """
            UPDATE event_groups
               SET title = ?,
                   status = 'manual',
                   updated_at = CURRENT_TIMESTAMP
             WHERE user_id = ? AND event_id = ?
            """,
            (clean_title, self._user_id, event_id),
        )
        self._conn.commit()
        after = self.get_event_group(event_id)
        self.write_audit(
            action="event.rename",
            before_state=before["title"],
            after_state=clean_title,
            details_json=_json.dumps({"event_id": event_id}, sort_keys=True),
        )
        if after is None:
            raise LookupError(f"event {event_id} not found")
        return after

    def merge_event_groups(
        self,
        *,
        target_event_id: str,
        source_event_ids: Sequence[str],
    ) -> dict[str, Any]:
        """Merge event groups.

        Parameters
        ----------
        target_event_id : str
        source_event_ids : Sequence[str]

        Returns
        -------
        dict[str, Any]
        """
        target_row = self._assert_visible_event(target_event_id)
        clean_sources = _dedupe_event_ids(source_event_ids)
        clean_sources = [
            source_id for source_id in clean_sources if source_id != target_event_id
        ]
        if not clean_sources:
            raise ValueError("at least one source event is required")
        source_rows = [self._assert_visible_event(source_id) for source_id in clean_sources]
        before = {
            "target": _event_group_record_from_row(target_row),
            "sources": [_event_group_record_from_row(row) for row in source_rows],
        }

        all_ids = [target_event_id, *clean_sources]
        placeholders = sql_placeholders(len(all_ids))
        self._conn.execute(
            build_sql(
                """
            UPDATE asset_event_memberships
               SET event_id = ?,
                   assignment_source = 'manual',
                   updated_at = CURRENT_TIMESTAMP
             WHERE user_id = ?
               AND event_id IN (""",
                placeholders,
                ")",
            ),
            (target_event_id, self._user_id, *all_ids),
        )
        self._conn.execute(
            """
            UPDATE event_groups
               SET status = 'manual',
                   updated_at = CURRENT_TIMESTAMP
             WHERE user_id = ? AND event_id = ?
            """,
            (self._user_id, target_event_id),
        )
        for source_id, source_row in zip(clean_sources, source_rows, strict=True):
            source = _source_from_json(source_row["source_json"])
            source["merged_into_event_id"] = target_event_id
            self._conn.execute(
                """
                UPDATE event_groups
                   SET status = 'merged',
                       source_json = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE user_id = ? AND event_id = ?
                """,
                (_json.dumps(source, sort_keys=True), self._user_id, source_id),
            )
        self._conn.commit()
        after = self.get_event_group(target_event_id)
        self.write_audit(
            action="event.merge",
            before_state=_json.dumps(before, sort_keys=True),
            after_state=_json.dumps(
                {
                    "target_event_id": target_event_id,
                    "source_event_ids": clean_sources,
                },
                sort_keys=True,
            ),
        )
        if after is None:
            raise LookupError(f"event {target_event_id} not found")
        return after

    def split_event_group(
        self,
        *,
        event_id: str,
        asset_ids: Sequence[str],
        title: str,
    ) -> dict[str, Any]:
        """Split event group.

        Parameters
        ----------
        event_id : str
        asset_ids : Sequence[str]
        title : str

        Returns
        -------
        dict[str, Any]
        """
        self._assert_visible_event(event_id)
        clean_asset_ids = _dedupe_event_ids(asset_ids)
        if not clean_asset_ids:
            raise ValueError("at least one asset is required")
        placeholders = sql_placeholders(len(clean_asset_ids))
        rows = self._conn.execute(
            build_sql(
                """
            SELECT asset_id
              FROM asset_event_memberships
             WHERE user_id = ?
               AND event_id = ?
               AND asset_id IN (""",
                placeholders,
                ")",
            ),
            (self._user_id, event_id, *clean_asset_ids),
        ).fetchall()
        found = {str(row["asset_id"]) for row in rows}
        missing = [asset_id for asset_id in clean_asset_ids if asset_id not in found]
        if missing:
            raise ValueError("all split assets must belong to the source event")

        new_event_id = self._new_manual_event_id()
        clean_title = _normalize_event_title(title)
        source = {
            "operation": "split",
            "source_event_id": event_id,
            "asset_ids": clean_asset_ids,
        }
        self._conn.execute(
            """
            INSERT INTO event_groups(
                user_id, event_id, auto_key, title, status, sort_at, source_json
            )
            VALUES (?, ?, NULL, ?, 'manual', NULL, ?)
            """,
            (
                self._user_id,
                new_event_id,
                clean_title,
                _json.dumps(source, sort_keys=True),
            ),
        )
        self._conn.execute(
            build_sql(
                """
            UPDATE asset_event_memberships
               SET event_id = ?,
                   assignment_source = 'manual',
                   updated_at = CURRENT_TIMESTAMP
             WHERE user_id = ?
               AND asset_id IN (""",
                placeholders,
                ")",
            ),
            (new_event_id, self._user_id, *clean_asset_ids),
        )
        self._conn.commit()
        after = self.get_event_group(new_event_id)
        self.write_audit(
            action="event.split",
            after_state=_json.dumps(
                {
                    "source_event_id": event_id,
                    "new_event_id": new_event_id,
                    "asset_ids": clean_asset_ids,
                },
                sort_keys=True,
            ),
        )
        if after is None:
            raise LookupError(f"event {new_event_id} not found")
        return after

    def remove_asset_from_event(self, *, event_id: str, asset_id: str) -> None:
        """Remove asset from event.

        Parameters
        ----------
        event_id : str
        asset_id : str

        Returns
        -------
        None
        """
        self._assert_visible_event(event_id)
        row = self._conn.execute(
            """
            SELECT event_id
              FROM asset_event_memberships
             WHERE user_id = ?
               AND asset_id = ?
               AND event_id = ?
            """,
            (self._user_id, asset_id, event_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"asset {asset_id} is not in event {event_id}")
        self._conn.execute(
            """
            UPDATE asset_event_memberships
               SET event_id = NULL,
                   assignment_source = 'removed',
                   updated_at = CURRENT_TIMESTAMP
             WHERE user_id = ? AND asset_id = ?
            """,
            (self._user_id, asset_id),
        )
        self._conn.commit()
        self.write_audit(
            action="event.asset.remove",
            target_asset_id=asset_id,
            before_state=event_id,
            after_state=None,
            details_json=_json.dumps({"event_id": event_id}, sort_keys=True),
        )

    def reset_event_group(self, *, event_id: str) -> dict[str, Any]:
        """Reset event group.

        Parameters
        ----------
        event_id : str

        Returns
        -------
        dict[str, Any]
        """
        row = self._assert_visible_event(event_id)
        rows = self._conn.execute(
            """
            SELECT asset_id
              FROM asset_event_memberships
             WHERE user_id = ?
               AND event_id = ?
            """,
            (self._user_id, event_id),
        ).fetchall()
        asset_ids = [str(asset_row["asset_id"]) for asset_row in rows]
        for asset_id in asset_ids:
            analysis = self.get_asset_analysis(asset_id)
            if analysis is None:
                self._conn.execute(
                    "DELETE FROM asset_event_memberships "
                    "WHERE user_id = ? AND asset_id = ?",
                    (self._user_id, asset_id),
                )
                continue
            self._sync_asset_event_membership(
                asset_id=asset_id,
                analysis=analysis,
                preserve_manual=False,
            )
        if row["auto_key"] is None:
            self._conn.execute(
                """
                UPDATE event_groups
                   SET status = 'reset',
                       updated_at = CURRENT_TIMESTAMP
                 WHERE user_id = ? AND event_id = ?
                """,
                (self._user_id, event_id),
            )
        self._conn.commit()
        self.write_audit(
            action="event.reset",
            before_state=event_id,
            details_json=_json.dumps(
                {"event_id": event_id, "asset_ids": asset_ids},
                sort_keys=True,
            ),
        )
        return {"event_id": event_id, "reset_assets": len(asset_ids)}

    def list_review_assets_paginated(
        self,
        *,
        cursor: str | None,
        page_size: int,
        queue: str | None = None,
        media_kind: str | None = None,
        q: str | None = None,
        event_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List analyzed assets for review/search queues."""
        import json as _json

        page_size = max(1, min(int(page_size), 100))
        clauses = ["a.user_id = ?"]
        params: list[object] = [self._user_id]
        if cursor:
            clauses.append("a.asset_id > ?")
            params.append(cursor)
        if queue:
            clauses.append("aa.review_queues_json LIKE ?")
            params.append(f'%"{queue}"%')
        if media_kind:
            clauses.append("aa.media_kind = ?")
            params.append(media_kind)
        if q:
            clauses.append(
                "(aa.ocr_text LIKE ? OR aa.analysis_json LIKE ? OR a.asset_id LIKE ?)"
            )
            needle = f"%{q}%"
            params.extend([needle, needle, needle])
        if event_id:
            clauses.append("eg.event_id = ?")
            params.append(event_id)
        params.append(page_size + 1)
        sql = build_sql(
            """
            SELECT a.asset_id AS asset_id,
                   a.media_type AS media_type,
                   (SELECT action_name FROM actions
                      WHERE user_id = ? AND asset_id = a.asset_id
                      ORDER BY id DESC LIMIT 1) AS last_action,
                   (SELECT run_id FROM actions
                      WHERE user_id = ? AND asset_id = a.asset_id
                      ORDER BY id DESC LIMIT 1) AS last_run_id,
                   o.category_id AS override_category_id,
                   eg.event_id AS event_id,
                   eg.title AS event_title,
                   aa.analysis_json AS analysis_json
              FROM assets a
              JOIN asset_analysis aa
                ON aa.user_id = a.user_id AND aa.asset_id = a.asset_id
              LEFT JOIN asset_overrides o
                ON o.user_id = a.user_id AND o.asset_id = a.asset_id
              LEFT JOIN asset_event_memberships em
                ON em.user_id = a.user_id
               AND em.asset_id = a.asset_id
               AND em.event_id IS NOT NULL
              LEFT JOIN event_groups eg
                ON eg.user_id = em.user_id
               AND eg.event_id = em.event_id
               AND eg.status NOT IN ('merged', 'reset')
             WHERE """,
            " AND ".join(clauses),
            """
             ORDER BY a.asset_id ASC
             LIMIT ?
        """,
        )
        rows = self._conn.execute(
            sql,
            (self._user_id, self._user_id, *params),
        ).fetchall()
        next_cursor: str | None = None
        if len(rows) > page_size:
            rows = rows[:page_size]
            next_cursor = rows[-1]["asset_id"]
        out: list[dict[str, Any]] = []
        for row in rows:
            analysis = _json.loads(row["analysis_json"])
            if not isinstance(analysis, dict):
                analysis = {}
            out.append(
                {
                    "asset_id": str(row["asset_id"]),
                    "media_type": str(row["media_type"]),
                    "last_action": (
                        None if row["last_action"] is None else str(row["last_action"])
                    ),
                    "last_run_id": (
                        None if row["last_run_id"] is None else int(row["last_run_id"])
                    ),
                    "last_seen_category": (
                        None
                        if row["override_category_id"] is None
                        else str(row["override_category_id"])
                    ),
                    "analysis": analysis_summary(analysis),
                    "event_id": (
                        None if row["event_id"] is None else str(row["event_id"])
                    ),
                    "event_title": (
                        None if row["event_title"] is None else str(row["event_title"])
                    ),
                    "can_override": True,
                }
            )
        return out, next_cursor

    def list_user_asset_rows_by_ids(
        self,
        asset_ids: Sequence[str],
        *,
        queue: str | None = None,
        media_kind: str | None = None,
        event_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return user-visible asset rows for ranked ids, preserving id order."""
        ordered_ids: list[str] = []
        seen: set[str] = set()
        for asset_id in asset_ids:
            clean_id = str(asset_id)
            if not clean_id or clean_id in seen:
                continue
            seen.add(clean_id)
            ordered_ids.append(clean_id)
        if not ordered_ids:
            return []

        placeholders = ", ".join("?" for _ in ordered_ids)
        clauses = [
            "a.user_id = ?",
            f"a.asset_id IN ({placeholders})",
            """
            EXISTS (
                SELECT 1 FROM actions ax
                 WHERE ax.user_id = a.user_id AND ax.asset_id = a.asset_id
            )
            """,
        ]
        where_params: list[object] = [self._user_id, *ordered_ids]
        if queue:
            clauses.append("aa.review_queues_json LIKE ?")
            where_params.append(f'%"{queue}"%')
        if media_kind:
            clauses.append("aa.media_kind = ?")
            where_params.append(media_kind)
        if event_id:
            clauses.append("eg.event_id = ?")
            where_params.append(event_id)
        sql = build_sql(
            """
            SELECT a.asset_id AS asset_id,
                   a.media_type AS media_type,
                   (SELECT action_name FROM actions
                      WHERE user_id = ? AND asset_id = a.asset_id
                      ORDER BY id DESC LIMIT 1) AS last_action,
                   (SELECT run_id FROM actions
                      WHERE user_id = ? AND asset_id = a.asset_id
                      ORDER BY id DESC LIMIT 1) AS last_run_id,
                   o.category_id AS override_category_id,
                   eg.event_id AS event_id,
                   eg.title AS event_title,
                   aa.analysis_json AS analysis_json
              FROM assets a
              LEFT JOIN asset_overrides o
                ON o.user_id = a.user_id AND o.asset_id = a.asset_id
              LEFT JOIN asset_analysis aa
                ON aa.user_id = a.user_id AND aa.asset_id = a.asset_id
              LEFT JOIN asset_event_memberships em
                ON em.user_id = a.user_id
               AND em.asset_id = a.asset_id
               AND em.event_id IS NOT NULL
              LEFT JOIN event_groups eg
                ON eg.user_id = em.user_id
               AND eg.event_id = em.event_id
               AND eg.status NOT IN ('merged', 'reset')
             WHERE """,
            " AND ".join(clauses),
        )
        rows = self._conn.execute(
            sql,
            (self._user_id, self._user_id, *where_params),
        ).fetchall()
        rows_by_id = {
            str(row["asset_id"]): {
                "asset_id": str(row["asset_id"]),
                "media_type": str(row["media_type"]),
                "last_action": (
                    None if row["last_action"] is None else str(row["last_action"])
                ),
                "last_run_id": (
                    None if row["last_run_id"] is None else int(row["last_run_id"])
                ),
                "last_seen_category": (
                    None
                    if row["override_category_id"] is None
                    else str(row["override_category_id"])
                ),
                "analysis": _analysis_summary_from_json(row["analysis_json"]),
                "event_id": None if row["event_id"] is None else str(row["event_id"]),
                "event_title": (
                    None if row["event_title"] is None else str(row["event_title"])
                ),
                "can_override": True,
            }
            for row in rows
        }
        return [rows_by_id[asset_id] for asset_id in ordered_ids if asset_id in rows_by_id]

    # -- runs --------------------------------------------------------

    def start_run(self, *, dry_run: bool, command: str) -> int:
        """Start run.

        Parameters
        ----------
        dry_run : bool
        command : str

        Returns
        -------
        int
        """
        cursor = self._conn.execute(
            """
            INSERT INTO runs(user_id, status, dry_run, command)
            VALUES (?, 'running', ?, ?)
            """,
            (self._user_id, int(dry_run), command),
        )
        self._conn.commit()
        last_id = cursor.lastrowid
        assert last_id is not None  # INSERT just succeeded
        return int(last_id)

    def list_runs(self) -> list[sqlite3.Row]:
        """List runs.

        Returns
        -------
        list[sqlite3.Row]
        """
        cursor = self._conn.execute(
            "SELECT * FROM runs WHERE user_id = ? ORDER BY id",
            (self._user_id,),
        )
        return list(cursor.fetchall())

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        """Return run.

        Parameters
        ----------
        run_id : int

        Returns
        -------
        sqlite3.Row | None
        """
        cursor = self._conn.execute(
            "SELECT * FROM runs WHERE id = ? AND user_id = ?",
            (run_id, self._user_id),
        )
        return cast("sqlite3.Row | None", cursor.fetchone())

    # -- actions / errors --------------------------------------------

    def record_action(
        self,
        *,
        run_id: int,
        asset_id: str,
        action_name: str,
        dry_run: bool,
        would_apply: bool,
        success: bool | None = None,
        error_code: str | None = None,
    ) -> int:
        """Record action.

        Parameters
        ----------
        run_id : int
        asset_id : str
        action_name : str
        dry_run : bool
        would_apply : bool
        success : bool | None, optional
        error_code : str | None, optional

        Returns
        -------
        int
        """
        self._assert_owns_run(run_id)
        cursor = self._conn.execute(
            """
            INSERT INTO actions(
                user_id, run_id, asset_id, action_name,
                dry_run, would_apply, success, error_code
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._user_id,
                run_id,
                asset_id,
                action_name,
                int(dry_run),
                int(would_apply),
                None if success is None else int(success),
                error_code,
            ),
        )
        self._conn.commit()
        last_id = cursor.lastrowid
        assert last_id is not None  # INSERT just succeeded
        return int(last_id)

    def list_actions(self) -> list[sqlite3.Row]:
        """List actions.

        Returns
        -------
        list[sqlite3.Row]
        """
        cursor = self._conn.execute(
            "SELECT * FROM actions WHERE user_id = ? ORDER BY id",
            (self._user_id,),
        )
        return list(cursor.fetchall())

    def record_error(
        self,
        *,
        stage: str,
        message_code: str,
        run_id: int | None = None,
        asset_id: str | None = None,
        message: str | None = None,
    ) -> int:
        """Record error.

        Parameters
        ----------
        stage : str
        message_code : str
        run_id : int | None, optional
        asset_id : str | None, optional
        message : str | None, optional

        Returns
        -------
        int
        """
        if run_id is not None:
            self._assert_owns_run(run_id)
        cursor = self._conn.execute(
            """
            INSERT INTO errors(user_id, run_id, asset_id, stage, message_code, message)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (self._user_id, run_id, asset_id, stage, message_code, message),
        )
        self._conn.commit()
        last_id = cursor.lastrowid
        assert last_id is not None  # INSERT just succeeded
        return int(last_id)

    def list_errors(self) -> list[sqlite3.Row]:
        """List errors.

        Returns
        -------
        list[sqlite3.Row]
        """
        cursor = self._conn.execute(
            "SELECT * FROM errors WHERE user_id = ? ORDER BY id",
            (self._user_id,),
        )
        return list(cursor.fetchall())

    # -- config ------------------------------------------------------

    def get_config(self) -> dict[str, Any]:
        """Return config.

        Returns
        -------
        dict[str, Any]
        """
        cursor = self._conn.execute(
            "SELECT categories_json, policies_json FROM user_config WHERE user_id = ?",
            (self._user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return {"categories": {}, "policies": {}}
        import json as _json

        return {
            "categories": _json.loads(row["categories_json"]),
            "policies": _json.loads(row["policies_json"]),
        }

    def set_categories(self, categories: dict[str, Any]) -> None:
        """Set categories.

        Parameters
        ----------
        categories : dict[str, Any]

        Returns
        -------
        None
        """
        import json as _json

        self._conn.execute(
            """
            INSERT INTO user_config(user_id, categories_json)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                categories_json = excluded.categories_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (self._user_id, _json.dumps(categories, sort_keys=True)),
        )
        self._conn.commit()

    def mark_model_seen(self, sha256: str) -> None:
        """Mark model seen.

        Parameters
        ----------
        sha256 : str

        Returns
        -------
        None
        """
        self._conn.execute(
            """
            INSERT INTO user_config(user_id, last_seen_model_sha256)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                last_seen_model_sha256 = excluded.last_seen_model_sha256,
                updated_at = CURRENT_TIMESTAMP
            """,
            (self._user_id, sha256),
        )
        self._conn.commit()

    def last_seen_model_sha256(self) -> str | None:
        """Last seen model sha256.

        Returns
        -------
        str | None
        """
        cursor = self._conn.execute(
            "SELECT last_seen_model_sha256 FROM user_config WHERE user_id = ?",
            (self._user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return None if row["last_seen_model_sha256"] is None else str(row["last_seen_model_sha256"])

    def set_policies(self, policies: dict[str, Any]) -> None:
        """Set policies.

        Parameters
        ----------
        policies : dict[str, Any]

        Returns
        -------
        None
        """
        import json as _json

        self._conn.execute(
            """
            INSERT INTO user_config(user_id, policies_json)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                policies_json = excluded.policies_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (self._user_id, _json.dumps(policies, sort_keys=True)),
        )
        self._conn.commit()

    # -- scans -------------------------------------------------------

    def has_active_run(self) -> bool:
        """Indicate whether active run.

        Returns
        -------
        bool
        """
        cursor = self._conn.execute(
            "SELECT 1 FROM runs WHERE user_id = ? AND status = 'running' LIMIT 1",
            (self._user_id,),
        )
        return cursor.fetchone() is not None

    def runs_started_today(self, *, since_iso: str) -> int:
        """Return the number of runs started today.

        Parameters
        ----------
        since_iso : str

        Returns
        -------
        int
        """
        cursor = self._conn.execute(
            "SELECT COUNT(*) AS c FROM runs WHERE user_id = ? AND started_at >= ?",
            (self._user_id, since_iso),
        )
        return int(cursor.fetchone()["c"])

    def finish_run(self, run_id: int, *, status: str, summary_json: str | None = None) -> None:
        """Finish run.

        Parameters
        ----------
        run_id : int
        status : str
        summary_json : str | None, optional

        Returns
        -------
        None
        """
        self._assert_owns_run(run_id)
        self._conn.execute(
            "UPDATE runs SET status = ?, ended_at = CURRENT_TIMESTAMP, summary_json = ? "
            "WHERE id = ? AND user_id = ?",
            (status, summary_json, run_id, self._user_id),
        )
        self._conn.commit()

    def revert_run_actions(self, run_id: int) -> int:
        """Revert run actions.

        Parameters
        ----------
        run_id : int

        Returns
        -------
        int
        """
        self._assert_owns_run(run_id)
        cursor = self._conn.execute(
            "UPDATE actions SET success = 0, error_code = 'reverted' "
            "WHERE user_id = ? AND run_id = ? AND success = 1",
            (self._user_id, run_id),
        )
        self._conn.commit()
        return cursor.rowcount

    # -- audit -------------------------------------------------------

    def write_audit(
        self,
        *,
        action: str,
        target_asset_id: str | None = None,
        run_id: int | None = None,
        before_state: str | None = None,
        after_state: str | None = None,
        details_json: str | None = None,
    ) -> int:
        """Write audit.

        Parameters
        ----------
        action : str
        target_asset_id : str | None, optional
        run_id : int | None, optional
        before_state : str | None, optional
        after_state : str | None, optional
        details_json : str | None, optional

        Returns
        -------
        int
        """
        cursor = self._conn.execute(
            """
            INSERT INTO audit_log(
                user_id, action, target_asset_id, run_id,
                before_state, after_state, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self._user_id,
                action,
                target_asset_id,
                run_id,
                before_state,
                after_state,
                details_json,
            ),
        )
        self._conn.commit()
        last_id = cursor.lastrowid
        assert last_id is not None  # INSERT just succeeded
        return int(last_id)

    def list_audit(self) -> list[sqlite3.Row]:
        """List audit.

        Returns
        -------
        list[sqlite3.Row]
        """
        cursor = self._conn.execute(
            "SELECT * FROM audit_log WHERE user_id = ? ORDER BY id",
            (self._user_id,),
        )
        return list(cursor.fetchall())

    # -- sessions ----------------------------------------------------

    def create_session(
        self,
        *,
        session_id: str,
        encrypted_immich_token: bytes,
        expires_at: str,
    ) -> None:
        """Create session.

        Parameters
        ----------
        session_id : str
        encrypted_immich_token : bytes
        expires_at : str

        Returns
        -------
        None
        """
        self._conn.execute(
            """
            INSERT INTO sessions(session_id, user_id, encrypted_immich_token, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, self._user_id, encrypted_immich_token, expires_at),
        )
        self._conn.commit()

    def list_sessions(self) -> list[sqlite3.Row]:
        """List sessions.

        Returns
        -------
        list[sqlite3.Row]
        """
        cursor = self._conn.execute(
            "SELECT * FROM sessions WHERE user_id = ? ORDER BY created_at",
            (self._user_id,),
        )
        return list(cursor.fetchall())

    # -- api keys ----------------------------------------------------

    def store_api_key(
        self,
        *,
        encrypted_key: bytes,
        label: str | None = None,
    ) -> int:
        """Store api key.

        Parameters
        ----------
        encrypted_key : bytes
        label : str | None, optional

        Returns
        -------
        int
        """
        cursor = self._conn.execute(
            """
            INSERT INTO user_api_keys(user_id, label, encrypted_key)
            VALUES (?, ?, ?)
            """,
            (self._user_id, label, encrypted_key),
        )
        self._conn.commit()
        last_id = cursor.lastrowid
        assert last_id is not None  # INSERT just succeeded
        return int(last_id)

    def list_api_keys(self) -> list[sqlite3.Row]:
        """List api keys.

        Returns
        -------
        list[sqlite3.Row]
        """
        cursor = self._conn.execute(
            "SELECT * FROM user_api_keys WHERE user_id = ? ORDER BY id",
            (self._user_id,),
        )
        return list(cursor.fetchall())

    # -- asset overrides ---------------------------------------------

    def set_asset_override(
        self,
        *,
        asset_id: str,
        category_id: str | None,
        reason: str = "manual",
    ) -> str | None:
        """Upsert a manual override and return the previous category_id.

        Returns ``None`` if no prior override existed; the caller can
        use this for the audit-log ``before_state``. Idempotent on
        ``(user_id, asset_id)``.
        """
        cursor = self._conn.execute(
            "SELECT category_id FROM asset_overrides "
            "WHERE user_id = ? AND asset_id = ?",
            (self._user_id, asset_id),
        )
        prior = cursor.fetchone()
        before = None if prior is None else prior["category_id"]
        self._conn.execute(
            """
            INSERT INTO asset_overrides(user_id, asset_id, category_id, reason)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, asset_id) DO UPDATE SET
                category_id = excluded.category_id,
                reason = excluded.reason,
                set_at = CURRENT_TIMESTAMP
            """,
            (self._user_id, asset_id, category_id, reason),
        )
        self._conn.commit()
        return before

    def get_asset_override(self, asset_id: str) -> str | None:
        """Return asset override.

        Parameters
        ----------
        asset_id : str

        Returns
        -------
        str | None
        """
        cursor = self._conn.execute(
            "SELECT category_id FROM asset_overrides "
            "WHERE user_id = ? AND asset_id = ?",
            (self._user_id, asset_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return None if row["category_id"] is None else str(row["category_id"])

    def asset_id_in_user_actions(self, asset_id: str) -> bool:
        """Asset id in user actions.

        Parameters
        ----------
        asset_id : str

        Returns
        -------
        bool
        """
        cursor = self._conn.execute(
            "SELECT 1 FROM actions WHERE user_id = ? AND asset_id = ? LIMIT 1",
            (self._user_id, asset_id),
        )
        return cursor.fetchone() is not None

    def list_user_assets_paginated(
        self,
        *,
        cursor: str | None,
        page_size: int,
        event_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """List assets that appear in this user's actions, newest first.

        Each row carries the most recent action_name + run_id and the
        manual override category (if any). The cursor is the last
        ``asset_id`` from the previous page.
        """
        page_size = max(1, min(int(page_size), 100))
        params: list[object] = [self._user_id, self._user_id, self._user_id]
        cursor_clause = ""
        if cursor:
            cursor_clause = " AND a.asset_id > ?"
            params.append(cursor)
        event_clause = ""
        if event_id:
            event_clause = " AND eg.event_id = ?"
            params.append(event_id)
        params.append(page_size + 1)
        sql = build_sql(
            """
            SELECT a.asset_id AS asset_id,
                   a.media_type AS media_type,
                   (SELECT action_name FROM actions
                      WHERE user_id = ? AND asset_id = a.asset_id
                      ORDER BY id DESC LIMIT 1) AS last_action,
                   (SELECT run_id FROM actions
                      WHERE user_id = ? AND asset_id = a.asset_id
                      ORDER BY id DESC LIMIT 1) AS last_run_id,
                   o.category_id AS override_category_id,
                   eg.event_id AS event_id,
                   eg.title AS event_title,
                   aa.analysis_json AS analysis_json
              FROM assets a
              LEFT JOIN asset_overrides o
                ON o.user_id = a.user_id AND o.asset_id = a.asset_id
              LEFT JOIN asset_analysis aa
                ON aa.user_id = a.user_id AND aa.asset_id = a.asset_id
              LEFT JOIN asset_event_memberships em
                ON em.user_id = a.user_id
               AND em.asset_id = a.asset_id
               AND em.event_id IS NOT NULL
              LEFT JOIN event_groups eg
                ON eg.user_id = em.user_id
               AND eg.event_id = em.event_id
               AND eg.status NOT IN ('merged', 'reset')
             WHERE a.user_id = ?""",
            cursor_clause,
            """
               AND EXISTS (
                   SELECT 1 FROM actions ax
                    WHERE ax.user_id = a.user_id AND ax.asset_id = a.asset_id
               )""",
            event_clause,
            """
             ORDER BY a.asset_id ASC
             LIMIT ?
        """,
        )
        rows = self._conn.execute(sql, tuple(params)).fetchall()
        next_cursor: str | None = None
        if len(rows) > page_size:
            rows = rows[:page_size]
            next_cursor = rows[-1]["asset_id"]
        return (
            [
                {
                    "asset_id": str(row["asset_id"]),
                    "media_type": str(row["media_type"]),
                    "last_action": (
                        None if row["last_action"] is None else str(row["last_action"])
                    ),
                    "last_run_id": (
                        None if row["last_run_id"] is None else int(row["last_run_id"])
                    ),
                    "last_seen_category": (
                        None
                        if row["override_category_id"] is None
                        else str(row["override_category_id"])
                    ),
                    "analysis": _analysis_summary_from_json(row["analysis_json"]),
                    "event_id": (
                        None if row["event_id"] is None else str(row["event_id"])
                    ),
                    "event_title": (
                        None if row["event_title"] is None else str(row["event_title"])
                    ),
                    "can_override": True,
                }
                for row in rows
            ],
            next_cursor,
        )

    # -- auto-scan ---------------------------------------------------

    def get_auto_scan(self) -> dict[str, Any]:
        """Return auto scan.

        Returns
        -------
        dict[str, Any]
        """
        cursor = self._conn.execute(
            "SELECT enabled, interval_minutes, last_seen_taken_at, "
            "last_run_at, last_status, last_error_code "
            "FROM user_auto_scan WHERE user_id = ?",
            (self._user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return {
                "enabled": False,
                "interval_minutes": 30,
                "last_seen_taken_at": None,
                "last_run_at": None,
                "last_status": None,
                "last_error_code": None,
            }
        return {
            "enabled": bool(row["enabled"]),
            "interval_minutes": int(row["interval_minutes"]),
            "last_seen_taken_at": row["last_seen_taken_at"],
            "last_run_at": row["last_run_at"],
            "last_status": row["last_status"],
            "last_error_code": row["last_error_code"],
        }

    def set_auto_scan(self, *, enabled: bool, interval_minutes: int) -> None:
        """Set auto scan.

        Parameters
        ----------
        enabled : bool
        interval_minutes : int

        Returns
        -------
        None
        """
        self._conn.execute(
            """
            INSERT INTO user_auto_scan(user_id, enabled, interval_minutes)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                enabled = excluded.enabled,
                interval_minutes = excluded.interval_minutes,
                updated_at = CURRENT_TIMESTAMP
            """,
            (self._user_id, int(enabled), int(interval_minutes)),
        )
        self._conn.commit()

    def record_auto_scan_tick(
        self,
        *,
        now_iso: str,
        status: str,
        error_code: str | None,
        last_seen_taken_at: str | None,
        disable: bool = False,
    ) -> None:
        # Ensure a row exists; the coordinator only iterates enabled
        # rows so usually one already does, but the upsert keeps this
        # helper safe to call from tests too.
        """Record auto scan tick.

        Parameters
        ----------
        now_iso : str
        status : str
        error_code : str | None
        last_seen_taken_at : str | None
        disable : bool, optional

        Returns
        -------
        None
        """
        self._conn.execute(
            """
            INSERT INTO user_auto_scan(user_id, enabled, interval_minutes)
            VALUES (?, 0, 30)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (self._user_id,),
        )
        if last_seen_taken_at is None:
            self._conn.execute(
                """
                UPDATE user_auto_scan SET
                    last_run_at = ?,
                    last_status = ?,
                    last_error_code = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (now_iso, status, error_code, self._user_id),
            )
        else:
            self._conn.execute(
                """
                UPDATE user_auto_scan SET
                    last_seen_taken_at = ?,
                    last_run_at = ?,
                    last_status = ?,
                    last_error_code = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE user_id = ?
                """,
                (
                    last_seen_taken_at,
                    now_iso,
                    status,
                    error_code,
                    self._user_id,
                ),
            )
        if disable:
            self._conn.execute(
                "UPDATE user_auto_scan SET enabled = 0 WHERE user_id = ?",
                (self._user_id,),
            )
        self._conn.commit()

    # -- account purge -----------------------------------------------

    def purge(self) -> None:
        """Idempotent purge of every row that belongs to this tenant.

        - All user-scoped tables (sessions, user_api_keys, actions,
          errors, assets, runs, user_config) lose their rows.
        - ``sessions.encrypted_immich_token`` and
          ``user_api_keys.encrypted_key`` blobs are zeroed in place
          before the row is deleted, so a recovered DB page does not
          yield a decryptable ciphertext.
        - ``audit_log`` rows are anonymized in place by rewriting
          ``user_id`` to a sentinel ``"user_deleted"`` (the threat
          model accepts either delete-or-anonymize; anonymize-in-place
          preserves the audit trail).
        - The ``users`` row is then deleted.
        """
        conn = self._conn
        # Zero the encrypted blobs first, then delete the rows.
        conn.execute(
            "UPDATE sessions SET encrypted_immich_token = zeroblob(length(encrypted_immich_token)) "
            "WHERE user_id = ?",
            (self._user_id,),
        )
        conn.execute(
            "UPDATE user_api_keys SET encrypted_key = zeroblob(length(encrypted_key)) "
            "WHERE user_id = ?",
            (self._user_id,),
        )
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (self._user_id,))
        conn.execute("DELETE FROM user_api_keys WHERE user_id = ?", (self._user_id,))
        conn.execute("DELETE FROM actions WHERE user_id = ?", (self._user_id,))
        conn.execute("DELETE FROM errors WHERE user_id = ?", (self._user_id,))
        conn.execute(
            "DELETE FROM asset_event_memberships WHERE user_id = ?",
            (self._user_id,),
        )
        conn.execute("DELETE FROM event_groups WHERE user_id = ?", (self._user_id,))
        conn.execute("DELETE FROM asset_analysis WHERE user_id = ?", (self._user_id,))
        conn.execute("DELETE FROM assets WHERE user_id = ?", (self._user_id,))
        conn.execute("DELETE FROM runs WHERE user_id = ?", (self._user_id,))
        conn.execute("DELETE FROM user_config WHERE user_id = ?", (self._user_id,))
        conn.execute("DELETE FROM asset_overrides WHERE user_id = ?", (self._user_id,))
        conn.execute("DELETE FROM user_auto_scan WHERE user_id = ?", (self._user_id,))

        # Ensure the anonymization sentinel user exists so the audit_log
        # FK stays satisfied after rewrite.
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id, email, name, is_admin) "
            "VALUES ('user_deleted', '', NULL, 0)"
        )
        conn.execute(
            "UPDATE audit_log SET user_id = 'user_deleted' WHERE user_id = ?",
            (self._user_id,),
        )
        conn.execute("DELETE FROM users WHERE user_id = ?", (self._user_id,))
        conn.commit()

    # -- internals ---------------------------------------------------

    def _sync_asset_event_membership(
        self,
        *,
        asset_id: str,
        analysis: Mapping[str, Any],
        preserve_manual: bool,
    ) -> str | None:
        event_input = _event_group_input_from_analysis(analysis)
        current = self._conn.execute(
            """
            SELECT event_id, assignment_source
              FROM asset_event_memberships
             WHERE user_id = ? AND asset_id = ?
            """,
            (self._user_id, asset_id),
        ).fetchone()
        if event_input is None:
            if current is not None and (
                not preserve_manual or current["assignment_source"] == "auto"
            ):
                self._conn.execute(
                    "DELETE FROM asset_event_memberships "
                    "WHERE user_id = ? AND asset_id = ?",
                    (self._user_id, asset_id),
                )
            return None
        if (
            preserve_manual
            and current is not None
            and current["assignment_source"] in {"manual", "removed"}
        ):
            return None if current["event_id"] is None else str(current["event_id"])

        event_id = self._ensure_auto_event_group(
            event_input,
            restore_manual=not preserve_manual,
        )
        self._conn.execute(
            """
            INSERT INTO asset_event_memberships(
                user_id, asset_id, event_id, auto_event_key, assignment_source
            )
            VALUES (?, ?, ?, ?, 'auto')
            ON CONFLICT(user_id, asset_id) DO UPDATE SET
                event_id = excluded.event_id,
                auto_event_key = excluded.auto_event_key,
                assignment_source = 'auto',
                updated_at = CURRENT_TIMESTAMP
            """,
            (self._user_id, asset_id, event_id, event_input["auto_key"]),
        )
        return event_id

    def _ensure_auto_event_group(
        self,
        event_input: Mapping[str, Any],
        *,
        restore_manual: bool,
    ) -> str:
        auto_key = str(event_input["auto_key"])
        title = str(event_input["title"])
        sort_at = (
            None if event_input.get("sort_at") is None else str(event_input["sort_at"])
        )
        source_json = _json.dumps(event_input["source"], sort_keys=True)
        event_id = _event_id_for_auto_key(auto_key)
        row = self._conn.execute(
            """
            SELECT event_id, status, source_json
              FROM event_groups
             WHERE user_id = ? AND auto_key = ?
            """,
            (self._user_id, auto_key),
        ).fetchone()
        if row is None:
            self._conn.execute(
                """
                INSERT INTO event_groups(
                    user_id, event_id, auto_key, title, status, sort_at, source_json
                )
                VALUES (?, ?, ?, ?, 'auto', ?, ?)
                """,
                (self._user_id, event_id, auto_key, title, sort_at, source_json),
            )
            return event_id

        existing_event_id = str(row["event_id"])
        if row["status"] == "merged" and not restore_manual:
            source = _source_from_json(row["source_json"])
            merged_into = source.get("merged_into_event_id")
            if isinstance(merged_into, str) and merged_into:
                return merged_into
        if row["status"] == "auto" or restore_manual:
            self._conn.execute(
                """
                UPDATE event_groups
                   SET title = ?,
                       status = 'auto',
                       sort_at = ?,
                       source_json = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE user_id = ? AND event_id = ?
                """,
                (title, sort_at, source_json, self._user_id, existing_event_id),
            )
        else:
            self._conn.execute(
                """
                UPDATE event_groups
                   SET sort_at = ?,
                       source_json = ?,
                       updated_at = CURRENT_TIMESTAMP
                 WHERE user_id = ? AND event_id = ?
                """,
                (sort_at, source_json, self._user_id, existing_event_id),
            )
        return existing_event_id

    def _assert_visible_event(self, event_id: str) -> sqlite3.Row:
        row = self._conn.execute(
            """
            SELECT event_id, auto_key, title, status, sort_at, source_json
              FROM event_groups
             WHERE user_id = ?
               AND event_id = ?
               AND status NOT IN ('merged', 'reset')
            """,
            (self._user_id, event_id),
        ).fetchone()
        if row is None:
            raise LookupError(f"event {event_id} not found")
        return cast(sqlite3.Row, row)

    def _new_manual_event_id(self) -> str:
        while True:
            event_id = f"manual-{uuid.uuid4().hex[:16]}"
            row = self._conn.execute(
                "SELECT 1 FROM event_groups WHERE user_id = ? AND event_id = ?",
                (self._user_id, event_id),
            ).fetchone()
            if row is None:
                return event_id

    def _assert_owns_run(self, run_id: int) -> None:
        cursor = self._conn.execute(
            "SELECT user_id FROM runs WHERE id = ?", (run_id,)
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"run {run_id} not found")
        if row["user_id"] != self._user_id:
            raise PermissionError(
                f"run {run_id} does not belong to user {self._user_id}"
            )

    def _assert_owns_asset(self, asset_id: str) -> None:
        cursor = self._conn.execute(
            "SELECT user_id FROM assets WHERE user_id = ? AND asset_id = ?",
            (self._user_id, asset_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise LookupError(f"asset {asset_id} not found")


def _analysis_summary_from_json(value: object) -> dict[str, Any] | None:
    if not isinstance(value, str) or not value:
        return None
    import json as _json

    try:
        data = _json.loads(value)
    except _json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return analysis_summary(data)


def _event_group_input_from_analysis(
    analysis: Mapping[str, Any],
) -> dict[str, Any] | None:
    events = analysis.get("events")
    if not isinstance(events, Mapping):
        return None
    raw_key = events.get("event_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return None
    auto_key = raw_key.strip()
    media_info = analysis.get("media_info")
    if not isinstance(media_info, Mapping):
        media_info = {}
    semantic = analysis.get("semantic")
    if not isinstance(semantic, Mapping):
        semantic = {}

    day = _optional_text(events.get("day"))
    city = _optional_text(media_info.get("city"))
    country = _optional_text(media_info.get("country"))
    place = ", ".join(part for part in (city, country) if part)
    albums = _strings_from(media_info.get("albums"), limit=8)
    people = _people_names_from_analysis(analysis.get("people"))
    terms = _strings_from(semantic.get("terms"), limit=12)
    title_parts = [
        part
        for part in (
            day,
            place,
            ", ".join(people[:2]),
            albums[0] if albums else None,
        )
        if part
    ]
    title = _normalize_event_title(" - ".join(title_parts) if title_parts else auto_key)
    return {
        "auto_key": auto_key,
        "title": title,
        "sort_at": day,
        "source": {
            "auto_event_key": auto_key,
            "day": day,
            "place": place or None,
            "people": people,
            "albums": albums,
            "semantic_terms": terms,
        },
    }


def _event_id_for_auto_key(auto_key: str) -> str:
    digest = hashlib.sha256(auto_key.encode("utf-8")).hexdigest()[:16]
    return f"auto-{digest}"


def _normalize_event_title(title: str) -> str:
    clean = " ".join(str(title).strip().split())
    if not clean:
        raise ValueError("event title is required")
    return clean[:160]


def _event_group_from_row(row: sqlite3.Row) -> dict[str, Any]:
    out = _event_group_record_from_row(row)
    out["asset_count"] = int(row["asset_count"])
    return out


def _event_group_record_from_row(row: sqlite3.Row) -> dict[str, Any]:
    row_keys = set(row.keys())
    return {
        "event_id": str(row["event_id"]),
        "auto_key": None if row["auto_key"] is None else str(row["auto_key"]),
        "title": str(row["title"]),
        "status": str(row["status"]),
        "sort_at": None if row["sort_at"] is None else str(row["sort_at"]),
        "source": _source_from_json(row["source_json"]),
        "created_at": str(row["created_at"]) if "created_at" in row_keys else None,
        "updated_at": str(row["updated_at"]) if "updated_at" in row_keys else None,
    }


def _source_from_json(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        data = _json.loads(value)
    except _json.JSONDecodeError:
        return {}
    return dict(data) if isinstance(data, Mapping) else {}


def _dedupe_event_ids(values: Sequence[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value).strip()
        if clean and clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    clean = str(value).strip()
    return clean or None


def _strings_from(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        clean = _optional_text(item)
        if clean is None or clean in seen:
            continue
        seen.add(clean)
        out.append(clean)
        if len(out) >= limit:
            break
    return out


def _people_names_from_analysis(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return []
    names: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        clean = _optional_text(item.get("name")) or _optional_text(item.get("id"))
        if clean is None or clean in seen:
            continue
        seen.add(clean)
        names.append(clean)
    return names


__all__ = [
    "SERVICE_SCHEMA_SQL",
    "SERVICE_SCHEMA_VERSION",
    "StateStore",
    "UserRecord",
    "UserScopedState",
]


# Silence unused-import linters in environments that strip type-only imports.
_ = (Any, Mapping)
