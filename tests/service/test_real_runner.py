"""Real-pipeline runner tests.

These tests exercise ``service.runner.make_real_runner`` /
``submit_real_scan`` end-to-end against an in-memory state store and
``MockImmichClient``. The real ONNX session and authenticated Immich
client paths are injected; here we validate the wiring, multi-tenant
invariants, ``no_active_model`` refusal, and privacy constraints.
"""

from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mediarefinery.classifier import (
    ClassificationResult,
    NoopClassifier,
)
from mediarefinery.config import AppConfig, Category, ClassifierProfile
from mediarefinery.immich import SYNTHETIC_IMAGE_PREVIEW_BYTES, AssetRef, MockImmichClient
from mediarefinery.ocr import OcrInput, OcrResult
from mediarefinery.service.config import MediaSamplingConfig
from mediarefinery.service.runner import (
    RunnerFactories,
    default_factories,
    make_real_runner,
    submit_real_scan,
    synthesize_app_config,
)
from mediarefinery.service.scheduler import ScanRejected
from mediarefinery.service.state_store import StateStore


def _seed_user(store: StateStore, user_id: str = "user-a") -> None:
    store.upsert_user(user_id=user_id, email=f"{user_id}@x.invalid")


def _seed_active_model(store: StateStore, sha: str = "a" * 64) -> None:
    store._conn.execute(  # type: ignore[attr-defined]
        "INSERT INTO model_registry(name, version, sha256, active) VALUES (?,?,?,1)",
        ("test-model", "1.0", sha),
    )
    store._conn.commit()  # type: ignore[attr-defined]


def _seed_adult_subtype_model(store: StateStore, sha: str = "c" * 64) -> None:
    store._conn.execute(  # type: ignore[attr-defined]
        """
        INSERT INTO model_registry(
            name, version, sha256, kind, active_slot, metadata_json, active
        )
        VALUES (?, ?, ?, ?, ?, ?, 1)
        """,
        (
            "local-subtypes",
            "local-subtypes",
            sha,
            "adult_subtype_classifier",
            "adult_subtype",
            (
                '{"admin_acknowledged":true,"model_id":"local-subtypes",'
                '"output_labels":["custom_one"],'
                '"thresholds":{"custom_one":0.7}}'
            ),
        ),
    )
    store._conn.commit()  # type: ignore[attr-defined]


def _make_assets(n: int = 2) -> list[AssetRef]:
    now = datetime.now(UTC)
    return [
        AssetRef(
            asset_id=f"asset-{i}",
            media_type="image",
            checksum=f"sum-{i}",
            created_at=now,
            updated_at=now,
        )
        for i in range(n)
    ]


def _factories_with(
    *,
    assets: Sequence[AssetRef],
    classifier_label: str,
    config_factory=synthesize_app_config,
    client: MockImmichClient | None = None,
    ocr_analyzer=None,
    adult_subtype_classifier=None,
) -> RunnerFactories:
    def immich_factory(_user_id):
        return client if client is not None else MockImmichClient(assets=list(assets))

    def classifier_factory(_sha):
        profile = ClassifierProfile(
            name="test",
            backend="noop",
            model_path=None,
            output_mapping={classifier_label: classifier_label},
        )
        cfg = AppConfig(
            source=None,
            raw={
                "version": 1,
                "categories": [{"id": classifier_label}],
                "classifier_profiles": {"test": {"backend": "noop"}},
                "classifier": {"active_profile": "test"},
            },
            categories=(Category(id=classifier_label),),
            classifier_profiles={"test": profile},
            active_profile_name="test",
        )
        return NoopClassifier(cfg)

    def ocr_factory(_store):
        return ocr_analyzer

    def adult_subtype_classifier_factory(_active_model):
        return adult_subtype_classifier

    return RunnerFactories(
        immich_factory=immich_factory,
        classifier_factory=classifier_factory,
        config_factory=config_factory,
        ocr_factory=ocr_factory if ocr_analyzer is not None else None,
        adult_subtype_classifier_factory=adult_subtype_classifier_factory
        if adult_subtype_classifier is not None
        else None,
    )


