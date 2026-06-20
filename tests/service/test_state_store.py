"""Multi-tenant isolation tests for the state store.

These tests are the load-bearing proof for ADR-0010's multi-tenant
isolation non-negotiable: two users' rows must be invisible to each
other through every read API exposed by ``UserScopedState``.
"""

from __future__ import annotations

import sqlite3

import pytest

from mediarefinery.service.state_store import (
    SERVICE_SCHEMA_VERSION,
    StateStore,
    _validate_user_id,
)


@pytest.fixture
def store(tmp_path):
    db = StateStore(tmp_path / "state.db")
    db.initialize()
    db.upsert_user(user_id="alice", email="alice@example.invalid")
    db.upsert_user(user_id="bob", email="bob@example.invalid")
    try:
        yield db
    finally:
        db.close()


def test_schema_version_pinned(store):
    """Test schema version pinned."""
    assert store.schema_version() == SERVICE_SCHEMA_VERSION


def test_expected_tables_present(store):
    """Test expected tables present."""
    rows = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    names = {row["name"] for row in rows}
    assert {
        "users",
        "sessions",
        "user_api_keys",
        "audit_log",
        "model_registry",
        "assets",
        "runs",
        "actions",
        "errors",
        "asset_analysis",
        "event_groups",
        "asset_event_memberships",
    } <= names


def test_user_id_validation():
    """Test user id validation."""
    with pytest.raises(ValueError):
        _validate_user_id("")
    with pytest.raises(ValueError):
        _validate_user_id("has spaces")
    with pytest.raises(ValueError):
        _validate_user_id("a" * 200)
    assert _validate_user_id("user-123") == "user-123"
    assert _validate_user_id("user@example.com") == "user@example.com"


def test_assets_isolated_between_users(store):
    """Test assets isolated between users."""
    a = store.with_user("alice")
    b = store.with_user("bob")
    a.upsert_asset(asset_id="asset-A1", media_type="image", checksum="aaa")
    a.upsert_asset(asset_id="asset-A2", media_type="image", checksum="bbb")
    b.upsert_asset(asset_id="asset-B1", media_type="image", checksum="ccc")

    a_assets = {row["asset_id"] for row in a.list_assets()}
    b_assets = {row["asset_id"] for row in b.list_assets()}
    assert a_assets == {"asset-A1", "asset-A2"}
    assert b_assets == {"asset-B1"}
    assert a_assets.isdisjoint(b_assets)


def test_runs_actions_errors_audit_isolated(store):
    """Test runs actions errors audit isolated."""
    a = store.with_user("alice")
    b = store.with_user("bob")
    run_a = a.start_run(dry_run=True, command="scan")
    run_b = b.start_run(dry_run=True, command="scan")
    a.record_action(
        run_id=run_a, asset_id="x", action_name="tag",
        dry_run=True, would_apply=True,
    )
    b.record_action(
        run_id=run_b, asset_id="y", action_name="tag",
        dry_run=True, would_apply=True,
    )
    a.record_error(stage="scan", message_code="oops", run_id=run_a)
    a.write_audit(action="login", details_json='{"ip":"redacted"}')
    b.write_audit(action="login", details_json='{"ip":"redacted"}')

    assert [r["id"] for r in a.list_runs()] == [run_a]
    assert [r["id"] for r in b.list_runs()] == [run_b]
    assert a.get_run(run_b) is None
    assert b.get_run(run_a) is None
    assert len(a.list_actions()) == 1 and a.list_actions()[0]["asset_id"] == "x"
    assert len(b.list_actions()) == 1 and b.list_actions()[0]["asset_id"] == "y"
    assert len(a.list_errors()) == 1 and len(b.list_errors()) == 0
    assert len(a.list_audit()) == 1 and len(b.list_audit()) == 1


