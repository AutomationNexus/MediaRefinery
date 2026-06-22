from __future__ import annotations

import argparse
import builtins
import subprocess
import sys
import types
from pathlib import Path

import httpx
import pytest

from mediarefinery import analysis as analysis_module
from mediarefinery import cli, doctor, onnx_backend
from mediarefinery.analysis import (
    AdultSubtypeModelContext,
    AnalysisContext,
    analysis_summary,
    analyze_asset,
)
from mediarefinery.classifier import (
    ClassificationResult,
    ClassifierBackendError,
    ClassifierError,
    ClassifierInput,
    RawModelOutput,
    aggregate_model_outputs,
    register_classifier_backend,
)
from mediarefinery.config import ClassifierProfile, ConfigError, load_config, validate_config_data
from mediarefinery.extractor import MediaExtractionError, MediaExtractor
from mediarefinery.immich import (
    SYNTHETIC_IMAGE_PREVIEW_BYTES,
    AssetRef,
    ImmichCapabilities,
    MockImmichClient,
)
from mediarefinery.ocr import (
    NoopOcrAnalyzer,
    OcrInput,
    OcrModelPaths,
    RapidOcrAnalyzer,
    ocr_result_metadata,
)


def _example_config() -> dict:
    return dict(load_config("templates/config.example.yml").raw)


def _profile(**kwargs: object) -> ClassifierProfile:
    values = {
        "name": "test",
        "backend": "noop",
        "model_path": None,
        "output_mapping": {"sfw": "sfw", "nsfw": "nsfw"},
    }
    values.update(kwargs)
    return ClassifierProfile(**values)


def test_config_validation_collects_shape_errors(tmp_path: Path) -> None:
    """Test config validation collects shape errors."""
    data = _example_config()
    data["unknown"] = True
    data["preset"] = ""
    data["categories"] = [{"description": 12}, "bad"]
    data["classifier_profiles"] = {
        "": {},
        "default": {
            "backend": "",
            "model_path": 12,
            "output_mapping": {"": "needs_review", "raw": 12},
            "video_aggregation": "median",
        },
    }
    data["classifier"] = {}
    data["integration"] = {"immich": {"url": ""}}
    data["scanner"] = {"media_types": ["image", "audio"]}
    data["video"] = []
    data["actions"] = []
    data["policies"] = []

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data, source=tmp_path / "config.yml")

    errors = "\n".join(exc_info.value.errors)
    assert "unknown: unknown top-level key" in errors
    assert "preset: optional preset metadata" in errors
    assert "categories[0].id" in errors
    assert "categories[1]: must be a mapping" in errors
    assert "classifier.profile: must be a non-empty string" in errors
    assert "integration.immich.url" in errors
    assert "scanner.media_types[1]" in errors
    assert "video: must be a mapping" in errors
    assert "actions: must be a mapping" in errors
    assert "policies: must be a mapping" in errors


def test_classifier_edge_cases() -> None:
    """Test classifier edge cases."""
    raw = RawModelOutput(asset_id="a", raw_label="sfw", raw_scores={"sfw": 1.0})
    assert raw.raw_labels == ("sfw",)

    profile = _profile(video_aggregation="mean")
    result = aggregate_model_outputs(
        profile,
        [
            RawModelOutput("a", "sfw", {"sfw": 0.2, "nsfw": 0.8}),
            RawModelOutput("a", "sfw", {"sfw": 0.6, "nsfw": 0.4}),
        ],
        asset_id="a",
    )
    assert result.raw_scores["sfw"] == pytest.approx(0.4)

    with pytest.raises(ClassifierError):
        aggregate_model_outputs(profile, [], asset_id="a")
    with pytest.raises(ClassifierError):
        aggregate_model_outputs(
            profile, [RawModelOutput("other", "sfw", {"sfw": 1.0})], asset_id="a"
        )
    with pytest.raises(ClassifierError):
        aggregate_model_outputs(_profile(video_aggregation="median"), [raw], asset_id="a")
    register_classifier_backend("  custom  ", lambda profile: pytest.fail("not constructed"))
    with pytest.raises(ValueError):
        register_classifier_backend("", lambda profile: pytest.fail("not constructed"))