class _FakeOcrAnalyzer:
    version = "fake-ocr"

    def __init__(self, text: str) -> None:
        self.text = text
        self.inputs: list[OcrInput] = []

    def analyze(self, inputs, *, asset_id: str) -> OcrResult:
        self.inputs = list(inputs)
        return OcrResult(
            asset_id=asset_id,
            available=bool(self.text),
            status="local" if self.text else "no_text",
            text=self.text,
            confidence=0.91 if self.text else None,
            language="en",
            source_frames=tuple(
                item.frame_index for item in self.inputs if item.frame_index is not None
            ),
            analyzer_version=self.version,
            model_sha256="o" * 64,
        )


class _FixedAdultSubtypeClassifier:
    def __init__(self, label: str, confidence: float) -> None:
        self.profile = ClassifierProfile(
            name="adult-subtype",
            backend="noop",
            model_path=None,
            output_mapping={label: label},
        )
        self._label = label
        self._confidence = confidence

    def predict_one(self, classifier_input):
        return ClassificationResult(
            asset_id=classifier_input.asset_id,
            category_id=self._label,
            raw_label=self._label,
            raw_labels=(self._label,),
            raw_scores={self._label: self._confidence},
        )

    def predict_aggregate(self, inputs, *, asset_id: str, aggregation: str | None = None):
        return ClassificationResult(
            asset_id=asset_id,
            category_id=self._label,
            raw_label=self._label,
            raw_labels=(self._label,),
            raw_scores={self._label: self._confidence},
        )


def test_submit_real_scan_refuses_when_no_active_model(tmp_path):
    """Test submit real scan refuses when no active model."""
    db = tmp_path / "state.db"
    with StateStore(db) as store:
        _seed_user(store)
        with pytest.raises(ScanRejected) as exc:
            submit_real_scan(store=store, user_id="user-a")
        assert exc.value.reason == "no_active_model"


def test_submit_real_scan_runs_pipeline_against_mock_immich(tmp_path):
    """Test submit real scan runs pipeline against mock immich."""
    db = tmp_path / "state.db"
    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"nsfw": {"id": "nsfw"}})
        scoped.set_policies(
            {"nsfw": {"image": {"on_match": ["add_tag"]}}}
        )

        assets = _make_assets(3)
        factories = _factories_with(assets=assets, classifier_label="nsfw")

        submitted = submit_real_scan(
            store=store, user_id="user-a", factories=factories
        )

        # Wait briefly for daemon thread to finish.
        import time

        for _ in range(50):
            row = scoped.get_run(submitted.run_id)
            if row is not None and row["status"] != "running":
                break
            time.sleep(0.05)

        run = scoped.get_run(submitted.run_id)
        assert run is not None
        assert run["status"] == "completed"
        assert run["dry_run"] == 1

        actions = scoped.list_actions()
        assert len(actions) == 3
        assert all(a["dry_run"] == 1 for a in actions)
        # add_tag is unsupported by MockImmichClient capabilities by
        # default -> ActionExecutor records tag_unsupported but
        # success=False; this test only asserts rows were persisted
        # and the dry_run flag is honoured.

        audit = [a["action"] for a in scoped.list_audit()]
        assert "scan.start" in audit
        assert "scan.finish" in audit


def test_real_runner_records_asset_analysis_and_video_preview(tmp_path):
    """Test real runner records asset analysis and video preview."""
    db = tmp_path / "state.db"
    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories(
            {
                "nsfw": {"id": "nsfw", "threshold": 0.85},
                "invoice": {"enabled": True},
            }
        )
        scoped.set_policies(
            {"nsfw": {"video": {"on_match": ["manual_review"]}}}
        )
        asset = AssetRef(
            asset_id="video-1",
            media_type="video",
            checksum="sum-video",
            metadata={
                "mock_raw_label": "nsfw",
                "duration": "0:00:03",
                "ocr_text": "invoice total tax",
                "people_json": '[{"id":"p1","name":"Alice"}]',
            },
        )
        factories = _factories_with(assets=[asset], classifier_label="nsfw")
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("video-1")
        assert analysis is not None
        assert analysis["media_info"]["kind"] == "video"
        assert analysis["safety"]["label"] == "nsfw"
        assert analysis["document"]["type"] == "invoice"
        assert analysis["people"] == [{"id": "p1", "name": "Alice"}]
        rows, _ = scoped.list_review_assets_paginated(
            cursor=None, page_size=10, queue="documents"
        )
        assert [row["asset_id"] for row in rows] == ["video-1"]


