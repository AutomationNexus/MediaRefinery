"""Install / uninstall ONNX models from the curated catalog.

Per the service-mode invariants (ADR-0010) and threat-model T08:
- No bundled weights. First-run downloads to ``/data/models/<id>.onnx``.
- SHA256 verification on every download; mismatch -> file deleted,
  install refused.
- Explicit license acceptance is captured in ``audit_log`` with the
  acting admin user_id, model id, sha256, and timestamp.
- HTTPS only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import httpx

from .model_catalog import (
    BINARY_SAFETY_LABELS,
    MODEL_TASK_ADULT_SUBTYPE,
    MODEL_TASK_OCR,
    MODEL_TASK_PRIMARY_SAFETY,
    CatalogEntry,
)

log = logging.getLogger("mediarefinery.service.models")

CHUNK_SIZE = 64 * 1024
MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024  # 1 GiB cap; refuse oversized payloads
USER_MODEL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SUBTYPE_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_ADULT_SUBTYPE_THRESHOLD = 0.65


class InstallError(RuntimeError):
    """Raised on any install-time failure that is not a hash mismatch."""


class HashMismatch(InstallError):
    """Represent HashMismatch."""

    pass


@dataclass(frozen=True)
class InstalledModel:
    """Represent InstalledModel.

    Attributes
    ----------
    id : int
    name : str
    version : str
    sha256 : str
    license : str | None
    active : bool
    path : Path | None
    kind : str
    active_slot : str
    """

    id: int
    name: str
    version: str
    sha256: str
    license: str | None
    active: bool
    path: Path | None  # None for older registry rows without model paths
    kind: str = "classifier"
    active_slot: str = "classifier"


@dataclass(frozen=True)
class AdultSubtypeProfile:
    """Represent AdultSubtypeProfile.

    Attributes
    ----------
    model_id : str
    name : str
    model_path : Path
    output_labels : tuple[str, ...]
    thresholds : dict[str, float]
    admin_acknowledged : bool
    input_size : int
    input_mean : tuple[float, float, float]
    input_std : tuple[float, float, float]
    input_name : str | None
    output_name : str | None
    """

    model_id: str
    name: str
    model_path: Path
    output_labels: tuple[str, ...]
    thresholds: dict[str, float]
    admin_acknowledged: bool
    input_size: int = 224
    input_mean: tuple[float, float, float] = (0.0, 0.0, 0.0)
    input_std: tuple[float, float, float] = (1.0, 1.0, 1.0)
    input_name: str | None = None
    output_name: str | None = None


def model_storage_path(data_dir: Path, entry: CatalogEntry) -> Path:
    """Model storage path.

    Parameters
    ----------
    data_dir : Path
    entry : CatalogEntry

    Returns
    -------
    Path
    """
    if entry.artifacts:
        return data_dir / "models" / entry.id
    return data_dir / "models" / f"{entry.id}.onnx"


def install_model(
    *,
    entry: CatalogEntry,
    data_dir: Path,
    conn: sqlite3.Connection,
    actor_user_id: str,
    license_accepted: bool,
    client: httpx.Client | None = None,
    timeout: float = 60.0,
) -> InstalledModel:
    """Install model.

    Parameters
    ----------
    entry : CatalogEntry
    data_dir : Path
    conn : sqlite3.Connection
    actor_user_id : str
    license_accepted : bool
    client : httpx.Client | None, optional
    timeout : float, optional

    Returns
    -------
    InstalledModel
    """
    if not entry.installable:
        raise InstallError(f"model {entry.id} is not installable (status={entry.status})")
    if not license_accepted:
        raise InstallError("license must be accepted before install")
    if not entry.url.startswith("https://"):
        raise InstallError(f"refusing non-HTTPS download URL: {entry.url}")

    target = model_storage_path(data_dir, entry)
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = _find_registry_row(conn, name=entry.name, sha256=entry.sha256)
    if existing is not None and _installed_target_present(target, entry):
        # Idempotent: model already installed and on disk.
        _set_active(conn, existing["id"], _active_slot_for_entry(entry))
        _audit(
            conn=conn,
            user_id=actor_user_id,
            action="model.install",
            sha256=entry.sha256,
            model_id=entry.id,
            license=entry.license,
            already_installed=True,
        )
        return _row_to_installed(existing, target)

    own_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout, follow_redirects=True)
    try:
        total = _install_entry_payload(entry=entry, target=target, client=client)
    finally:
        if own_client:
            client.close()

    row_id = _insert_registry_row(
        conn,
        name=entry.name,
        version=entry.id,
        sha256=entry.sha256,
        license_=entry.license,
        kind=entry.kind,
        active_slot=_active_slot_for_entry(entry),
        metadata=_metadata_for_entry(entry),
    )
    _set_active(conn, row_id, _active_slot_for_entry(entry))
    _audit(
        conn=conn,
        user_id=actor_user_id,
        action="model.install",
        sha256=entry.sha256,
        model_id=entry.id,
        license=entry.license,
        size_bytes=total,
    )
    log.info(
        "model installed",
        extra={
            "event": "model.install",
            "user_id": actor_user_id,
            "model_id": entry.id,
            "sha256": entry.sha256,
        },
    )
    return InstalledModel(
        id=row_id,
        name=entry.name,
        version=entry.id,
        sha256=entry.sha256,
        license=entry.license,
        active=True,
        kind=entry.kind,
        active_slot=_active_slot_for_entry(entry),
        path=target,
    )


def register_adult_subtype_model(
    *,
    model_id: str,
    name: str | None,
    model_path: Path | str,
    output_labels: list[str] | tuple[str, ...],
    thresholds: Mapping[str, float] | None,
    admin_acknowledged: bool,
    data_dir: Path,
    conn: sqlite3.Connection,
    actor_user_id: str,
    input_size: int = 224,
    input_mean: tuple[float, float, float] | list[float] | None = None,
    input_std: tuple[float, float, float] | list[float] | None = None,
    input_name: str | None = None,
    output_name: str | None = None,
) -> InstalledModel:
    """Register an admin-supplied ONNX adult-subtype profile.

    The source model is copied into managed model storage and activated in the
    ``adult_subtype`` slot. No curated act-level model is implied; labels and
    thresholds come exclusively from the acknowledged profile.
    """
    profile = validate_adult_subtype_profile(
        model_id=model_id,
        name=name,
        model_path=model_path,
        output_labels=output_labels,
        thresholds=thresholds,
        admin_acknowledged=admin_acknowledged,
        input_size=input_size,
        input_mean=input_mean,
        input_std=input_std,
        input_name=input_name,
        output_name=output_name,
    )
    sha256, size_bytes = _hash_file(profile.model_path)
    target = data_dir / "models" / f"{profile.model_id}.onnx"
    target.parent.mkdir(parents=True, exist_ok=True)

    existing = _find_registry_row(conn, name=profile.name, sha256=sha256)
    if existing is not None:
        if not target.is_file():
            _copy_verified_user_model(profile.model_path, target, sha256)
        _set_active(conn, existing["id"], MODEL_TASK_ADULT_SUBTYPE)
        _audit(
            conn=conn,
            user_id=actor_user_id,
            action="model.adult_subtype.register",
            sha256=sha256,
            model_id=profile.model_id,
            license="user-supplied",
            size_bytes=size_bytes,
            already_installed=True,
            extra=_adult_subtype_audit_details(profile),
        )
        return _row_to_installed(existing, target)

    _copy_verified_user_model(profile.model_path, target, sha256)
    metadata = _metadata_for_adult_subtype_profile(
        profile=profile,
        managed_path=target,
        sha256=sha256,
        size_bytes=size_bytes,
    )
    row_id = _insert_registry_row(
        conn,
        name=profile.name,
        version=profile.model_id,
        sha256=sha256,
        license_="user-supplied",
        kind="adult_subtype_classifier",
        active_slot=MODEL_TASK_ADULT_SUBTYPE,
        metadata=metadata,
    )
    _set_active(conn, row_id, MODEL_TASK_ADULT_SUBTYPE)
    _audit(
        conn=conn,
        user_id=actor_user_id,
        action="model.adult_subtype.register",
        sha256=sha256,
        model_id=profile.model_id,
        license="user-supplied",
        size_bytes=size_bytes,
        extra=_adult_subtype_audit_details(profile),
    )
    log.info(
        "adult subtype model registered",
        extra={
            "event": "model.adult_subtype.register",
            "user_id": actor_user_id,
            "model_id": profile.model_id,
            "sha256": sha256,
        },
    )
    return InstalledModel(
        id=row_id,
        name=profile.name,
        version=profile.model_id,
        sha256=sha256,
        license="user-supplied",
        active=True,
        kind="adult_subtype_classifier",
        active_slot=MODEL_TASK_ADULT_SUBTYPE,
        path=target,
    )


def validate_adult_subtype_profile(
    *,
    model_id: str,
    name: str | None,
    model_path: Path | str,
    output_labels: list[str] | tuple[str, ...],
    thresholds: Mapping[str, float] | None,
    admin_acknowledged: bool,
    input_size: int = 224,
    input_mean: tuple[float, float, float] | list[float] | None = None,
    input_std: tuple[float, float, float] | list[float] | None = None,
    input_name: str | None = None,
    output_name: str | None = None,
) -> AdultSubtypeProfile:
    """Validate adult subtype profile.

    Parameters
    ----------
    model_id : str
    name : str | None
    model_path : Path | str
    output_labels : list[str] | tuple[str, ...]
    thresholds : Mapping[str, float] | None
    admin_acknowledged : bool
    input_size : int, optional
    input_mean : tuple[float, float, float] | list[float] | None, optional
    input_std : tuple[float, float, float] | list[float] | None, optional
    input_name : str | None, optional
    output_name : str | None, optional

    Returns
    -------
    AdultSubtypeProfile
    """
    if not admin_acknowledged:
        raise InstallError("admin acknowledgement is required for adult subtype models")
    normalized_id = str(model_id).strip()
    if not USER_MODEL_ID_RE.fullmatch(normalized_id):
        raise InstallError(
            "model_id must start with a letter or number and contain only "
            "letters, numbers, dot, underscore, or dash"
        )
    path = Path(model_path)
    try:
        if not path.is_file():
            raise InstallError("model_path must point to a readable ONNX file")
    except OSError as exc:
        raise InstallError("model_path must point to a readable ONNX file") from exc

    labels = _normalize_subtype_labels(output_labels)
    if not labels:
        raise InstallError("adult subtype model must declare at least one output label")
    if _binary_only_subtype_labels(labels):
        raise InstallError("binary-only safety models cannot activate as adult subtypes")

    threshold_map = _normalize_subtype_thresholds(thresholds, labels)
    if isinstance(input_size, bool) or not isinstance(input_size, int) or input_size < 1:
        raise InstallError("input_size must be a positive integer")
    mean = _normalize_triplet(input_mean, default=(0.0, 0.0, 0.0), name="input_mean")
    std = _normalize_triplet(input_std, default=(1.0, 1.0, 1.0), name="input_std")
    input_name = _optional_non_empty(input_name, "input_name")
    output_name = _optional_non_empty(output_name, "output_name")
    display_name = str(name).strip() if isinstance(name, str) and name.strip() else normalized_id
    return AdultSubtypeProfile(
        model_id=normalized_id,
        name=display_name,
        model_path=path,
        output_labels=labels,
        thresholds=threshold_map,
        admin_acknowledged=True,
        input_size=input_size,
        input_mean=mean,
        input_std=std,
        input_name=input_name,
        output_name=output_name,
    )


def uninstall_model(
    *,
    registry_id: int,
    data_dir: Path,
    conn: sqlite3.Connection,
    actor_user_id: str,
) -> None:
    """Uninstall model.

    Parameters
    ----------
    registry_id : int
    data_dir : Path
    conn : sqlite3.Connection
    actor_user_id : str

    Returns
    -------
    None
    """
    cursor = conn.execute(
        "SELECT id, name, version, sha256, metadata_json FROM model_registry WHERE id = ?",
        (registry_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise InstallError(f"installed model {registry_id} not found")
    metadata = _metadata_from_row(row)
    target = _target_for_row(data_dir, row, metadata)
    if target.exists():
        try:
            _remove_installed_target(target, data_dir=data_dir)
        except OSError as exc:
            raise InstallError(f"unable to remove {target}: {exc}") from exc
    conn.execute("DELETE FROM model_registry WHERE id = ?", (registry_id,))
    conn.commit()
    _audit(
        conn=conn,
        user_id=actor_user_id,
        action="model.uninstall",
        sha256=row["sha256"],
        model_id=row["version"],
    )
    log.info(
        "model uninstalled",
        extra={
            "event": "model.uninstall",
            "user_id": actor_user_id,
            "model_id": row["version"],
        },
    )


def list_installed(*, conn: sqlite3.Connection, data_dir: Path) -> list[InstalledModel]:
    """List installed.

    Parameters
    ----------
    conn : sqlite3.Connection
    data_dir : Path

    Returns
    -------
    list[InstalledModel]
    """
    cursor = conn.execute(
        "SELECT id, name, version, sha256, license, active, kind, active_slot, metadata_json "
        "FROM model_registry ORDER BY id"
    )
    out: list[InstalledModel] = []
    for row in cursor.fetchall():
        metadata = _metadata_from_row(row)
        path = _target_for_row(data_dir, row, metadata)
        out.append(
            InstalledModel(
                id=int(row["id"]),
                name=row["name"],
                version=row["version"],
                sha256=row["sha256"],
                license=row["license"],
                active=bool(row["active"]),
                kind=row["kind"],
                active_slot=row["active_slot"],
                path=path if path.exists() else None,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _find_registry_row(
    conn: sqlite3.Connection, *, name: str, sha256: str
) -> sqlite3.Row | None:
    cursor = conn.execute(
        "SELECT * FROM model_registry WHERE name = ? AND sha256 = ?",
        (name, sha256),
    )
    return cast("sqlite3.Row | None", cursor.fetchone())


def _insert_registry_row(
    conn: sqlite3.Connection,
    *,
    name: str,
    version: str,
    sha256: str,
    license_: str | None,
    kind: str,
    active_slot: str,
    metadata: dict[str, Any],
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO model_registry(
            name, version, sha256, license, kind, active_slot, metadata_json, active
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            name,
            version,
            sha256,
            license_,
            kind,
            active_slot,
            json.dumps(metadata, sort_keys=True),
        ),
    )
    conn.commit()
    last_id = cursor.lastrowid
    assert last_id is not None  # INSERT just succeeded
    return int(last_id)


def _set_active(
    conn: sqlite3.Connection,
    registry_id: int,
    active_slot: str,
) -> None:
    slots = (
        (MODEL_TASK_PRIMARY_SAFETY, "classifier")
        if active_slot == MODEL_TASK_PRIMARY_SAFETY
        else (active_slot,)
    )
    with conn:
        conn.execute(
            "UPDATE model_registry SET active = 0 "
            f"WHERE active_slot IN ({','.join('?' for _ in slots)})",
            slots,
        )
        conn.execute(
            "UPDATE model_registry SET active = 1 WHERE id = ?", (registry_id,)
        )


def _audit(
    *,
    conn: sqlite3.Connection,
    user_id: str,
    action: str,
    sha256: str,
    model_id: str,
    license: str | None = None,
    size_bytes: int | None = None,
    already_installed: bool = False,
    extra: dict[str, Any] | None = None,
) -> None:
    details = {
        "model_id": model_id,
        "sha256": sha256,
        "license": license,
        "size_bytes": size_bytes,
        "already_installed": already_installed,
        "accepted_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if extra:
        details.update(extra)
    conn.execute(
        """
        INSERT INTO audit_log(user_id, action, details_json)
        VALUES (?, ?, ?)
        """,
        (user_id, action, json.dumps(details, sort_keys=True)),
    )
    conn.commit()


def _row_to_installed(row: sqlite3.Row, path: Path | None) -> InstalledModel:
    return InstalledModel(
        id=int(row["id"]),
        name=row["name"],
        version=row["version"],
        sha256=row["sha256"],
        license=row["license"],
        active=True,
        kind=row["kind"],
        active_slot=row["active_slot"],
        path=path,
    )


def _install_entry_payload(
    *,
    entry: CatalogEntry,
    target: Path,
    client: httpx.Client,
) -> int:
    if entry.artifacts:
        return _install_artifact_bundle(entry=entry, target=target, client=client)
    return _install_single_file(entry=entry, target=target, client=client)


def _install_single_file(
    *,
    entry: CatalogEntry,
    target: Path,
    client: httpx.Client,
) -> int:
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f"{entry.id}.",
        suffix=".part",
        dir=target.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        total = _download_to_path(
            url=entry.url,
            destination=tmp_path,
            expected_sha256=entry.sha256,
            expected_size=entry.size_bytes,
            client=client,
            label=entry.id,
            open_fd=tmp_fd,
        )
        if target.exists():
            target.unlink()
        os.replace(tmp_path, target)
        return total
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def _install_artifact_bundle(
    *,
    entry: CatalogEntry,
    target: Path,
    client: httpx.Client,
) -> int:
    tmp_dir = Path(
        tempfile.mkdtemp(prefix=f"{entry.id}.", suffix=".partdir", dir=target.parent)
    )
    total = 0
    try:
        for artifact in entry.artifacts:
            artifact_target = tmp_dir / _artifact_target(artifact)
            artifact_target.parent.mkdir(parents=True, exist_ok=True)
            total += _download_to_path(
                url=str(artifact["url"]),
                destination=artifact_target,
                expected_sha256=str(artifact["sha256"]),
                expected_size=int(artifact["size_bytes"]),
                client=client,
                label=f"{entry.id}:{artifact_target.name}",
            )
        if target.exists():
            _remove_installed_target(target, data_dir=target.parent.parent)
        os.replace(tmp_dir, target)
        return total
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def _download_to_path(
    *,
    url: str,
    destination: Path,
    expected_sha256: str,
    expected_size: int | None,
    client: httpx.Client,
    label: str,
    open_fd: int | None = None,
) -> int:
    hasher = hashlib.sha256()
    total = 0
    fh = os.fdopen(open_fd, "wb") if open_fd is not None else destination.open("wb")
    with fh:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise InstallError(f"download failed: HTTP {response.status_code}")
            for chunk in response.iter_bytes(chunk_size=CHUNK_SIZE):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise InstallError(
                        f"download exceeded {MAX_DOWNLOAD_BYTES} byte cap"
                    )
                hasher.update(chunk)
                fh.write(chunk)
    actual = hasher.hexdigest()
    if actual != expected_sha256:
        raise HashMismatch(
            f"sha256 mismatch for {label}: expected {expected_sha256}, got {actual}"
        )
    if expected_size is not None and total != expected_size:
        raise InstallError(
            f"size mismatch for {label}: expected {expected_size}, got {total}"
        )
    return total


def _installed_target_present(target: Path, entry: CatalogEntry) -> bool:
    if not target.exists():
        return False
    if not entry.artifacts:
        return target.is_file()
    return all((target / _artifact_target(artifact)).is_file() for artifact in entry.artifacts)


def _artifact_target(artifact: dict[str, Any]) -> Path:
    raw = artifact.get("target") or artifact.get("path")
    if not isinstance(raw, str):
        raise InstallError("artifact target is missing")
    target = Path(raw)
    if target.is_absolute() or ".." in target.parts:
        raise InstallError(f"unsafe artifact target: {raw}")
    return target


def _metadata_for_entry(entry: CatalogEntry) -> dict[str, Any]:
    metadata: dict[str, Any] = {"task": entry.task}
    if not entry.artifacts:
        return metadata
    metadata.update(
        {
            "artifacts": [
                {
                    "role": str(artifact.get("role") or ""),
                    "target": str(_artifact_target(artifact)).replace("\\", "/"),
                    "sha256": str(artifact["sha256"]),
                    "size_bytes": int(artifact["size_bytes"]),
                }
                for artifact in entry.artifacts
            ],
            "language": entry.raw.get("language"),
            "runtime": entry.raw.get("runtime"),
        }
    )
    return metadata


def _metadata_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        metadata = {}
    return metadata if isinstance(metadata, dict) else {}


def _target_for_row(
    data_dir: Path,
    row: sqlite3.Row,
    metadata: dict[str, Any],
) -> Path:
    if isinstance(metadata.get("artifacts"), list):
        return data_dir / "models" / str(row["version"])
    return data_dir / "models" / f"{row['version']}.onnx"


def _remove_installed_target(target: Path, *, data_dir: Path) -> None:
    models_dir = (data_dir / "models").resolve()
    resolved = target.resolve()
    if resolved == models_dir or not resolved.is_relative_to(models_dir):
        raise OSError("refusing to remove path outside model storage")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def _active_slot_for_entry(entry: CatalogEntry) -> str:
    if entry.task == MODEL_TASK_OCR:
        return MODEL_TASK_OCR
    if entry.task == MODEL_TASK_ADULT_SUBTYPE:
        return MODEL_TASK_ADULT_SUBTYPE
    return MODEL_TASK_PRIMARY_SAFETY


def _normalize_subtype_labels(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        raise InstallError("output_labels must be a list of labels")
    seen: set[str] = set()
    labels: list[str] = []
    for index, value in enumerate(values):
        if not isinstance(value, str):
            raise InstallError(f"output_labels[{index}] must be a string")
        label = value.strip().lower()
        if not SUBTYPE_LABEL_RE.fullmatch(label):
            raise InstallError(
                f"output_labels[{index}] must match {SUBTYPE_LABEL_RE.pattern}"
            )
        if label in seen:
            raise InstallError(f"duplicate adult subtype label: {label}")
        seen.add(label)
        labels.append(label)
    return tuple(labels)


def _binary_only_subtype_labels(labels: tuple[str, ...]) -> bool:
    normalized = {label.lower() for label in labels}
    return bool(normalized) and normalized.issubset(BINARY_SAFETY_LABELS)


def _normalize_subtype_thresholds(
    values: Mapping[str, float] | None,
    labels: tuple[str, ...],
) -> dict[str, float]:
    label_set = set(labels)
    thresholds = {
        label: DEFAULT_ADULT_SUBTYPE_THRESHOLD
        for label in labels
    }
    if values is None:
        return thresholds
    if not isinstance(values, Mapping):
        raise InstallError("thresholds must be an object keyed by subtype label")
    for raw_label, raw_value in values.items():
        label = str(raw_label).strip().lower()
        if label not in label_set:
            raise InstallError(f"thresholds.{raw_label}: unknown subtype label")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise InstallError(f"thresholds.{raw_label}: must be a number")
        value = float(raw_value)
        if value < 0.0 or value > 1.0:
            raise InstallError(f"thresholds.{raw_label}: must be between 0 and 1")
        thresholds[label] = value
    return thresholds


def _normalize_triplet(
    value: tuple[float, float, float] | list[float] | None,
    *,
    default: tuple[float, float, float],
    name: str,
) -> tuple[float, float, float]:
    if value is None:
        return default
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise InstallError(f"{name} must contain exactly three numbers")
    out: list[float] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise InstallError(f"{name}[{index}] must be a number")
        out.append(float(item))
    return (out[0], out[1], out[2])


def _optional_non_empty(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InstallError(f"{name} must be a non-empty string when provided")
    return value.strip()


def _hash_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as fh:
            while True:
                chunk = fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise InstallError(
                        f"model file exceeded {MAX_DOWNLOAD_BYTES} byte cap"
                    )
                hasher.update(chunk)
    except OSError as exc:
        raise InstallError(f"unable to read user model: {exc}") from exc
    if total < 1:
        raise InstallError("model file is empty")
    return hasher.hexdigest(), total


def _copy_verified_user_model(source: Path, target: Path, expected_sha256: str) -> None:
    try:
        if source.resolve() == target.resolve():
            return
    except OSError:
        pass
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=f"{target.stem}.",
        suffix=".part",
        dir=target.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        os.close(tmp_fd)
        shutil.copyfile(source, tmp_path)
        actual, _size = _hash_file(tmp_path)
        if actual != expected_sha256:
            raise HashMismatch(
                f"sha256 changed while copying {source}: "
                f"expected {expected_sha256}, got {actual}"
            )
        os.replace(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def _metadata_for_adult_subtype_profile(
    *,
    profile: AdultSubtypeProfile,
    managed_path: Path,
    sha256: str,
    size_bytes: int,
) -> dict[str, Any]:
    return {
        "task": MODEL_TASK_ADULT_SUBTYPE,
        "profile_schema_version": 1,
        "model_id": profile.model_id,
        "model_path": str(managed_path),
        "source_path": str(profile.model_path),
        "sha256": sha256,
        "size_bytes": size_bytes,
        "runtime": "onnxruntime",
        "output_labels": list(profile.output_labels),
        "thresholds": dict(profile.thresholds),
        "admin_acknowledged": profile.admin_acknowledged,
        "preprocessing": {
            "input_size": profile.input_size,
            "input_mean": list(profile.input_mean),
            "input_std": list(profile.input_std),
            "input_name": profile.input_name,
            "output_name": profile.output_name,
        },
        "policy": {
            "low_confidence_queue": "review_needed",
            "automatic_actions": "disabled_unless_policy_explicitly_matches_primary_category",
        },
    }


def _adult_subtype_audit_details(profile: AdultSubtypeProfile) -> dict[str, Any]:
    return {
        "profile_schema_version": 1,
        "output_labels": list(profile.output_labels),
        "thresholds": dict(profile.thresholds),
        "admin_acknowledged": profile.admin_acknowledged,
        "task": MODEL_TASK_ADULT_SUBTYPE,
    }


__all__ = [
    "AdultSubtypeProfile",
    "CHUNK_SIZE",
    "DEFAULT_ADULT_SUBTYPE_THRESHOLD",
    "HashMismatch",
    "InstallError",
    "InstalledModel",
    "MAX_DOWNLOAD_BYTES",
    "install_model",
    "list_installed",
    "model_storage_path",
    "register_adult_subtype_model",
    "uninstall_model",
    "validate_adult_subtype_profile",
]