def test_actions_cover_failure_and_capability_paths() -> None:
    """Test actions cover failure and capability paths."""
    from mediarefinery.actions import ActionExecutor
    from mediarefinery.decision import ActionPlan

    class Client:
        capabilities = ImmichCapabilities(tags=False, archive=False, locked_folder=False)

    config = load_config("templates/config.example.yml")
    executor = ActionExecutor(config, Client(), dry_run_override=False)
    plan = ActionPlan(
        category_id="needs_review",
        media_type="image",
        actions=("add_tag", "move_to_locked_folder"),
        dry_run=False,
        asset_id="a",
        reason="test",
    )

    results = executor.execute(plan)
    assert [result.error_code for result in results] == [
        "tag_unsupported",
        "locked_folder_unsupported",
    ]

    live_config = load_config("templates/config.example.yml")
    live_config.raw["actions"]["dry_run"] = False
    live_config.raw["actions"]["archive_enabled"] = True
    live_client = MockImmichClient(
        [AssetRef("a", "image")],
        capabilities=ImmichCapabilities(tags=True, archive=True, locked_folder=True),
    )
    live_plan = ActionPlan(
        "needs_review",
        "image",
        ("archive", "move_to_locked_folder"),
        False,
        asset_id="a",
    )
    live_results = ActionExecutor(live_config, live_client).execute(live_plan)
    assert [result.success for result in live_results] == [True, True]
    assert live_client.archived_asset_ids() == ("a",)
    assert live_client.visibility_requests == [{"asset_id": "a", "visibility": "locked"}]

    class RaisingClient(MockImmichClient):
        def find_album_by_name(self, name: str) -> str | None:
            return None

        def archive_asset(self, asset_id: str) -> None:
            raise RuntimeError("archive failed")

        def set_asset_visibility(self, asset_id: str, visibility: str) -> None:
            raise RuntimeError("visibility failed")

    error_config = load_config("templates/config.example.yml")
    error_config.raw["actions"].update(
        {
            "dry_run": False,
            "archive_enabled": True,
            "create_album_if_missing": False,
        }
    )
    error_client = RaisingClient(
        [AssetRef("a", "image")],
        capabilities=ImmichCapabilities(archive=True, locked_folder=True),
    )
    error_plan = ActionPlan(
        "needs_review",
        "image",
        ("add_to_review_album", "archive", "move_to_locked_folder"),
        False,
        asset_id="a",
    )
    assert [
        result.error_code
        for result in ActionExecutor(error_config, error_client).execute(error_plan)
    ] == [
        "review_album_missing",
        "archive_action_failed",
        "locked_folder_action_failed",
    ]


def test_analysis_custom_categories_and_subtype_unavailable_paths() -> None:
    """Test analysis custom categories and subtype unavailable paths."""
    asset = AssetRef(
        asset_id="a",
        media_type="image",
        metadata={
            "filename": "receipt.jpg",
            "people_json": '[{"personId":"p1"}]',
            "ocr_text": "receipt total paid",
            "ocr_confidence": "0.95",
            "ocr_source_frames_json": '[0, 2, "bad"]',
            "smart_tags_json": '["receipt"]',
            "smart_objects_json": '["paper"]',
        },
    )
    primary = ClassificationResult(
        asset_id="a",
        category_id="unknown_category",
        raw_label="raw",
        raw_scores={"raw": 0.2},
    )
    context = AnalysisContext(
        categories={
            "receipt": {"aliases": ["paper"]},
            "disabled": {"enabled": False},
            "people_docs": {"rules": [{"people": ["p1"], "ocr_contains": ["total"]}]},
        },
        adult_subtype_model=AdultSubtypeModelContext(
            model_id="subtypes",
            output_labels=("known",),
            thresholds={},
            admin_acknowledged=False,
        ),
    )

    analysis = analyze_asset(asset, primary, context=context)
    summary = analysis_summary(analysis)

    assert "receipt" in analysis["custom_categories"]
    assert "people_docs" in analysis["custom_categories"]
    assert analysis["adult_subtypes"]["status"] == "not_applicable"
    assert summary["document_type"] == "receipt"

    sensitive = ClassificationResult(
        asset_id="a",
        category_id="nsfw",
        raw_label="nsfw",
        raw_scores={"nsfw": 0.9},
    )
    blocked = analyze_asset(asset, sensitive, context=context)
    assert blocked["adult_subtypes"]["reason"] == "admin_acknowledgement_required"


