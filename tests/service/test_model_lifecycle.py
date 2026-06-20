"""Install / uninstall lifecycle unit tests + e2e router probe."""

from __future__ import annotations

import hashlib
import json

import pytest

httpx = pytest.importorskip("httpx")
fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from mediarefinery.service.app import API_PREFIX, create_app  # noqa: E402
from mediarefinery.service.config import ServiceConfig  # noqa: E402
from mediarefinery.service.model_catalog import CatalogEntry  # noqa: E402
from mediarefinery.service.model_lifecycle import (  # noqa: E402
    HashMismatch,
    InstallError,
    install_model,
    list_installed,
    register_adult_subtype_model,
    uninstall_model,
)
from mediarefinery.service.security import CSRF_COOKIE_NAME  # noqa: E402
from mediarefinery.service.state_store import StateStore  # noqa: E402

PAYLOAD = b"x" * 4096
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
DET_PAYLOAD = b"detector"
REC_PAYLOAD = b"recognizer"
DICT_PAYLOAD = b"abc\n"


def _entry(**overrides) -> CatalogEntry:
    base = {
        "id": "m-1",
        "name": "M1",
        "kind": "generic_image_classifier",
        "status": "verified",
        "url": "https://example.invalid/model.onnx",
        "sha256": PAYLOAD_SHA,
        "size_bytes": len(PAYLOAD),
        "license": "Apache-2.0",
        "license_url": "https://example.invalid/LICENSE",
        "presets": ("generic",),
        "raw": {},
    }
    base.update(overrides)
    return CatalogEntry(**base)


def _ocr_entry() -> CatalogEntry:
    artifacts = [
        {
            "role": "detector",
            "target": "det/det.onnx",
            "url": "https://example.invalid/det.onnx",
            "sha256": hashlib.sha256(DET_PAYLOAD).hexdigest(),
            "size_bytes": len(DET_PAYLOAD),
        },
        {
            "role": "recognizer",
            "target": "rec/rec.onnx",
            "url": "https://example.invalid/rec.onnx",
            "sha256": hashlib.sha256(REC_PAYLOAD).hexdigest(),
            "size_bytes": len(REC_PAYLOAD),
        },
        {
            "role": "dictionary",
            "target": "rec/dict.txt",
            "url": "https://example.invalid/dict.txt",
            "sha256": hashlib.sha256(DICT_PAYLOAD).hexdigest(),
            "size_bytes": len(DICT_PAYLOAD),
        },
    ]
    return _entry(
        id="ocr-bundle",
        name="OCR Bundle",
        kind="ocr_bundle",
        url="https://example.invalid/ocr",
        sha256="c" * 64,
        size_bytes=sum(int(item["size_bytes"]) for item in artifacts),
        raw={"artifacts": artifacts, "language": "en", "runtime": "rapidocr"},
    )


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def _ok_handler(_request):
    return httpx.Response(200, content=PAYLOAD)


@pytest.fixture
def db(tmp_path):
    store = StateStore(tmp_path / "state.db")
    store.initialize()
    store.upsert_user(user_id="alice", email="a@x.invalid", is_admin=True)
    yield store
    store.close()


def test_install_success(db, tmp_path):
    """Test install success."""
    entry = _entry()
    with _client(_ok_handler) as client:
        result = install_model(
            entry=entry,
            data_dir=tmp_path,
            conn=db._conn,
            actor_user_id="alice",
            license_accepted=True,
            client=client,
        )
    assert result.sha256 == PAYLOAD_SHA
    assert result.path is not None and result.path.exists()
    assert result.path.read_bytes() == PAYLOAD
    audit = db.with_user("alice").list_audit()
    assert any(row["action"] == "model.install" for row in audit)


def test_install_rejects_unaccepted_license(db, tmp_path):
    """Test install rejects unaccepted license."""
    with _client(_ok_handler) as client:
        with pytest.raises(InstallError, match="license"):
            install_model(
                entry=_entry(),
                data_dir=tmp_path,
                conn=db._conn,
                actor_user_id="alice",
                license_accepted=False,
                client=client,
            )


def test_install_rejects_unavailable_status(db, tmp_path):
    """Test install rejects unavailable status."""
    with _client(_ok_handler) as client:
        with pytest.raises(InstallError, match="not installable"):
            install_model(
                entry=_entry(status="unavailable"),
                data_dir=tmp_path,
                conn=db._conn,
                actor_user_id="alice",
                license_accepted=True,
                client=client,
            )


def test_install_rejects_non_https(db, tmp_path):
    """Test install rejects non https."""
    with _client(_ok_handler) as client:
        with pytest.raises(InstallError, match="HTTPS"):
            install_model(
                entry=_entry(url="http://example.invalid/x.onnx"),
                data_dir=tmp_path,
                conn=db._conn,
                actor_user_id="alice",
                license_accepted=True,
                client=client,
            )


