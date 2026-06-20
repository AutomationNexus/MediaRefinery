"""GET /api/assets/{id}/preview - Immich thumbnail proxy.

Asserts the privacy contract from threat-model T13 (no preview bytes
ever land in structured logs or any persistent store) and the
multi-tenant gate from T05 (only assets surfaced in the caller's own
runs are reachable).
"""

from __future__ import annotations

import base64
import json
import logging

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from mediarefinery.service.app import API_PREFIX, create_app  # noqa: E402
from mediarefinery.service.config import ServiceConfig  # noqa: E402
from mediarefinery.service.security import CSRF_COOKIE_NAME  # noqa: E402

ALICE_PW = "alice-pw-not-real"
BOB_PW = "bob-pw-not-real"
ALICE_TOKEN = "alice-immich-token-AAAA"
BOB_TOKEN = "bob-immich-token-BBBB"

PREVIEW_BYTES = b"\x89PNG\r\n\x1a\nFAKE-PREVIEW-PAYLOAD-FOR-TESTS"


def _login_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body.get("email") == "alice@x.invalid" and body.get("password") == ALICE_PW:
        return httpx.Response(
            201,
            json={
                "accessToken": ALICE_TOKEN,
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
                "accessToken": BOB_TOKEN,
                "userId": "user-bob",
                "userEmail": "bob@x.invalid",
                "name": "Bob",
                "isAdmin": False,
            },
        )
    return httpx.Response(401, json={"error": "Unauthorized"})


def _immich_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/auth/login":
        return _login_handler(request)
    if request.url.path == "/api/auth/logout":
        return httpx.Response(200)
    if request.url.path == "/api/users/me":
        return httpx.Response(200, json={"id": "ok"})
    if request.url.path.startswith("/api/assets/") and request.url.path.endswith(
        "/thumbnail"
    ):
        return httpx.Response(
            200,
            content=PREVIEW_BYTES,
            headers={"content-type": "image/png"},
        )
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
        f"{API_PREFIX}/auth/login",
        json={"email": email, "password": password},
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


def test_preview_returns_200_for_owner_with_streaming_bytes(app, caplog):
    """Test preview returns 200 for owner with streaming bytes."""
    with TestClient(app) as client:
        _login(client, "alice@x.invalid", ALICE_PW)
        _seed_action(app.state.store, "user-alice", "asset-A1")

        with caplog.at_level(logging.DEBUG):
            r = client.get(f"{API_PREFIX}/assets/asset-A1/preview")
        assert r.status_code == 200, r.text
        assert r.content == PREVIEW_BYTES
        assert r.headers["cache-control"] == "private, no-store"
        assert r.headers["content-disposition"] == "inline"
        assert r.headers["content-type"].startswith("image/png")


def test_preview_404_when_asset_not_in_user_actions(app):
    """Test preview 404 when asset not in user actions."""
    with TestClient(app) as client:
        _login(client, "alice@x.invalid", ALICE_PW)
        # No seed: alice has never seen asset-X.
        r = client.get(f"{API_PREFIX}/assets/asset-X/preview")
        assert r.status_code == 404
        assert "asset-X" not in r.text


def test_preview_401_when_unauthenticated(app):
    """Test preview 401 when unauthenticated."""
    with TestClient(app) as client:
        r = client.get(f"{API_PREFIX}/assets/asset-A1/preview")
        assert r.status_code == 401


def test_preview_does_not_leak_bytes_to_logs(app, caplog):
    """Test preview does not leak bytes to logs."""
    with TestClient(app) as client:
        _login(client, "alice@x.invalid", ALICE_PW)
        _seed_action(app.state.store, "user-alice", "asset-A1")

        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            r = client.get(f"{API_PREFIX}/assets/asset-A1/preview")
        assert r.status_code == 200

        b64 = base64.b64encode(PREVIEW_BYTES).decode("ascii")
        for record in caplog.records:
            msg = record.getMessage()
            assert PREVIEW_BYTES.hex() not in msg
            assert b64 not in msg
            assert "data:image" not in msg
            assert "FAKE-PREVIEW-PAYLOAD" not in msg


def test_preview_cross_tenant_404(app):
    """Test preview cross tenant 404."""
    with TestClient(app) as client:
        # Alice has the asset.
        _login(client, "alice@x.invalid", ALICE_PW)
        _seed_action(app.state.store, "user-alice", "asset-A1")
        client.cookies.clear()

        # Bob logs in fresh and tries to fetch alice's asset.
        _login(client, "bob@x.invalid", BOB_PW)
        r = client.get(f"{API_PREFIX}/assets/asset-A1/preview")
        assert r.status_code == 404