def test_analysis_quality_signal_with_optional_image_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test analysis quality signal with optional image helpers."""
    class FakeArray:
        def var(self) -> float:
            return 12.0

        def mean(self) -> float:
            return 12.0

    class FakeImage:
        def __enter__(self) -> FakeImage:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def convert(self, mode: str) -> FakeImage:
            return self

        def resize(self, size: tuple[int, int]) -> FakeImage:
            return self

        def filter(self, image_filter: object) -> FakeImage:
            return self

    numpy_module = types.ModuleType("numpy")
    numpy_module.float32 = object()
    numpy_module.asarray = lambda image, dtype=None: FakeArray()

    image_module = types.ModuleType("PIL.Image")
    image_module.open = lambda payload: FakeImage()

    filter_module = types.ModuleType("PIL.ImageFilter")
    filter_module.FIND_EDGES = object()

    pil_module = types.ModuleType("PIL")
    pil_module.Image = image_module
    pil_module.ImageFilter = filter_module

    monkeypatch.setitem(sys.modules, "numpy", numpy_module)
    monkeypatch.setitem(sys.modules, "PIL", pil_module)
    monkeypatch.setitem(sys.modules, "PIL.Image", image_module)
    monkeypatch.setitem(sys.modules, "PIL.ImageFilter", filter_module)

    quality = analysis_module._quality_signal({"width": 640, "height": 640}, b"data")

    assert "blurry" in quality["flags"]
    assert "dark" in quality["flags"]
    assert quality["blur_score"] == 12.0
    assert quality["brightness"] == 12.0


def test_extractor_additional_error_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test extractor additional error paths."""
    extractor = MediaExtractor()
    jpeg = b"\xff\xd8\xff\xc0\x00\x08\x08\x00\x10\x00\x20\x00"
    info = extractor.image_input(asset_id="j", media_type="image", image_bytes=jpeg)
    assert info.content_type == "image/jpeg"

    with pytest.raises(MediaExtractionError) as missing_source:
        with extractor.video_frame_inputs(
            asset_id="v",
            media_type="video",
            video_path="",
            video_config={"enabled": True},
        ):
            pass
    assert missing_source.value.message_code == "missing_video_source"

    with pytest.raises(MediaExtractionError) as strategy:
        with extractor.video_frame_inputs(
            asset_id="v",
            media_type="video",
            video_path=tmp_path / "missing.mp4",
            video_config={"enabled": True, "frame_strategy": "scene"},
        ):
            pass
    assert strategy.value.message_code == "unsupported_frame_strategy"

    with pytest.raises(MediaExtractionError) as unsupported:
        with extractor.video_frame_inputs(
            asset_id="a",
            media_type="image",
            video_path=tmp_path / "still.jpg",
            video_config={"enabled": True},
        ):
            pass
    assert unsupported.value.message_code == "unsupported_media_type"

    for image_bytes in (b"\x89PNG\r\n\x1a\n" + b"\x00" * 25, b"GIF89a"):
        with pytest.raises(MediaExtractionError):
            extractor.image_input(asset_id="bad", media_type="image", image_bytes=image_bytes)

    video = tmp_path / "input.mp4"
    video.write_bytes(b"placeholder")

    def fake_timeout(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if Path(command[0]).name.startswith("ffprobe"):
            return subprocess.CompletedProcess(command, 1, "", "")
        raise subprocess.TimeoutExpired(command, timeout=1)

    monkeypatch.setattr("mediarefinery.extractor.subprocess.run", fake_timeout)
    with pytest.raises(MediaExtractionError) as timeout:
        with extractor.video_frame_inputs(
            asset_id="v",
            media_type="video",
            video_path=video,
            video_config={"enabled": True, "ffmpeg_path": "ffmpeg"},
            runtime_config={"temp_dir": str(tmp_path / "frames")},
        ):
            pass
    assert timeout.value.message_code == "ffmpeg_failed"

    frame = tmp_path / "frame.png"
    frame.write_bytes(b"not-an-image")
    with pytest.raises(MediaExtractionError) as corrupt:
        from mediarefinery import extractor as extractor_module

        extractor_module._frame_classifier_input(
            asset_id="v",
            asset_media_type="video",
            frame_path=frame,
            frame_index=0,
            frame_total=1,
            frame_strategy="uniform",
            metadata={"video_path": "hidden"},
        )
    assert corrupt.value.message_code == "corrupt_video_frame"

    from mediarefinery import extractor as extractor_module

    monkeypatch.setattr(extractor_module, "_executable_available", lambda path: True)
    monkeypatch.setattr(
        extractor_module.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "not-a-number\n", ""),
    )
    assert extractor_module._probe_video_duration_seconds("ffmpeg", tmp_path / "v.mp4") is None
    assert extractor_module._format_seconds(1.250000) == "1.25"