def test_install_hash_mismatch_deletes_partial(db, tmp_path):
    """Test install hash mismatch deletes partial."""
    entry = _entry(sha256="b" * 64)
    with _client(_ok_handler) as client:
        with pytest.raises(HashMismatch):
            install_model(
                entry=entry,
                data_dir=tmp_path,
                conn=db._conn,
                actor_user_id="alice",
                license_accepted=True,
                client=client,
            )
    # No leftover file or registry row.
    assert list((tmp_path / "models").glob("*")) == []
    rows = db._conn.execute("SELECT * FROM model_registry").fetchall()
    assert rows == []


def test_install_size_mismatch_rejected(db, tmp_path):
    """Test install size mismatch rejected."""
    entry = _entry(size_bytes=999999)
    with _client(_ok_handler) as client:
        with pytest.raises(InstallError, match="size mismatch"):
            install_model(
                entry=entry,
                data_dir=tmp_path,
                conn=db._conn,
                actor_user_id="alice",
                license_accepted=True,
                client=client,
            )


def test_install_http_error(db, tmp_path):
    """Test install http error."""
    def handler(_req):
        return httpx.Response(404)

    with _client(handler) as client:
        with pytest.raises(InstallError, match="HTTP 404"):
            install_model(
                entry=_entry(),
                data_dir=tmp_path,
                conn=db._conn,
                actor_user_id="alice",
                license_accepted=True,
                client=client,
            )


def test_install_idempotent(db, tmp_path):
    """Test install idempotent."""
    entry = _entry()
    with _client(_ok_handler) as client:
        first = install_model(
            entry=entry, data_dir=tmp_path, conn=db._conn,
            actor_user_id="alice", license_accepted=True, client=client,
        )
        second = install_model(
            entry=entry, data_dir=tmp_path, conn=db._conn,
            actor_user_id="alice", license_accepted=True, client=client,
        )
    assert first.id == second.id
    rows = db._conn.execute("SELECT COUNT(*) AS c FROM model_registry").fetchone()
    assert rows["c"] == 1
    audit = db.with_user("alice").list_audit()
    install_events = [r for r in audit if r["action"] == "model.install"]
    assert len(install_events) == 2
    second_details = json.loads(install_events[-1]["details_json"])
    assert second_details["already_installed"] is True


def test_uninstall_removes_file_and_row(db, tmp_path):
    """Test uninstall removes file and row."""
    with _client(_ok_handler) as client:
        installed = install_model(
            entry=_entry(), data_dir=tmp_path, conn=db._conn,
            actor_user_id="alice", license_accepted=True, client=client,
        )
    assert installed.path is not None
    assert installed.path.exists()
    uninstall_model(
        registry_id=installed.id,
        data_dir=tmp_path,
        conn=db._conn,
        actor_user_id="alice",
    )
    assert not installed.path.exists()
    rows = db._conn.execute("SELECT * FROM model_registry").fetchall()
    assert rows == []
    audit = db.with_user("alice").list_audit()
    assert any(r["action"] == "model.uninstall" for r in audit)


def test_list_installed(db, tmp_path):
    """Test list installed."""
    with _client(_ok_handler) as client:
        install_model(
            entry=_entry(), data_dir=tmp_path, conn=db._conn,
            actor_user_id="alice", license_accepted=True, client=client,
        )
    listed = list_installed(conn=db._conn, data_dir=tmp_path)
    assert len(listed) == 1
    assert listed[0].active is True


def test_install_ocr_bundle_activates_ocr_slot_without_primary_classifier(db, tmp_path):
    """Test install ocr bundle activates ocr slot without primary classifier."""
    entry = _ocr_entry()

    def handler(request):
        name = request.url.path.rsplit("/", 1)[-1]
        payloads = {
            "det.onnx": DET_PAYLOAD,
            "rec.onnx": REC_PAYLOAD,
            "dict.txt": DICT_PAYLOAD,
        }
        return httpx.Response(200, content=payloads[name])

    with _client(handler) as client:
        installed = install_model(
            entry=entry,
            data_dir=tmp_path,
            conn=db._conn,
            actor_user_id="alice",
            license_accepted=True,
            client=client,
        )

    assert installed.active_slot == "ocr"
    assert db.active_model_sha256() is None
    active_ocr = db.active_ocr_model()
    assert active_ocr is not None
    assert active_ocr["sha256"] == "c" * 64
    assert (tmp_path / "models" / "ocr-bundle" / "det" / "det.onnx").read_bytes() == DET_PAYLOAD
    assert (tmp_path / "models" / "ocr-bundle" / "rec" / "rec.onnx").read_bytes() == REC_PAYLOAD


