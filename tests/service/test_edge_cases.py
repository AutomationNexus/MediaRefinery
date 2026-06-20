from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from mediarefinery.classifier import (
    ClassificationResult,
    ClassifierError,
    ClassifierInput,
    RawModelOutput,
)
from mediarefinery.config import ClassifierProfile
from mediarefinery.extractor import MediaExtractionError
from mediarefinery.immich import AssetRef, ImmichClientError
from mediarefinery.ocr import OcrModelPaths
from mediarefinery.service import (
    auth,
    auto_scan,
    compatibility,
    deps,
    locked_folder,
    model_lifecycle,
    runner,
    search,
)
from mediarefinery.service.classifier_cache import (
    ClassifierSessionCache,
    UnknownModelError,
    make_cached_adult_subtype_classifier_factory,
    profile_from_adult_subtype_model,
    profile_from_catalog_entry,
)
from mediarefinery.service.config import OcrConfig, ServiceConfig
from mediarefinery.service.model_catalog import CatalogEntry
from mediarefinery.service.production import (
    ApiKeyValidationError,
    MissingUserApiKey,
    build_ocr_analyzer,
    latest_user_api_key,
    validate_api_key,
)
from mediarefinery.service.security import AesGcmCipher
from mediarefinery.service.state_store import StateStore


def _service_config(tmp_path: Path, *, ocr_enabled: bool = True) -> ServiceConfig:
    return ServiceConfig(
        immich_base_url="http://immich.invalid",
        base_url="http://localhost:8080",
        data_dir=tmp_path,
        trusted_proxies=(),
        session_ttl_seconds=3600,
        revalidate_interval_seconds=300,
        login_rate_per_min=10,
        cookie_secure=False,
        ocr=OcrConfig(enabled=ocr_enabled),
    )


def _catalog_entry(kind: str = "binary_nsfw_classifier") -> CatalogEntry:
    raw = {
        "id": "model-a",
        "name": "model-a",
        "kind": kind,
        "status": "verified",
        "url": "https://example.invalid/model.onnx",
        "sha256": "a" * 64,
        "size_bytes": 4,
        "license": "Apache-2.0",
    }
    return CatalogEntry(
        id="model-a",
        name="model-a",
        kind=kind,
        status="verified",
        url=raw["url"],
        sha256=raw["sha256"],
        size_bytes=4,
        license="Apache-2.0",
        license_url="",
        presets=(),
        raw=raw,
    )


class _Backend:
    def __init__(self, profile: ClassifierProfile) -> None:
        self.profile = profile
        self.version = "test"

    def load(self) -> None:
        return None

    def predict_batch(self, inputs: list[ClassifierInput]) -> list[RawModelOutput]:
        return [RawModelOutput(item.asset_id, "known", {"known": 1.0}) for item in inputs]


def test_auth_helper_error_paths() -> None:
    """Test auth helper error paths."""
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    with httpx.Client(transport=transport, base_url="http://immich.invalid") as client:
        with pytest.raises(auth.AuthError):
            auth.proxy_login(
                immich_base_url="http://immich.invalid",
                email="u@example.invalid",
                password="pw",
                client=client,
            )
        with pytest.raises(auth.AuthError):
            auth.revalidate_via_users_me(
                immich_base_url="http://immich.invalid",
                access_token="token",
                client=client,
            )

    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert auth.should_revalidate_session(None, interval_seconds=300, now=now) is True
    assert auth.should_revalidate_session("not-a-date", interval_seconds=300, now=now) is True
    assert (
        auth.should_revalidate_session(
            (now - timedelta(seconds=10)).replace(tzinfo=None).isoformat(),
            interval_seconds=300,
            now=now,
        )
        is False
    )

    class FailingPost:
        def post(self, path: str, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("offline")

        def close(self) -> None:
            return None

    with pytest.raises(auth.AuthError):
        auth.proxy_login(
            immich_base_url="http://immich.invalid",
            email="u@example.invalid",
            password="pw",
            client=FailingPost(),
        )

    bad_json = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(201, content=b"{bad")),
        base_url="http://immich.invalid",
    )
    with pytest.raises(auth.AuthError):
        auth.proxy_login(
            immich_base_url="http://immich.invalid",
            email="u@example.invalid",
            password="pw",
            client=bad_json,
        )
    missing = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(201, json={"userId": "u"})),
        base_url="http://immich.invalid",
    )
    with pytest.raises(auth.AuthError):
        auth.proxy_login(
            immich_base_url="http://immich.invalid",
            email="u@example.invalid",
            password="pw",
            client=missing,
        )


