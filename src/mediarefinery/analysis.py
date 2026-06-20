"""Analysis for MediaRefinery."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Any

from .classifier import ClassificationResult
from .immich import AssetRef

DEFAULT_SAFETY_THRESHOLD = 0.85
LOW_RESOLUTION_EDGE = 720
TEXT_DENSITY_DOCUMENT_THRESHOLD = 0.18

DOCUMENT_KEYWORDS = frozenset(
    {
        "account",
        "amount",
        "balance",
        "bill",
        "date",
        "document",
        "due",
        "invoice",
        "paid",
        "payment",
        "receipt",
        "statement",
        "subtotal",
        "tax",
        "total",
    }
)
RECEIPT_KEYWORDS = frozenset({"receipt", "subtotal", "tax", "total", "cashier"})
INVOICE_KEYWORDS = frozenset({"invoice", "amount due", "invoice number", "payment"})
SAFETY_LABELS = frozenset({"sfw", "nsfw", "suggestive", "explicit", "review_needed"})


@dataclass(frozen=True)
class AnalysisContext:
    """Represent AnalysisContext.

    Attributes
    ----------
    categories : Mapping[str, Any]
    model_sha256 : str | None
    adult_subtype_model : AdultSubtypeModelContext | None
    adult_subtype_result : ClassificationResult | None
    """

    categories: Mapping[str, Any]
    model_sha256: str | None = None
    adult_subtype_model: AdultSubtypeModelContext | None = None
    adult_subtype_result: ClassificationResult | None = None


@dataclass(frozen=True)
class AdultSubtypeModelContext:
    """Represent AdultSubtypeModelContext.

    Attributes
    ----------
    model_id : str
    output_labels : tuple[str, ...]
    thresholds : Mapping[str, float]
    model_sha256 : str | None
    admin_acknowledged : bool
    """

    model_id: str
    output_labels: tuple[str, ...]
    thresholds: Mapping[str, float]
    model_sha256: str | None = None
    admin_acknowledged: bool = False


def analyze_asset(
    asset: AssetRef,
    result: ClassificationResult,
    *,
    classifier_metadata: Mapping[str, str] | None = None,
    preview_bytes: bytes | None = None,
    context: AnalysisContext | None = None,
) -> dict[str, Any]:
    """Build additive, persistent analysis signals for an asset.

    The analyzer is intentionally conservative: signals backed by existing
    metadata or model scores are marked available, while capabilities that
    require a future verified model are represented as unavailable instead of
    guessed.
    """
    metadata = _merged_metadata(asset.metadata, classifier_metadata)
    categories = context.categories if context is not None else {}
    media_info = _media_info(asset, metadata)
    safety = _safety_signal(result, categories)
    people = _people_signal(metadata)
    ocr = _ocr_signal(metadata)
    sfw_facets = _sfw_facets(
        result=result,
        metadata=metadata,
        media_info=media_info,
        ocr=ocr,
    )
    document = _document_signal(metadata=metadata, media_info=media_info, ocr=ocr)
    duplicates = _duplicate_signal(asset, metadata, preview_bytes)
    quality = _quality_signal(media_info, preview_bytes)
    sampling = _sampling_signal(metadata)
    semantic = _semantic_signal(asset, metadata, people, ocr, sfw_facets, document)
    events = _event_signal(asset, metadata, people, media_info)
    adult_subtypes = _adult_subtype_signal(safety, context)

    if document["type"] != "none" and document["type"] not in sfw_facets:
        sfw_facets.append(document["type"])
    custom_categories = _custom_categories(
        categories=categories,
        media_info=media_info,
        people=people,
        ocr=ocr,
        sfw_facets=sfw_facets,
        semantic=semantic,
        document=document,
    )
    review_queues = _review_queues(
        safety=safety,
        adult_subtypes=adult_subtypes,
        sfw_facets=sfw_facets,
        document=document,
        duplicates=duplicates,
        quality=quality,
        custom_categories=custom_categories,
        people=people,
    )

    policy_categories = _dedupe_strings(
        [result.category_id, *custom_categories, *sfw_facets]
    )
    return {
        "version": 1,
        "asset_id": asset.asset_id,
        "primary_category_id": result.category_id,
        "model_sha256": context.model_sha256 if context is not None else None,
        "media_info": media_info,
        "safety": safety,
        "adult_subtypes": adult_subtypes,
        "sfw_facets": sfw_facets,
        "people": people,
        "quality": quality,
        "sampling": sampling,
        "duplicates": duplicates,
        "ocr": ocr,
        "document": document,
        "semantic": semantic,
        "events": events,
        "custom_categories": custom_categories,
        "review_queues": review_queues,
        "policy_categories": policy_categories,
        "raw_model": {
            "label": result.raw_label,
            "labels": list(result.raw_labels),
            "scores": dict(result.raw_scores),
        },
    }


def analysis_summary(analysis: Mapping[str, Any]) -> dict[str, Any]:
    """Analysis summary.

    Parameters
    ----------
    analysis : Mapping[str, Any]

    Returns
    -------
    dict[str, Any]
    """
    media_info = _mapping(analysis.get("media_info"))
    safety = _mapping(analysis.get("safety"))
    quality = _mapping(analysis.get("quality"))
    duplicates = _mapping(analysis.get("duplicates"))
    document = _mapping(analysis.get("document"))
    ocr = _mapping(analysis.get("ocr"))
    events = _mapping(analysis.get("events"))
    adult_subtypes = _mapping(analysis.get("adult_subtypes"))
    return {
        "primary_category_id": _optional_string(analysis.get("primary_category_id")),
        "media_kind": _optional_string(media_info.get("kind")),
        "mime_type": _optional_string(media_info.get("mime_type")),
        "safety_label": _optional_string(safety.get("label")),
        "safety_confidence": _optional_float(safety.get("confidence")),
        "review_needed": bool(safety.get("review_needed")),
        "sfw_facets": list(analysis.get("sfw_facets") or []),
        "custom_categories": list(analysis.get("custom_categories") or []),
        "review_queues": list(analysis.get("review_queues") or []),
        "people_count": len(list(analysis.get("people") or [])),
        "quality_flags": list(quality.get("flags") or []),
        "duplicate_key": _optional_string(duplicates.get("group_key")),
        "document_type": _optional_string(document.get("type")),
        "ocr_available": bool(ocr.get("available")),
        "event_key": _optional_string(events.get("event_key")),
        "adult_subtype_status": _optional_string(adult_subtypes.get("status")),
        "adult_subtype_top_label": _optional_string(adult_subtypes.get("top_label")),
        "adult_subtype_review_needed": bool(adult_subtypes.get("review_needed")),
    }


def _merged_metadata(
    asset_metadata: Mapping[str, str],
    classifier_metadata: Mapping[str, str] | None,
) -> dict[str, str]:
    merged = dict(asset_metadata)
    if classifier_metadata is not None:
        merged.update({str(k): str(v) for k, v in classifier_metadata.items()})
    return merged


def _media_info(asset: AssetRef, metadata: Mapping[str, str]) -> dict[str, Any]:
    mime_type = _first_non_empty(metadata, "mime_type", "original_mime_type")
    image_format = _first_non_empty(metadata, "image_format")
    width = _optional_int(_first_non_empty(metadata, "image_width", "width"))
    height = _optional_int(_first_non_empty(metadata, "image_height", "height"))
    if width is None:
        width = _optional_int(_first_non_empty(metadata, "exif_image_width"))
    if height is None:
        height = _optional_int(_first_non_empty(metadata, "exif_image_height"))

    kind = asset.media_type
    if asset.media_type == "image" and (
        str(mime_type).lower() == "image/gif" or str(image_format).lower() == "gif"
    ):
        kind = "gif"

    return {
        "type": asset.media_type,
        "kind": kind,
        "mime_type": mime_type,
        "format": image_format,
        "width": width,
        "height": height,
        "duration": _first_non_empty(metadata, "duration"),
        "file_size_bytes": _optional_int(_first_non_empty(metadata, "file_size_bytes")),
        "albums": list(asset.albums),
        "favorite": asset.favorite,
        "archived": asset.archived,
        "created_at": asset.created_at.isoformat() if asset.created_at else None,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
        "filename": _first_non_empty(metadata, "filename", "original_file_name"),
        "city": _first_non_empty(metadata, "city"),
        "country": _first_non_empty(metadata, "country"),
    }


def _safety_signal(
    result: ClassificationResult,
    categories: Mapping[str, Any],
) -> dict[str, Any]:
    scores = {str(k).lower(): float(v) for k, v in result.raw_scores.items()}
    label = str(result.category_id or result.raw_label or "unknown").lower()
    confidence = scores.get(label)
    if confidence is None and scores:
        confidence = max(scores.values())
    if confidence is None:
        confidence = 0.0

    if label not in SAFETY_LABELS:
        safety_label = "unknown"
    else:
        safety_label = label
    threshold = _threshold_for(label, categories)
    review_needed = safety_label == "unknown" or confidence < threshold
    return {
        "label": safety_label,
        "source_label": label,
        "confidence": confidence,
        "threshold": threshold,
        "review_needed": review_needed,
    }


def _adult_subtype_signal(
    safety: Mapping[str, Any],
    context: AnalysisContext | None,
) -> dict[str, Any]:
    if safety.get("label") not in {"nsfw", "explicit", "suggestive"}:
        return {
            "status": "not_applicable",
            "labels": [],
            "reason": "asset_not_classified_as_sensitive",
            "review_needed": False,
            "top_label": None,
        }
    model = context.adult_subtype_model if context is not None else None
    if model is None:
        return {
            "status": "unavailable",
            "labels": [],
            "reason": "no_verified_adult_subtype_model",
            "review_needed": False,
            "top_label": None,
        }
    base = {
        "model_id": model.model_id,
        "model_sha256": model.model_sha256,
        "source": "adult_subtype_model",
    }
    if not model.admin_acknowledged:
        return {
            **base,
            "status": "unavailable",
            "labels": [],
            "reason": "admin_acknowledgement_required",
            "review_needed": False,
            "top_label": None,
        }
    result = context.adult_subtype_result if context is not None else None
    if result is None:
        return {
            **base,
            "status": "unavailable",
            "labels": [],
            "reason": "subtype_model_not_run",
            "review_needed": False,
            "top_label": None,
        }

    configured = tuple(str(label) for label in model.output_labels)
    configured_set = set(configured)
    observed = {str(label) for label in result.raw_scores}
    unknown = sorted(observed - configured_set)
    if unknown:
        return {
            **base,
            "status": "failed_closed",
            "labels": [],
            "unknown_labels": unknown,
            "reason": "unknown_subtype_label",
            "review_needed": True,
            "top_label": None,
        }

    labels: list[dict[str, str | float | bool]] = []
    for label in configured:
        if label not in result.raw_scores:
            continue
        confidence = float(result.raw_scores[label])
        threshold = _adult_subtype_threshold(label, model.thresholds)
        labels.append(
            {
                "label": label,
                "confidence": confidence,
                "threshold": threshold,
                "review_needed": confidence < threshold,
            }
        )
    labels.sort(key=lambda item: float(item["confidence"]), reverse=True)
    if not labels:
        return {
            **base,
            "status": "failed_closed",
            "labels": [],
            "reason": "no_configured_subtype_score",
            "review_needed": True,
            "top_label": None,
        }
    top = labels[0]
    review_needed = bool(top["review_needed"])
    return {
        **base,
        "status": "review_needed" if review_needed else "available",
        "labels": labels,
        "top_label": str(top["label"]),
        "reason": "low_confidence" if review_needed else None,
        "review_needed": review_needed,
    }


def _people_signal(metadata: Mapping[str, str]) -> list[dict[str, Any]]:
    people = _json_list(metadata.get("people_json"))
    out: list[dict[str, Any]] = []
    for item in people:
        if not isinstance(item, Mapping):
            continue
        person_id = _optional_string(item.get("id") or item.get("personId"))
        name = _optional_string(item.get("name"))
        if person_id is None and name is None:
            continue
        out.append({"id": person_id, "name": name})
    return out


def _ocr_signal(metadata: Mapping[str, str]) -> dict[str, Any]:
    status = _first_non_empty(metadata, "ocr_status")
    text = _first_non_empty(
        metadata,
        "ocr_text",
        "smart_text",
        "exif_description",
        "description",
    )
    if not text:
        return {
            "available": False,
            "status": status or "unavailable",
            "text": "",
            "language": None,
            "script": None,
            "confidence": None,
            "text_density": 0.0,
            "keywords": [],
            "source_frames": [],
            "analyzer_version": _first_non_empty(metadata, "ocr_analyzer_version"),
            "model_sha256": _first_non_empty(metadata, "ocr_model_sha256"),
            "error_code": _first_non_empty(metadata, "ocr_error_code"),
            "lines": [],
        }
    normalized = _normalize_text(text)
    words = _words(normalized)
    keyword_hits = sorted({word for word in DOCUMENT_KEYWORDS if word in normalized})
    return {
        "available": True,
        "status": status or "metadata",
        "text": text,
        "language": _first_non_empty(metadata, "ocr_language"),
        "script": _first_non_empty(metadata, "ocr_script"),
        "confidence": _optional_float(_first_non_empty(metadata, "ocr_confidence")),
        "text_density": min(1.0, len(words) / 120.0),
        "keywords": keyword_hits,
        "source_frames": _json_int_list(metadata.get("ocr_source_frames_json")),
        "analyzer_version": _first_non_empty(metadata, "ocr_analyzer_version"),
        "model_sha256": _first_non_empty(metadata, "ocr_model_sha256"),
        "error_code": _first_non_empty(metadata, "ocr_error_code"),
        "lines": _json_list(metadata.get("ocr_lines_json")),
    }


def _sfw_facets(
    *,
    result: ClassificationResult,
    metadata: Mapping[str, str],
    media_info: Mapping[str, Any],
    ocr: Mapping[str, Any],
) -> list[str]:
    facets: list[str] = []
    label = str(result.category_id or "").lower()
    if label and label not in SAFETY_LABELS and label != "unknown":
        facets.append(label)
    for value in _json_list(metadata.get("smart_tags_json")):
        if isinstance(value, str):
            facets.append(_slug(value))
    for value in _json_list(metadata.get("smart_objects_json")):
        if isinstance(value, str):
            facets.append(_slug(value))
    filename = str(media_info.get("filename") or "").lower()
    mime_type = str(media_info.get("mime_type") or "").lower()
    if "screenshot" in filename:
        facets.append("screenshot")
    if mime_type in {"application/pdf", "image/tiff"}:
        facets.append("document")
    if ocr.get("available"):
        facets.append("contains_text")
    return _dedupe_strings(facets)


def _document_signal(
    *,
    metadata: Mapping[str, str],
    media_info: Mapping[str, Any],
    ocr: Mapping[str, Any],
) -> dict[str, Any]:
    filename = _normalize_text(str(media_info.get("filename") or ""))
    mime_type = str(media_info.get("mime_type") or "").lower()
    text = _normalize_text(str(ocr.get("text") or ""))
    combined = f"{filename} {text}"
    reasons: list[str] = []

    doc_type = "none"
    if "screenshot" in filename:
        doc_type = "screenshot"
        reasons.append("filename")
    if any(keyword in combined for keyword in INVOICE_KEYWORDS):
        doc_type = "invoice"
        reasons.append("keyword")
    elif any(keyword in combined for keyword in RECEIPT_KEYWORDS):
        doc_type = "receipt"
        reasons.append("keyword")
    elif (
        mime_type in {"application/pdf", "image/tiff"}
        or float(ocr.get("text_density") or 0.0) >= TEXT_DENSITY_DOCUMENT_THRESHOLD
        or any(keyword in combined for keyword in DOCUMENT_KEYWORDS)
    ):
        doc_type = "document"
        reasons.append("text_or_mime")

    return {
        "type": doc_type,
        "confidence": 0.0 if doc_type == "none" else 0.72,
        "reasons": _dedupe_strings(reasons),
    }


def _duplicate_signal(
    asset: AssetRef,
    metadata: Mapping[str, str],
    preview_bytes: bytes | None,
) -> dict[str, Any]:
    duplicate_id = _first_non_empty(metadata, "duplicate_id", "duplicateId")
    checksum = asset.checksum
    preview_hash = (
        hashlib.sha256(preview_bytes).hexdigest()[:24] if preview_bytes else None
    )
    group_key = duplicate_id or checksum
    return {
        "duplicate_id": duplicate_id,
        "checksum": checksum,
        "preview_hash": preview_hash,
        "group_key": group_key,
        "source": "immich" if duplicate_id else ("checksum" if checksum else None),
    }


def _sampling_signal(metadata: Mapping[str, str]) -> dict[str, Any]:
    status = _first_non_empty(metadata, "sampling_status") or "not_applicable"
    return {
        "sampling_status": status,
        "sampling_source": _first_non_empty(metadata, "sampling_source"),
        "sampled_frame_count": _optional_int(
            _first_non_empty(metadata, "sampled_frame_count")
        )
        or 0,
        "frame_aggregation_method": _first_non_empty(
            metadata,
            "frame_aggregation_method",
        ),
        "error_code": _first_non_empty(metadata, "sampling_error_code"),
    }


def _quality_signal(
    media_info: Mapping[str, Any],
    preview_bytes: bytes | None,
) -> dict[str, Any]:
    flags: list[str] = []
    width = _optional_int(media_info.get("width"))
    height = _optional_int(media_info.get("height"))
    if width is not None and height is not None and min(width, height) < LOW_RESOLUTION_EDGE:
        flags.append("low_resolution")

    blur = _optional_blur_score(preview_bytes)
    brightness = _optional_brightness(preview_bytes)
    if blur is not None and blur < 80.0:
        flags.append("blurry")
    if brightness is not None and brightness < 35.0:
        flags.append("dark")
    if brightness is not None and brightness > 225.0:
        flags.append("overexposed")

    return {
        "flags": _dedupe_strings(flags),
        "low_resolution": "low_resolution" in flags,
        "blur_score": blur,
        "blur_status": "available" if blur is not None else "unavailable",
        "brightness": brightness,
    }


def _semantic_signal(
    asset: AssetRef,
    metadata: Mapping[str, str],
    people: list[dict[str, Any]],
    ocr: Mapping[str, Any],
    sfw_facets: list[str],
    document: Mapping[str, Any],
) -> dict[str, Any]:
    terms: list[str] = []
    terms.extend(asset.albums)
    terms.extend(sfw_facets)
    terms.append(str(document.get("type") or ""))
    terms.append(_first_non_empty(metadata, "city") or "")
    terms.append(_first_non_empty(metadata, "country") or "")
    terms.append(_first_non_empty(metadata, "filename", "original_file_name") or "")
    terms.extend(str(person.get("name") or "") for person in people)
    terms.extend(_words(str(ocr.get("text") or ""))[:80])
    return {
        "status": "metadata_index",
        "terms": _dedupe_strings(_slug(term) for term in terms if term),
        "embedding_status": "unavailable",
        "provider": "metadata",
    }


def _event_signal(
    asset: AssetRef,
    metadata: Mapping[str, str],
    people: list[dict[str, Any]],
    media_info: Mapping[str, Any],
) -> dict[str, Any]:
    day = _event_day(asset.created_at, metadata)
    place = _slug(
        " ".join(
            value
            for value in (
                str(media_info.get("city") or ""),
                str(media_info.get("country") or ""),
            )
            if value
        )
    )
    person_part = "-".join(
        sorted(
            _slug(str(person.get("name") or person.get("id") or ""))
            for person in people
            if person.get("name") or person.get("id")
        )[:4]
    )
    album_part = "-".join(sorted(_slug(album) for album in asset.albums)[:2])
    parts = [part for part in (day, place, person_part, album_part) if part]
    return {
        "event_key": "::".join(parts) if parts else None,
        "status": "auto",
        "day": day,
        "place": place or None,
    }


def _custom_categories(
    *,
    categories: Mapping[str, Any],
    media_info: Mapping[str, Any],
    people: list[dict[str, Any]],
    ocr: Mapping[str, Any],
    sfw_facets: list[str],
    semantic: Mapping[str, Any],
    document: Mapping[str, Any],
) -> list[str]:
    matched: list[str] = []
    terms = set(str(term) for term in semantic.get("terms") or [])
    people_terms = {
        _slug(str(person.get("name") or person.get("id") or ""))
        for person in people
        if person.get("name") or person.get("id")
    }
    ocr_text = _normalize_text(str(ocr.get("text") or ""))
    for category_id, raw in categories.items():
        cid = str(category_id)
        if not isinstance(raw, Mapping) or raw.get("enabled", True) is False:
            continue
        rules = raw.get("rules")
        aliases = [cid]
        aliases.extend(str(value) for value in raw.get("aliases") or [])
        if any(_slug(alias) in terms or _slug(alias) in sfw_facets for alias in aliases):
            matched.append(cid)
            continue
        if document.get("type") == cid:
            matched.append(cid)
            continue
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if _rule_matches(
                rule,
                media_info=media_info,
                people_terms=people_terms,
                ocr_text=ocr_text,
                terms=terms,
                sfw_facets=sfw_facets,
                document=document,
            ):
                matched.append(cid)
                break
    return _dedupe_strings(matched)


def _rule_matches(
    rule: object,
    *,
    media_info: Mapping[str, Any],
    people_terms: set[str],
    ocr_text: str,
    terms: set[str],
    sfw_facets: list[str],
    document: Mapping[str, Any],
) -> bool:
    if not isinstance(rule, Mapping):
        return False
    if rule.get("media_kind") and rule.get("media_kind") != media_info.get("kind"):
        return False
    if rule.get("document_type") and rule.get("document_type") != document.get("type"):
        return False
    for value in rule.get("people") or []:
        if _slug(str(value)) not in people_terms:
            return False
    for value in rule.get("ocr_contains") or []:
        if _normalize_text(str(value)) not in ocr_text:
            return False
    match_any = {_slug(str(value)) for value in rule.get("match_any") or []}
    if match_any and match_any.isdisjoint(terms) and match_any.isdisjoint(sfw_facets):
        return False
    return True


def _review_queues(
    *,
    safety: Mapping[str, Any],
    adult_subtypes: Mapping[str, Any],
    sfw_facets: list[str],
    document: Mapping[str, Any],
    duplicates: Mapping[str, Any],
    quality: Mapping[str, Any],
    custom_categories: list[str],
    people: list[dict[str, Any]],
) -> list[str]:
    queues: list[str] = []
    if safety.get("label") in {"nsfw", "explicit", "suggestive"}:
        queues.append("nsfw")
    if adult_subtypes.get("status") in {"available", "review_needed", "failed_closed"}:
        queues.append("adult_subtypes")
    if safety.get("review_needed") or adult_subtypes.get("review_needed"):
        queues.append("review_needed")
    if document.get("type") in {"document", "receipt", "invoice", "screenshot"}:
        queues.append("documents")
    if duplicates.get("group_key"):
        queues.append("duplicates")
    if quality.get("flags"):
        queues.append("quality")
    if custom_categories:
        queues.append("custom")
    if people:
        queues.append("people")
    if sfw_facets:
        queues.append("sfw")
    return _dedupe_strings(queues)


def _threshold_for(label: str, categories: Mapping[str, Any]) -> float:
    raw = categories.get(label)
    if isinstance(raw, Mapping):
        threshold = raw.get("threshold")
        if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
            return max(0.0, min(1.0, float(threshold)))
    return DEFAULT_SAFETY_THRESHOLD if label == "nsfw" else 0.5


def _adult_subtype_threshold(label: str, thresholds: Mapping[str, float]) -> float:
    value = thresholds.get(label)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, min(1.0, float(value)))
    return 0.65


def _optional_blur_score(preview_bytes: bytes | None) -> float | None:
    if not preview_bytes:
        return None
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except ImportError:
        return None
    try:
        with Image.open(BytesIO(preview_bytes)) as image:
            gray = image.convert("L").resize((128, 128))
            edges = gray.filter(ImageFilter.FIND_EDGES)
            return float(np.asarray(edges, dtype=np.float32).var())
    except Exception:
        return None


def _optional_brightness(preview_bytes: bytes | None) -> float | None:
    if not preview_bytes:
        return None
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(BytesIO(preview_bytes)) as image:
            gray = image.convert("L").resize((64, 64))
            return float(np.asarray(gray, dtype=np.float32).mean())
    except Exception:
        return None


def _event_day(created_at: datetime | None, metadata: Mapping[str, str]) -> str | None:
    if created_at is not None:
        return created_at.date().isoformat()
    raw = _first_non_empty(metadata, "date_time_original", "created_at")
    if not raw:
        return None
    return raw[:10]


def _json_list(value: str | None) -> list[object]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if isinstance(parsed, list):
        return parsed
    return []


def _json_int_list(value: str | None) -> list[int]:
    out: list[int] = []
    for item in _json_list(value):
        parsed = _optional_int(item)
        if parsed is not None:
            out.append(parsed)
    return out


def _first_non_empty(metadata: Mapping[str, str], *keys: str) -> str | None:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    return {}


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9_-]{1,}", _normalize_text(value))


def _slug(value: str) -> str:
    text = _normalize_text(value)
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def _dedupe_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out
