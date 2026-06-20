"""POST /api/me/assets/{id}/category + GET /api/me/assets.

Covers manual category override semantics: idempotency on
``(user_id, asset_id)``, audit-row provenance, surfacing through the
list endpoint, and cross-tenant rejection.
"""

from __future__ import annotations

import json

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from mediarefinery.service.app import API_PREFIX, create_app  # noqa: E402
from mediarefinery.service.config import ServiceConfig  # noqa: E402
from mediarefinery.service.security import CSRF_COOKIE_NAME  # noqa: E402

ALICE_PW = "alice-pw-not-real"
BOB_PW = "bob-pw-not-real"


def _login_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body.get("email") == "alice@x.invalid" and body.get("password") == ALICE_PW:
        return httpx.Response(
            201,
            json={
                "accessToken": "alice-token",
                "userId": "user-alice",
                "userEmail": "alice@x.invalid",
                "name": "Alice",
                "isAdmin": False,
            },
        )
    if body.get("email") == "bob@x.invalid" and body.get("password") == BOB_PW:
        return httpx.Response(
            201,
            json={
                "accessToken": "bob-token",
                "userId": "user-bob",
                "userEmail": "bob@x.invalid",
                "name": "Bob",
                "isAdmin": False,
            },
        )
    return httpx.Response(401)


def _immich_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/auth/login":
        return _login_handler(request)
    if request.url.path == "/api/users/me":
        return httpx.Response(200, json={"id": "ok"})
    if request.url.path == "/api/auth/logout":
        return httpx.Response(200)
    return httpx.Response(404)


@pytest.fixture
def app(tmp_path, monkeypatch):
    cfg = ServiceConfig(
        immich_base_url="http://immich.invalid",
        base_url="http://localhost:8080",
        data_dir=tmp_path,
        trusted_proxies=(),
        session_ttl_seconds=3600,
        revalidate_interval_seconds=10_000_000,
        login_rate_per_min=100,
        cookie_secure=False,
    )
    original = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_immich_handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("mediarefinery.service.app.httpx.Client", patched)
    return create_app(config=cfg)


def _login(client: TestClient, email: str, password: str) -> str:
    r = client.post(
        f"{API_PREFIX}/auth/login", json={"email": email, "password": password}
    )
    assert r.status_code == 200, r.text
    return client.cookies[CSRF_COOKIE_NAME]


def _seed_action(store, user_id: str, asset_id: str) -> None:
    scoped = store.with_user(user_id)
    scoped.upsert_asset(asset_id=asset_id, media_type="image")
    run_id = scoped.start_run(dry_run=True, command="scan")
    scoped.record_action(
        run_id=run_id,
        asset_id=asset_id,
        action_name="tag",
        dry_run=True,
        would_apply=True,
        success=True,
    )
    scoped.finish_run(run_id, status="completed")


def test_override_records_row_and_audit(app):
    """Test override records row and audit."""
    with TestClient(app) as client:
        csrf = _login(client, "alice@x.invalid", ALICE_PW)
        _seed_action(app.state.store, "user-alice", "asset-A1")

        r = client.post(
            f"{API_PREFIX}/me/assets/asset-A1/category",
            json={"category_id": "pets"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["asset_id"] == "asset-A1"
        assert data["category_id"] == "pets"
        assert data["before"] is None

        audit = client.get(f"{API_PREFIX}/audit").json()["entries"]
        overrides = [e for e in audit if e["action"] == "asset.category.override"]
        assert len(overrides) == 1
        assert overrides[0]["target_asset_id"] == "asset-A1"


def test_override_idempotent_on_user_asset(app):
    """Test override idempotent on user asset."""
    with TestClient(app) as client:
        csrf = _login(client, "alice@x.invalid", ALICE_PW)
        _seed_action(app.state.store, "user-alice", "asset-A1")
        h = {"X-CSRF-Token": csrf}

        client.post(
            f"{API_PREFIX}/me/assets/asset-A1/category",
            json={"category_id": "pets"},
            headers=h,
        )
        r2 = client.post(
            f"{API_PREFIX}/me/assets/asset-A1/category",
            json={"category_id": "landscape"},
            headers=h,
        )
        assert r2.status_code == 200
        assert r2.json()["before"] == "pets"
        assert r2.json()["category_id"] == "landscape"

        # Only one override row for (user, asset).
        conn = app.state.store._conn
        count = conn.execute(
            "SELECT COUNT(*) FROM asset_overrides WHERE user_id=? AND asset_id=?",
            ("user-alice", "asset-A1"),
        ).fetchone()[0]
        assert count == 1


def test_list_assets_surfaces_override_category(app):
    """Test list assets surfaces override category."""
    with TestClient(app) as client:
        csrf = _login(client, "alice@x.invalid", ALICE_PW)
        _seed_action(app.state.store, "user-alice", "asset-A1")

        client.post(
            f"{API_PREFIX}/me/assets/asset-A1/category",
            json={"category_id": "pets"},
            headers={"X-CSRF-Token": csrf},
        )
        r = client.get(f"{API_PREFIX}/me/assets")
        assert r.status_code == 200
        assets = r.json()["assets"]
        assert len(assets) == 1
        row = assets[0]
        assert row["asset_id"] == "asset-A1"
        assert row["last_seen_category"] == "pets"
        assert row["last_action"] == "tag"
        assert row["can_override"] is True


def test_override_cross_tenant_rejected(app):
    """Test override cross tenant rejected."""
    with TestClient(app) as client:
        # Alice owns asset-A1.
        _login(client, "alice@x.invalid", ALICE_PW)
        _seed_action(app.state.store, "user-alice", "asset-A1")
        client.cookies.clear()

        # Bob attempts to override Alice's asset.
        bob_csrf = _login(client, "bob@x.invalid", BOB_PW)
        r = client.post(
            f"{API_PREFIX}/me/assets/asset-A1/category",
            json={"category_id": "pets"},
            headers={"X-CSRF-Token": bob_csrf},
        )
        assert r.status_code == 404

        # No override row was written under bob.
        conn = app.state.store._conn
        count = conn.execute(
            "SELECT COUNT(*) FROM asset_overrides WHERE user_id = ?",
            ("user-bob",),
        ).fetchone()[0]
        assert count == 0
