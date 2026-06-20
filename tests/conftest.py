"""pytest bootstrap and hermetic test environment defaults."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mediarefinery.settings.defaults import default_nested_config
from mediarefinery.settings.load import ensure_config_db_seeded


@pytest.fixture(autouse=True)
def _clean_cred_env(monkeypatch):
    """Scrub inherited credential env vars so tests never pick up local secrets."""
    prefixes = (
        "MEDIAREFINERY_",
        "MR_",
        "IMMICH_",
        "GIT_PROVIDER_",
        "GIT_DEFAULT",
        "GIT_SERVICE",
    )
    for key in list(os.environ):
        if key == "MEDIAREFINERY_DATA_DIR":
            continue
        if key.startswith(prefixes):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture(autouse=True)
def _hermetic_config_db(tmp_path, monkeypatch):
    """Isolate config.db under tmp_path for every test."""
    monkeypatch.setenv("MEDIAREFINERY_DATA_DIR", str(tmp_path))
    ensure_config_db_seeded(tmp_path).bulk_upsert(default_nested_config())


@pytest.fixture
def seeded_config_db(tmp_path) -> Path:
    """Temp data dir with a seeded config.db."""
    nested = default_nested_config()
    ensure_config_db_seeded(tmp_path).bulk_upsert(nested)
    return tmp_path
