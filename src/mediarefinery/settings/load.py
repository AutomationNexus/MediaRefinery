"""Load system settings from config.db and build ServiceConfig."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mediarefinery.service.config import (
    MediaSamplingConfig,
    OcrConfig,
    ServiceConfig,
)

from .config_db import config_db_path, default_data_dir, open_config_db
from .defaults import default_nested_config
from .repository import ConfigDBRepository


def ensure_config_db_seeded(data_dir: Path | None = None) -> ConfigDBRepository:
    """Create config.db and seed defaults when empty."""
    conn = open_config_db(data_dir)
    repo = ConfigDBRepository(conn)
    if not repo.get_all():
        repo.bulk_upsert(default_nested_config())
    return repo


def load_nested_system_config(data_dir: Path | None = None) -> dict[str, Any]:
    """Return nested system config from config.db."""
    return ensure_config_db_seeded(data_dir).get_nested()


def service_config_from_nested(
    nested: dict[str, Any],
    *,
    data_dir: Path,
    state_db_path_override: Path | None = None,
) -> ServiceConfig:
    """Build :class:`ServiceConfig` from a nested config.db dict."""
    sys = nested.get("system") or {}
    ms = sys.get("media_sampling") or {}
    ocr = sys.get("ocr") or {}
    immich = str(sys.get("immich_base_url", "")).rstrip("/")
    if not immich:
        raise RuntimeError("system.immich_base_url must be set in config.db")
    base_url = str(sys.get("base_url", "http://localhost:8080")).rstrip("/")
    demo_mode = bool(sys.get("demo_mode", False))
    temp_dir_raw = ms.get("temp_dir")
    temp_dir = Path(temp_dir_raw) if temp_dir_raw else data_dir / "tmp"
    return ServiceConfig(
        immich_base_url=immich,
        base_url=base_url,
        data_dir=data_dir,
        trusted_proxies=tuple(
            part.strip()
            for part in str(sys.get("trusted_proxies") or "").split(",")
            if part.strip()
        ),
        session_ttl_seconds=int(sys.get("session_ttl_seconds", 12 * 60 * 60)),
        revalidate_interval_seconds=int(sys.get("revalidate_interval_seconds", 5 * 60)),
        login_rate_per_min=int(sys.get("login_rate_per_min", 5)),
        cookie_secure=base_url.startswith("https://"),
        demo_mode=demo_mode,
        auto_scan_enabled=bool(sys.get("auto_scan_enabled", True)) and not demo_mode,
        state_db_path_override=state_db_path_override,
        media_sampling=MediaSamplingConfig(
            enabled=bool(ms.get("enabled", False)),
            max_original_bytes=int(ms.get("max_original_bytes", 250 * 1024 * 1024)),
            max_duration_seconds=int(ms.get("max_duration_seconds", 300)),
            max_frames=int(ms.get("max_frames", 3)),
            extraction_timeout_seconds=int(ms.get("extraction_timeout_seconds", 60)),
            temp_dir=temp_dir,
            ffmpeg_path=str(ms.get("ffmpeg_path") or "ffmpeg"),
        ),
        ocr=OcrConfig(
            enabled=bool(ocr.get("enabled", True)),
            max_inputs=int(ocr.get("max_inputs", 4)),
            max_text_chars=int(ocr.get("max_text_chars", 20_000)),
        ),
    )


def load_system_config(
    data_dir: Path | None = None,
    *,
    state_db_path_override: Path | None = None,
) -> ServiceConfig:
    """Load :class:`ServiceConfig` from config.db under *data_dir*."""
    root = data_dir if data_dir is not None else default_data_dir()
    nested = load_nested_system_config(root)
    return service_config_from_nested(
        nested,
        data_dir=root,
        state_db_path_override=state_db_path_override,
    )


def config_db_exists(data_dir: Path | None = None) -> bool:
    """Return whether ``config.db`` exists under the runtime data directory."""
    return config_db_path(data_dir).is_file()