def test_ocr_analyzer_success_error_and_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Test ocr analyzer success error and metadata."""
    paths = OcrModelPaths(
        detector=tmp_path / "det.onnx",
        recognizer=tmp_path / "rec.onnx",
        dictionary=tmp_path / "dict.txt",
        classifier=tmp_path / "cls.onnx",
    )
    monkeypatch.setattr("mediarefinery.ocr._bytes_to_ndarray", lambda image_bytes: object())

    def engine_factory(model_paths: OcrModelPaths):
        assert model_paths == paths

        def engine(image: object) -> object:
            return [[(" Hello  world ", 0.9), [[0, 0], "Invoice", 0.7], "Hello world"]]

        return engine

    analyzer = RapidOcrAnalyzer(
        model_paths=paths,
        model_sha256="o" * 64,
        engine_factory=engine_factory,
        max_inputs=2,
        max_text_chars=12,
    )
    result = analyzer.analyze(
        [
            OcrInput(asset_id="a", image_bytes=b"image", source="preview", frame_index=1),
            OcrInput(asset_id="a", image_bytes=b"", source="empty"),
        ],
        asset_id="a",
    )
    metadata = ocr_result_metadata(result)

    assert result.status == "local"
    assert result.text == "Hello world"
    assert result.source_frames == (1,)
    assert "ocr_lines_json" in metadata

    no_input = analyzer.analyze([], asset_id="a")
    assert no_input.status == "no_input"
    assert (
        NoopOcrAnalyzer(status="disabled", reason="off").analyze([], asset_id="a").error_code
        == "off"
    )

    failing = RapidOcrAnalyzer(
        model_paths=paths,
        model_sha256="o" * 64,
        engine_factory=lambda model_paths: (_ for _ in ()).throw(ImportError("missing")),
    )
    assert (
        failing.analyze([OcrInput("a", b"x", "preview")], asset_id="a").error_code
        == "dependency_missing"
    )

    calls: dict[str, object] = {}

    class Module:
        class RapidOCR:
            def __init__(self, **kwargs: object) -> None:
                calls.update(kwargs)

            def __call__(self, image: object) -> object:
                return None

    monkeypatch.setattr("mediarefinery.ocr.import_module", lambda name: Module)
    default_analyzer = RapidOcrAnalyzer(
        model_paths=paths,
        model_sha256="o" * 64,
    )
    assert default_analyzer._require_engine() is default_analyzer._require_engine()
    assert calls["use_angle_cls"] is True


def test_onnx_backend_session_and_helper_edges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test onnx backend session and helper edges."""
    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")

    class Io:
        def __init__(self, name: str) -> None:
            self.name = name

    class Session:
        def get_inputs(self) -> list[Io]:
            return [Io("pixels")]

        def get_outputs(self) -> list[Io]:
            return [Io("scores")]

    class Ort:
        def InferenceSession(self, path: str, providers: list[str]) -> Session:
            assert path == str(model)
            assert providers == ["CPUExecutionProvider"]
            return Session()

    original_load_onnx_dependencies = onnx_backend._load_onnx_dependencies
    monkeypatch.setattr(
        onnx_backend,
        "_load_onnx_dependencies",
        lambda: onnx_backend._OnnxDependencies(ort=Ort(), np=None, image=None, image_ops=None),
    )
    backend = onnx_backend.OnnxClassifierBackend(
        _profile(backend="onnx", model_path=str(model), input_name="pixels", output_name="scores")
    )
    backend.load()
    assert backend.predict_batch([]) == []

    with pytest.raises(ClassifierBackendError):
        onnx_backend._select_io_name([Io("actual")], configured_name="missing", kind="input")
    with pytest.raises(ClassifierBackendError):
        onnx_backend._select_io_name([], configured_name=None, kind="output")
    with pytest.raises(ClassifierError):
        onnx_backend._score_rows([], labels=("a",), batch_size=1)
    with pytest.raises(ClassifierError):
        onnx_backend._score_rows([[1.0, 2.0]], labels=("a",), batch_size=1)
    assert onnx_backend._score_rows([[0.1, 0.9]], labels=("a", "b"), batch_size=1)[0]["b"] == 0.9
    assert onnx_backend._flatten_numbers([1, [2.5]]) == [1.0, 2.5]

    class Array:
        def __truediv__(self, other: object) -> Array:
            return self

        def __sub__(self, other: object) -> Array:
            return self

        def astype(self, *args: object, **kwargs: object) -> Array:
            return self

    class Image:
        def __enter__(self) -> Image:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            return None

        def convert(self, mode: str) -> Image:
            return self

        def resize(self, size: tuple[int, int]) -> Image:
            return self

    class ImageModule:
        @staticmethod
        def open(data: object) -> Image:
            return Image()

    class ImageOps:
        @staticmethod
        def exif_transpose(image: Image) -> Image:
            return image

    class Numpy:
        float32 = "float32"

        @staticmethod
        def asarray(value: object, dtype: object = None) -> Array:
            return Array()

        @staticmethod
        def transpose(value: Array, axes: tuple[int, int, int]) -> Array:
            return value

    backend._deps = onnx_backend._OnnxDependencies(
        ort=None, np=Numpy, image=ImageModule, image_ops=ImageOps
    )
    assert isinstance(
        backend._preprocess_input(
            ClassifierInput("a", "image", data=SYNTHETIC_IMAGE_PREVIEW_BYTES)
        ),
        Array,
    )
    with pytest.raises(ClassifierError):
        backend._preprocess_input(ClassifierInput("a", "image", data=None))
    unloaded = onnx_backend.OnnxClassifierBackend(_profile(backend="onnx", model_path=str(model)))
    with pytest.raises(ClassifierError):
        unloaded._require_deps()
    with pytest.raises(ClassifierError):
        unloaded._require_session()
    with pytest.raises(ClassifierError):
        unloaded._require_input_name()
    with pytest.raises(ClassifierError):
        unloaded._require_output_name()

    real_import = builtins.__import__

    def missing_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "numpy":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(onnx_backend, "_load_onnx_dependencies", original_load_onnx_dependencies)
    monkeypatch.setattr(builtins, "__import__", missing_import)
    with pytest.raises(ClassifierBackendError):
        onnx_backend._load_onnx_dependencies()


