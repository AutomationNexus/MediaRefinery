from __future__ import annotations

import json

import httpx
import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from mediarefinery.classifier import NoopClassifier, RawModelOutput  # noqa: E402
from mediarefinery.config import AppConfig, Category, ClassifierProfile  # noqa: E402
from mediarefinery.immich import MockImmichClient  # noqa: E402
from mediarefinery.service.app import API_PREFIX, create_app  # noqa: E402
from mediarefinery.service.classifier_cache import ClassifierSessionCache  # noqa: E402
from mediarefinery.service.config import ServiceConfig  # noqa: E402
from mediarefinery.service.model_catalog import CatalogEntry  # noqa: E402
from mediarefinery.service.production import (  # noqa: E402
    ApiKeyValidationError,
    build_ocr_analyzer,
    build_runner_factories,
    latest_user_api_key,
)
from mediarefinery.service.runner import RunnerFactories, synthesize_app_config  # noqa: E402
from mediarefinery.service.security import CSRF_COOKIE_NAME, AesGcmCipher  # noqa: E402
from mediarefinery.service.state_store import StateStore  # noqa: E402

USER_TOKEN = "user-token"
USER_PASSWORD = "pw"


def _immich_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/auth/login":
        body = json.loads(request.content)
        if body["email"] != "user@x.invalid" or body["password"] != USER_PASSWORD:
            return httpx.Response(401)
        return httpx.Response(
            201,
            json={
                "accessToken": USER_TOKEN,
                "userId": "user-1",
                "userEmail": "user@x.invalid",
                "name": "User",
                "isAdmin": True,
            },
        )
    if request.url.path == "/api/auth/logout":
        return httpx.Response(200)
    if request.url.path == "/api/users/me":
        return httpx.Response(200, json={"id": "user-1"})
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
        auto_scan_enabled=False,
    )
    original = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_immich_handler)
        return original(*args, **kwargs)

    monkeypatch.setattr("mediarefinery.service.app.httpx.Client", patched)
    return create_app(config=cfg)


def _login(client: TestClient) -> str:
    r = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": "user@x.invalid", "password": USER_PASSWORD},
    )
    assert r.status_code == 200, r.text
    return client.cookies[CSRF_COOKIE_NAME]


def _seed_active_model(store: StateStore, sha: str = "a" * 64) -> None:
    store._conn.execute(
        "INSERT INTO model_registry(name, version, sha256, active) VALUES (?,?,?,1)",
        ("test-model", "test", sha),
    )
    store._conn.commit()


