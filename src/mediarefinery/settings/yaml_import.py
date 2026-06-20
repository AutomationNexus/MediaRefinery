"""One-shot YAML → config.db import (pipeline/operator migration)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from mediarefinery.settings.load import ensure_config_db_seeded


def yaml_to_system_nested(yaml_data: dict[str, Any]) -> dict[str, Any]:
    """Map legacy pipeline YAML keys into config.db ``system.*`` keys."""
    system: dict[str, Any] = {}
    immich = yaml_data.get("immich") or {}
    if base := immich.get("base_url"):
        system["immich_base_url"] = str(base).rstrip("/")
    if svc := yaml_data.get("service"):
        if base := svc.get("base_url"):
            system["base_url"] = str(base).rstrip("/")
    sampling = yaml_data.get("media_sampling") or {}
    if sampling:
        system["media_sampling"] = {
            "enabled": bool(sampling.get("enabled", False)),
            "max_original_bytes": int(sampling.get("max_original_bytes", 250 * 1024 * 1024)),
            "max_duration_seconds": int(sampling.get("max_duration_seconds", 300)),
            "max_frames": int(sampling.get("max_frames", 3)),
            "extraction_timeout_seconds": int(
                sampling.get("extraction_timeout_seconds", 60)
            ),
            "ffmpeg_path": str(sampling.get("ffmpeg_path") or "ffmpeg"),
        }
    ocr = yaml_data.get("ocr") or {}
    if ocr:
        system["ocr"] = {
            "enabled": bool(ocr.get("enabled", True)),
            "max_inputs": int(ocr.get("max_inputs", 4)),
            "max_text_chars": int(ocr.get("max_text_chars", 20_000)),
        }
    return {"system": system}


def import_yaml_to_config_db(yaml_path: Path, *, data_dir: Path | None = None) -> int:
    """Import a YAML file into config.db; returns number of keys written."""
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    nested = yaml_to_system_nested(raw if isinstance(raw, dict) else {})
    repo = ensure_config_db_seeded(data_dir)
    return repo.bulk_upsert(nested)
