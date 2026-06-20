"""Default system settings seeded into config.db."""

from __future__ import annotations

DEFAULT_IMMICH_BASE_URL = "http://immich:2283"
DEFAULT_BASE_URL = "http://localhost:8080"


def default_nested_config() -> dict:
    """Nested defaults for a fresh config.db."""
    return {
        "system": {
            "immich_base_url": DEFAULT_IMMICH_BASE_URL,
            "base_url": DEFAULT_BASE_URL,
            "trusted_proxies": "",
            "session_ttl_seconds": 12 * 60 * 60,
            "revalidate_interval_seconds": 5 * 60,
            "login_rate_per_min": 5,
            "auto_scan_enabled": True,
            "demo_mode": False,
            "media_sampling": {
                "enabled": False,
                "max_original_bytes": 250 * 1024 * 1024,
                "max_duration_seconds": 300,
                "max_frames": 3,
                "extraction_timeout_seconds": 60,
                "ffmpeg_path": "ffmpeg",
            },
            "ocr": {
                "enabled": True,
                "max_inputs": 4,
                "max_text_chars": 20_000,
            },
        }
    }
