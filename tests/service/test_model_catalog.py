"""Tests for the catalog loader."""

from __future__ import annotations

import json
import shutil

import pytest

from mediarefinery.service import model_catalog
from mediarefinery.service.model_catalog import (
    SUPPORTED_SCHEMA_VERSION,
    CatalogError,
    find_entry,
    load_catalog,
)


def _write(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def _entry(**overrides):
    base = {
        "id": "m-1",
        "name": "M1",
        "kind": "generic_image_classifier",
        "status": "verified",
        "url": "https://example.invalid/model.onnx",
        "sha256": "a" * 64,
        "size_bytes": 100,
        "license": "Apache-2.0",
        "license_url": "https://example.invalid/LICENSE",
        "presets": ["generic"],
    }
    base.update(overrides)
    return base


def test_load_catalog_real_file():
    """Test load catalog real file."""
    entries = load_catalog()
    ids = {e.id for e in entries}
    assert "mobilenet-v2-imagenet-onnx" in ids
    assert all(e.installable for e in entries if e.status == "verified")


def test_load_catalog_falls_back_to_installed_data_files(tmp_path, monkeypatch):
    """Test load catalog falls back to installed data files."""
    prefix_catalog = tmp_path / "prefix" / "docs" / "models" / "catalog.json"
    prefix_catalog.parent.mkdir(parents=True)
    shutil.copyfile("docs/models/catalog.json", prefix_catalog)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(model_catalog.sys, "prefix", str(tmp_path / "prefix"))

    entries = load_catalog()

    assert find_entry(entries, "mobilenet-v2-imagenet-onnx") is not None


def test_missing_file_raises(tmp_path):
    """Test missing file raises."""
    with pytest.raises(CatalogError, match="not found"):
        load_catalog(tmp_path / "missing.json")


def test_invalid_json_raises(tmp_path):
    """Test invalid json raises."""
    p = tmp_path / "c.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(CatalogError, match="not valid JSON"):
        load_catalog(p)


def test_unsupported_schema_raises(tmp_path):
    """Test unsupported schema raises."""
    p = tmp_path / "c.json"
    _write(p, {"$schema_version": "9", "models": []})
    with pytest.raises(CatalogError, match="schema"):
        load_catalog(p)


def test_models_must_be_list(tmp_path):
    """Test models must be list."""
    p = tmp_path / "c.json"
    _write(p, {"$schema_version": SUPPORTED_SCHEMA_VERSION, "models": {}})
    with pytest.raises(CatalogError, match="must be a list"):
        load_catalog(p)


def test_duplicate_id_rejected(tmp_path):
    """Test duplicate id rejected."""
    p = tmp_path / "c.json"
    _write(p, {"$schema_version": SUPPORTED_SCHEMA_VERSION, "models": [_entry(), _entry()]})
    with pytest.raises(CatalogError, match="duplicate"):
        load_catalog(p)


def test_verified_must_be_https(tmp_path):
    """Test verified must be https."""
    p = tmp_path / "c.json"
    _write(
        p,
        {
            "$schema_version": SUPPORTED_SCHEMA_VERSION,
            "models": [_entry(url="http://example.invalid/m.onnx")],
        },
    )
    with pytest.raises(CatalogError, match="https://"):
        load_catalog(p)


def test_verified_must_have_64_char_sha(tmp_path):
    """Test verified must have 64 char sha."""
    p = tmp_path / "c.json"
    _write(
        p,
        {
            "$schema_version": SUPPORTED_SCHEMA_VERSION,
            "models": [_entry(sha256="short")],
        },
    )
    with pytest.raises(CatalogError, match="sha256"):
        load_catalog(p)


def test_verified_artifact_bundle_requires_pinned_artifacts(tmp_path):
    """Test verified artifact bundle requires pinned artifacts."""
    p = tmp_path / "c.json"
    entry = _entry(
        kind="ocr_bundle",
        artifacts=[
            {
                "role": "detector",
                "target": "det.onnx",
                "url": "https://example.invalid/det.onnx",
                "sha256": "b" * 64,
                "size_bytes": 10,
            }
        ],
    )
    _write(p, {"$schema_version": SUPPORTED_SCHEMA_VERSION, "models": [entry]})
    entries = load_catalog(p)
    assert entries[0].artifacts[0]["role"] == "detector"


def test_verified_artifact_bundle_rejects_unpinned_artifact(tmp_path):
    """Test verified artifact bundle rejects unpinned artifact."""
    p = tmp_path / "c.json"
    entry = _entry(
        kind="ocr_bundle",
        artifacts=[
            {
                "role": "detector",
                "target": "det.onnx",
                "url": "https://example.invalid/det.onnx",
                "sha256": "short",
                "size_bytes": 10,
            }
        ],
    )
    _write(p, {"$schema_version": SUPPORTED_SCHEMA_VERSION, "models": [entry]})
    with pytest.raises(CatalogError, match="sha256"):
        load_catalog(p)


def test_adult_subtype_catalog_entry_rejects_binary_only_labels(tmp_path):
    """Test adult subtype catalog entry rejects binary only labels."""
    p = tmp_path / "c.json"
    entry = _entry(
        kind="adult_subtype_classifier",
        task="adult_subtype",
        output_classes=["sfw", "nsfw"],
    )
    _write(p, {"$schema_version": SUPPORTED_SCHEMA_VERSION, "models": [entry]})
    with pytest.raises(CatalogError, match="binary-only"):
        load_catalog(p)


def test_adult_subtype_catalog_entry_requires_output_labels(tmp_path):
    """Test adult subtype catalog entry requires output labels."""
    p = tmp_path / "c.json"
    entry = _entry(kind="adult_subtype_classifier", task="adult_subtype")
    _write(p, {"$schema_version": SUPPORTED_SCHEMA_VERSION, "models": [entry]})
    with pytest.raises(CatalogError, match="output_classes"):
        load_catalog(p)


def test_unavailable_skips_strict_checks(tmp_path):
    """Test unavailable skips strict checks."""
    p = tmp_path / "c.json"
    _write(
        p,
        {
            "$schema_version": SUPPORTED_SCHEMA_VERSION,
            "models": [
                _entry(
                    id="dead",
                    status="unavailable",
                    url="http://gone/m.onnx",
                    sha256="UPSTREAM_NOT_FOUND",
                )
            ],
        },
    )
    entries = load_catalog(p)
    assert len(entries) == 1
    assert not entries[0].installable


def test_find_entry():
    """Test find entry."""
    entries = load_catalog()
    assert find_entry(entries, "mobilenet-v2-imagenet-onnx") is not None
    assert find_entry(entries, "nope") is None


def test_missing_required_field(tmp_path):
    """Test missing required field."""
    p = tmp_path / "c.json"
    bad = _entry()
    bad.pop("status")
    _write(p, {"$schema_version": SUPPORTED_SCHEMA_VERSION, "models": [bad]})
    with pytest.raises(CatalogError, match="missing field"):
        load_catalog(p)