def test_doctor_probe_paths() -> None:
    """Test doctor probe paths."""

    def _client(handler) -> doctor._ImmichDoctorHttpClient:
        return doctor._ImmichDoctorHttpClient(
            base_url="https://immich.example",
            api_key="secret",
            timeout_seconds=1,
            verify_tls=True,
            transport=httpx.MockTransport(handler),
        )

    # 200 with a non-JSON body -> status 200, json_data None.
    ok = _client(lambda request: httpx.Response(200, content=b"{bad-json"))
    result = ok.get_json("/server/about", authenticated=True)
    assert result.status_code == 200
    assert result.json_data is None

    # An HTTP error status is surfaced as the status code (httpx does not raise).
    forbidden = _client(lambda request: httpx.Response(403))
    assert forbidden.get_json("/server/about", authenticated=False).status_code == 403

    # A network error -> network_failed.
    def _offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    assert _client(_offline).get_json("/server/about", authenticated=False).network_failed


def test_doctor_capability_and_auth_checks() -> None:
    """Test doctor capability and auth checks."""
    class FakeClient:
        def __init__(self, responses: list[doctor._HttpProbeResult]) -> None:
            self.responses = responses

        def get_json(self, endpoint: str, *, authenticated: bool) -> doctor._HttpProbeResult:
            return self.responses.pop(0)

    reach = doctor._probe_reachability(
        FakeClient(
            [doctor._HttpProbeResult(None, error_code="offline"), doctor._HttpProbeResult(503)]
        )
    )
    assert reach.status == doctor.STATUS_WARNING

    auth = doctor._probe_authentication(FakeClient([doctor._HttpProbeResult(403)]))
    assert auth.status == doctor.STATUS_WARNING
    auth = doctor._probe_authentication(FakeClient([doctor._HttpProbeResult(401)]))
    assert auth.status == doctor.STATUS_FAILED

    caps = doctor._probe_capabilities(
        FakeClient([doctor._HttpProbeResult(200, {"tag": True, "archive": False})])
    )
    assert caps.status == doctor.STATUS_OK
    assert "tags=available" in caps.message