def test_compatibility_request_and_version_edges() -> None:
    """Test compatibility request and version edges."""
    responses = iter(
        [
            httpx.Response(200, json={"version": "2.7.5"}),
            httpx.Response(401),
            httpx.Response(200, json={"version": "2.6.0"}),
            httpx.Response(200, json={"version": "2.6.0"}),
            httpx.Response(200, json={"version": "2.7.5"}),
            httpx.Response(200, json={"version": "2.8.0"}),
        ]
    )
    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: next(responses)),
        base_url="http://immich.invalid",
    )
    assert compatibility.check_immich_compatibility(client)["status"] == "ok"
    assert compatibility.check_immich_compatibility(client)["status"] == "unsupported"
    assert compatibility.check_immich_compatibility(client)["status"] == "fail"

    failing = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: (_ for _ in ()).throw(httpx.ConnectError("off"))
        ),
        base_url="http://immich.invalid",
    )
    assert compatibility.check_immich_compatibility(failing)["status"] == "fail"

    for response in (
        httpx.Response(500),
        httpx.Response(400),
        httpx.Response(200, content=b"{bad"),
        httpx.Response(200, json=[]),
    ):
        client = httpx.Client(
            transport=httpx.MockTransport(lambda request, response=response: response),
            base_url="http://immich.invalid",
        )
        check, body = compatibility._request_json(client, "/api/server/version", timeout=1.0)
        assert body is None
        assert check["status"] == "fail"

    assert compatibility._parse_version_string("not-version") is None
    assert compatibility._coerce_int(True) is None


def test_search_provider_success_and_failure_paths() -> None:
    """Test search provider success and failure paths."""
    requests: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "assets": {
                    "items": [
                        {"id": "a", "score": 0.9},
                        {"id": "a", "score": 0.8},
                        {"id": "", "score": True},
                        {"id": "b", "searchScore": 0.4},
                    ]
                }
            },
        )

    provider = search.ImmichSmartSearchProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://immich.invalid"
        ),
        bearer_token="token",
    )
    page = provider.search(query=" cats ", cursor=None, page_size=1, media_kind="gif")
    assert [hit.asset_id for hit in page.hits] == ["a"]
    assert page.next_cursor == "semantic:1"
    assert requests[0]["type"] == "IMAGE"

    assert provider.search(query=" ", cursor=None, page_size=10).hits == []
    with pytest.raises(ValueError):
        provider.search(query="cats", cursor="bad", page_size=10)

    error_provider = search.ImmichSmartSearchProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(404)),
            base_url="http://immich.invalid",
        ),
        bearer_token="token",
    )
    with pytest.raises(search.SearchUnavailable) as exc_info:
        error_provider.search(query="cats", cursor=None, page_size=10)
    assert exc_info.value.reason == "immich_smart_search_unsupported"

    for status_code, reason in (
        (400, "immich_smart_search_unavailable"),
        (401, "immich_smart_search_forbidden"),
        (500, "immich_smart_search_unavailable"),
        (418, "immich_smart_search_unavailable"),
    ):
        provider = search.ImmichSmartSearchProvider(
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request, status_code=status_code: httpx.Response(status_code)
                ),
                base_url="http://immich.invalid",
            ),
            bearer_token="token",
        )
        with pytest.raises(search.SearchUnavailable) as error:
            provider.search(query="cats", cursor=None, page_size=10)
        assert error.value.reason == reason

    bad_json = search.ImmichSmartSearchProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"{bad")),
            base_url="http://immich.invalid",
        ),
        bearer_token="token",
    )
    with pytest.raises(search.SearchUnavailable):
        bad_json.search(query="cats", cursor=None, page_size=10)

    network = search.ImmichSmartSearchProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: (_ for _ in ()).throw(httpx.ConnectError("off"))
            ),
            base_url="http://immich.invalid",
        ),
        bearer_token="token",
    )
    with pytest.raises(search.SearchUnavailable):
        network.search(query="cats", cursor=None, page_size=10)
    invalid_items = search.ImmichSmartSearchProvider(
        client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"assets": {"items": "bad"}})
            ),
            base_url="http://immich.invalid",
        ),
        bearer_token="token",
    )
    with pytest.raises(search.SearchUnavailable):
        invalid_items.search(query="cats", cursor=None, page_size=10)

    assert search._immich_asset_type("video") == "VIDEO"
    assert search._page_size(0) == 1
    assert search._page_size(500) == 100


