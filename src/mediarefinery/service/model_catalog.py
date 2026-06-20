"""Loader for the curated ONNX model catalog (`docs/models/catalog.json`).

The catalog ships in the repository (and inside the Docker image) and
is read at request-time by the model-lifecycle endpoints. Schema is
documented in catalog.json itself; this module enforces the bits the
backend cares about: every entry has a stable id, a verified or
unavailable status, and (when verified) a sha256 + size_bytes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_PATH = Path("docs/models/catalog.json")
SUPPORTED_SCHEMA_VERSION = "2"
MODEL_TASK_PRIMARY_SAFETY = "primary_safety"
MODEL_TASK_ADULT_SUBTYPE = "adult_subtype"
MODEL_TASK_OCR = "ocr"
MODEL_TASK_SEMANTIC_EMBEDDING = "semantic_embedding"
MODEL_TASKS = frozenset(
    {
        MODEL_TASK_PRIMARY_SAFETY,
        MODEL_TASK_ADULT_SUBTYPE,
        MODEL_TASK_OCR,
        MODEL_TASK_SEMANTIC_EMBEDDING,
    }
)
BINARY_SAFETY_LABELS = frozenset({"sfw", "safe", "nsfw", "unsafe"})


class CatalogError(RuntimeError):
    """Raised when the catalog file is missing, malformed, or unsupported.

    Also raised when the catalog refers to an unknown schema version.
    """


@dataclass(frozen=True)
class CatalogEntry:
    """Represent CatalogEntry.

    Attributes
    ----------
    id : str
    name : str
    kind : str
    status : str
    url : str
    sha256 : str
    size_bytes : int | None
    license : str
    license_url : str
    presets : tuple[str, ...]
    raw : dict[str, Any]
    """

    id: str
    name: str
    kind: str
    status: str
    url: str
    sha256: str
    size_bytes: int | None
    license: str
    license_url: str
    presets: tuple[str, ...]
    raw: dict[str, Any]

    @property
    def installable(self) -> bool:
        """Installable.

        Returns
        -------
        bool
        """
        return self.status == "verified"

    @property
    def artifacts(self) -> tuple[dict[str, Any], ...]:
        """Artifacts.

        Returns
        -------
        tuple[dict[str, Any], ...]
        """
        artifacts = self.raw.get("artifacts")
        if not isinstance(artifacts, list):
            return ()
        return tuple(item for item in artifacts if isinstance(item, dict))

    @property
    def task(self) -> str:
        """Task.

        Returns
        -------
        str
        """
        raw_task = self.raw.get("task")
        if isinstance(raw_task, str) and raw_task.strip():
            return raw_task.strip()
        return model_task_for_kind(self.kind)


def _default_catalog_path() -> Path:
    if CATALOG_PATH.exists():
        return CATALOG_PATH
    return Path(sys.prefix) / CATALOG_PATH


def load_catalog(path: Path | str | None = None) -> list[CatalogEntry]:
    """Load catalog.

    Parameters
    ----------
    path : Path | str | None, optional

    Returns
    -------
    list[CatalogEntry]
    """
    target = Path(path) if path is not None else _default_catalog_path()
    if not target.exists():
        raise CatalogError(f"catalog file not found: {target}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalog is not valid JSON: {exc}") from exc

    schema = str(data.get("$schema_version", ""))
    if schema != SUPPORTED_SCHEMA_VERSION:
        raise CatalogError(
            f"catalog schema {schema!r} is not supported "
            f"(expected {SUPPORTED_SCHEMA_VERSION!r})"
        )

    raw_models = data.get("models")
    if not isinstance(raw_models, list):
        raise CatalogError("catalog.models must be a list")

    entries: list[CatalogEntry] = []
    seen_ids: set[str] = set()
    for raw in raw_models:
        if not isinstance(raw, dict):
            raise CatalogError("each catalog entry must be an object")
        try:
            entry = CatalogEntry(
                id=str(raw["id"]),
                name=str(raw["name"]),
                kind=str(raw["kind"]),
                status=str(raw["status"]),
                url=str(raw["url"]),
                sha256=str(raw["sha256"]),
                size_bytes=raw.get("size_bytes"),
                license=str(raw["license"]),
                license_url=str(raw.get("license_url", "")),
                presets=tuple(str(p) for p in raw.get("presets", ())),
                raw=raw,
            )
        except KeyError as exc:
            raise CatalogError(f"catalog entry missing field: {exc}") from exc
        if entry.id in seen_ids:
            raise CatalogError(f"duplicate catalog entry id: {entry.id}")
        seen_ids.add(entry.id)
        _validate_task(entry)
        if entry.installable:
            if not entry.url.startswith("https://"):
                raise CatalogError(
                    f"verified entry {entry.id} must use https:// (got {entry.url!r})"
                )
            if not entry.sha256 or len(entry.sha256) != 64:
                raise CatalogError(
                    f"verified entry {entry.id} must have a 64-char sha256"
                )
            _validate_artifacts(entry)
            if entry.task == MODEL_TASK_ADULT_SUBTYPE:
                _validate_adult_subtype_catalog_entry(entry)
        entries.append(entry)
    return entries


def model_task_for_kind(kind: str) -> str:
    """Model task for kind.

    Parameters
    ----------
    kind : str

    Returns
    -------
    str
    """
    normalized = kind.strip().lower()
    if normalized == "ocr_bundle" or normalized.startswith("ocr_"):
        return MODEL_TASK_OCR
    if normalized in {"adult_subtype_classifier", "adult_subtype_model"}:
        return MODEL_TASK_ADULT_SUBTYPE
    if normalized in {
        "semantic_embedding",
        "semantic_embedding_model",
        "clip_embedding",
        "siglip_embedding",
    }:
        return MODEL_TASK_SEMANTIC_EMBEDDING
    return MODEL_TASK_PRIMARY_SAFETY


def _validate_task(entry: CatalogEntry) -> None:
    if entry.task not in MODEL_TASKS:
        supported = ", ".join(sorted(MODEL_TASKS))
        raise CatalogError(
            f"catalog entry {entry.id} task {entry.task!r} is not supported "
            f"(expected one of {supported})"
        )


def _validate_adult_subtype_catalog_entry(entry: CatalogEntry) -> None:
    labels = _output_classes(entry.raw)
    if not labels:
        raise CatalogError(
            f"adult subtype entry {entry.id} must declare non-empty output_classes"
        )
    if _binary_only(labels):
        raise CatalogError(
            f"adult subtype entry {entry.id} cannot use binary-only safety labels"
        )


def _validate_artifacts(entry: CatalogEntry) -> None:
    artifacts = entry.raw.get("artifacts")
    if artifacts is None:
        return
    if not isinstance(artifacts, list) or not artifacts:
        raise CatalogError(f"verified entry {entry.id} artifacts must be a non-empty list")
    seen_targets: set[str] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, dict):
            raise CatalogError(f"{entry.id}.artifacts[{index}] must be an object")
        path = f"{entry.id}.artifacts[{index}]"
        url = artifact.get("url")
        sha256 = artifact.get("sha256")
        size_bytes = artifact.get("size_bytes")
        target = artifact.get("target") or artifact.get("path")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise CatalogError(f"{path}.url must use https://")
        if not isinstance(sha256, str) or len(sha256) != 64:
            raise CatalogError(f"{path}.sha256 must be 64 chars")
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 1:
            raise CatalogError(f"{path}.size_bytes must be a positive integer")
        if not isinstance(target, str) or not target.strip():
            raise CatalogError(f"{path}.target must be a non-empty string")
        if target in seen_targets:
            raise CatalogError(f"{path}.target duplicates {target!r}")
        seen_targets.add(target)


def _output_classes(raw: dict[str, Any]) -> list[str]:
    value = raw.get("output_classes")
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def _binary_only(labels: list[str]) -> bool:
    normalized = {label.strip().lower() for label in labels if label.strip()}
    return bool(normalized) and normalized.issubset(BINARY_SAFETY_LABELS)


def find_entry(entries: list[CatalogEntry], model_id: str) -> CatalogEntry | None:
    """Find entry.

    Parameters
    ----------
    entries : list[CatalogEntry]
    model_id : str

    Returns
    -------
    CatalogEntry | None
    """
    for entry in entries:
        if entry.id == model_id:
            return entry
    return None


__all__ = [
    "CATALOG_PATH",
    "BINARY_SAFETY_LABELS",
    "CatalogEntry",
    "CatalogError",
    "MODEL_TASK_ADULT_SUBTYPE",
    "MODEL_TASK_OCR",
    "MODEL_TASK_PRIMARY_SAFETY",
    "MODEL_TASK_SEMANTIC_EMBEDDING",
    "MODEL_TASKS",
    "SUPPORTED_SCHEMA_VERSION",
    "_default_catalog_path",
    "find_entry",
    "load_catalog",
    "model_task_for_kind",
]