def test_doctor_run_probe_and_path_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test doctor run probe and path helpers."""
    config = load_config("templates/config.example.yml")
    config.raw["state"]["sqlite_path"] = ":memory:"
    config.raw["runtime"]["temp_dir"] = None
    assert doctor._check_state_path(config).status == doctor.STATUS_OK
    assert doctor._check_temp_path(config).status == doctor.STATUS_OK

    model = tmp_path / "model.onnx"
    model.write_bytes(b"model")
    object.__setattr__(config.active_profile, "model_path", str(model))
    assert doctor._check_model_path(config).status == doctor.STATUS_OK

    monkeypatch.setattr(doctor, "_executable_available", lambda command: True)
    config.raw["video"]["enabled"] = True
    assert doctor._check_ffmpeg(config).status == doctor.STATUS_OK

    class FakeDoctorClient:
        def __init__(self, **kwargs: object) -> None:
            self.responses = [
                doctor._HttpProbeResult(200),
                doctor._HttpProbeResult(200),
                doctor._HttpProbeResult(200, {"features": [{"tags": True}, {"archive": True}]}),
            ]

        def get_json(self, endpoint: str, *, authenticated: bool) -> doctor._HttpProbeResult:
            return self.responses.pop(0)

    monkeypatch.setattr(doctor, "_ImmichDoctorHttpClient", FakeDoctorClient)
    checks = doctor.probe_immich(config, "api-key")
    assert [check.status for check in checks] == [
        doctor.STATUS_OK,
        doctor.STATUS_OK,
        doctor.STATUS_OK,
    ]

    assert doctor._doctor_timeout(True) == doctor.DOCTOR_NETWORK_TIMEOUT_SECONDS
    assert doctor._doctor_timeout(-1) == doctor.DOCTOR_NETWORK_TIMEOUT_SECONDS
    assert doctor._immich_api_url("https://host/base/api", "server/ping").endswith(
        "/base/api/server/ping"
    )
    assert doctor._find_bool_feature([{"nested": {"archive": False}}], {"archive"}) is False


def test_cli_error_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Test cli error paths."""
    assert cli.main([]) == 0

    invalid = argparse.Namespace(config=str(tmp_path / "missing.yml"))
    assert cli._cmd_config_validate(invalid) == 1

    monkeypatch.setattr(cli, "run_doctor_checks", lambda config: doctor.DoctorResult(()))
    assert cli._cmd_doctor(argparse.Namespace(config=None)) == 0

    monkeypatch.setattr(
        cli, "load_config", lambda config: (_ for _ in ()).throw(ConfigError(["bad"]))
    )
    assert (
        cli._cmd_scan(
            argparse.Namespace(config=None, immich_http=False, state_path=None, dry_run=False)
        )
        == 1
    )
    assert cli._load_report_config(argparse.Namespace(config=None, state_path=None)) == 1

    out = capsys.readouterr()
    assert "bad" in out.err


def test_config_and_state_helper_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test config and state helper edges."""
    from mediarefinery import config as config_module
    from mediarefinery import state as state_module

    monkeypatch.setenv("MEDIAREFINERY_CONFIG", "env-config.yml")
    assert config_module.discover_config_path().name == "env-config.yml"
    monkeypatch.delenv("MEDIAREFINERY_CONFIG")
    with pytest.raises(ConfigError):
        validate_config_data(None)

    assert state_module._summary_json(None) == {}
    assert state_module._summary_json("[]") == {}
    assert state_module._summary_int({"x": True}, "x", fallback=7) == 7
    assert state_module._summary_counts({"x": {"a": True}}, "x", fallback={"b": 2}) == {"b": 2}
    assert state_module._json_safe({b"k": {Path("secret"): b"bytes"}})
    redacted = state_module._safe_error_value(
        {"api_key": "secret", "path": "C:\\Users\\a\\photo.jpg"}
    )
    assert redacted == {"path": "<user-home-path>", "redacted": "<redacted>"}
