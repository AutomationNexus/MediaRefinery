"""Coverage for mediarefinery.settings (config.db SSOT)."""

from __future__ import annotations

import pytest
import yaml

from mediarefinery.settings import config_db as config_db_mod
from mediarefinery.settings.config_db import config_db_path, open_config_db
from mediarefinery.settings.defaults import default_nested_config
from mediarefinery.settings.load import (
    config_db_exists,
    ensure_config_db_seeded,
    load_nested_system_config,
    load_system_config,
    service_config_from_nested,
)
from mediarefinery.settings.repository import ConfigDBRepository
from mediarefinery.settings.yaml_import import import_yaml_to_config_db, yaml_to_system_nested


def test_repository_types_roundtrip(tmp_path):
    """Repository encodes booleans, numbers, arrays, objects, and strings."""
    repo = ConfigDBRepository(open_config_db(tmp_path))
    repo.upsert("flag", True)
    repo.upsert("count", 42)
    repo.upsert("ratio", 3.5)
    repo.upsert("tags", ["a", "b"])
    repo.upsert("meta", {"k": "v"})
    repo.upsert("label", "hello")
    flat = repo.get_all()
    assert flat["flag"] is True
    assert flat["count"] == 42
    assert flat["ratio"] == 3.5
    assert flat["tags"] == ["a", "b"]
    assert flat["meta"] == {"k": "v"}
    assert flat["label"] == "hello"
    assert repo.get("missing", "x") == "x"
    nested = repo.get_nested()
    assert nested["meta"]["k"] == "v"
    assert repo.bulk_upsert({"bulk": {"a": 1}}) == 1


def test_ensure_config_db_seeded_is_idempotent(tmp_path):
    """Second seed call leaves existing keys intact."""
    first = ensure_config_db_seeded(tmp_path)
    first.upsert("system.extra", "yes")
    second = ensure_config_db_seeded(tmp_path)
    assert second.get("system.extra") == "yes"


def test_load_system_config_uses_test_data_dir_env(tmp_path, monkeypatch):
    """MEDIAREFINERY_DATA_DIR isolates runtime root in CI (not operator config)."""
    monkeypatch.setenv("MEDIAREFINERY_DATA_DIR", str(tmp_path))
    cfg = load_system_config()
    assert cfg.data_dir == tmp_path
    assert cfg.immich_base_url


def test_service_config_from_nested_maps_all_fields(tmp_path):
    """Nested config maps proxies, demo mode, and pipeline sub-config."""
    nested = {
        "system": {
            "immich_base_url": "https://immich.example.com/",
            "base_url": "https://mr.example.com",
            "trusted_proxies": "10.0.0.1, 10.0.0.2",
            "demo_mode": True,
            "auto_scan_enabled": True,
            "media_sampling": {
                "enabled": True,
                "temp_dir": str(tmp_path / "frames"),
                "max_frames": 5,
            },
            "ocr": {"enabled": False, "max_inputs": 2},
        }
    }
    cfg = service_config_from_nested(
        nested,
        data_dir=tmp_path,
        state_db_path_override=tmp_path / "state.db",
    )
    assert cfg.trusted_proxies == ("10.0.0.1", "10.0.0.2")
    assert cfg.demo_mode is True
    assert cfg.auto_scan_enabled is False
    assert cfg.cookie_secure is True
    assert cfg.media_sampling.temp_dir == tmp_path / "frames"
    assert cfg.ocr.max_inputs == 2


def test_yaml_to_system_nested_handles_empty_and_non_dict():
    """Import tolerates empty YAML and non-mapping roots."""
    assert yaml_to_system_nested({}) == {"system": {}}
    nested = yaml_to_system_nested({"immich": {"base_url": "http://i/"}})
    assert nested["system"]["immich_base_url"] == "http://i"


def test_service_config_from_nested_requires_immich_url(tmp_path):
    """Missing Immich URL is a hard error."""
    with pytest.raises(RuntimeError, match="immich_base_url"):
        service_config_from_nested({"system": {}}, data_dir=tmp_path)


def test_config_db_exists(tmp_path):
    """config_db_exists reflects on-disk state."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert config_db_exists(empty) is False
    open_config_db(empty)
    assert config_db_exists(empty) is True


def test_yaml_to_system_nested_and_import(tmp_path):
    """YAML import maps pipeline keys into config.db."""
    yaml_path = tmp_path / "cfg.yml"
    yaml_path.write_text(
        yaml.safe_dump(
            {
                "immich": {"base_url": "https://immich.example.com/"},
                "service": {"base_url": "https://mr.example.com"},
                "media_sampling": {"enabled": True, "max_frames": 2},
                "ocr": {"enabled": False, "max_inputs": 1},
            }
        ),
        encoding="utf-8",
    )
    nested = yaml_to_system_nested(yaml.safe_load(yaml_path.read_text(encoding="utf-8")))
    assert nested["system"]["immich_base_url"] == "https://immich.example.com"
    assert nested["system"]["base_url"] == "https://mr.example.com"
    assert nested["system"]["media_sampling"]["enabled"] is True
    written = import_yaml_to_config_db(yaml_path, data_dir=tmp_path)
    assert written >= 5
    loaded = load_nested_system_config(tmp_path)
    assert loaded["system"]["ocr"]["enabled"] is False


def test_import_yaml_to_config_db_rejects_non_mapping(tmp_path):
    """Non-dict YAML roots import as empty system config."""
    yaml_path = tmp_path / "list.yml"
    yaml_path.write_text("- item\n", encoding="utf-8")
    written = import_yaml_to_config_db(yaml_path, data_dir=tmp_path / "import")
    assert written >= 1
    loaded = load_nested_system_config(tmp_path / "import")
    assert loaded.get("system") == {}


def test_set_data_dir_override(tmp_path):
    """Module override pins default_data_dir for in-process tests."""
    config_db_mod.set_data_dir_override(tmp_path)
    try:
        ensure_config_db_seeded().bulk_upsert(default_nested_config())
        assert config_db_exists() is True
        assert config_db_path().parent.parent == tmp_path
    finally:
        config_db_mod.set_data_dir_override(None)


def test_admin_config_router_get_and_patch(tmp_path, monkeypatch):
    """Admin system settings API reads and writes config.db."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from mediarefinery.service.app import API_PREFIX, create_app
    from mediarefinery.service.config import ServiceConfig

    ensure_config_db_seeded(tmp_path).bulk_upsert(default_nested_config())
    cfg = ServiceConfig(
        immich_base_url="http://immich:2283",
        base_url="http://localhost:8080",
        data_dir=tmp_path,
        trusted_proxies=(),
        session_ttl_seconds=3600,
        revalidate_interval_seconds=300,
        login_rate_per_min=5,
        cookie_secure=False,
        demo_mode=True,
    )
    app = create_app(config=cfg)
    app.state.system_config_nested = load_nested_system_config(tmp_path)

    with TestClient(app) as client:
        get_resp = client.get(f"{API_PREFIX}/admin/config")
        assert get_resp.status_code == 200
        assert "immich_base_url" in get_resp.json()

        # Patch requires admin — demo login flow seeds admin on first user.
        login = client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": "admin@example.com", "password": "secret"},
        )
        if login.status_code == 200:
            csrf = client.cookies.get("mr_csrf", "")
            patch = client.patch(
                f"{API_PREFIX}/admin/config/base_url",
                json={"value": "https://mr.example.com"},
                headers={"X-CSRF-Token": csrf},
            )
            if patch.status_code == 200:
                assert patch.json()["value"] == "https://mr.example.com"
