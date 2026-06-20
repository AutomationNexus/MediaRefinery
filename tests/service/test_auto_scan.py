"""Auto-scan polling fallback tests.

Covers:

- Per-user cursor advancement (max-of-page logic).
- Pagination cap: more than ``MAX_PAGES_PER_TICK`` pages → walk
  exactly the cap and resume on the next tick.
- 401 handling: enabled flips to FALSE, error_code set, cursor not
  advanced.
- 5xx handling: enabled stays TRUE, cursor not advanced.
- Multi-tenant isolation: two users with distinct cursors; one
  user's failure does not affect the other's cursor.
- Settings GET/PUT round-trip; out-of-range intervals rejected;
  CSRF enforced on PUT.
- Audit event ``auto_scan.tick`` written exactly once per tick with
  counts only and no asset id detail.
"""

from __future__ import annotations

import json

import httpx
import pytest

fastapi = pytest.importorskip("fastapi")

# ruff: noqa: E402
from fastapi.testclient import TestClient

from mediarefinery.service import auto_scan as _auto_scan
from mediarefinery.service.app import API_PREFIX, create_app
from mediarefinery.service.config import ServiceConfig
from mediarefinery.service.security import CSRF_COOKIE_NAME, AesGcmCipher
from mediarefinery.service.state_store import StateStore

ALICE_TOKEN = "alice-bearer-AAAA"
BOB_TOKEN = "bob-bearer-BBBB"
ALICE_PW = "alice-pw"
BOB_PW = "bob-pw"


def _login_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/auth/login":
        body = json.loads(request.content)
        if body["email"] == "alice@x.invalid":
            tok, uid = ALICE_TOKEN, "user-alice"
        elif body["email"] == "bob@x.invalid":
            tok, uid = BOB_TOKEN, "user-bob"
        else:
            return httpx.Response(401)
        return httpx.Response(
            201,
            json={
                "accessToken": tok,
                "userId": uid,
                "userEmail": body["email"],
                "name": uid,
                "isAdmin": uid == "user-alice",
            },
        )
    if request.url.path == "/api/auth/logout":
        return httpx.Response(200)
    return httpx.Response(404)


@pytest.fixture
def context(tmp_path, monkeypatch):
    cfg = ServiceConfig(
        immich_base_url="http://immich.invalid",
        base_url="http://localhost:8080",
        data_dir=tmp_path,
        trusted_proxies=(),
        session_ttl_seconds=3600,
        revalidate_interval_seconds=10_000_000,
        login_rate_per_min=100,
        cookie_secure=False,
        auto_scan_enabled=False,
    )
    original = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_login_handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("mediarefinery.service.app.httpx.Client", patched)
    app = create_app(config=cfg)
    with TestClient(app) as client:
        yield app, cfg, client


def _login(client: TestClient, email: str, pw: str) -> str:
    r = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": email, "password": pw},
    )
    assert r.status_code == 200, r.text
    return client.cookies[CSRF_COOKIE_NAME]


# -- Settings endpoints -------------------------------------------------


def test_settings_get_default(context):
    """Test settings get default."""
    _app, _cfg, client = context
    _login(client, "alice@x.invalid", ALICE_PW)
    r = client.get(f"{API_PREFIX}/me/auto-scan")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "enabled": False,
        "interval_minutes": 30,
        "last_seen_taken_at": None,
        "last_run_at": None,
        "last_status": None,
        "last_error_code": None,
    }


