"""MR_DEMO / config.db system settings."""

from __future__ import annotations

import logging

import pytest

from mediarefinery.service.config import load_service_config
from mediarefinery.settings.defaults import default_nested_config
from mediarefinery.settings.load import ensure_config_db_seeded


def _seed(tmp_path, **system_overrides):
    nested = default_nested_config()
    nested["system"].update(system_overrides)
    repo = ensure_config_db_seeded(tmp_path)
    repo.bulk_upsert(nested)
    return tmp_path


def test_demo_mode_defaults_false(tmp_path):
    """Fresh config.db defaults demo_mode to false."""
    _seed(tmp_path)
    cfg = load_service_config(data_dir=tmp_path)
    assert cfg.demo_mode is False


def test_demo_mode_enabled_from_config_db(tmp_path):
    """system.demo_mode in config.db enables demo mode."""
    _seed(tmp_path, demo_mode=True)
    cfg = load_service_config(data_dir=tmp_path)
    assert cfg.demo_mode is True


def test_state_db_path_override_is_loaded(tmp_path):
    """Optional state_db_path_override still applies for tests."""
    db_path = tmp_path / "custom-state.sqlite3"
    _seed(tmp_path)
    cfg = load_service_config(data_dir=tmp_path, state_db_path_override=db_path)
    assert cfg.state_db_path == db_path


def test_media_sampling_knobs_from_config_db(tmp_path):
    """Media sampling nested keys load from config.db."""
    _seed(
        tmp_path,
        media_sampling={
            "enabled": True,
            "max_original_bytes": 1234,
            "max_duration_seconds": 12,
            "max_frames": 5,
            "extraction_timeout_seconds": 9,
            "temp_dir": str(tmp_path / "sampling"),
            "ffmpeg_path": "custom-ffmpeg",
        },
    )
    cfg = load_service_config(data_dir=tmp_path)
    assert cfg.media_sampling.enabled is True
    assert cfg.media_sampling.max_original_bytes == 1234
    assert cfg.media_sampling.max_duration_seconds == 12
    assert cfg.media_sampling.max_frames == 5
    assert cfg.media_sampling.extraction_timeout_seconds == 9
    assert cfg.media_sampling.temp_dir == tmp_path / "sampling"
    assert cfg.media_sampling.ffmpeg_path == "custom-ffmpeg"


def test_ocr_knobs_from_config_db(tmp_path):
    """OCR nested keys load from config.db."""
    _seed(
        tmp_path,
        ocr={"enabled": False, "max_inputs": 7, "max_text_chars": 12345},
    )
    cfg = load_service_config(data_dir=tmp_path)
    assert cfg.ocr.enabled is False
    assert cfg.ocr.max_inputs == 7
    assert cfg.ocr.max_text_chars == 12345


def test_demo_banner_logged_on_create_app(tmp_path, monkeypatch, caplog):
    """Test demo banner logged on create app."""
    pytest.importorskip("fastapi")
    import httpx

    from mediarefinery.service.app import create_app
    from mediarefinery.service.config import ServiceConfig

    cfg = ServiceConfig(
        immich_base_url="http://immich.invalid",
        base_url="http://localhost:8080",
        data_dir=tmp_path,
        trusted_proxies=(),
        session_ttl_seconds=3600,
        revalidate_interval_seconds=10_000_000,
        login_rate_per_min=100,
        cookie_secure=False,
        demo_mode=True,
    )

    original = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(404)
        )
        return original(*args, **kwargs)

    monkeypatch.setattr("mediarefinery.service.app.httpx.Client", patched)

    with caplog.at_level(logging.WARNING, logger="mediarefinery.service"):
        create_app(config=cfg)

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("demo_mode active" in m for m in messages), messages