def test_register_adult_subtype_model_activates_separate_slot(db, tmp_path):
    """Test register adult subtype model activates separate slot."""
    source = tmp_path / "subtype.onnx"
    source.write_bytes(b"fake-onnx-subtype")

    installed = register_adult_subtype_model(
        model_id="local-subtypes",
        name="Local Subtypes",
        model_path=source,
        output_labels=["custom_one", "custom_two"],
        thresholds={"custom_one": 0.7},
        admin_acknowledged=True,
        data_dir=tmp_path,
        conn=db._conn,
        actor_user_id="alice",
        input_mean=(0.5, 0.5, 0.5),
        input_std=(0.5, 0.5, 0.5),
    )

    assert installed.active_slot == "adult_subtype"
    assert db.active_model_sha256() is None
    active = db.active_adult_subtype_model()
    assert active is not None
    assert active["sha256"] == installed.sha256
    assert active["metadata"]["output_labels"] == ["custom_one", "custom_two"]
    assert active["metadata"]["thresholds"]["custom_one"] == 0.7
    assert (tmp_path / "models" / "local-subtypes.onnx").read_bytes() == source.read_bytes()


def test_register_adult_subtype_rejects_binary_only_model(db, tmp_path):
    """Test register adult subtype rejects binary only model."""
    source = tmp_path / "binary.onnx"
    source.write_bytes(b"fake-onnx-binary")

    with pytest.raises(InstallError, match="binary-only"):
        register_adult_subtype_model(
            model_id="binary",
            name=None,
            model_path=source,
            output_labels=["sfw", "nsfw"],
            thresholds={},
            admin_acknowledged=True,
            data_dir=tmp_path,
            conn=db._conn,
            actor_user_id="alice",
        )


def test_register_adult_subtype_requires_admin_acknowledgement(db, tmp_path):
    """Test register adult subtype requires admin acknowledgement."""
    source = tmp_path / "subtype.onnx"
    source.write_bytes(b"fake-onnx-subtype")

    with pytest.raises(InstallError, match="acknowledgement"):
        register_adult_subtype_model(
            model_id="local-subtypes",
            name=None,
            model_path=source,
            output_labels=["custom_one"],
            thresholds={},
            admin_acknowledged=False,
            data_dir=tmp_path,
            conn=db._conn,
            actor_user_id="alice",
        )


# ---------------------------------------------------------------------------
# Router e2e: catalog endpoint + admin gate + install/uninstall via HTTP.
# ---------------------------------------------------------------------------


SMOKE_PASSWORD = "pw-not-real"
SMOKE_TOKEN = "tok-not-real"


def _login_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    if body.get("password") != SMOKE_PASSWORD:
        return httpx.Response(401)
    is_admin = body["email"].startswith("admin@")
    user_id = "admin-user" if is_admin else "regular-user"
    return httpx.Response(
        201,
        json={
            "accessToken": SMOKE_TOKEN,
            "userId": user_id,
            "userEmail": body["email"],
            "name": "Test",
            "isAdmin": is_admin,
            "profileImagePath": "",
            "shouldChangePassword": False,
            "isOnboarded": True,
        },
    )


def _immich_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/auth/login":
        return _login_handler(request)
    if request.url.path == "/api/auth/logout":
        return httpx.Response(200)
    if request.url.path == "/api/users/me":
        return httpx.Response(200, json={"id": "ok"})
    return httpx.Response(404)