def _entry(id_: str, sha: str) -> CatalogEntry:
    raw = {
        "id": id_,
        "name": id_,
        "kind": "binary",
        "status": "verified",
        "url": f"https://example.invalid/{id_}.onnx",
        "sha256": sha,
        "size_bytes": 1,
        "license": "Apache-2.0",
        "output_classes": ["ok"],
    }
    return CatalogEntry(
        id=id_,
        name=id_,
        kind="binary",
        status="verified",
        url=raw["url"],
        sha256=sha,
        size_bytes=1,
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

    def predict_batch(self, inputs):
        return [
            RawModelOutput(asset_id=i.asset_id, raw_label="ok", raw_scores={"ok": 1.0})
            for i in inputs
        ]


def test_create_app_wires_production_runner_factories(app):
    """Test create app wires production runner factories."""
    with TestClient(app):
        assert app.state.runner_factories is not None
        assert app.state.runner_requires_api_key is True
        assert app.state.classifier_cache is not None


def test_production_factories_decrypt_latest_user_api_key(tmp_path):
    """Test production factories decrypt latest user api key."""
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    cipher = AesGcmCipher(b"0" * 32)
    cfg = ServiceConfig(
        immich_base_url="http://immich.invalid",
        base_url="http://localhost:8080",
        data_dir=tmp_path,
        trusted_proxies=(),
        session_ttl_seconds=3600,
        revalidate_interval_seconds=3600,
        login_rate_per_min=100,
        cookie_secure=False,
    )
    store.upsert_user(user_id="user-1", email="user@x.invalid")
    scoped = store.with_user("user-1")
    scoped.store_api_key(encrypted_key=cipher.encrypt(b"old-key"), label="old")
    scoped.store_api_key(encrypted_key=cipher.encrypt(b"new-key"), label="new")

    seen: dict[str, str] = {}

    def fake_immich_client_factory(**kwargs):
        seen["base_url"] = kwargs["base_url"]
        seen["api_key"] = kwargs["api_key"]
        return MockImmichClient(assets=[])

    cache = ClassifierSessionCache(
        models_dir=tmp_path / "models",
        catalog=[_entry("test-model", "a" * 64)],
        backend_factory=_Backend,
    )
    factories = build_runner_factories(
        store=store,
        cipher=cipher,
        config=cfg,
        classifier_cache=cache,
        immich_client_factory=fake_immich_client_factory,
    )

    assert latest_user_api_key(store=store, cipher=cipher, user_id="user-1") == "new-key"
    factories.immich_factory("user-1")
    assert seen == {"base_url": "http://immich.invalid", "api_key": "new-key"}
    assert factories.classifier_factory("a" * 64) is cache.get("a" * 64)
    store.close()


def test_build_ocr_analyzer_reports_missing_model_paths(tmp_path):
    """Test build ocr analyzer reports missing model paths."""
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    cfg = ServiceConfig(
        immich_base_url="http://immich.invalid",
        base_url="http://localhost:8080",
        data_dir=tmp_path,
        trusted_proxies=(),
        session_ttl_seconds=3600,
        revalidate_interval_seconds=3600,
        login_rate_per_min=100,
        cookie_secure=False,
    )
    store._conn.execute(
        """
        INSERT INTO model_registry(
            name, version, sha256, kind, active_slot, active, metadata_json
        )
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
                    "artifacts": [
                        {"role": "detector", "target": "det.onnx"},
                        {"role": "recognizer", "target": "rec.onnx"},
                        {"role": "dictionary", "target": "dict.txt"},
                    ]
                }
            ),
        ),
    )
    store._conn.commit()

    analyzer = build_ocr_analyzer(store=store, config=cfg)
    result = analyzer.analyze([], asset_id="asset-1")
    assert result.status == "model_missing"
    assert result.error_code == "ocr_artifact_missing"
    store.close()


def test_active_model_scan_requires_stored_api_key(app):
    """Test active model scan requires stored api key."""
    with TestClient(app) as client:
        csrf = _login(client)
        _seed_active_model(app.state.store)
        r = client.post(
            f"{API_PREFIX}/scans",
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 409
        assert r.json()["detail"] == "api_key_required"


def test_active_model_scan_uses_configured_runner_factories(app):
    """Test active model scan uses configured runner factories."""
    with TestClient(app) as client:
        csrf = _login(client)
        h = {"X-CSRF-Token": csrf}
        _seed_active_model(app.state.store)
        client.post(
            f"{API_PREFIX}/me/api-key",
            json={"api_key": "stored-key", "label": "primary"},
            headers=h,
        )
        calls = {"immich": 0, "classifier": 0}

        def immich_factory(_user_id: str) -> MockImmichClient:
            calls["immich"] += 1
            return MockImmichClient(assets=[])

        def classifier_factory(_sha: str | None) -> NoopClassifier:
            calls["classifier"] += 1
            profile = ClassifierProfile(
                name="test",
                backend="noop",
                model_path=None,
                output_mapping={"ok": "ok"},
            )
            cfg = AppConfig(
                source=None,
                raw={
                    "version": 1,
                    "categories": [{"id": "ok"}],
                    "classifier_profiles": {"test": {"backend": "noop"}},
                    "classifier": {"active_profile": "test"},
                },
                categories=(Category(id="ok"),),
                classifier_profiles={"test": profile},
                active_profile_name="test",
            )
            return NoopClassifier(cfg)

        app.state.runner_factories = RunnerFactories(
            immich_factory=immich_factory,
            classifier_factory=classifier_factory,
            config_factory=synthesize_app_config,
        )
        r = client.post(f"{API_PREFIX}/scans", headers=h)
        assert r.status_code == 202, r.text
        run_id = r.json()["run_id"]
        body = client.get(f"{API_PREFIX}/scans/{run_id}").json()
        assert body["status"] in {"running", "completed"}

        import time

        for _ in range(50):
            body = client.get(f"{API_PREFIX}/scans/{run_id}").json()
            if body["status"] != "running":
                break
            time.sleep(0.05)
        assert body["status"] == "completed"
        assert calls == {"immich": 1, "classifier": 1}


def test_api_key_validation_is_opt_in_and_does_not_store_failures(app):
    """Test api key validation is opt in and does not store failures."""
    with TestClient(app) as client:
        csrf = _login(client)

        def validator(api_key: str) -> None:
            if api_key == "bad":
                raise ApiKeyValidationError("auth_failed")

        app.state.api_key_validator = validator
        h = {"X-CSRF-Token": csrf}

        r = client.post(
            f"{API_PREFIX}/me/api-key",
            json={"api_key": "bad", "label": "bad", "validate_api_key": True},
            headers=h,
        )
        assert r.status_code == 400
        assert client.get(f"{API_PREFIX}/me/api-key").json()["api_keys"] == []

        r = client.post(
            f"{API_PREFIX}/me/api-key",
            json={"api_key": "good", "label": "primary", "validate_api_key": True},
            headers=h,
        )
        assert r.status_code == 201, r.text
        listed = client.get(f"{API_PREFIX}/me/api-key").json()["api_keys"]
        assert len(listed) == 1
        assert listed[0]["label"] == "primary"