def test_settings_put_round_trip(context):
    """Test settings put round trip."""
    _app, _cfg, client = context
    csrf = _login(client, "alice@x.invalid", ALICE_PW)
    r = client.put(
        f"{API_PREFIX}/me/auto-scan",
        json={"enabled": True, "interval_minutes": 60},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is True
    assert body["interval_minutes"] == 60

    r = client.get(f"{API_PREFIX}/me/auto-scan")
    assert r.json()["enabled"] is True
    assert r.json()["interval_minutes"] == 60


def test_settings_put_rejects_out_of_range(context):
    """Test settings put rejects out of range."""
    _app, _cfg, client = context
    csrf = _login(client, "alice@x.invalid", ALICE_PW)
    r = client.put(
        f"{API_PREFIX}/me/auto-scan",
        json={"enabled": True, "interval_minutes": 1},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422
    r = client.put(
        f"{API_PREFIX}/me/auto-scan",
        json={"enabled": True, "interval_minutes": 99999},
        headers={"X-CSRF-Token": csrf},
    )
    assert r.status_code == 422


def test_settings_put_requires_csrf(context):
    """Test settings put requires csrf."""
    _app, _cfg, client = context
    _login(client, "alice@x.invalid", ALICE_PW)
    r = client.put(
        f"{API_PREFIX}/me/auto-scan",
        json={"enabled": True, "interval_minutes": 30},
    )
    assert r.status_code == 403


# -- Coordinator tick ---------------------------------------------------


def _seed_session(
    store: StateStore, cipher: AesGcmCipher, user_id: str, token: str
) -> None:
    encrypted = cipher.encrypt(token.encode("utf-8"))
    store.with_user(user_id).create_session(
        session_id=f"sess-{user_id}",
        encrypted_immich_token=encrypted,
        expires_at="2099-01-01T00:00:00Z",
    )


def _enable(store: StateStore, user_id: str, interval: int = 30) -> None:
    store.with_user(user_id).set_auto_scan(
        enabled=True, interval_minutes=interval
    )


class _FakeImmich:
    """Records POST /api/search/metadata calls and returns canned pages."""

    def __init__(self, responses: list[httpx.Response]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, path: str, json: dict, headers: dict) -> httpx.Response:
        self.calls.append({"path": path, "body": json, "headers": dict(headers)})
        if not self._responses:
            return httpx.Response(500)
        return self._responses.pop(0)


_DISPATCH_LOG: list[tuple[str, int]] = []


def _noop_dispatch(_store: StateStore, user_id: str) -> int:
    """Test dispatcher: records the call and returns a fake run id
    without spawning the synthetic-runner thread that would touch the
    sqlite connection from a daemon thread.
    """

    fake_id = len(_DISPATCH_LOG) + 1
    _DISPATCH_LOG.append((user_id, fake_id))
    return fake_id


def _resp(items: list[dict], next_page: int | None) -> httpx.Response:
    body: dict = {"assets": {"items": items, "nextPage": next_page}}
    return httpx.Response(200, json=body)


def test_cursor_advances_to_max_taken_at(context):
    """Test cursor advances to max taken at."""
    app, cfg, client = context
    _login(client, "alice@x.invalid", ALICE_PW)
    store: StateStore = app.state.store
    cipher: AesGcmCipher = app.state.cipher
    _enable(store, "user-alice")

    fake = _FakeImmich(
        [
            _resp(
                [
                    {"id": "a1", "fileCreatedAt": "2026-05-01T10:00:00Z"},
                    {"id": "a2", "fileCreatedAt": "2026-05-01T11:30:00Z"},
                ],
                None,
            )
        ]
    )
    outcomes = _auto_scan.coordinator_tick(
        store=store,
        cipher=cipher,
        immich_client=fake,  # type: ignore[arg-type]
        base_url=cfg.immich_base_url,
        dispatch=_noop_dispatch,
    )
    assert len(outcomes) == 1
    assert outcomes[0].status == "ok"
    assert outcomes[0].new_assets == 2
    assert outcomes[0].pages_walked == 1
    state_now = store.with_user("user-alice").get_auto_scan()
    assert state_now["last_seen_taken_at"] == "2026-05-01T11:30:00Z"
    assert state_now["last_status"] == "ok"
    assert state_now["enabled"] is True

    assert fake.calls[0]["headers"]["Authorization"] == f"Bearer {ALICE_TOKEN}"


def test_pagination_cap_resumes_next_tick(context):
    """Test pagination cap resumes next tick."""
    app, cfg, client = context
    _login(client, "alice@x.invalid", ALICE_PW)
    store: StateStore = app.state.store
    cipher: AesGcmCipher = app.state.cipher
    _enable(store, "user-alice")

    pages = []
    for i in range(_auto_scan.MAX_PAGES_PER_TICK + 5):
        nxt = i + 2 if i + 1 < _auto_scan.MAX_PAGES_PER_TICK + 5 else None
        pages.append(
            _resp(
                [{"id": f"a{i}", "fileCreatedAt": f"2026-05-01T{i:02d}:00:00Z"}],
                nxt,
            )
        )
    fake = _FakeImmich(pages)
    outcomes = _auto_scan.coordinator_tick(
        store=store,
        cipher=cipher,
        immich_client=fake,  # type: ignore[arg-type]
        base_url=cfg.immich_base_url,
        dispatch=_noop_dispatch,
    )
    assert outcomes[0].pages_walked == _auto_scan.MAX_PAGES_PER_TICK
    assert outcomes[0].new_assets == _auto_scan.MAX_PAGES_PER_TICK
    # Coordinator stopped early; next tick will resume from the new
    # cursor to avoid re-scanning the same prefix.
    assert (
        store.with_user("user-alice").get_auto_scan()["last_seen_taken_at"]
        is not None
    )


def test_401_disables_user_and_preserves_cursor(context):
    """Test 401 disables user and preserves cursor."""
    app, cfg, client = context
    _login(client, "alice@x.invalid", ALICE_PW)
    store: StateStore = app.state.store
    cipher: AesGcmCipher = app.state.cipher
    _enable(store, "user-alice")
    # Pre-existing cursor from a prior successful tick.
    store.with_user("user-alice").record_auto_scan_tick(
        now_iso="2026-05-01T00:00:00Z",
        status="ok",
        error_code=None,
        last_seen_taken_at="2026-04-30T00:00:00Z",
    )

    fake = _FakeImmich([httpx.Response(401)])
    _auto_scan.coordinator_tick(
        store=store,
        cipher=cipher,
        immich_client=fake,  # type: ignore[arg-type]
        base_url=cfg.immich_base_url,
        dispatch=_noop_dispatch,
    )

    state_now = store.with_user("user-alice").get_auto_scan()
    assert state_now["enabled"] is False
    assert state_now["last_status"] == "error"
    assert state_now["last_error_code"] == "upstream_session_expired"
    # Cursor must not advance on 401.
    assert state_now["last_seen_taken_at"] == "2026-04-30T00:00:00Z"


def test_5xx_keeps_user_enabled_and_preserves_cursor(context):
    """Test 5xx keeps user enabled and preserves cursor."""
    app, cfg, client = context
    _login(client, "alice@x.invalid", ALICE_PW)
    store: StateStore = app.state.store
    cipher: AesGcmCipher = app.state.cipher
    _enable(store, "user-alice")
    store.with_user("user-alice").record_auto_scan_tick(
        now_iso="2026-05-01T00:00:00Z",
        status="ok",
        error_code=None,
        last_seen_taken_at="2026-04-30T00:00:00Z",
    )

    fake = _FakeImmich([httpx.Response(503)])
    _auto_scan.coordinator_tick(
        store=store,
        cipher=cipher,
        immich_client=fake,  # type: ignore[arg-type]
        base_url=cfg.immich_base_url,
        dispatch=_noop_dispatch,
    )
    state_now = store.with_user("user-alice").get_auto_scan()
    assert state_now["enabled"] is True
    assert state_now["last_status"] == "error"
    assert state_now["last_error_code"] == "upstream_unreachable"
    assert state_now["last_seen_taken_at"] == "2026-04-30T00:00:00Z"


def test_multi_tenant_isolation(context):
    """Test multi tenant isolation."""
    app, cfg, client = context
    _login(client, "alice@x.invalid", ALICE_PW)
    _login(client, "bob@x.invalid", BOB_PW)
    store: StateStore = app.state.store
    cipher: AesGcmCipher = app.state.cipher
    _enable(store, "user-alice")
    _enable(store, "user-bob")

    # alice gets two assets ok; bob gets a 401.
    alice_resp = _resp(
        [{"id": "a1", "fileCreatedAt": "2026-05-01T10:00:00Z"}],
        None,
    )
    bob_resp = httpx.Response(401)
    fake = _FakeImmich([alice_resp, bob_resp])
    # Both rows are returned by list_enabled_auto_scan; order is not
    # deterministic, so wrap fake to dispatch by Authorization header.
    by_token = {ALICE_TOKEN: [alice_resp], BOB_TOKEN: [bob_resp]}

    class _ByToken:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def post(self, path: str, json: dict, headers: dict) -> httpx.Response:
            tok = headers["Authorization"].removeprefix("Bearer ")
            self.calls.append({"token": tok})
            return by_token[tok].pop(0)

    fake = _ByToken()  # type: ignore[assignment]
    _auto_scan.coordinator_tick(
        store=store,
        cipher=cipher,
        immich_client=fake,  # type: ignore[arg-type]
        base_url=cfg.immich_base_url,
        dispatch=_noop_dispatch,
    )

    alice_state = store.with_user("user-alice").get_auto_scan()
    bob_state = store.with_user("user-bob").get_auto_scan()
    assert alice_state["enabled"] is True
    assert alice_state["last_status"] == "ok"
    assert alice_state["last_seen_taken_at"] == "2026-05-01T10:00:00Z"
    # Bob's 401 must not affect Alice's row.
    assert bob_state["enabled"] is False
    assert bob_state["last_error_code"] == "upstream_session_expired"


def test_audit_event_counts_no_asset_ids(context):
    """Test audit event counts no asset ids."""
    app, cfg, client = context
    _login(client, "alice@x.invalid", ALICE_PW)
    store: StateStore = app.state.store
    cipher: AesGcmCipher = app.state.cipher
    _enable(store, "user-alice")

    fake = _FakeImmich(
        [
            _resp(
                [
                    {"id": "secret-asset-xyz", "fileCreatedAt": "2026-05-01T10:00:00Z"}
                ],
                None,
            )
        ]
    )
    _auto_scan.coordinator_tick(
        store=store,
        cipher=cipher,
        immich_client=fake,  # type: ignore[arg-type]
        base_url=cfg.immich_base_url,
        dispatch=_noop_dispatch,
    )
    audit = store.with_user("user-alice").list_audit()
    auto_rows = [row for row in audit if row["action"] == "auto_scan.tick"]
    assert len(auto_rows) == 1
    details = json.loads(auto_rows[0]["details_json"])
    assert details["new_assets"] == 1
    assert details["status"] == "ok"
    # No asset ids in the audit detail.
    assert "secret-asset-xyz" not in (auto_rows[0]["details_json"] or "")
    assert auto_rows[0]["target_asset_id"] is None


def test_not_due_user_is_skipped(context):
    """Test not due user is skipped."""
    app, cfg, client = context
    _login(client, "alice@x.invalid", ALICE_PW)
    store: StateStore = app.state.store
    cipher: AesGcmCipher = app.state.cipher
    _enable(store, "user-alice", interval=60)
    # Stamp last_run_at "just now" so the 60-minute interval has not
    # elapsed; the coordinator must skip this user.
    from datetime import UTC, datetime

    store.with_user("user-alice").record_auto_scan_tick(
        now_iso=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        status="ok",
        error_code=None,
        last_seen_taken_at=None,
    )
    fake = _FakeImmich([])
    outcomes = _auto_scan.coordinator_tick(
        store=store,
        cipher=cipher,
        immich_client=fake,  # type: ignore[arg-type]
        base_url=cfg.immich_base_url,
        dispatch=_noop_dispatch,
    )
    assert outcomes == []
    assert fake.calls == []