def test_action_on_other_users_run_is_refused(store):
    """Test action on other users run is refused."""
    a = store.with_user("alice")
    b = store.with_user("bob")
    run_a = a.start_run(dry_run=False, command="scan")
    with pytest.raises(PermissionError):
        b.record_action(
            run_id=run_a, asset_id="x", action_name="tag",
            dry_run=False, would_apply=True,
        )
    with pytest.raises(PermissionError):
        b.record_error(stage="scan", message_code="x", run_id=run_a)


def test_sessions_and_api_keys_isolated(store):
    """Test sessions and api keys isolated."""
    a = store.with_user("alice")
    b = store.with_user("bob")
    a.create_session(
        session_id="sess-a", encrypted_immich_token=b"\x01\x02",
        expires_at="2099-01-01T00:00:00Z",
    )
    b.create_session(
        session_id="sess-b", encrypted_immich_token=b"\x03\x04",
        expires_at="2099-01-01T00:00:00Z",
    )
    a.store_api_key(encrypted_key=b"\xaa", label="alice-key")
    b.store_api_key(encrypted_key=b"\xbb", label="bob-key")

    a_sessions = {row["session_id"] for row in a.list_sessions()}
    b_sessions = {row["session_id"] for row in b.list_sessions()}
    assert a_sessions == {"sess-a"}
    assert b_sessions == {"sess-b"}

    a_keys = {row["label"] for row in a.list_api_keys()}
    b_keys = {row["label"] for row in b.list_api_keys()}
    assert a_keys == {"alice-key"}
    assert b_keys == {"bob-key"}


def test_unknown_user_id_for_run_raises(store):
    """Test unknown user id for run raises."""
    a = store.with_user("alice")
    with pytest.raises(LookupError):
        a._assert_owns_run(99999)


def test_user_id_format_rejected_at_with_user(store):
    """Test user id format rejected at with user."""
    with pytest.raises(ValueError):
        store.with_user("nope nope")


def test_foreign_key_cascade_on_user_delete(store):
    """Test foreign key cascade on user delete."""
    a = store.with_user("alice")
    a.upsert_asset(asset_id="asset-A1", media_type="image")
    run_a = a.start_run(dry_run=True, command="scan")
    a.record_action(
        run_id=run_a, asset_id="asset-A1", action_name="tag",
        dry_run=True, would_apply=True,
    )
    store._conn.execute("DELETE FROM users WHERE user_id = ?", ("alice",))
    store._conn.commit()
    assert a.list_assets() == []
    assert a.list_runs() == []
    assert a.list_actions() == []


def test_foreign_keys_pragma_is_on(store):
    """Test foreign keys pragma is on."""
    row = store._conn.execute("PRAGMA foreign_keys").fetchone()
    assert int(row[0]) == 1


def test_service_state_db_uses_configured_path(tmp_path):
    """The service state store creates exactly the configured DB path."""
    state_path = tmp_path / "state.db"
    db = StateStore(state_path)
    db.initialize()
    db.close()
    assert state_path.exists()
    assert db.path == state_path


def test_raw_sql_outside_scoped_helpers_can_still_leak(store):
    """Documents the load-bearing assumption: isolation is enforced by
    ``UserScopedState``, not by SQLite. Code must go through with_user."""
    a = store.with_user("alice")
    b = store.with_user("bob")
    a.upsert_asset(asset_id="leak-test", media_type="image")
    cursor = store._conn.execute("SELECT COUNT(*) AS c FROM assets")
    assert int(cursor.fetchone()["c"]) == 1
    assert b.list_assets() == []


def test_in_memory_store_works():
    """Test in memory store works."""
    db = StateStore(":memory:")
    db.initialize()
    try:
        db.upsert_user(user_id="x", email="x@example.invalid")
        scoped = db.with_user("x")
        scoped.upsert_asset(asset_id="a1", media_type="image")
        assert len(scoped.list_assets()) == 1
    finally:
        db.close()


def test_connection_uses_row_factory(store):
    """Test connection uses row factory."""
    assert store._conn.row_factory is sqlite3.Row
