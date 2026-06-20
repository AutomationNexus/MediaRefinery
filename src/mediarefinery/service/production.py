"""Production runner-factory wiring for real service scans."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import AppConfig
from ..immich import HttpImmichClient, ImmichClient, ImmichClientError
from ..ocr import NoopOcrAnalyzer, OcrAnalyzer, OcrModelPaths, RapidOcrAnalyzer
from .classifier_cache import (
    ClassifierSessionCache,
    make_cached_adult_subtype_classifier_factory,
    make_cached_classifier_factory,
)
from .config import ServiceConfig
from .runner import RunnerFactories, synthesize_app_config
from .security import AesGcmCipher
from .state_store import StateStore, UserScopedState

ImmichClientFactory = Callable[[str], ImmichClient]


class MissingUserApiKey(RuntimeError):
    """Raised when a real scan is requested before the user stores an API key."""


class ApiKeyValidationError(RuntimeError):
    """Raised when Immich rejects or cannot validate a submitted API key."""


@dataclass(frozen=True)
class ApiKeyValidationResult:
    """Represent ApiKeyValidationResult.

    Attributes
    ----------
    ok : bool
    """

    ok: bool


def latest_user_api_key(
    *,
    store: StateStore,
    cipher: AesGcmCipher,
    user_id: str,
) -> str:
    """Latest user api key.

    Parameters
    ----------
    store : StateStore
    cipher : AesGcmCipher
    user_id : str

    Returns
    -------
    str
    """
    rows = store.with_user(user_id).list_api_keys()
    if not rows:
        raise MissingUserApiKey("user has no stored Immich API key")
    row = rows[-1]
    try:
        plaintext = cipher.decrypt(bytes(row["encrypted_key"])).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise MissingUserApiKey("stored Immich API key cannot be decrypted") from exc
    if not plaintext.strip():
        raise MissingUserApiKey("stored Immich API key is empty")
    return plaintext


def build_runner_factories(
    *,
    store: StateStore,
    cipher: AesGcmCipher,
    config: ServiceConfig,
    classifier_cache: ClassifierSessionCache,
    immich_client_factory: Callable[..., ImmichClient] = HttpImmichClient,
) -> RunnerFactories:
    """Build real production factories for ``submit_real_scan``.

    The classifier factory uses the process cache keyed by active model
    SHA256. The Immich factory decrypts the calling user's latest stored
    API key and creates a real API-key-backed Immich client.
    """

    def immich_factory(user_id: str) -> ImmichClient:
        api_key = latest_user_api_key(store=store, cipher=cipher, user_id=user_id)
        return immich_client_factory(
            base_url=config.immich_base_url,
            api_key=api_key,
        )

    def config_factory(scoped: UserScopedState) -> AppConfig:
        return synthesize_app_config(scoped, media_sampling=config.media_sampling)

    def ocr_factory(active_store: StateStore) -> OcrAnalyzer:
        return build_ocr_analyzer(store=active_store, config=config)

    return RunnerFactories(
        immich_factory=immich_factory,
        classifier_factory=make_cached_classifier_factory(classifier_cache),
        config_factory=config_factory,
        ocr_factory=ocr_factory,
        adult_subtype_classifier_factory=make_cached_adult_subtype_classifier_factory(
            classifier_cache
        ),
    )


def build_ocr_analyzer(
    *,
    store: StateStore,
    config: ServiceConfig,
) -> OcrAnalyzer:
    """Build ocr analyzer.

    Parameters
    ----------
    store : StateStore
    config : ServiceConfig

    Returns
    -------
    OcrAnalyzer
    """
    if not config.ocr.enabled:
        return NoopOcrAnalyzer(status="disabled")
    active = store.active_ocr_model()
    if active is None:
        return NoopOcrAnalyzer(status="model_missing")

    metadata = active.get("metadata")
    if not isinstance(metadata, dict):
        return NoopOcrAnalyzer(status="model_missing", reason="ocr_metadata_missing")
    artifacts = metadata.get("artifacts")
    if not isinstance(artifacts, list):
        return NoopOcrAnalyzer(status="model_missing", reason="ocr_artifacts_missing")

    root = config.data_dir / "models" / str(active["version"])
    paths = _ocr_model_paths(root, artifacts)
    if paths is None:
        return NoopOcrAnalyzer(status="model_missing", reason="ocr_artifact_missing")

    language = metadata.get("language")
    return RapidOcrAnalyzer(
        model_paths=paths,
        model_sha256=str(active["sha256"]),
        language=str(language) if isinstance(language, str) and language else "en",
        max_inputs=config.ocr.max_inputs,
        max_text_chars=config.ocr.max_text_chars,
    )


def _ocr_model_paths(
    root: Path,
    artifacts: list[object],
) -> OcrModelPaths | None:
    by_role: dict[str, Path] = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        role = artifact.get("role")
        target = artifact.get("target")
        if not isinstance(role, str) or not isinstance(target, str):
            continue
        candidate = (root / target).resolve()
        if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
            return None
        by_role[role] = candidate

    detector = by_role.get("detector")
    recognizer = by_role.get("recognizer")
    dictionary = by_role.get("dictionary")
    if detector is None or recognizer is None or dictionary is None:
        return None
    classifier = by_role.get("classifier")
    return OcrModelPaths(
        detector=detector,
        recognizer=recognizer,
        dictionary=dictionary,
        classifier=classifier,
    )


def validate_api_key(
    *,
    config: ServiceConfig,
    api_key: str,
    immich_client_factory: Callable[..., HttpImmichClient] = HttpImmichClient,
) -> ApiKeyValidationResult:
    """Validate that an API key can perform the read path needed by scans."""
    try:
        client = immich_client_factory(
            base_url=config.immich_base_url,
            api_key=api_key,
        )
        client.list_assets(page_size=1, media_types={"image"})
    except ImmichClientError as exc:
        raise ApiKeyValidationError(exc.error_code) from exc
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise ApiKeyValidationError("api_key_validation_failed") from exc
    return ApiKeyValidationResult(ok=True)


__all__ = [
    "ApiKeyValidationError",
    "ApiKeyValidationResult",
    "MissingUserApiKey",
    "build_ocr_analyzer",
    "build_runner_factories",
    "latest_user_api_key",
    "validate_api_key",
]
