from __future__ import annotations

from pathlib import Path

from mediarefinery.ocr import (
    NoopOcrAnalyzer,
    OcrInput,
    OcrModelPaths,
    RapidOcrAnalyzer,
    ocr_result_metadata,
)


def test_noop_ocr_analyzer_reports_disabled_without_text():
    """Test noop ocr analyzer reports disabled without text."""
    analyzer = NoopOcrAnalyzer(status="disabled")
    result = analyzer.analyze([], asset_id="asset-1")
    assert result.available is False
    assert result.status == "disabled"
    assert ocr_result_metadata(result)["ocr_status"] == "disabled"


def test_rapidocr_analyzer_aggregates_text_without_persisting_image_bytes(monkeypatch):
    """Test rapidocr analyzer aggregates text without persisting image bytes."""
    monkeypatch.setattr("mediarefinery.ocr._bytes_to_ndarray", lambda image_bytes: image_bytes)

    def engine_factory(_paths):
        def engine(_image):
            return [
                [
                    ([[0, 0], [1, 0], [1, 1], [0, 1]], ("Invoice 123", 0.95)),
                    ([[0, 2], [1, 2], [1, 3], [0, 3]], ("Total tax", 0.85)),
                ]
            ], 0.01

        return engine

    analyzer = RapidOcrAnalyzer(
        model_paths=OcrModelPaths(
            detector=Path("det.onnx"),
            recognizer=Path("rec.onnx"),
            dictionary=Path("dict.txt"),
        ),
        model_sha256="a" * 64,
        engine_factory=engine_factory,
    )
    result = analyzer.analyze(
        [
            OcrInput(
                asset_id="asset-1",
                image_bytes=b"private-image-bytes",
                source="preview",
            )
        ],
        asset_id="asset-1",
    )

    assert result.available is True
    assert result.status == "local"
    assert result.text == "Invoice 123\nTotal tax"
    assert result.confidence is not None
    assert round(result.confidence, 2) == 0.9
    metadata = ocr_result_metadata(result)
    assert metadata["ocr_text"] == "Invoice 123\nTotal tax"
    assert "private-image-bytes" not in str(metadata)


def test_rapidocr_analyzer_records_source_frames(monkeypatch):
    """Test rapidocr analyzer records source frames."""
    monkeypatch.setattr("mediarefinery.ocr._bytes_to_ndarray", lambda image_bytes: image_bytes)

    def engine_factory(_paths):
        def engine(_image):
            return [([[0, 0], [1, 0]], "Receipt total", 0.8)], 0.01

        return engine

    analyzer = RapidOcrAnalyzer(
        model_paths=OcrModelPaths(
            detector=Path("det.onnx"),
            recognizer=Path("rec.onnx"),
            dictionary=Path("dict.txt"),
        ),
        model_sha256="b" * 64,
        engine_factory=engine_factory,
    )
    result = analyzer.analyze(
        [
            OcrInput(
                asset_id="asset-1",
                image_bytes=b"frame",
                source="ffmpeg_frame",
                frame_index=2,
                frame_total=3,
            )
        ],
        asset_id="asset-1",
    )

    assert result.source_frames == (2,)
    assert ocr_result_metadata(result)["ocr_source_frames_json"] == "[2]"
