"""Real-pipeline runner for service scans.

This module adapts the shared core pipeline (``AssetScanner`` ->
``MediaExtractor`` -> ``ConfiguredClassifier`` -> ``DecisionEngine`` ->
``ActionExecutor``) to the multi-tenant service state store.

Three injectable factories isolate the network, classifier, and config
surfaces:

- ``immich_factory(user_id) -> ImmichClient`` returns the client used
  for a user's scan.
- ``classifier_factory(active_model_sha256) -> ConfiguredClassifier``
  returns the active classifier.
- ``config_factory(scoped_state) -> AppConfig`` synthesizes an
  ``AppConfig`` from per-user categories and policies.

Default factories are intentionally inert and are suitable for tests
only. Production startup supplies real factories from
``service.production``.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..actions import ActionExecutor
from ..analysis import AdultSubtypeModelContext, AnalysisContext, analyze_asset
from ..classifier import (
    ClassificationResult,
    ClassifierError,
    ClassifierInput,
    ConfiguredClassifier,
    NoopClassifier,
)
from ..config import AppConfig, Category, ClassifierProfile
from ..decision import DecisionEngine
from ..extractor import MediaExtractionError, MediaExtractor
from ..immich import AssetRef, ImmichClient, ImmichClientError, MockImmichClient
from ..ocr import NoopOcrAnalyzer, OcrAnalyzer, OcrInput, ocr_result_metadata
from ..scanner import AssetScanner
from .config import MediaSamplingConfig
from .scheduler import ScanRejected, SubmittedScan, submit_scan
from .state_store import StateStore, UserScopedState

log = logging.getLogger("mediarefinery.service.runner")


ImmichFactory = Callable[[str], ImmichClient]
ClassifierFactory = Callable[[str | None], ConfiguredClassifier]
AdultSubtypeClassifierFactory = Callable[
    [dict[str, Any] | None],
    ConfiguredClassifier | None,
]
ConfigFactory = Callable[[UserScopedState], AppConfig]
OcrFactory = Callable[[StateStore], OcrAnalyzer]


@dataclass(frozen=True)
class RunnerFactories:
    """Injection seams for ``make_real_runner``.

    Tests pass deterministic stubs; production startup must inject the
    real ONNX and authenticated-Immich implementations.
    """

    immich_factory: ImmichFactory
    classifier_factory: ClassifierFactory
    config_factory: ConfigFactory
    ocr_factory: OcrFactory | None = None
    adult_subtype_classifier_factory: AdultSubtypeClassifierFactory | None = None


@dataclass(frozen=True)
class ClassificationOutcome:
    """Represent ClassificationOutcome.

    Attributes
    ----------
    result : ClassificationResult
    classifier_metadata : dict[str, str]
    preview_bytes : bytes | None
    sampling_warning : MediaExtractionError | None
    ocr_inputs : tuple[OcrInput, ...]
    subtype_inputs : tuple[ClassifierInput, ...]
    """

    result: ClassificationResult
    classifier_metadata: dict[str, str]
    preview_bytes: bytes | None = None
    sampling_warning: MediaExtractionError | None = None
    ocr_inputs: tuple[OcrInput, ...] = ()
    subtype_inputs: tuple[ClassifierInput, ...] = ()


def synthesize_app_config(
    scoped: UserScopedState,
    *,
    media_sampling: MediaSamplingConfig | None = None,
) -> AppConfig:
    """Build an in-memory ``AppConfig`` from a user's stored config.

    Policies and categories live in ``user_config``; the
    pipeline modules consume an ``AppConfig`` so we synthesise one
    here. Sensitive defaults: ``actions.dry_run=true`` is forced until
    the caller explicitly opts into live writes.
    """
    persisted = scoped.get_config()
    raw_categories = persisted.get("categories") or {}
    raw_policies = persisted.get("policies") or {}

    category_ids = sorted(str(cid) for cid in raw_categories.keys()) or [
        "uncategorised"
    ]
    output_mapping = {cid: cid for cid in category_ids}
    profile = ClassifierProfile(
        name="service-default",
        backend="noop",
        model_path=None,
        output_mapping=output_mapping,
    )
    categories = tuple(Category(id=cid) for cid in category_ids)

    raw: dict[str, Any] = {
        "version": 1,
        "categories": [{"id": cid} for cid in category_ids],
        "classifier_profiles": {"service-default": {"backend": "noop"}},
        "classifier": {"active_profile": "service-default"},
        "scanner": {"mode": "full", "media_types": ["image", "video"]},
        "actions": {"dry_run": True},
        "policies": dict(raw_policies),
        "video": _video_config_from_media_sampling(media_sampling),
        "runtime": _runtime_config_from_media_sampling(media_sampling),
        "state": {},
        "_user_categories": dict(raw_categories),
    }

    return AppConfig(
        source=None,
        raw=raw,
        categories=categories,
        classifier_profiles={"service-default": profile},
        active_profile_name="service-default",
    )


def _video_config_from_media_sampling(
    media_sampling: MediaSamplingConfig | None,
) -> dict[str, Any]:
    if media_sampling is None:
        return {}
    return {
        "enabled": media_sampling.enabled,
        "frame_count": media_sampling.max_frames,
        "max_frames": media_sampling.max_frames,
        "max_original_bytes": media_sampling.max_original_bytes,
        "max_duration_seconds": media_sampling.max_duration_seconds,
        "extraction_timeout_seconds": media_sampling.extraction_timeout_seconds,
        "ffmpeg_path": media_sampling.ffmpeg_path,
    }


def _runtime_config_from_media_sampling(
    media_sampling: MediaSamplingConfig | None,
) -> dict[str, Any]:
    if media_sampling is None or media_sampling.temp_dir is None:
        return {}
    return {"temp_dir": str(media_sampling.temp_dir)}


def _default_immich_factory(_user_id: str) -> ImmichClient:
    return MockImmichClient(assets=[])


def _default_classifier_factory(_active_sha: str | None) -> ConfiguredClassifier:
    placeholder_config = synthesize_app_config_placeholder()
    return NoopClassifier(placeholder_config)


def synthesize_app_config_placeholder() -> AppConfig:
    """Return a stand-in ``AppConfig`` for constructing ``NoopClassifier``.

    The classifier profile's ``output_mapping`` matters for the noop
    backend; the categories are overwritten by the real config built
    inside the runner.
    """
    profile = ClassifierProfile(
        name="service-default",
        backend="noop",
        model_path=None,
        output_mapping={"uncategorised": "uncategorised"},
    )
    return AppConfig(
        source=None,
        raw={
            "version": 1,
            "categories": [{"id": "uncategorised"}],
            "classifier_profiles": {"service-default": {"backend": "noop"}},
            "classifier": {"active_profile": "service-default"},
        },
        categories=(Category(id="uncategorised"),),
        classifier_profiles={"service-default": profile},
        active_profile_name="service-default",
    )


def default_factories() -> RunnerFactories:
    """Default factories.

    Returns
    -------
    RunnerFactories
    """
    return RunnerFactories(
        immich_factory=_default_immich_factory,
        classifier_factory=_default_classifier_factory,
        config_factory=synthesize_app_config,
    )


def make_real_runner(
    factories: RunnerFactories | None = None,
    *,
    dry_run: bool = True,
) -> Callable[[StateStore, str, int], None]:
    """Return a runner callable compatible with ``submit_scan(runner=...)``.

    ``dry_run=True`` records intended actions
    but issues no Immich writes. ``dry_run=False`` lets the executor
    perform live mutations.
    """
    f = factories or default_factories()

    def _runner(store: StateStore, user_id: str, run_id: int) -> None:
        scoped = store.with_user(user_id)
        active_sha = store.active_model_sha256()
        processed = 0
        errors = 0
        action_count = 0
        try:
            config = f.config_factory(scoped)
            client = f.immich_factory(user_id)
            classifier = f.classifier_factory(active_sha)
            active_adult_subtype_model = store.active_adult_subtype_model()
            adult_subtype_model_context = _adult_subtype_model_context(
                active_adult_subtype_model
            )
            adult_subtype_classifier = _build_adult_subtype_classifier(
                f.adult_subtype_classifier_factory,
                active_adult_subtype_model,
            )
            ocr_analyzer = (
                f.ocr_factory(store)
                if f.ocr_factory is not None
                else NoopOcrAnalyzer(status="disabled")
            )
            extractor = MediaExtractor()
            decisions = DecisionEngine(config)
            executor = ActionExecutor(
                config, client, dry_run_override=dry_run
            )
            scanner = AssetScanner(config, client)
            review_queue_counts: dict[str, int] = {}

            for asset in scanner.iter_candidates():
                try:
                    outcome = _classify_asset(
                        asset,
                        client,
                        extractor,
                        classifier,
                        config,
                    )
                except (MediaExtractionError, ClassifierError) as exc:
                    errors += 1
                    scoped.upsert_asset(
                        asset_id=asset.asset_id,
                        media_type=asset.media_type,
                        checksum=asset.checksum,
                    )
                    scoped.record_error(
                        run_id=run_id,
                        asset_id=asset.asset_id,
                        stage="extractor"
                        if isinstance(exc, MediaExtractionError)
                        else "classifier",
                        message_code=getattr(exc, "message_code", None)
                        or "pipeline_error",
                    )
                    continue

                scoped.upsert_asset(
                    asset_id=asset.asset_id,
                    media_type=asset.media_type,
                    checksum=asset.checksum,
                )
                if outcome.sampling_warning is not None:
                    errors += 1
                    scoped.record_error(
                        run_id=run_id,
                        asset_id=asset.asset_id,
                        stage="extractor",
                        message_code=outcome.sampling_warning.message_code,
                        message=outcome.sampling_warning.message,
                    )
                raw_analysis_categories = config.raw.get("_user_categories")
                analysis_categories = (
                    raw_analysis_categories
                    if isinstance(raw_analysis_categories, dict)
                    else {}
                )
                ocr_result = ocr_analyzer.analyze(
                    outcome.ocr_inputs,
                    asset_id=asset.asset_id,
                )
                if ocr_result.status == "error" and ocr_result.error_code:
                    errors += 1
                    scoped.record_error(
                        run_id=run_id,
                        asset_id=asset.asset_id,
                        stage="ocr",
                        message_code=ocr_result.error_code,
                    )
                analysis_metadata = dict(outcome.classifier_metadata)
                if ocr_result.text or not _metadata_has_ocr_text(analysis_metadata):
                    analysis_metadata.update(ocr_result_metadata(ocr_result))
                adult_subtype_result = _adult_subtype_result_for_asset(
                    asset=asset,
                    outcome=outcome,
                    classifier=adult_subtype_classifier,
                    primary_result=outcome.result,
                    config=config,
                )
                analysis = analyze_asset(
                    asset,
                    outcome.result,
                    classifier_metadata=analysis_metadata,
                    preview_bytes=outcome.preview_bytes,
                    context=AnalysisContext(
                        categories=analysis_categories,
                        model_sha256=active_sha,
                        adult_subtype_model=adult_subtype_model_context,
                        adult_subtype_result=adult_subtype_result,
                    ),
                )
                scoped.record_asset_analysis(
                    asset_id=asset.asset_id,
                    analysis=analysis,
                )
                for queue in analysis.get("review_queues") or []:
                    if isinstance(queue, str):
                        review_queue_counts[queue] = review_queue_counts.get(queue, 0) + 1
                plan = decisions.decide(
                    outcome.result.category_id,
                    asset.media_type,
                    dry_run=dry_run,
                    asset_id=asset.asset_id,
                )
                for action_result in executor.execute(plan):
                    scoped.record_action(
                        run_id=run_id,
                        asset_id=asset.asset_id,
                        action_name=action_result.action_name,
                        dry_run=action_result.dry_run,
                        would_apply=action_result.would_apply,
                        success=action_result.success,
                        error_code=action_result.error_code,
                    )
                    action_count += 1
                    # Every locked-folder write or attempt is
                    # an audit event. Asset id is logged; no asset bytes
                    # ever reach the audit table.
                    if action_result.action_name == "move_to_locked_folder":
                        scoped.write_audit(
                            action="asset.locked"
                            if action_result.success and not action_result.dry_run
                            else "asset.locked.attempt",
                            target_asset_id=asset.asset_id,
                            run_id=run_id,
                            after_state="locked"
                            if action_result.success and not action_result.dry_run
                            else None,
                        )
                processed += 1

            scoped.write_audit(action="scan.finish", run_id=run_id)
            scoped.finish_run(
                run_id,
                status="completed",
                summary_json=json.dumps(
                    {
                        "processed": processed,
                        "errors": errors,
                        "actions": action_count,
                        "dry_run": dry_run,
                        "model_sha256": active_sha,
                        "review_queues": review_queue_counts,
                    },
                    sort_keys=True,
                ),
            )
        except Exception:
            log.exception(
                "real scan runner failed",
                extra={"user_id": user_id, "run_id": run_id},
            )
            scoped.write_audit(action="scan.failed", run_id=run_id)
            scoped.finish_run(run_id, status="failed")

    return _runner


def _classify_asset(
    asset: AssetRef,
    client: ImmichClient,
    extractor: MediaExtractor,
    classifier: ConfiguredClassifier,
    config: AppConfig,
) -> ClassificationOutcome:
    if _sampling_applies_to(asset):
        if bool(config.video.get("enabled", False)):
            try:
                return _classify_sampled_original(
                    asset,
                    client,
                    extractor,
                    classifier,
                    config,
                )
            except MediaExtractionError as exc:
                return _classify_preview_fallback(
                    asset,
                    client,
                    extractor,
                    classifier,
                    config,
                    sampling_warning=exc,
                )
        return _classify_preview_fallback(
            asset,
            client,
            extractor,
            classifier,
            config,
            sampling_status="disabled",
            sampling_source="preview",
        )

    classifier_input = _build_preview_classifier_input(asset, client, extractor)
    return ClassificationOutcome(
        result=classifier.predict_one(classifier_input),
        classifier_metadata=dict(classifier_input.metadata),
        preview_bytes=classifier_input.data,
        ocr_inputs=_ocr_inputs_from_classifier_inputs([classifier_input]),
        subtype_inputs=(classifier_input,),
    )


def _classify_preview_fallback(
    asset: AssetRef,
    client: ImmichClient,
    extractor: MediaExtractor,
    classifier: ConfiguredClassifier,
    config: AppConfig,
    *,
    sampling_warning: MediaExtractionError | None = None,
    sampling_status: str = "failed",
    sampling_source: str = "preview_fallback",
) -> ClassificationOutcome:
    classifier_input = _build_preview_classifier_input(asset, client, extractor)
    metadata = dict(classifier_input.metadata)
    metadata.update(
        _sampling_metadata(
            status=sampling_status,
            source=sampling_source,
            sampled_frame_count=0,
            aggregation_method=_aggregation_method(config),
            error_code=sampling_warning.message_code if sampling_warning else None,
        )
    )
    return ClassificationOutcome(
        result=classifier.predict_one(classifier_input),
        classifier_metadata=metadata,
        preview_bytes=classifier_input.data,
        sampling_warning=sampling_warning,
        ocr_inputs=_ocr_inputs_from_classifier_inputs([classifier_input]),
        subtype_inputs=(classifier_input,),
    )


def _classify_sampled_original(
    asset: AssetRef,
    client: ImmichClient,
    extractor: MediaExtractor,
    classifier: ConfiguredClassifier,
    config: AppConfig,
) -> ClassificationOutcome:
    temp_root = _temp_root(config.runtime, asset_id=asset.asset_id)
    try:
        with tempfile.TemporaryDirectory(
            prefix="mediarefinery-original-",
            dir=temp_root,
        ) as temp_dir:
            original_path = Path(temp_dir) / _original_temp_name(asset)
            _download_original_to_path(
                client=client,
                asset_id=asset.asset_id,
                media_type="gif" if _is_gif_asset(asset) else asset.media_type,
                destination=original_path,
                max_bytes=_positive_int_config(
                    config.video.get("max_original_bytes"),
                    250 * 1024 * 1024,
                ),
            )
            if _is_gif_asset(asset):
                return _classify_gif_original(
                    asset,
                    original_path,
                    extractor,
                    classifier,
                    config,
                )
            return _classify_frame_sampled_original(
                asset,
                original_path,
                "video",
                extractor,
                classifier,
                config,
            )
    except OSError as exc:
        raise MediaExtractionError(
            asset_id=asset.asset_id,
            media_type=asset.media_type,
            source="original",
            message_code="original_temp_failed",
            message="original asset temp storage failed",
            details={"reason": type(exc).__name__},
        ) from exc


def _classify_gif_original(
    asset: AssetRef,
    original_path: Path,
    extractor: MediaExtractor,
    classifier: ConfiguredClassifier,
    config: AppConfig,
) -> ClassificationOutcome:
    original_bytes = original_path.read_bytes()
    if not _is_animated_gif(original_bytes):
        metadata = dict(asset.metadata)
        metadata["asset_media_type"] = asset.media_type
        classifier_input = extractor.image_input(
            asset_id=asset.asset_id,
            media_type="image",
            image_bytes=original_bytes,
            metadata=metadata,
            source="original",
        )
        classifier_metadata = dict(classifier_input.metadata)
        classifier_metadata.update(
            _sampling_metadata(
                status="static_gif",
                source="original_still",
                sampled_frame_count=1,
                aggregation_method="none",
            )
        )
        return ClassificationOutcome(
            result=classifier.predict_one(classifier_input),
            classifier_metadata=classifier_metadata,
            preview_bytes=None,
            ocr_inputs=_ocr_inputs_from_classifier_inputs([classifier_input]),
            subtype_inputs=(classifier_input,),
        )
    return _classify_frame_sampled_original(
        asset,
        original_path,
        "gif",
        extractor,
        classifier,
        config,
    )


def _classify_frame_sampled_original(
    asset: AssetRef,
    original_path: Path,
    extractor_media_type: str,
    extractor: MediaExtractor,
    classifier: ConfiguredClassifier,
    config: AppConfig,
) -> ClassificationOutcome:
    metadata = dict(asset.metadata)
    metadata["asset_media_type"] = asset.media_type
    aggregation = _aggregation_method(config)
    with extractor.video_frame_inputs(
        asset_id=asset.asset_id,
        media_type=extractor_media_type,
        video_path=original_path,
        metadata=metadata,
        video_config=config.video,
        runtime_config=config.runtime,
    ) as frame_inputs:
        result = classifier.predict_aggregate(
            frame_inputs,
            asset_id=asset.asset_id,
            aggregation=aggregation,
        )
        classifier_metadata = _sampled_frame_metadata(
            frame_inputs,
            aggregation_method=aggregation,
        )
        ocr_inputs = _ocr_inputs_from_classifier_inputs(frame_inputs)
    return ClassificationOutcome(
        result=result,
        classifier_metadata=classifier_metadata,
        preview_bytes=None,
        ocr_inputs=ocr_inputs,
        subtype_inputs=tuple(frame_inputs),
    )


def _build_adult_subtype_classifier(
    factory: AdultSubtypeClassifierFactory | None,
    active_model: dict[str, Any] | None,
) -> ConfiguredClassifier | None:
    if factory is None or active_model is None:
        return None
    try:
        return factory(active_model)
    except Exception:
        log.exception("adult subtype classifier setup failed")
        return None


def _adult_subtype_model_context(
    active_model: dict[str, Any] | None,
) -> AdultSubtypeModelContext | None:
    if active_model is None:
        return None
    metadata = active_model.get("metadata")
    if not isinstance(metadata, dict):
        return None
    labels = metadata.get("output_labels")
    if not isinstance(labels, list) or not labels:
        return None
    thresholds = metadata.get("thresholds")
    if not isinstance(thresholds, dict):
        thresholds = {}
    return AdultSubtypeModelContext(
        model_id=str(
            metadata.get("model_id") or active_model.get("version") or "adult_subtype"
        ),
        output_labels=tuple(str(label) for label in labels if isinstance(label, str)),
        thresholds={
            str(label): float(value)
            for label, value in thresholds.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        },
        model_sha256=str(active_model["sha256"])
        if active_model.get("sha256")
        else None,
        admin_acknowledged=metadata.get("admin_acknowledged") is True,
    )


def _adult_subtype_result_for_asset(
    *,
    asset: AssetRef,
    outcome: ClassificationOutcome,
    classifier: ConfiguredClassifier | None,
    primary_result: ClassificationResult,
    config: AppConfig,
) -> ClassificationResult | None:
    if classifier is None or not _primary_result_is_sensitive(primary_result):
        return None
    inputs = list(outcome.subtype_inputs)
    if not inputs:
        return None
    try:
        if len(inputs) == 1:
            return classifier.predict_one(inputs[0])
        return classifier.predict_aggregate(
            inputs,
            asset_id=asset.asset_id,
            aggregation=classifier.profile.video_aggregation or _aggregation_method(config),
        )
    except ClassifierError:
        log.exception(
            "adult subtype classification failed",
            extra={"asset_id": asset.asset_id},
        )
        return None


def _primary_result_is_sensitive(result: ClassificationResult) -> bool:
    return str(result.category_id or result.raw_label or "").lower() in {
        "nsfw",
        "explicit",
        "suggestive",
    }


def _build_preview_classifier_input(
    asset: AssetRef,
    client: ImmichClient,
    extractor: MediaExtractor,
) -> ClassifierInput:
    if asset.media_type not in {"image", "video"}:
        return extractor.image_input(
            asset_id=asset.asset_id,
            media_type=asset.media_type,
            image_bytes=None,
            metadata=dict(asset.metadata),
        )
    preview = client.get_preview_bytes(asset.asset_id)
    metadata = dict(asset.metadata)
    metadata["asset_media_type"] = asset.media_type
    return extractor.image_input(
        asset_id=asset.asset_id,
        media_type="image",
        image_bytes=preview,
        metadata=metadata,
        source="preview",
    )


def _download_original_to_path(
    *,
    client: ImmichClient,
    asset_id: str,
    media_type: str,
    destination: Path,
    max_bytes: int,
) -> int:
    try:
        return client.download_asset_to_file(
            asset_id,
            destination,
            max_bytes=max_bytes,
        )
    except AttributeError:
        original_bytes = client.download_asset_bytes(asset_id)
        if len(original_bytes) > max_bytes:
            raise _original_download_error(
                asset_id,
                media_type=media_type,
                message_code="original_too_large",
            ) from None
        destination.write_bytes(original_bytes)
        return len(original_bytes)
    except ImmichClientError as exc:
        raise _original_download_error(
            asset_id,
            media_type=media_type,
            message_code=exc.error_code
            if exc.error_code == "original_too_large"
            else "original_download_failed",
        ) from exc


def _original_download_error(
    asset_id: str,
    *,
    media_type: str,
    message_code: str,
) -> MediaExtractionError:
    message = (
        "original asset exceeded configured byte limit"
        if message_code == "original_too_large"
        else "original asset download failed"
    )
    return MediaExtractionError(
        asset_id=asset_id,
        media_type=media_type,
        source="original",
        message_code=message_code,
        message=message,
    )


def _sampled_frame_metadata(
    frame_inputs: list[ClassifierInput],
    *,
    aggregation_method: str,
) -> dict[str, str]:
    metadata = dict(frame_inputs[0].metadata) if frame_inputs else {}
    metadata.update(
        _sampling_metadata(
            status="sampled",
            source="original_frames",
            sampled_frame_count=len(frame_inputs),
            aggregation_method=aggregation_method,
        )
    )
    return metadata


def _ocr_inputs_from_classifier_inputs(
    classifier_inputs: list[ClassifierInput],
) -> tuple[OcrInput, ...]:
    inputs: list[OcrInput] = []
    for classifier_input in classifier_inputs:
        if not classifier_input.data:
            continue
        frame_index = _positive_optional_int(
            classifier_input.metadata.get("video_frame_index")
        )
        frame_total = _positive_optional_int(
            classifier_input.metadata.get("video_frame_count")
        )
        inputs.append(
            OcrInput(
                asset_id=classifier_input.asset_id,
                image_bytes=classifier_input.data,
                source=classifier_input.source or "unknown",
                frame_index=frame_index,
                frame_total=frame_total,
            )
        )
    return tuple(inputs)


def _metadata_has_ocr_text(metadata: dict[str, str]) -> bool:
    return any(
        isinstance(metadata.get(key), str) and bool(str(metadata[key]).strip())
        for key in ("ocr_text", "smart_text", "exif_description", "description")
    )


def _sampling_metadata(
    *,
    status: str,
    source: str,
    sampled_frame_count: int,
    aggregation_method: str,
    error_code: str | None = None,
) -> dict[str, str]:
    metadata = {
        "sampling_status": status,
        "sampling_source": source,
        "sampled_frame_count": str(sampled_frame_count),
        "frame_aggregation_method": aggregation_method,
    }
    if error_code is not None:
        metadata["sampling_error_code"] = error_code
    return metadata


def _sampling_applies_to(asset: AssetRef) -> bool:
    return asset.media_type == "video" or _is_gif_asset(asset)


def _is_gif_asset(asset: AssetRef) -> bool:
    metadata = asset.metadata
    mime_type = (
        metadata.get("mime_type")
        or metadata.get("original_mime_type")
        or metadata.get("image_content_type")
        or ""
    )
    image_format = metadata.get("image_format") or ""
    filename = metadata.get("filename") or metadata.get("original_file_name") or ""
    return asset.media_type == "image" and (
        mime_type.lower() == "image/gif"
        or image_format.lower() == "gif"
        or filename.lower().endswith(".gif")
    )


def _is_animated_gif(data: bytes) -> bool:
    if len(data) < 13 or not data.startswith((b"GIF87a", b"GIF89a")):
        return False
    pos = 13
    packed = data[10]
    if packed & 0x80:
        pos += 3 * (2 ** ((packed & 0x07) + 1))
    frames = 0
    while pos < len(data):
        marker = data[pos]
        if marker == 0x2C:
            frames += 1
            if frames > 1:
                return True
            pos += 1
            if pos + 9 > len(data):
                return False
            image_packed = data[pos + 8]
            pos += 9
            if image_packed & 0x80:
                pos += 3 * (2 ** ((image_packed & 0x07) + 1))
            if pos >= len(data):
                return False
            pos += 1
            pos = _skip_gif_sub_blocks(data, pos)
            continue
        if marker == 0x21:
            pos += 2
            pos = _skip_gif_sub_blocks(data, pos)
            continue
        if marker == 0x3B:
            break
        return False
    return False


def _skip_gif_sub_blocks(data: bytes, pos: int) -> int:
    while pos < len(data):
        block_size = data[pos]
        pos += 1
        if block_size == 0:
            break
        pos += block_size
    return pos


def _aggregation_method(config: AppConfig) -> str:
    return config.active_profile.video_aggregation or "max"


def _temp_root(
    runtime_config: dict[str, Any],
    *,
    asset_id: str,
) -> str | None:
    value = runtime_config.get("temp_dir")
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        temp_root = Path(value)
        temp_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MediaExtractionError(
            asset_id=asset_id,
            media_type="video",
            source="original",
            message_code="original_temp_failed",
            message="original asset temp storage failed",
            details={"reason": type(exc).__name__},
        ) from exc
    return str(temp_root)


def _original_temp_name(asset: AssetRef) -> str:
    return "original.gif" if _is_gif_asset(asset) else "original.media"


def _positive_int_config(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return default
    return value


def _positive_optional_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def submit_real_scan(
    *,
    store: StateStore,
    user_id: str,
    factories: RunnerFactories | None = None,
    daily_quota: int | None = None,
    dry_run: bool = True,
) -> SubmittedScan:
    """Enqueue a real-pipeline scan when an active model is registered.

    Raises :class:`~mediarefinery.service.scheduler.ScanRejected` when no
    model is active; otherwise delegates to
    :func:`~mediarefinery.service.scheduler.submit_scan`.
    """
    if store.active_model_sha256() is None:
        raise ScanRejected("no_active_model")
    runner = make_real_runner(factories, dry_run=dry_run)
    kwargs: dict[str, Any] = {"store": store, "user_id": user_id, "runner": runner}
    if daily_quota is not None:
        kwargs["daily_quota"] = daily_quota
    return submit_scan(**kwargs)


__all__ = [
    "AdultSubtypeClassifierFactory",
    "ConfigFactory",
    "ClassifierFactory",
    "ImmichFactory",
    "OcrFactory",
    "RunnerFactories",
    "default_factories",
    "make_real_runner",
    "submit_real_scan",
    "synthesize_app_config",
]


# Silence linters in environments that strip unused-but-documented imports.
_ = (Iterable, field, replace)
