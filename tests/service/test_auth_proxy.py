"""Integration tests for the auth-proxy router.

Uses a mock ``httpx.MockTransport`` for the upstream Immich, FastAPI's
``TestClient`` for the MR backend, and tmp paths for the state DB
and master key. Covers:

- successful login → cookies set → ``/api/me`` returns identity
- bad credentials → 401, no session row
- rate limiter trips after the configured number of attempts
- session cookie tamper → 401
- logout revokes session and Immich logout is called
- privacy gate: passwords/tokens/PINs do not appear in any response
  body or in JSON log output
"""

from __future__ import annotations

import io
import json
import logging

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from mediarefinery.service.app import API_PREFIX, create_app  # noqa: E402
from mediarefinery.service.config import ServiceConfig  # noqa: E402
from mediarefinery.service.security import (  # noqa: E402
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
)

SMOKE_PASSWORD = "S3cret-not-real-pw"
SMOKE_TOKEN = "fake-immich-access-token-AAAAAAAAAAAAAAAA"
SMOKE_PIN = "424242"


@pytest.fixture
def service_config(tmp_path):
    return ServiceConfig(
        immich_base_url="http://immich.invalid",
        base_url="http://localhost:8080",
        data_dir=tmp_path,
        trusted_proxies=(),
        session_ttl_seconds=3600,
        revalidate_interval_seconds=10_000_000,  # disable in most tests
        login_rate_per_min=5,
        cookie_secure=False,
    )


def _ok_login(request: httpx.Request) -> httpx.Response:
    payload = json.loads(request.content)
    if payload.get("password") != SMOKE_PASSWORD:
        return httpx.Response(401, json={"error": "Unauthorized"})
    return httpx.Response(
        201,
        json={
            "accessToken": SMOKE_TOKEN,
            "userId": "user-123",
            "userEmail": payload["email"],
            "name": "Test User",
            "isAdmin": False,
            "profileImagePath": "",
            "shouldChangePassword": False,
            "isOnboarded": True,
        },
    )


def _build_immich_handler(login_handler=_ok_login):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return login_handler(request)
        if path == "/api/auth/logout":
            return httpx.Response(200, json={"successful": True})
        if path == "/api/users/me":
            return httpx.Response(
                200,
                json={"id": "user-123", "email": "u@example.invalid", "isAdmin": False},
            )
        if path == "/api/server/version":
            return httpx.Response(200, json={"major": 2, "minor": 7, "patch": 5})
        if path == "/api/server/about":
            return httpx.Response(200, json={"version": "2.7.5"})
        return httpx.Response(404)

    return handler


@pytest.fixture
def app_factory(service_config, monkeypatch):
    """Returns a callable that builds an app with a custom Immich
    handler injected via httpx.MockTransport.
    """

    def make(login_handler=_ok_login):
        app = create_app(config=service_config)

        # Override the immich client in the lifespan: monkeypatch the
        # httpx.Client constructor used inside lifespan so the test
        # transport is wired in transparently.
        original_client_cls = httpx.Client

        def _patched(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                _build_immich_handler(login_handler)
            )
            return original_client_cls(*args, **kwargs)

        monkeypatch.setattr("mediarefinery.service.app.httpx.Client", _patched)
        return app

    return make


def test_login_success_sets_cookies_and_me_returns_identity(app_factory):
    """Test login success sets cookies and me returns identity."""
    app = app_factory()
    with TestClient(app) as client:
        r = client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": "u@example.invalid", "password": SMOKE_PASSWORD},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["user_id"] == "user-123"
        assert SMOKE_TOKEN not in r.text
        assert SMOKE_PASSWORD not in r.text
        assert SESSION_COOKIE_NAME in client.cookies
        assert CSRF_COOKIE_NAME in client.cookies

        me = client.get(f"{API_PREFIX}/me")
        assert me.status_code == 200
        assert me.json()["user_id"] == "user-123"


def test_login_bad_credentials_returns_401(app_factory):
    """Test login bad credentials returns 401."""
    app = app_factory()
    with TestClient(app) as client:
        r = client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": "u@example.invalid", "password": "wrong"},
        )
        assert r.status_code == 401
        assert SESSION_COOKIE_NAME not in client.cookies


