"""Semantic asset search API behavior."""

from __future__ import annotations

import json
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
httpx = pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from mediarefinery.service.app import API_PREFIX, create_app  # noqa: E402
from mediarefinery.service.config import ServiceConfig  # noqa: E402

ALICE_PW = "alice-pw-not-real"
BOB_PW = "bob-pw-not-real"
ALICE_TOKEN = "alice-token-for-smart-search"
BOB_TOKEN = "bob-token-for-smart-search"


def _login_response(request: httpx.Request) -> httpx.Response:
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
    return httpx.Response(401)


def _make_app(tmp_path, monkeypatch, smart_response: httpx.Response):
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body: object = None
        if request.content:
            try:
                body = json.loads(request.content)
            except json.JSONDecodeError:
                body = None
        calls.append(
            {
                "path": request.url.path,
                "headers": dict(request.headers),
                "body": body,
            }
        )
        if request.url.path == "/api/auth/login":
            return _login_response(request)
        if request.url.path == "/api/users/me":
            return httpx.Response(200, json={"id": "ok"})
        if request.url.path == "/api/auth/logout":
            return httpx.Response(200)
        if request.url.path == "/api/search/smart":
            return smart_response
        return httpx.Response(404)

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

    def patched(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("mediarefinery.service.app.httpx.Client", patched)
    return create_app(config=cfg), calls


def _login(client: TestClient) -> None:
    response = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": "alice@x.invalid", "password": ALICE_PW},
    )
    assert response.status_code == 200, response.text


def _seed_visible_asset(
    store: Any,
    user_id: str,
    asset_id: str,
    *,
    ocr_text: str = "",
) -> None:
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
    scoped.record_asset_analysis(
        asset_id=asset_id,
        analysis={
            "asset_id": asset_id,
            "primary_category_id": "sfw",
            "media_info": {"kind": "image", "mime_type": "image/jpeg"},
            "safety": {
                "label": "sfw",
                "confidence": 0.98,
                "review_needed": False,
            },
            "ocr": {"available": bool(ocr_text), "text": ocr_text},
            "review_queues": ["sfw"],
        },
    )
    scoped.finish_run(run_id, status="completed")


def test_semantic_search_returns_ranked_immich_hits_for_visible_assets(
    tmp_path,
    monkeypatch,
) -> None:
    """Test semantic search returns ranked immich hits for visible assets."""
    app, calls = _make_app(
        tmp_path,
        monkeypatch,
        httpx.Response(
            200,
            json={
                "assets": {
                    "items": [
                        {"id": "asset-unknown", "type": "IMAGE", "score": 0.99},
                        {"id": "asset-B", "type": "IMAGE", "score": 0.91},
                        {"id": "asset-A", "type": "IMAGE", "score": 0.77},
                    ],
                    "nextPage": None,
                }
            },
        ),
    )
    with TestClient(app) as client:
        _login(client)
        _seed_visible_asset(app.state.store, "user-alice", "asset-A")
        _seed_visible_asset(app.state.store, "user-alice", "asset-B")

        response = client.get(
            f"{API_PREFIX}/me/assets",
            params={"q": "vacation in snow", "search_mode": "semantic"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["search_source"] == "immich_smart_search"
    assert body["search_unavailable_reason"] is None
    assets = body["assets"]
    assert [asset["asset_id"] for asset in assets] == ["asset-B", "asset-A"]
    assert assets[0]["search_source"] == "immich_smart_search"
    assert assets[0]["search_score"] == 0.91
    smart_call = [call for call in calls if call["path"] == "/api/search/smart"][0]
    assert smart_call["headers"]["authorization"] == f"Bearer {ALICE_TOKEN}"
    assert smart_call["body"]["query"] == "vacation in snow"
    assert smart_call["body"]["size"] == 26


def test_semantic_search_falls_back_to_metadata_when_smart_search_unavailable(
    tmp_path,
    monkeypatch,
) -> None:
    """Test semantic search falls back to metadata when smart search unavailable."""
    app, _ = _make_app(tmp_path, monkeypatch, httpx.Response(404))
    with TestClient(app) as client:
        _login(client)
        _seed_visible_asset(
            app.state.store,
            "user-alice",
            "asset-text",
            ocr_text="snow vacation receipt",
        )
        _seed_visible_asset(app.state.store, "user-alice", "asset-other")

        response = client.get(
            f"{API_PREFIX}/me/assets",
            params={"q": "snow", "search_mode": "semantic"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["search_source"] == "metadata_fallback"
    assert body["search_unavailable_reason"] == "immich_smart_search_unsupported"
    assert [asset["asset_id"] for asset in body["assets"]] == ["asset-text"]
    assert body["assets"][0]["search_source"] == "metadata_fallback"
    assert body["assets"][0]["search_score"] is None


def test_semantic_search_preserves_tenant_isolation_for_immich_hits(
    tmp_path,
    monkeypatch,
) -> None:
    """Test semantic search preserves tenant isolation for immich hits."""
    app, _ = _make_app(
        tmp_path,
        monkeypatch,
        httpx.Response(
            200,
            json={
                "assets": {
                    "items": [
                        {"id": "asset-bob", "type": "IMAGE", "score": 0.96},
                        {"id": "asset-alice", "type": "IMAGE", "score": 0.85},
                    ]
                }
            },
        ),
    )
    with TestClient(app) as client:
        _login(client)
        app.state.store.upsert_user(user_id="user-bob", email="bob@x.invalid")
        _seed_visible_asset(app.state.store, "user-alice", "asset-alice")
        _seed_visible_asset(app.state.store, "user-bob", "asset-bob")

        response = client.get(
            f"{API_PREFIX}/me/assets",
            params={"q": "snow", "search_mode": "semantic"},
        )

    assert response.status_code == 200, response.text
    assets = response.json()["assets"]
    assert [asset["asset_id"] for asset in assets] == ["asset-alice"]