def test_locked_folder_errors_and_partial_revert() -> None:
    """Test locked folder errors and partial revert."""
    with pytest.raises(locked_folder.UnlockError):
        locked_folder.unlock_and_revert(
            immich_base_url="http://immich.invalid",
            bearer="",
            pin="1234",
            asset_ids=[],
        )
    with pytest.raises(locked_folder.InvalidPin):
        locked_folder.unlock_and_revert(
            immich_base_url="http://immich.invalid",
            bearer="token",
            pin="",
            asset_ids=[],
        )

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == locked_folder.UNLOCK_PATH:
            return httpx.Response(204)
        if request.url.path.endswith("/bad"):
            return httpx.Response(500)
        return httpx.Response(204)

    outcome = locked_folder.unlock_and_revert(
        immich_base_url="http://immich.invalid",
        bearer="token",
        pin="1234",
        asset_ids=["ok", "bad"],
        client=httpx.Client(
            transport=httpx.MockTransport(handler), base_url="http://immich.invalid"
        ),
    )
    assert outcome.reverted_count == 1
    assert outcome.failed_asset_ids == ("bad",)
    assert locked_folder.LOCK_PATH in calls

    for status_code, exc_type in (
        (401, locked_folder.InvalidPin),
        (500, locked_folder.UnlockError),
    ):
        with pytest.raises(exc_type):
            locked_folder.unlock_and_revert(
                immich_base_url="http://immich.invalid",
                bearer="token",
                pin="1234",
                asset_ids=[],
                client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request, status_code=status_code: httpx.Response(status_code)
                    ),
                    base_url="http://immich.invalid",
                ),
            )

    with pytest.raises(locked_folder.UpstreamUnavailable):
        locked_folder.unlock_and_revert(
            immich_base_url="http://immich.invalid",
            bearer="token",
            pin="1234",
            asset_ids=[],
            client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: (_ for _ in ()).throw(httpx.ConnectError("off"))
                ),
                base_url="http://immich.invalid",
            ),
        )

    class OwnClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.closed = False

        def post(self, path: str, **kwargs: object) -> httpx.Response:
            if path == locked_folder.LOCK_PATH:
                raise httpx.ConnectError("lock failed")
            return httpx.Response(204)

        def put(self, path: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(204)

        def close(self) -> None:
            self.closed = True

    original_client = locked_folder.httpx.Client
    locked_folder.httpx.Client = OwnClient  # type: ignore[assignment]
    try:
        assert (
            locked_folder.unlock_and_revert(
                immich_base_url="http://immich.invalid",
                bearer="token",
                pin="1234",
                asset_ids=["a"],
            ).reverted_count
            == 1
        )
    finally:
        locked_folder.httpx.Client = original_client  # type: ignore[assignment]


def test_production_api_key_and_ocr_paths(tmp_path: Path) -> None:
    """Test production api key and ocr paths."""
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    cipher = AesGcmCipher(b"1" * 32)
    cfg = _service_config(tmp_path)

    store.upsert_user(user_id="u", email="u@example.invalid")
    with pytest.raises(MissingUserApiKey):
        latest_user_api_key(store=store, cipher=cipher, user_id="u")
    store.with_user("u").store_api_key(encrypted_key=cipher.encrypt(b""), label="empty")
    with pytest.raises(MissingUserApiKey):
        latest_user_api_key(store=store, cipher=cipher, user_id="u")
    store.with_user("u").store_api_key(encrypted_key=b"not-ciphertext", label="bad")
    with pytest.raises(MissingUserApiKey):
        latest_user_api_key(store=store, cipher=cipher, user_id="u")

    assert (
        build_ocr_analyzer(store=store, config=_service_config(tmp_path, ocr_enabled=False))
        .analyze([], asset_id="a")
        .status
        == "disabled"
    )
    assert (
        build_ocr_analyzer(store=store, config=cfg).analyze([], asset_id="a").status
        == "model_missing"
    )

    root = tmp_path / "models" / "ocr-bundle"
    root.mkdir(parents=True)
    for name in ("det.onnx", "rec.onnx", "dict.txt"):
        (root / name).write_text("x", encoding="utf-8")
    store._conn.execute(
        """
        INSERT INTO model_registry(name, version, sha256, kind, active_slot, active, metadata_json)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """,
        (
            "OCR",
            "ocr-bundle",
            "o" * 64,
            "ocr_bundle",
            "ocr",
            json.dumps(
                {
                    "language": "fr",
                    "artifacts": [
                        {"role": "detector", "target": "det.onnx"},
                        {"role": "recognizer", "target": "rec.onnx"},
                        {"role": "dictionary", "target": "dict.txt"},
                    ],
                }
            ),
        ),
    )
    store._conn.commit()
    analyzer = build_ocr_analyzer(store=store, config=cfg)
    assert analyzer.version == "rapidocr-onnxruntime"

    assert build_ocr_analyzer(store=store, config=cfg).model_paths == OcrModelPaths(
        detector=root / "det.onnx",
        recognizer=root / "rec.onnx",
        dictionary=root / "dict.txt",
        classifier=None,
    )

    class Client:
        def __init__(self, *, fail: bool = False) -> None:
            self.fail = fail

        def list_assets(self, **kwargs: object) -> tuple[list[AssetRef], None]:
            if self.fail:
                raise ImmichClientError("bad", error_code="auth_failed")
            return [], None

    assert validate_api_key(
        config=cfg, api_key="key", immich_client_factory=lambda **kwargs: Client()
    ).ok
    with pytest.raises(ApiKeyValidationError):
        validate_api_key(
            config=cfg, api_key="key", immich_client_factory=lambda **kwargs: Client(fail=True)
        )
    with pytest.raises(ApiKeyValidationError):
        validate_api_key(
            config=cfg,
            api_key="key",
            immich_client_factory=lambda **kwargs: (_ for _ in ()).throw(ValueError("bad")),
        )
    store.close()


def test_classifier_cache_profile_edges(tmp_path: Path) -> None:
    """Test classifier cache profile edges."""
    generic = _catalog_entry(kind="generic_image_classifier")
    generic.raw["output_classes_url"] = "https://example.invalid/classes.txt"
    profile = profile_from_catalog_entry(generic, tmp_path)
    assert profile.output_mapping["imagenet_0000"] == "imagenet_0000"

    with pytest.raises(UnknownModelError):
        profile_from_adult_subtype_model({"metadata": {}})
    model_path = tmp_path / "subtype.onnx"
    model_path.write_bytes(b"x")
    active = {
        "version": "v",
        "sha256": "b" * 64,
        "metadata": {"model_path": str(model_path), "output_labels": ["known"]},
    }
    cache = ClassifierSessionCache(
        models_dir=tmp_path,
        catalog=[],
        backend_factory=_Backend,
    )
    factory = make_cached_adult_subtype_classifier_factory(cache)
    assert factory(None) is None
    classifier = factory(active)
    assert classifier is factory(active)
    with pytest.raises(UnknownModelError):
        factory({"metadata": {}, "sha256": ""})


def test_auto_scan_error_and_helper_paths(tmp_path: Path) -> None:
    """Test auto scan error and helper paths."""
    assert auto_scan.clamp_interval(1) == auto_scan.MIN_INTERVAL_MINUTES
    assert auto_scan._is_due("bad", 30, datetime.now(UTC)) is True
    assert auto_scan._extract_items_and_next({"assets": {"items": "bad", "nextPage": "x"}}) == (
        [],
        None,
    )
    assert (
        auto_scan._max_taken_at([{}, {"fileCreatedAt": "2026-01-01T00:00:00Z"}], None)
        == "2026-01-01T00:00:00Z"
    )

    store = StateStore(tmp_path / "state.db")
    store.initialize()
    store.upsert_user(user_id="u", email="u@example.invalid")
    store.with_user("u").set_auto_scan(enabled=True, interval_minutes=30)
    cipher = AesGcmCipher(b"2" * 32)

    outcome = auto_scan.run_user_tick(
        store=store,
        user_id="u",
        cipher=cipher,
        immich_client=httpx.Client(),
        base_url="http://immich.invalid",
        last_seen_taken_at=None,
    )
    assert outcome.error_code == "no_active_session"

    scoped = store.with_user("u")
    scoped.create_session(
        session_id="sess-u",
        encrypted_immich_token=cipher.encrypt(b"token"),
        expires_at="2099-01-01T00:00:00Z",
    )

    store._conn.execute(
        "INSERT INTO model_registry(name, version, sha256, active) VALUES (?, ?, ?, 1)",
        ("model", "v", "a" * 64),
    )
    store._conn.commit()
    assert auto_scan._dispatch_scan(store=store, user_id="u", runner_factories=None) is None
    store.with_user("u").store_api_key(encrypted_key=cipher.encrypt(b"api-key"), label="key")

    original_submit_real = auto_scan._runner.submit_real_scan
    auto_scan._runner.submit_real_scan = lambda **kwargs: (_ for _ in ()).throw(
        auto_scan._scheduler.ScanRejected("daily_quota")
    )  # type: ignore[assignment]
    try:
        assert auto_scan._dispatch_scan(store=store, user_id="u", runner_factories=None) is None
    finally:
        auto_scan._runner.submit_real_scan = original_submit_real  # type: ignore[assignment]

    store.upsert_user(user_id="bad-user", email="bad@example.invalid")
    store.with_user("bad-user").create_session(
        session_id="bad-session",
        encrypted_immich_token=b"bad",
        expires_at="2099-01-01T00:00:00Z",
    )
    outcome = auto_scan.run_user_tick(
        store=store,
        user_id="bad-user",
        cipher=cipher,
        immich_client=httpx.Client(),
        base_url="http://immich.invalid",
        last_seen_taken_at=None,
    )
    assert outcome.error_code == "session_decrypt_failed"

    class RaisingClient:
        def post(self, *args: object, **kwargs: object) -> httpx.Response:
            raise httpx.ConnectError("offline")

    outcome = auto_scan.run_user_tick(
        store=store,
        user_id="u",
        cipher=cipher,
        immich_client=RaisingClient(),
        base_url="http://immich.invalid",
        last_seen_taken_at="2026-01-01T00:00:00Z",
    )
    assert outcome.error_code == "upstream_unreachable"

    for response, code in (
        (httpx.Response(400), "upstream_bad_request"),
        (httpx.Response(200, content=b"{bad"), "upstream_invalid_json"),
    ):
        client = httpx.Client(
            transport=httpx.MockTransport(lambda request, response=response: response),
            base_url="http://immich.invalid",
        )
        outcome = auto_scan.run_user_tick(
            store=store,
            user_id="u",
            cipher=cipher,
            immich_client=client,
            base_url="http://immich.invalid",
            last_seen_taken_at=None,
        )
        assert outcome.error_code == code

    def bad_tick(**kwargs: object) -> auto_scan.TickOutcome:
        raise sqlite3.Error("db")

    original = auto_scan.run_user_tick
    auto_scan.run_user_tick = bad_tick  # type: ignore[assignment]
    try:
        assert (
            auto_scan.coordinator_tick(
                store=store,
                cipher=cipher,
                immich_client=httpx.Client(),
                base_url="http://immich.invalid",
            )
            == []
        )
    finally:
        auto_scan.run_user_tick = original  # type: ignore[assignment]

    def generic_tick(**kwargs: object) -> auto_scan.TickOutcome:
        raise RuntimeError("boom")

    auto_scan.run_user_tick = generic_tick  # type: ignore[assignment]
    try:
        assert (
            auto_scan.coordinator_tick(
                store=store,
                cipher=cipher,
                immich_client=httpx.Client(),
                base_url="http://immich.invalid",
            )
            == []
        )
    finally:
        auto_scan.run_user_tick = original  # type: ignore[assignment]

    tick = auto_scan.make_coordinator_callable(
        store=store,
        cipher=cipher,
        immich_client=httpx.Client(),
        base_url="http://immich.invalid",
        runner_factories_provider=lambda: None,
    )
    tick()
    store.close()


def test_deps_direct_edges() -> None:
    """Test deps direct edges."""
    class Client:
        host = "10.0.0.1"

    request = type(
        "Request",
        (),
        {"client": Client(), "headers": {"x-forwarded-for": "1.1.1.1, 2.2.2.2"}},
    )()
    config = _service_config(Path("."))
    object.__setattr__(config, "trusted_proxies", ("10.0.0.1",))
    assert deps.client_ip(request, config) == "2.2.2.2"
    with pytest.raises(deps.HTTPException):
        deps.require_csrf("a", "b")


def test_app_entrypoint_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test app entrypoint edges."""
    from mediarefinery.service import app as app_module

    cfg = _service_config(tmp_path)
    monkeypatch.setattr(app_module, "load_service_config", lambda: cfg)
    created = app_module.create_app()
    assert created.title == "MediaRefinery"

    class Uvicorn:
        calls: list[dict[str, object]] = []

        @staticmethod
        def run(app: str, **kwargs: object) -> None:
            Uvicorn.calls.append({"app": app, **kwargs})

    monkeypatch.setitem(sys.modules, "uvicorn", Uvicorn)
    app_module.run()
    assert Uvicorn.calls[0]["factory"] is True


def test_router_error_edges(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test router error edges."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from mediarefinery.service import routers as routers_module
    from mediarefinery.service.app import API_PREFIX, create_app

    real_client = httpx.Client

    def build_app(login_response: httpx.Response | None = None):
        cfg = _service_config(tmp_path)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/auth/login":
                return login_response or httpx.Response(
                    201,
                    json={
                        "accessToken": "token",
                        "userId": "user-1",
                        "userEmail": "u@example.invalid",
                        "name": "User",
                        "isAdmin": True,
                    },
                )
            if request.url.path == "/api/auth/logout":
                return httpx.Response(200)
            if request.url.path == "/api/users/me":
                return httpx.Response(200, json={"id": "user-1"})
            if request.url.path == "/api/server/version":
                return httpx.Response(200, json={"major": 2, "minor": 7, "patch": 5})
            if request.url.path == "/api/server/about":
                return httpx.Response(200, json={"version": "2.7.5"})
            return httpx.Response(404)

        def patched_client(*args: object, **kwargs: object) -> httpx.Client:
            kwargs["transport"] = httpx.MockTransport(handler)
            return real_client(*args, **kwargs)

        monkeypatch.setattr("mediarefinery.service.app.httpx.Client", patched_client)
        return create_app(config=cfg)

    upstream_fail = build_app(httpx.Response(500))
    with TestClient(upstream_fail) as client:
        assert (
            client.post(
                f"{API_PREFIX}/auth/login",
                json={"email": "u@example.invalid", "password": "pw"},
            ).status_code
            == 502
        )

    app = build_app()
    with TestClient(app) as client:
        login = client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": "u@example.invalid", "password": "pw"},
        )
        assert login.status_code == 200
        csrf = client.cookies["mr_csrf"]
        headers = {"X-CSRF-Token": csrf}

        app.state.api_key_validator = None
        assert (
            client.post(
                f"{API_PREFIX}/me/api-key",
                json={"api_key": "key", "validate_api_key": True},
                headers=headers,
            ).status_code
            == 503
        )

        app.state.store._conn.execute(
            "INSERT INTO model_registry(name, version, sha256, active) VALUES (?, ?, ?, 1)",
            ("model", "v", "a" * 64),
        )
        app.state.store._conn.commit()
        app.state.runner_factories = None
        app.state.runner_requires_api_key = False
        assert client.post(f"{API_PREFIX}/scans", headers=headers).status_code == 503
        app.state.store._conn.execute("UPDATE model_registry SET active = 0")
        app.state.store._conn.commit()

        for reason, status_code in (
            ("concurrency_cap", 409),
            ("daily_quota", 429),
            ("bad_request", 400),
        ):
            monkeypatch.setattr(
                routers_module._scheduler,
                "submit_scan",
                lambda reason=reason, **kwargs: (_ for _ in ()).throw(
                    routers_module._scheduler.ScanRejected(reason)
                ),
            )
            assert client.post(f"{API_PREFIX}/scans", headers=headers).status_code == status_code

        bad_catalog = tmp_path / "bad-catalog.json"
        bad_catalog.write_text("{bad", encoding="utf-8")
        app.state.catalog_path = bad_catalog
        assert client.get(f"{API_PREFIX}/models/catalog").status_code == 500
        app.state.catalog_path = Path("docs/models/catalog.json")
        assert (
            client.post(
                f"{API_PREFIX}/models/install",
                json={"model_id": "missing", "license_accepted": True},
                headers=headers,
            ).status_code
            == 404
        )

        assert client.get(f"{API_PREFIX}/me/events/missing").status_code == 404
        assert (
            client.post(
                f"{API_PREFIX}/me/events/missing/rename",
                json={"title": "New"},
                headers=headers,
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{API_PREFIX}/me/events/merge",
                json={"target_event_id": "missing", "source_event_ids": ["other"]},
                headers=headers,
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{API_PREFIX}/me/events/missing/split",
                json={"asset_ids": ["a"], "title": "Split"},
                headers=headers,
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{API_PREFIX}/me/events/missing/assets/a/remove",
                headers=headers,
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"{API_PREFIX}/me/events/missing/reset",
                headers=headers,
            ).status_code
            == 404
        )
        assert client.get(f"{API_PREFIX}/me/assets/missing").status_code == 404
        assert client.get(f"{API_PREFIX}/me/assets/missing/preview").status_code == 404

        app.state.store._conn.close()
        assert client.get(f"{API_PREFIX}/health/ready").json()["status"] == "degraded"


def test_runner_helper_edges(tmp_path: Path) -> None:
    """Test runner helper edges."""
    assert runner._build_adult_subtype_classifier(None, {"metadata": {}}) is None
    assert (
        runner._build_adult_subtype_classifier(
            lambda active: (_ for _ in ()).throw(RuntimeError("bad")), {"metadata": {}}
        )
        is None
    )
    assert runner._adult_subtype_model_context({"metadata": {}}) is None
    context = runner._adult_subtype_model_context(
        {
            "version": "v",
            "sha256": "s" * 64,
            "metadata": {"output_labels": ["known"], "thresholds": {"known": 0.7}},
        }
    )
    assert context is not None
    assert context.thresholds["known"] == 0.7

    class Classifier:
        profile = ClassifierProfile(
            name="subtype",
            backend="noop",
            model_path=None,
            output_mapping={"known": "known"},
        )

        def predict_one(self, item: ClassifierInput) -> ClassificationResult:
            raise ClassifierError("bad")

        def predict_aggregate(
            self,
            inputs: list[ClassifierInput],
            *,
            asset_id: str,
            aggregation: str | None,
        ) -> ClassificationResult:
            return ClassificationResult(asset_id, "known", {"known": 1.0}, raw_label="known")

    outcome = runner.ClassificationOutcome(
        result=ClassificationResult("a", "nsfw", {"nsfw": 1.0}, raw_label="nsfw"),
        classifier_metadata={},
        preview_bytes=None,
        ocr_inputs=(),
        subtype_inputs=(ClassifierInput("a", "image", data=b"x"),),
    )
    config = runner.synthesize_app_config(_FakeScopedState(), media_sampling=None)
    assert (
        runner._adult_subtype_result_for_asset(
            asset=AssetRef("a", "image"),
            outcome=outcome,
            classifier=Classifier(),
            primary_result=outcome.result,
            config=config,
        )
        is None
    )

    assert runner._positive_optional_int("5") == 5
    assert runner._positive_optional_int("-1") is None
    assert runner._is_animated_gif(b"GIF89a" + b"\x00" * 7) is False

    class FallbackClient:
        def download_asset_bytes(self, asset_id: str) -> bytes:
            return b"123"

    destination = tmp_path / "original.bin"
    assert (
        runner._download_original_to_path(
            client=FallbackClient(),
            asset_id="a",
            media_type="image",
            destination=destination,
            max_bytes=4,
        )
        == 3
    )
    with pytest.raises(MediaExtractionError):
        runner._download_original_to_path(
            client=FallbackClient(),
            asset_id="a",
            media_type="image",
            destination=tmp_path / "too-large.bin",
            max_bytes=2,
        )


class _FakeScopedState:
    def get_config(self) -> dict[str, object]:
        return {
            "categories": {"nsfw": {"enabled": True, "threshold": 0.7}},
            "policies": {"nsfw": {"image": {"on_match": ["manual_review"]}}},
        }

    def list_config_categories(self) -> list[dict[str, object]]:
        return [{"id": "nsfw", "enabled": True, "threshold": 0.7}]

    def list_config_policies(self) -> list[dict[str, object]]:
        return [{"category_id": "nsfw", "media_type": "image", "actions": ["manual_review"]}]


def test_model_lifecycle_validation_edges(tmp_path: Path) -> None:
    """Test model lifecycle validation edges."""
    model = tmp_path / "model.onnx"
    model.write_bytes(b"1234")

    def expect_invalid(**overrides: object) -> None:
        params: dict[str, object] = {
            "model_id": "valid",
            "name": None,
            "model_path": model,
            "output_labels": ["known"],
            "thresholds": None,
            "admin_acknowledged": True,
        }
        params.update(overrides)
        with pytest.raises(model_lifecycle.InstallError):
            model_lifecycle.validate_adult_subtype_profile(**params)

    expect_invalid(model_path=tmp_path / "missing.onnx")
    expect_invalid(output_labels=[])
    expect_invalid(output_labels="known")
    expect_invalid(output_labels=[1])
    expect_invalid(output_labels=["bad label"])
    expect_invalid(output_labels=["known", "known"])
    expect_invalid(thresholds=[])
    expect_invalid(thresholds={"known": True})
    expect_invalid(thresholds={"known": 1.5})
    expect_invalid(input_size=0)
    expect_invalid(input_mean=[0.1, 0.2])
    expect_invalid(input_mean=[0.1, True, 0.3])
    expect_invalid(input_name="")

    with pytest.raises(model_lifecycle.InstallError):
        model_lifecycle.validate_adult_subtype_profile(
            model_id="bad id",
            name=None,
            model_path=model,
            output_labels=["known"],
            thresholds=None,
            admin_acknowledged=True,
        )
    with pytest.raises(model_lifecycle.InstallError):
        model_lifecycle.validate_adult_subtype_profile(
            model_id="valid",
            name=None,
            model_path=model,
            output_labels=["sfw", "nsfw"],
            thresholds=None,
            admin_acknowledged=True,
        )
    with pytest.raises(model_lifecycle.InstallError):
        model_lifecycle.validate_adult_subtype_profile(
            model_id="valid",
            name=None,
            model_path=model,
            output_labels=["known"],
            thresholds={"other": 0.5},
            admin_acknowledged=True,
        )
    with pytest.raises(model_lifecycle.InstallError):
        model_lifecycle._artifact_target({"target": "../outside.onnx"})

    profile = model_lifecycle.validate_adult_subtype_profile(
        model_id="valid",
        name=" Adult Model ",
        model_path=model,
        output_labels=["Known"],
        thresholds={"known": 0.5},
        admin_acknowledged=True,
        input_mean=[0.1, 0.2, 0.3],
        input_std=[1.0, 1.0, 1.0],
        input_name=" pixels ",
        output_name=" scores ",
    )
    assert profile.name == "Adult Model"
    assert profile.thresholds == {"known": 0.5}

    assert (
        model_lifecycle._installed_target_present(tmp_path / "missing.onnx", _catalog_entry())
        is False
    )
    with pytest.raises(OSError):
        model_lifecycle._remove_installed_target(tmp_path, data_dir=tmp_path)


def test_model_lifecycle_existing_registration_and_download_edges(tmp_path: Path) -> None:
    """Test model lifecycle existing registration and download edges."""
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    store.upsert_user(
        user_id="admin",
        email="admin@example.invalid",
        name="Admin",
        is_admin=True,
    )
    model = tmp_path / "adult.onnx"
    model.write_bytes(b"adult-model")
    first = model_lifecycle.register_adult_subtype_model(
        model_id="adult-local",
        name="Adult Local",
        model_path=model,
        output_labels=["known"],
        thresholds=None,
        admin_acknowledged=True,
        data_dir=tmp_path,
        conn=store._conn,
        actor_user_id="admin",
    )
    assert first.path is not None
    first.path.unlink()
    second = model_lifecycle.register_adult_subtype_model(
        model_id="adult-local",
        name="Adult Local",
        model_path=model,
        output_labels=["known"],
        thresholds=None,
        admin_acknowledged=True,
        data_dir=tmp_path,
        conn=store._conn,
        actor_user_id="admin",
    )
    assert second.id == first.id
    assert second.path is not None and second.path.is_file()

    class Response:
        status_code = 200

        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = chunks

        def iter_bytes(self, chunk_size: int) -> list[bytes]:
            return self._chunks

        def __enter__(self) -> Response:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

    class Client:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = chunks

        def stream(self, method: str, url: str) -> Response:
            return Response(self.chunks)

    destination = tmp_path / "download.bin"
    sha = __import__("hashlib").sha256(b"ok").hexdigest()
    assert (
        model_lifecycle._download_to_path(
            url="https://example.invalid/model",
            destination=destination,
            expected_sha256=sha,
            expected_size=2,
            client=Client([b"", b"ok"]),
            label="model",
        )
        == 2
    )
    with pytest.raises(model_lifecycle.InstallError):
        model_lifecycle._download_to_path(
            url="https://example.invalid/model",
            destination=tmp_path / "size.bin",
            expected_sha256=sha,
            expected_size=3,
            client=Client([b"ok"]),
            label="model",
        )
    with pytest.raises(model_lifecycle.HashMismatch):
        model_lifecycle._download_to_path(
            url="https://example.invalid/model",
            destination=tmp_path / "hash.bin",
            expected_sha256="0" * 64,
            expected_size=2,
            client=Client([b"ok"]),
            label="model",
        )
    empty = tmp_path / "empty.onnx"
    empty.write_bytes(b"")
    with pytest.raises(model_lifecycle.InstallError):
        model_lifecycle._hash_file(empty)
    store.close()