def test_login_rate_limit_kicks_in(app_factory, service_config):
    """Test login rate limit kicks in."""
    app = app_factory()
    with TestClient(app) as client:
        # First N attempts (with bad password) yield 401; subsequent are 429.
        for _ in range(service_config.login_rate_per_min):
            r = client.post(
                f"{API_PREFIX}/auth/login",
                json={"email": "u@example.invalid", "password": "wrong"},
            )
            assert r.status_code == 401
        r = client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": "u@example.invalid", "password": "wrong"},
        )
        assert r.status_code == 429


def test_session_cookie_tamper_yields_401(app_factory):
    """Test session cookie tamper yields 401."""
    app = app_factory()
    with TestClient(app) as client:
        client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": "u@example.invalid", "password": SMOKE_PASSWORD},
        )
        # Tamper with the session cookie payload by sending it via the
        # explicit cookies kwarg, bypassing the jar's existing entry.
        signed = client.cookies[SESSION_COOKIE_NAME]
        client.cookies.clear()
        me = client.get(
            f"{API_PREFIX}/me",
            cookies={SESSION_COOKIE_NAME: signed + "x"},
        )
        assert me.status_code == 401


def test_logout_revokes_session(app_factory):
    """Test logout revokes session."""
    app = app_factory()
    with TestClient(app) as client:
        client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": "u@example.invalid", "password": SMOKE_PASSWORD},
        )
        csrf = client.cookies[CSRF_COOKIE_NAME]
        r = client.post(
            f"{API_PREFIX}/auth/logout",
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 204
        # Subsequent /me must fail because the session row is revoked.
        me = client.get(f"{API_PREFIX}/me")
        assert me.status_code == 401


def test_health_and_ready(app_factory):
    """Test health and ready."""
    app = app_factory()
    with TestClient(app) as client:
        assert client.get(f"{API_PREFIX}/health").json()["status"] == "ok"
        ready = client.get(f"{API_PREFIX}/health/ready").json()
        assert ready["status"] == "ok"
        assert ready["db"] == "ok"
        assert ready["immich"] == "ok"
        assert ready["compatibility"]["status"] == "ok"
        assert ready["compatibility"]["server_version"] == "2.7.5"
        assert ready["compatibility"]["server_about_version"] == "2.7.5"
        assert ready["compatibility"]["checks"]["server_version"]["status"] == "ok"


def _app_with_immich_handler(service_config, monkeypatch, handler):
    original_client_cls = httpx.Client

    def _patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return original_client_cls(*args, **kwargs)

    monkeypatch.setattr("mediarefinery.service.app.httpx.Client", _patched)
    return create_app(config=service_config)


def test_ready_reports_unsupported_immich_version(service_config, monkeypatch):
    """Test ready reports unsupported immich version."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/server/version":
            return httpx.Response(200, json={"major": 2, "minor": 8, "patch": 0})
        if request.url.path == "/api/server/about":
            return httpx.Response(200, json={"version": "2.8.0"})
        return httpx.Response(404)

    app = _app_with_immich_handler(service_config, monkeypatch, handler)
    with TestClient(app) as client:
        ready = client.get(f"{API_PREFIX}/health/ready").json()
        assert ready["status"] == "degraded"
        assert ready["db"] == "ok"
        assert ready["immich"] == "unsupported"
        assert ready["compatibility"]["status"] == "unsupported"
        assert ready["compatibility"]["server_version"] == "2.8.0"
        assert "newer than the maximum tested" in ready["compatibility"]["reason"]


def test_ready_accepts_auth_required_server_about_when_version_matches(
    service_config, monkeypatch
):
    """Test ready accepts auth required server about when version matches."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/server/version":
            return httpx.Response(200, json={"major": 2, "minor": 7, "patch": 5})
        if request.url.path == "/api/server/about":
            return httpx.Response(401, json={"message": "Unauthorized"})
        return httpx.Response(404)

    app = _app_with_immich_handler(service_config, monkeypatch, handler)
    with TestClient(app) as client:
        ready = client.get(f"{API_PREFIX}/health/ready").json()
        assert ready["status"] == "ok"
        assert ready["immich"] == "ok"
        assert ready["compatibility"]["status"] == "ok"
        assert ready["compatibility"]["server_version"] == "2.7.5"
        assert ready["compatibility"]["server_about_version"] is None
        assert (
            ready["compatibility"]["checks"]["server_about"]["status"]
            == "auth_required"
        )


def test_ready_reports_load_bearing_immich_shape_failure(
    service_config, monkeypatch
):
    """Test ready reports load bearing immich shape failure."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/server/version":
            return httpx.Response(200, json={"major": 2, "minor": 7})
        if request.url.path == "/api/server/about":
            return httpx.Response(200, json={"licensed": True})
        return httpx.Response(404)

    app = _app_with_immich_handler(service_config, monkeypatch, handler)
    with TestClient(app) as client:
        ready = client.get(f"{API_PREFIX}/health/ready").json()
        assert ready["status"] == "degraded"
        assert ready["db"] == "ok"
        assert ready["immich"] == "fail"
        assert ready["compatibility"]["status"] == "fail"
        assert ready["compatibility"]["server_version"] is None
        assert "parseable semantic version" in ready["compatibility"]["reason"]


def test_revalidate_kicks_out_session_when_upstream_returns_401(
    service_config, monkeypatch
):
    """Test revalidate kicks out session when upstream returns 401."""
    from dataclasses import replace

    cfg = replace(service_config, revalidate_interval_seconds=0)

    state = {"first": True}

    def custom_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth/login":
            return _ok_login(request)
        if path == "/api/users/me":
            if state["first"]:
                state["first"] = False
                return httpx.Response(200, json={"id": "user-123"})
            return httpx.Response(401, json={"error": "Unauthorized"})
        if path == "/api/auth/logout":
            return httpx.Response(200)
        return httpx.Response(404)

    original_client_cls = httpx.Client

    def _patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(custom_handler)
        return original_client_cls(*args, **kwargs)

    monkeypatch.setattr("mediarefinery.service.app.httpx.Client", _patched)
    app = create_app(config=cfg)
    with TestClient(app) as client:
        client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": "u@example.invalid", "password": SMOKE_PASSWORD},
        )
        # First /me triggers revalidate (200) → ok.
        assert client.get(f"{API_PREFIX}/me").status_code == 200
        # Second /me triggers revalidate (401) → session revoked.
        assert client.get(f"{API_PREFIX}/me").status_code == 401


# ---------------------------------------------------------------------------
# Privacy gate: extends test_scan_privacy.py for service-mode responses/logs.
# ---------------------------------------------------------------------------


_FORBIDDEN_TOKENS = (
    SMOKE_PASSWORD,
    SMOKE_TOKEN,
    SMOKE_PIN,
)


def _capture_logs() -> tuple[logging.Handler, io.StringIO]:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    from mediarefinery.service.security import _JsonFormatter

    handler.setFormatter(_JsonFormatter())
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    return handler, buf


def test_privacy_no_secrets_in_responses_or_logs(app_factory):
    """Test privacy no secrets in responses or logs."""
    handler, buf = _capture_logs()
    try:
        app = app_factory()
        with TestClient(app) as client:
            client.post(
                f"{API_PREFIX}/auth/login",
                json={"email": "u@example.invalid", "password": SMOKE_PASSWORD},
            )
            me = client.get(f"{API_PREFIX}/me")
            client.post(
                f"{API_PREFIX}/auth/logout",
                headers={"X-CSRF-Token": client.cookies[CSRF_COOKIE_NAME]},
            )
            bodies = "\n".join([me.text])
            for forbidden in _FORBIDDEN_TOKENS:
                assert forbidden not in bodies, f"leaked {forbidden!r} in response"
        log_text = buf.getvalue()
        for forbidden in _FORBIDDEN_TOKENS:
            assert forbidden not in log_text, f"leaked {forbidden!r} in logs"
        # Log lines must be JSON.
        for line in filter(None, log_text.strip().splitlines()):
            json.loads(line)
    finally:
        logging.getLogger().removeHandler(handler)


def test_privacy_login_failure_does_not_log_password(app_factory):
    """Test privacy login failure does not log password."""
    handler, buf = _capture_logs()
    try:
        app = app_factory()
        with TestClient(app) as client:
            client.post(
                f"{API_PREFIX}/auth/login",
                json={"email": "u@example.invalid", "password": SMOKE_PASSWORD + "X"},
            )
        assert SMOKE_PASSWORD not in buf.getvalue()
        assert SMOKE_PASSWORD + "X" not in buf.getvalue()
    finally:
        logging.getLogger().removeHandler(handler)


def test_login_payload_validation():
    # Schema-only check; no service instance required.
    """Test login payload validation."""
    from pydantic import ValidationError

    from mediarefinery.service.routers import LoginRequest

    LoginRequest(email="ok@example.invalid", password="x")
    with pytest.raises(ValidationError):
        LoginRequest(email="x", password="")