def test_real_runner_adds_model_backed_adult_subtype_review_queue(tmp_path):
    """Test real runner adds model backed adult subtype review queue."""
    db = tmp_path / "state.db"
    asset = AssetRef(
        asset_id="sensitive-1",
        media_type="image",
        checksum="sum-sensitive",
        metadata={"mock_raw_label": "nsfw"},
    )

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        _seed_adult_subtype_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"nsfw": {"id": "nsfw"}})
        scoped.set_policies({"nsfw": {"image": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="nsfw",
            adult_subtype_classifier=_FixedAdultSubtypeClassifier(
                "custom_one",
                0.91,
            ),
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("sensitive-1")
        assert analysis is not None
        assert analysis["adult_subtypes"]["status"] == "available"
        assert analysis["adult_subtypes"]["top_label"] == "custom_one"
        assert "adult_subtypes" in analysis["review_queues"]
        rows, _ = scoped.list_review_assets_paginated(
            cursor=None,
            page_size=10,
            queue="adult_subtypes",
        )
        assert [row["asset_id"] for row in rows] == ["sensitive-1"]


def test_real_runner_persists_local_ocr_text_and_searches_it(tmp_path):
    """Test real runner persists local ocr text and searches it."""
    db = tmp_path / "state.db"
    private_preview = SYNTHETIC_IMAGE_PREVIEW_BYTES + b"private-preview-marker"
    asset = AssetRef(
        asset_id="image-ocr-1",
        media_type="image",
        checksum="sum-image",
        metadata={"mock_raw_label": "sfw"},
    )
    client = MockImmichClient(
        assets=[asset],
        preview_bytes_by_asset_id={"image-ocr-1": private_preview},
    )
    ocr_analyzer = _FakeOcrAnalyzer("Invoice number 123\nTotal tax amount due")

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"sfw": {"id": "sfw"}, "invoice": {"enabled": True}})
        scoped.set_policies({"sfw": {"image": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="sfw",
            client=client,
            ocr_analyzer=ocr_analyzer,
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("image-ocr-1")
        assert analysis is not None
        assert analysis["ocr"]["status"] == "local"
        assert analysis["ocr"]["text"] == "Invoice number 123\nTotal tax amount due"
        assert analysis["ocr"]["model_sha256"] == "o" * 64
        assert analysis["document"]["type"] == "invoice"
        rows, _ = scoped.list_review_assets_paginated(
            cursor=None,
            page_size=10,
            q="number 123",
        )
        assert [row["asset_id"] for row in rows] == ["image-ocr-1"]
        assert private_preview.decode("latin1") not in _sqlite_text(db)


def test_real_runner_samples_video_original_frames_and_records_metadata(
    tmp_path,
    monkeypatch,
):
    """Test real runner samples video original frames and records metadata."""
    db = tmp_path / "state.db"
    temp_root = tmp_path / "sampling"
    private_original = b"private-original-video-marker"
    asset = AssetRef(
        asset_id="video-1",
        media_type="video",
        checksum="sum-video",
        metadata={"mock_raw_label": "nsfw"},
    )
    client = MockImmichClient(
        assets=[asset],
        original_bytes_by_asset_id={"video-1": private_original},
    )
    monkeypatch.setattr(
        "mediarefinery.extractor.subprocess.run",
        _fake_ffmpeg_success,
    )

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"nsfw": {"id": "nsfw"}})
        scoped.set_policies({"nsfw": {"video": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="nsfw",
            client=client,
            config_factory=_config_factory_with_sampling(
                temp_root,
                enabled=True,
                max_frames=2,
            ),
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("video-1")
        assert analysis is not None
        assert analysis["sampling"] == {
            "sampling_status": "sampled",
            "sampling_source": "original_frames",
            "sampled_frame_count": 2,
            "frame_aggregation_method": "max",
            "error_code": None,
        }
        assert client.original_requests == ["video-1"]
        assert client.preview_requests == []
        assert list(temp_root.iterdir()) == []
        stored_text = _sqlite_text(db)
        assert private_original.decode("ascii") not in stored_text
        assert "data:video" not in stored_text


def test_real_runner_ocr_uses_sampled_video_frames(tmp_path, monkeypatch):
    """Test real runner ocr uses sampled video frames."""
    db = tmp_path / "state.db"
    asset = AssetRef(
        asset_id="video-ocr-1",
        media_type="video",
        checksum="sum-video",
        metadata={"mock_raw_label": "sfw"},
    )
    client = MockImmichClient(
        assets=[asset],
        original_bytes_by_asset_id={"video-ocr-1": b"small-original-video"},
    )
    ocr_analyzer = _FakeOcrAnalyzer("Receipt total tax")
    monkeypatch.setattr(
        "mediarefinery.extractor.subprocess.run",
        _fake_ffmpeg_success,
    )

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"sfw": {"id": "sfw"}})
        scoped.set_policies({"sfw": {"video": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="sfw",
            client=client,
            ocr_analyzer=ocr_analyzer,
            config_factory=_config_factory_with_sampling(
                tmp_path / "sampling",
                enabled=True,
                max_frames=2,
            ),
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("video-ocr-1")
        assert analysis is not None
        assert analysis["ocr"]["text"] == "Receipt total tax"
        assert analysis["ocr"]["source_frames"] == [0, 1]
        assert [item.frame_index for item in ocr_analyzer.inputs] == [0, 1]
        assert analysis["document"]["type"] == "receipt"


def test_real_runner_sampling_disabled_uses_preview_fallback(tmp_path):
    """Test real runner sampling disabled uses preview fallback."""
    db = tmp_path / "state.db"
    asset = AssetRef(
        asset_id="video-1",
        media_type="video",
        checksum="sum-video",
        metadata={"mock_raw_label": "nsfw"},
    )
    client = MockImmichClient(assets=[asset])

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"nsfw": {"id": "nsfw"}})
        scoped.set_policies({"nsfw": {"video": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="nsfw",
            client=client,
            config_factory=_config_factory_with_sampling(
                tmp_path / "sampling",
                enabled=False,
            ),
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("video-1")
        assert analysis is not None
        assert analysis["sampling"]["sampling_status"] == "disabled"
        assert analysis["sampling"]["sampling_source"] == "preview"
        assert analysis["sampling"]["sampled_frame_count"] == 0
        assert client.original_requests == []
        assert client.preview_requests == ["video-1"]
        assert scoped.list_errors() == []


def test_real_runner_missing_ffmpeg_falls_back_with_warning(tmp_path):
    """Test real runner missing ffmpeg falls back with warning."""
    db = tmp_path / "state.db"
    asset = AssetRef(
        asset_id="video-1",
        media_type="video",
        checksum="sum-video",
        metadata={"mock_raw_label": "nsfw"},
    )
    client = MockImmichClient(
        assets=[asset],
        original_bytes_by_asset_id={"video-1": b"small-original"},
    )

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"nsfw": {"id": "nsfw"}})
        scoped.set_policies({"nsfw": {"video": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="nsfw",
            client=client,
            config_factory=_config_factory_with_sampling(
                tmp_path / "sampling",
                enabled=True,
                ffmpeg_path="definitely-not-mediarefinery-ffmpeg",
            ),
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("video-1")
        assert analysis is not None
        assert analysis["sampling"]["sampling_status"] == "failed"
        assert analysis["sampling"]["sampling_source"] == "preview_fallback"
        assert analysis["sampling"]["error_code"] == "ffmpeg_not_found"
        errors = scoped.list_errors()
        assert [(row["stage"], row["message_code"]) for row in errors] == [
            ("extractor", "ffmpeg_not_found")
        ]
        assert client.original_requests == ["video-1"]
        assert client.preview_requests == ["video-1"]


def test_real_runner_refuses_oversized_original_and_falls_back(tmp_path):
    """Test real runner refuses oversized original and falls back."""
    db = tmp_path / "state.db"
    private_original = b"private-original-too-large"
    asset = AssetRef(
        asset_id="video-1",
        media_type="video",
        checksum="sum-video",
        metadata={"mock_raw_label": "nsfw"},
    )
    client = MockImmichClient(
        assets=[asset],
        original_bytes_by_asset_id={"video-1": private_original},
    )

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"nsfw": {"id": "nsfw"}})
        scoped.set_policies({"nsfw": {"video": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="nsfw",
            client=client,
            config_factory=_config_factory_with_sampling(
                tmp_path / "sampling",
                enabled=True,
                max_original_bytes=4,
            ),
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("video-1")
        assert analysis is not None
        assert analysis["sampling"]["error_code"] == "original_too_large"
        assert private_original.decode("ascii") not in _sqlite_text(db)


def test_real_runner_refuses_too_long_video_and_falls_back(
    tmp_path,
    monkeypatch,
):
    """Test real runner refuses too long video and falls back."""
    db = tmp_path / "state.db"
    ffmpeg_path = tmp_path / "ffmpeg"
    ffprobe_path = tmp_path / "ffprobe"
    ffmpeg_path.write_text("", encoding="utf-8")
    ffprobe_path.write_text("", encoding="utf-8")
    asset = AssetRef(
        asset_id="video-1",
        media_type="video",
        checksum="sum-video",
        metadata={"mock_raw_label": "nsfw"},
    )
    client = MockImmichClient(
        assets=[asset],
        original_bytes_by_asset_id={"video-1": b"small-original"},
    )

    def fake_probe(command, **kwargs):
        if Path(command[0]).name.startswith("ffprobe"):
            return subprocess.CompletedProcess(command, 0, "120\n", "")
        raise AssertionError("ffmpeg should not run after duration refusal")

    monkeypatch.setattr("mediarefinery.extractor.subprocess.run", fake_probe)

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"nsfw": {"id": "nsfw"}})
        scoped.set_policies({"nsfw": {"video": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="nsfw",
            client=client,
            config_factory=_config_factory_with_sampling(
                tmp_path / "sampling",
                enabled=True,
                max_duration_seconds=2,
                ffmpeg_path=str(ffmpeg_path),
            ),
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("video-1")
        assert analysis is not None
        assert analysis["sampling"]["error_code"] == "video_duration_exceeds_limit"
        assert client.preview_requests == ["video-1"]


def test_real_runner_samples_animated_gif_original(
    tmp_path,
    monkeypatch,
):
    """Test real runner samples animated gif original."""
    db = tmp_path / "state.db"
    asset = AssetRef(
        asset_id="gif-1",
        media_type="image",
        checksum="sum-gif",
        metadata={"mock_raw_label": "nsfw", "mime_type": "image/gif"},
    )
    client = MockImmichClient(
        assets=[asset],
        original_bytes_by_asset_id={"gif-1": _animated_gif_bytes()},
    )
    monkeypatch.setattr(
        "mediarefinery.extractor.subprocess.run",
        _fake_ffmpeg_success,
    )

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"nsfw": {"id": "nsfw"}})
        scoped.set_policies({"nsfw": {"image": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="nsfw",
            client=client,
            config_factory=_config_factory_with_sampling(
                tmp_path / "sampling",
                enabled=True,
                max_frames=2,
            ),
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("gif-1")
        assert analysis is not None
        assert analysis["media_info"]["kind"] == "gif"
        assert analysis["sampling"]["sampling_status"] == "sampled"
        assert analysis["sampling"]["sampled_frame_count"] == 2
        assert client.preview_requests == []


def test_real_runner_classifies_static_gif_as_original_still(tmp_path, monkeypatch):
    """Test real runner classifies static gif as original still."""
    db = tmp_path / "state.db"
    asset = AssetRef(
        asset_id="gif-1",
        media_type="image",
        checksum="sum-gif",
        metadata={"mock_raw_label": "nsfw", "mime_type": "image/gif"},
    )
    client = MockImmichClient(
        assets=[asset],
        original_bytes_by_asset_id={"gif-1": _static_gif_bytes()},
    )

    def fail_ffmpeg(command, **kwargs):
        raise AssertionError("static GIF classification should not call ffmpeg")

    monkeypatch.setattr("mediarefinery.extractor.subprocess.run", fail_ffmpeg)

    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        scoped = store.with_user("user-a")
        scoped.set_categories({"nsfw": {"id": "nsfw"}})
        scoped.set_policies({"nsfw": {"image": {"on_match": ["manual_review"]}}})
        factories = _factories_with(
            assets=[asset],
            classifier_label="nsfw",
            client=client,
            config_factory=_config_factory_with_sampling(
                tmp_path / "sampling",
                enabled=True,
            ),
        )
        runner = make_real_runner(factories)
        run_id = scoped.start_run(dry_run=True, command="scan")

        runner(store, "user-a", run_id)

        analysis = scoped.get_asset_analysis("gif-1")
        assert analysis is not None
        assert analysis["sampling"]["sampling_status"] == "static_gif"
        assert analysis["sampling"]["sampling_source"] == "original_still"
        assert analysis["sampling"]["sampled_frame_count"] == 1
        assert client.preview_requests == []


def test_runner_two_user_isolation(tmp_path):
    """Test runner two user isolation."""
    db = tmp_path / "state.db"
    with StateStore(db) as store:
        _seed_user(store, "user-a")
        _seed_user(store, "user-b")
        _seed_active_model(store)
        for uid in ("user-a", "user-b"):
            sc = store.with_user(uid)
            sc.set_categories({"x": {"id": "x"}})
            sc.set_policies({"x": {"image": {"on_match": ["no_action"]}}})

        runner = make_real_runner(
            _factories_with(assets=_make_assets(2), classifier_label="x")
        )

        scoped_a = store.with_user("user-a")
        run_a = scoped_a.start_run(dry_run=True, command="scan")
        runner(store, "user-a", run_a)

        # user-b sees nothing from user-a's run
        scoped_b = store.with_user("user-b")
        assert scoped_b.list_actions() == []
        assert scoped_b.list_runs() == []
        assert scoped_b.list_assets() == []
        assert scoped_b.list_audit() == []

        # user-a sees its own rows
        assert len(scoped_a.list_actions()) == 2
        assert len(scoped_a.list_assets()) == 2


def test_synthesize_app_config_falls_back_to_uncategorised(tmp_path):
    """Test synthesize app config falls back to uncategorised."""
    db = tmp_path / "state.db"
    with StateStore(db) as store:
        _seed_user(store)
        scoped = store.with_user("user-a")
        cfg = synthesize_app_config(scoped)
        assert cfg.active_profile_name == "service-default"
        assert cfg.category_ids == {"uncategorised"}
        assert cfg.actions["dry_run"] is True


def test_default_factories_run_without_assets(tmp_path):
    """Smoke: default factories (Mock client with no seeded assets,
    NoopClassifier placeholder) execute an empty scan cleanly."""

    db = tmp_path / "state.db"
    with StateStore(db) as store:
        _seed_user(store)
        _seed_active_model(store)
        runner = make_real_runner(default_factories())
        scoped = store.with_user("user-a")
        run_id = scoped.start_run(dry_run=True, command="scan")
        runner(store, "user-a", run_id)
        run = scoped.get_run(run_id)
        assert run is not None
        assert run["status"] == "completed"
        assert scoped.list_actions() == []


def _config_factory_with_sampling(
    temp_dir,
    *,
    enabled: bool,
    max_original_bytes: int = 1024 * 1024,
    max_duration_seconds: int = 10,
    max_frames: int = 3,
    extraction_timeout_seconds: int = 5,
    ffmpeg_path: str = "ffmpeg",
):
    media_sampling = MediaSamplingConfig(
        enabled=enabled,
        max_original_bytes=max_original_bytes,
        max_duration_seconds=max_duration_seconds,
        max_frames=max_frames,
        extraction_timeout_seconds=extraction_timeout_seconds,
        temp_dir=Path(temp_dir),
        ffmpeg_path=ffmpeg_path,
    )

    def config_factory(scoped):
        return synthesize_app_config(scoped, media_sampling=media_sampling)

    return config_factory


def _sqlite_text(path) -> str:
    values: list[str] = []
    with sqlite3.connect(path) as conn:
        cursor = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
        table_names = [row[0] for row in cursor.fetchall()]
        for table_name in table_names:
            for row in conn.execute(f"SELECT * FROM {table_name}"):
                values.extend(str(value) for value in row if value is not None)
    return "\n".join(values)


def _fake_ffmpeg_success(command, **kwargs):
    if Path(command[0]).name.startswith("ffprobe"):
        return subprocess.CompletedProcess(command, 1, "", "")
    _write_fake_frames(command)
    return subprocess.CompletedProcess(command, 0, "", "")


def _write_fake_frames(command) -> None:
    frame_count = int(command[command.index("-frames:v") + 1])
    output_pattern = Path(command[-1])
    for index in range(1, frame_count + 1):
        frame_path = output_pattern.parent / f"frame-{index:06d}.png"
        frame_path.write_bytes(SYNTHETIC_IMAGE_PREVIEW_BYTES)


def _static_gif_bytes() -> bytes:
    return (
        b"GIF89a"
        b"\x01\x00\x01\x00"
        b"\x00"
        b"\x00"
        b"\x00"
        b"\x2c"
        b"\x00\x00\x00\x00\x01\x00\x01\x00"
        b"\x00"
        b"\x02\x02\x44\x01\x00"
        b"\x3b"
    )


def _animated_gif_bytes() -> bytes:
    frame = (
        b"\x2c"
        b"\x00\x00\x00\x00\x01\x00\x01\x00"
        b"\x00"
        b"\x02\x02\x44\x01\x00"
    )
    return b"GIF89a\x01\x00\x01\x00\x00\x00\x00" + frame + frame + b"\x3b"
