from __future__ import annotations

from datetime import UTC, datetime

from mediarefinery.analysis import (
    AdultSubtypeModelContext,
    AnalysisContext,
    analyze_asset,
)
from mediarefinery.classifier import ClassificationResult
from mediarefinery.immich import SYNTHETIC_IMAGE_PREVIEW_BYTES, AssetRef


def test_analysis_is_additive_and_marks_unavailable_adult_subtypes() -> None:
    """Test analysis is additive and marks unavailable adult subtypes."""
    asset = AssetRef(
        asset_id="a1",
        media_type="image",
        checksum="sha256:abc",
        metadata={
            "mime_type": "image/gif",
            "filename": "invoice-screenshot.gif",
            "people_json": '[{"id":"p1","name":"Alice"}]',
            "ocr_text": "Invoice number 123 total tax amount due",
            "duplicate_id": "dup-1",
        },
        albums=("family", "taxes"),
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    result = ClassificationResult(
        asset_id="a1",
        category_id="nsfw",
        raw_label="nsfw",
        raw_labels=("nsfw",),
        raw_scores={"sfw": 0.02, "nsfw": 0.98},
    )

    analysis = analyze_asset(
        asset,
        result,
        classifier_metadata={
            "image_width": "1",
            "image_height": "1",
            "image_format": "gif",
            "sampling_status": "sampled",
            "sampling_source": "original_frames",
            "sampled_frame_count": "2",
            "frame_aggregation_method": "max",
        },
        preview_bytes=SYNTHETIC_IMAGE_PREVIEW_BYTES,
        context=AnalysisContext(categories={"nsfw": {"threshold": 0.85}}),
    )

    assert analysis["media_info"]["kind"] == "gif"
    assert analysis["safety"]["label"] == "nsfw"
    assert analysis["adult_subtypes"]["status"] == "unavailable"
    assert analysis["people"] == [{"id": "p1", "name": "Alice"}]
    assert analysis["document"]["type"] == "invoice"
    assert analysis["sampling"]["sampling_status"] == "sampled"
    assert analysis["sampling"]["sampled_frame_count"] == 2
    assert "low_resolution" in analysis["quality"]["flags"]
    assert analysis["duplicates"]["group_key"] == "dup-1"
    assert {"nsfw", "documents", "duplicates", "quality", "people"} <= set(
        analysis["review_queues"]
    )


def test_binary_model_scores_do_not_create_fake_adult_subtypes() -> None:
    """Test binary model scores do not create fake adult subtypes."""
    asset = AssetRef(asset_id="a1", media_type="image")
    result = ClassificationResult(
        asset_id="a1",
        category_id="nsfw",
        raw_label="nsfw",
        raw_labels=("nsfw",),
        raw_scores={"sfw": 0.01, "nsfw": 0.9, "custom_adult_subtype": 0.7},
    )

    analysis = analyze_asset(asset, result)

    assert analysis["adult_subtypes"]["status"] == "unavailable"
    assert analysis["adult_subtypes"]["labels"] == []
    assert "adult_subtypes" not in analysis["review_queues"]


def test_analysis_accepts_acknowledged_subtype_model_scores() -> None:
    """Test analysis accepts acknowledged subtype model scores."""
    asset = AssetRef(asset_id="a1", media_type="image")
    primary = ClassificationResult(
        asset_id="a1",
        category_id="nsfw",
        raw_label="nsfw",
        raw_labels=("nsfw",),
        raw_scores={"sfw": 0.01, "nsfw": 0.9},
    )
    subtype = ClassificationResult(
        asset_id="a1",
        category_id="custom_subtype",
        raw_label="custom_subtype",
        raw_labels=("custom_subtype",),
        raw_scores={"custom_subtype": 0.82, "other_subtype": 0.2},
    )

    analysis = analyze_asset(
        asset,
        primary,
        context=AnalysisContext(
            categories={},
            adult_subtype_model=AdultSubtypeModelContext(
                model_id="local-subtypes",
                output_labels=("custom_subtype", "other_subtype"),
                thresholds={"custom_subtype": 0.7, "other_subtype": 0.7},
                model_sha256="s" * 64,
                admin_acknowledged=True,
            ),
            adult_subtype_result=subtype,
        ),
    )

    assert analysis["adult_subtypes"]["status"] == "available"
    assert analysis["adult_subtypes"]["top_label"] == "custom_subtype"
    assert analysis["adult_subtypes"]["labels"][0]["label"] == "custom_subtype"
    assert "adult_subtypes" in analysis["review_queues"]
    assert "custom_subtype" not in analysis["policy_categories"]


def test_unknown_subtype_output_fails_closed() -> None:
    """Test unknown subtype output fails closed."""
    asset = AssetRef(asset_id="a1", media_type="image")
    primary = ClassificationResult(
        asset_id="a1",
        category_id="nsfw",
        raw_label="nsfw",
        raw_labels=("nsfw",),
        raw_scores={"nsfw": 0.9},
    )
    subtype = ClassificationResult(
        asset_id="a1",
        category_id="known_subtype",
        raw_label="known_subtype",
        raw_labels=("known_subtype",),
        raw_scores={"known_subtype": 0.9, "surprise_subtype": 0.8},
    )

    analysis = analyze_asset(
        asset,
        primary,
        context=AnalysisContext(
            categories={},
            adult_subtype_model=AdultSubtypeModelContext(
                model_id="local-subtypes",
                output_labels=("known_subtype",),
                thresholds={"known_subtype": 0.6},
                admin_acknowledged=True,
            ),
            adult_subtype_result=subtype,
        ),
    )

    assert analysis["adult_subtypes"]["status"] == "failed_closed"
    assert analysis["adult_subtypes"]["unknown_labels"] == ["surprise_subtype"]
    assert "review_needed" in analysis["review_queues"]


def test_low_confidence_subtype_enters_review_needed_queue() -> None:
    """Test low confidence subtype enters review needed queue."""
    asset = AssetRef(asset_id="a1", media_type="image")
    primary = ClassificationResult(
        asset_id="a1",
        category_id="nsfw",
        raw_label="nsfw",
        raw_labels=("nsfw",),
        raw_scores={"nsfw": 0.9},
    )
    subtype = ClassificationResult(
        asset_id="a1",
        category_id="known_subtype",
        raw_label="known_subtype",
        raw_labels=("known_subtype",),
        raw_scores={"known_subtype": 0.54},
    )

    analysis = analyze_asset(
        asset,
        primary,
        context=AnalysisContext(
            categories={},
            adult_subtype_model=AdultSubtypeModelContext(
                model_id="local-subtypes",
                output_labels=("known_subtype",),
                thresholds={"known_subtype": 0.7},
                admin_acknowledged=True,
            ),
            adult_subtype_result=subtype,
        ),
    )

    assert analysis["adult_subtypes"]["status"] == "review_needed"
    assert analysis["adult_subtypes"]["reason"] == "low_confidence"
    assert "review_needed" in analysis["review_queues"]
