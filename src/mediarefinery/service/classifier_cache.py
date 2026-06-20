"""Process-cached ONNX classifier sessions for service scans.

``onnxruntime.InferenceSession`` construction is heavyweight (model
parse + graph optimisation + provider warm-up). Each scan would pay
that cost on every asset if the runner constructed a fresh classifier
per call. This module provides a small per-process cache keyed on the
active model's sha256 - the same key the catalog and ``model_registry``
agree on - plus a factory adapter that plugs into the runner's
``classifier_factory`` hook.

The cache is intentionally simple:

- One ``ConfiguredClassifier`` per sha; loaded lazily on first request
  for that sha and reused thereafter.
- A swap to a different active model lazily loads the new entry; the
  old session stays cached so a second swap back is free. Memory
  footprint is bounded by the catalog (3 entries today).
- ``invalidate()`` clears the cache when an admin uninstalls a model.

The actual ONNX backend is injectable so tests can exercise the cache
without a real model file. The default backend factory uses the shared
``OnnxClassifierBackend`` implementation.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..classifier import (
    ClassifierBackend,
    ConfiguredClassifier,
)
from ..config import ClassifierProfile
from ..onnx_backend import OnnxClassifierBackend
from .model_catalog import CatalogEntry

BackendFactory = Callable[[ClassifierProfile], ClassifierBackend]
ClassifierFactory = Callable[[str | None], ConfiguredClassifier]
AdultSubtypeClassifierFactory = Callable[
    [Mapping[str, Any] | None],
    ConfiguredClassifier | None,
]


def profile_from_catalog_entry(
    entry: CatalogEntry, models_dir: Path
) -> ClassifierProfile:
    """Build a :class:`ClassifierProfile` from a catalog entry.

    Pulls preprocessing fields (``input_size``, ``input_mean``,
    ``input_std``, ``output_classes``) out of ``entry.raw`` since
    :class:`CatalogEntry` only models the registry-relevant subset.
    """
    raw = entry.raw
    input_size_raw = raw.get("input_size") or [224, 224]
    if isinstance(input_size_raw, (list, tuple)) and input_size_raw:
        input_size = int(input_size_raw[0])
    else:
        input_size = int(input_size_raw)

    def _triplet(key: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
        value = raw.get(key)
        if not isinstance(value, (list, tuple)) or len(value) != 3:
            return default
        return (float(value[0]), float(value[1]), float(value[2]))

    mean = _triplet("input_mean", (0.0, 0.0, 0.0))
    std = _triplet("input_std", (1.0, 1.0, 1.0))

    classes = raw.get("output_classes")
    if not isinstance(classes, (list, tuple)) or not classes:
        if raw.get("output_classes_url") and entry.kind == "generic_image_classifier":
            # MobileNet/ImageNet catalog entries can reference the upstream
            # synset file instead of inlining 1000 labels. The ONNX backend
            # still needs exactly one configured label per output score, so use
            # stable placeholder labels until the dashboard grows a user-facing
            # ImageNet label mapper.
            classes = [f"imagenet_{index:04d}" for index in range(1000)]
        else:
            classes = ["uncategorised"]
    output_mapping = {str(label): str(label) for label in classes}

    model_path = Path(models_dir) / f"{entry.id}.onnx"
    return ClassifierProfile(
        name=entry.id,
        backend="onnx",
        model_path=str(model_path),
        output_mapping=output_mapping,
        model_version=entry.id,
        input_size=input_size,
        input_mean=mean,
        input_std=std,
    )


def profile_from_adult_subtype_model(
    active_model: Mapping[str, Any],
) -> ClassifierProfile:
    """Build an ONNX classifier profile from a registered subtype model row."""
    metadata = active_model.get("metadata")
    if not isinstance(metadata, Mapping):
        raise UnknownModelError("adult subtype model metadata is missing")
    labels = metadata.get("output_labels")
    if not isinstance(labels, list) or not labels:
        raise UnknownModelError("adult subtype model has no output_labels")
    output_mapping = {
        str(label): str(label)
        for label in labels
        if isinstance(label, str) and label.strip()
    }
    if not output_mapping:
        raise UnknownModelError("adult subtype model has no usable output labels")
    model_path = metadata.get("model_path")
    if not isinstance(model_path, str) or not model_path.strip():
        raise UnknownModelError("adult subtype model_path is missing")
    preprocessing = metadata.get("preprocessing")
    if not isinstance(preprocessing, Mapping):
        preprocessing = {}

    return ClassifierProfile(
        name=str(active_model.get("version") or metadata.get("model_id") or "adult_subtype"),
        backend="onnx",
        model_path=model_path,
        output_mapping=output_mapping,
        model_version=str(active_model.get("version") or "adult_subtype"),
        input_size=_positive_int(preprocessing.get("input_size"), default=224),
        input_mean=_triplet(preprocessing.get("input_mean"), default=(0.0, 0.0, 0.0)),
        input_std=_triplet(preprocessing.get("input_std"), default=(1.0, 1.0, 1.0)),
        input_name=_optional_string(preprocessing.get("input_name")),
        output_name=_optional_string(preprocessing.get("output_name")),
    )


def _default_backend_factory(profile: ClassifierProfile) -> ClassifierBackend:
    return OnnxClassifierBackend(profile)


class UnknownModelError(LookupError):
    """Raised when the active sha has no matching catalog entry."""


class ClassifierSessionCache:
    """Per-process cache of :class:`ConfiguredClassifier` keyed on sha256.

    Thread-safe under the single-replica execution model: a lock
    guards cache reads/writes so two scans starting simultaneously do
    not both pay the cold-load cost.
    """

    def __init__(
        self,
        *,
        models_dir: Path | str,
        catalog: list[CatalogEntry],
        backend_factory: BackendFactory | None = None,
    ) -> None:
        """Initialize the instance.

        Parameters
        ----------
        models_dir : Path | str
        catalog : list[CatalogEntry]
        backend_factory : BackendFactory | None, optional

        Returns
        -------
        None
        """
        self._dir = Path(models_dir)
        self._catalog = list(catalog)
        self._backend_factory = backend_factory or _default_backend_factory
        self._cache: dict[str, ConfiguredClassifier] = {}
        self._lock = threading.Lock()

    @property
    def cached_shas(self) -> tuple[str, ...]:
        """Cached shas.

        Returns
        -------
        tuple[str, ...]
        """
        with self._lock:
            return tuple(self._cache.keys())

    def get(self, sha256: str | None) -> ConfiguredClassifier | None:
        """Get.

        Parameters
        ----------
        sha256 : str | None

        Returns
        -------
        ConfiguredClassifier | None
        """
        if sha256 is None:
            return None
        with self._lock:
            cached = self._cache.get(sha256)
            if cached is not None:
                return cached
            entry = self._find_entry_by_sha(sha256)
            if entry is None:
                raise UnknownModelError(
                    f"no catalog entry matches active sha {sha256[:8]}…"
                )
            profile = profile_from_catalog_entry(entry, self._dir)
            backend = self._backend_factory(profile)
            classifier = ConfiguredClassifier(profile, backend)
            self._cache[sha256] = classifier
            return classifier

    def get_for_profile(
        self,
        cache_key: str,
        profile: ClassifierProfile,
    ) -> ConfiguredClassifier:
        """Return for profile.

        Parameters
        ----------
        cache_key : str
        profile : ClassifierProfile

        Returns
        -------
        ConfiguredClassifier
        """
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
            backend = self._backend_factory(profile)
            classifier = ConfiguredClassifier(profile, backend)
            self._cache[cache_key] = classifier
            return classifier

    def invalidate(self, sha256: str | None = None) -> None:
        """Invalidate.

        Parameters
        ----------
        sha256 : str | None, optional

        Returns
        -------
        None
        """
        with self._lock:
            if sha256 is None:
                self._cache.clear()
            else:
                self._cache.pop(sha256, None)

    def _find_entry_by_sha(self, sha256: str) -> CatalogEntry | None:
        for entry in self._catalog:
            if entry.sha256 == sha256:
                return entry
        return None


def make_cached_classifier_factory(
    cache: ClassifierSessionCache,
) -> ClassifierFactory:
    """Adapter for ``service.runner.RunnerFactories.classifier_factory``."""

    def factory(active_sha: str | None) -> ConfiguredClassifier:
        classifier = cache.get(active_sha)
        if classifier is None:
            raise UnknownModelError("classifier requested with no active model sha")
        return classifier

    return factory


def make_cached_adult_subtype_classifier_factory(
    cache: ClassifierSessionCache,
) -> AdultSubtypeClassifierFactory:
    """Adapter for the runner's optional adult subtype classifier slot."""

    def factory(active_model: Mapping[str, Any] | None) -> ConfiguredClassifier | None:
        if active_model is None:
            return None
        sha256 = active_model.get("sha256")
        if not isinstance(sha256, str) or not sha256:
            raise UnknownModelError("adult subtype model has no sha256")
        profile = profile_from_adult_subtype_model(active_model)
        return cache.get_for_profile(f"adult_subtype:{sha256}", profile)

    return factory


def _positive_int(value: object, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return default
    return int(value)


def _triplet(
    value: object,
    *,
    default: tuple[float, float, float],
) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return default
    out: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            return default
        out.append(float(item))
    return (out[0], out[1], out[2])


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


__all__ = [
    "AdultSubtypeClassifierFactory",
    "BackendFactory",
    "ClassifierFactory",
    "ClassifierSessionCache",
    "UnknownModelError",
    "make_cached_adult_subtype_classifier_factory",
    "make_cached_classifier_factory",
    "profile_from_adult_subtype_model",
    "profile_from_catalog_entry",
]