@pytest.fixture
def app_with_catalog(tmp_path, monkeypatch):
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "$schema_version": "2",
                "models": [
                    {
                        "id": "test-model",
                        "name": "Test Model",
                        "kind": "generic_image_classifier",
                        "status": "verified",
                        "url": "https://example.invalid/model.onnx",
                        "sha256": PAYLOAD_SHA,
                        "size_bytes": len(PAYLOAD),
                        "license": "Apache-2.0",
                        "license_url": "https://example.invalid/LICENSE",
                        "presets": ["generic"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
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

    download_calls = {"count": 0}

    def combined_handler(request):
        if "/api/auth/" in request.url.path or "/api/users/" in request.url.path:
            return _immich_handler(request)
        if request.url.host == "example.invalid":
            download_calls["count"] += 1
            return httpx.Response(200, content=PAYLOAD)
        return httpx.Response(404)

    original_client = httpx.Client

    def patched_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(combined_handler)
        return original_client(*args, **kwargs)

    monkeypatch.setattr("mediarefinery.service.app.httpx.Client", patched_client)
    monkeypatch.setattr("mediarefinery.service.model_lifecycle.httpx.Client", patched_client)

    app = create_app(config=cfg)
    app.state.catalog_path = catalog_path
    return app, download_calls


def _login(client, email, password=SMOKE_PASSWORD):
    r = client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return client.cookies[CSRF_COOKIE_NAME]


def test_catalog_endpoint_lists_entries(app_with_catalog):
    """Test catalog endpoint lists entries."""
    app, _ = app_with_catalog
    with TestClient(app) as client:
        _login(client, "regular@x.invalid")
        r = client.get(f"{API_PREFIX}/models/catalog")
        assert r.status_code == 200
        models = r.json()["models"]
        assert len(models) == 1
        assert models[0]["installed"] is False


def test_install_requires_admin(app_with_catalog):
    """Test install requires admin."""
    app, _ = app_with_catalog
    with TestClient(app) as client:
        # Bootstrap an admin first so the regular login does not
        # benefit from the first-user-becomes-admin promotion.
        _login(client, "admin@x.invalid")
        client.cookies.clear()
        csrf = _login(client, "regular@x.invalid")
        r = client.post(
            f"{API_PREFIX}/models/install",
            json={"model_id": "test-model", "license_accepted": True},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 403


def test_admin_install_uninstall_flow(app_with_catalog):
    """Test admin install uninstall flow."""
    app, downloads = app_with_catalog
    with TestClient(app) as client:
        csrf = _login(client, "admin@x.invalid")
        h = {"X-CSRF-Token": csrf}

        r = client.post(
            f"{API_PREFIX}/models/install",
            json={"model_id": "test-model", "license_accepted": True},
            headers=h,
        )
        assert r.status_code == 201, r.text
        registry_id = r.json()["id"]
        assert downloads["count"] == 1

        listed = client.get(f"{API_PREFIX}/models").json()["installed"]
        assert listed[0]["active"] is True
        assert listed[0]["sha256"] == PAYLOAD_SHA

        catalog = client.get(f"{API_PREFIX}/models/catalog").json()["models"]
        assert catalog[0]["installed"] is True

        # Audit captures license acceptance.
        audit = client.get(f"{API_PREFIX}/audit").json()["entries"]
        assert any(e["action"] == "model.install" for e in audit)

        r = client.delete(
            f"{API_PREFIX}/models/{registry_id}", headers=h
        )
        assert r.status_code == 204
        assert client.get(f"{API_PREFIX}/models").json()["installed"] == []


def test_admin_register_adult_subtype_profile_flow(app_with_catalog, tmp_path):
    """Test admin register adult subtype profile flow."""
    app, _ = app_with_catalog
    source = tmp_path / "local-subtypes.onnx"
    source.write_bytes(b"fake-local-subtype-model")
    with TestClient(app) as client:
        csrf = _login(client, "admin@x.invalid")
        r = client.post(
            f"{API_PREFIX}/models/adult-subtype-profile",
            json={
                "model_id": "local-subtypes",
                "name": "Local Subtypes",
                "model_path": str(source),
                "output_labels": ["custom_one", "custom_two"],
                "thresholds": {"custom_one": 0.7, "custom_two": 0.8},
                "admin_acknowledgement": True,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 201, r.text
        assert r.json()["active_slot"] == "adult_subtype"
        listed = client.get(f"{API_PREFIX}/models").json()["installed"]
        assert listed[0]["active_slot"] == "adult_subtype"
        assert listed[0]["active"] is True


def test_register_adult_subtype_profile_rejects_binary_labels(app_with_catalog, tmp_path):
    """Test register adult subtype profile rejects binary labels."""
    app, _ = app_with_catalog
    source = tmp_path / "binary.onnx"
    source.write_bytes(b"fake-binary-model")
    with TestClient(app) as client:
        csrf = _login(client, "admin@x.invalid")
        r = client.post(
            f"{API_PREFIX}/models/adult-subtype-profile",
            json={
                "model_id": "binary",
                "model_path": str(source),
                "output_labels": ["sfw", "nsfw"],
                "thresholds": {},
                "admin_acknowledgement": True,
            },
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 400
        assert "binary-only" in r.json()["detail"]


def test_install_without_license_acceptance_rejected(app_with_catalog):
    """Test install without license acceptance rejected."""
    app, _ = app_with_catalog
    with TestClient(app) as client:
        csrf = _login(client, "admin@x.invalid")
        r = client.post(
            f"{API_PREFIX}/models/install",
            json={"model_id": "test-model", "license_accepted": False},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 400
        assert "license" in r.json()["detail"].lower()


def test_install_unknown_model_404(app_with_catalog):
    """Test install unknown model 404."""
    app, _ = app_with_catalog
    with TestClient(app) as client:
        csrf = _login(client, "admin@x.invalid")
        r = client.post(
            f"{API_PREFIX}/models/install",
            json={"model_id": "does-not-exist", "license_accepted": True},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 404


def test_uninstall_unknown_id_404(app_with_catalog):
    """Test uninstall unknown id 404."""
    app, _ = app_with_catalog
    with TestClient(app) as client:
        csrf = _login(client, "admin@x.invalid")
        r = client.delete(
            f"{API_PREFIX}/models/9999",
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 404
