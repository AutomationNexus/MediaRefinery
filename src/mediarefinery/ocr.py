"""Ocr for MediaRefinery."""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable


@dataclass(frozen=True)
class OcrInput:
    """Represent OcrInput.

    Attributes
    ----------
    asset_id : str
    image_bytes : bytes
    source : str
    frame_index : int | None
    frame_total : int | None
    """

    asset_id: str
    image_bytes: bytes = field(repr=False, compare=False)
    source: str
    frame_index: int | None = None
    frame_total: int | None = None


@dataclass(frozen=True)
class OcrLine:
    """Represent OcrLine.

    Attributes
    ----------
    text : str
    confidence : float | None
    source : str
    frame_index : int | None
    """

    text: str
    confidence: float | None
    source: str
    frame_index: int | None = None


@dataclass(frozen=True)
class OcrResult:
    """Represent OcrResult.

    Attributes
    ----------
    asset_id : str
    available : bool
    status : str
    text : str
    confidence : float | None
    language : str | None
    script : str | None
    source_frames : tuple[int, ...]
    analyzer_version : str | None
    model_sha256 : str | None
    lines : tuple[OcrLine, ...]
    error_code : str | None
    """

    asset_id: str
    available: bool
    status: str
    text: str = ""
    confidence: float | None = None
    language: str | None = None
    script: str | None = None
    source_frames: tuple[int, ...] = ()
    analyzer_version: str | None = None
    model_sha256: str | None = None
    lines: tuple[OcrLine, ...] = ()
    error_code: str | None = None


@dataclass(frozen=True)
class OcrModelPaths:
    """Represent OcrModelPaths.

    Attributes
    ----------
    detector : Path
    recognizer : Path
    dictionary : Path
    classifier : Path | None
    """

    detector: Path
    recognizer: Path
    dictionary: Path
    classifier: Path | None = None


@runtime_checkable
class OcrAnalyzer(Protocol):
    """Represent OcrAnalyzer."""

    @property
    def version(self) -> str:
        """Version.

        Returns
        -------
        str
        """
        ...

    def analyze(
        self,
        inputs: Sequence[OcrInput],
        *,
        asset_id: str,
    ) -> OcrResult:
        """Analyze.

        Parameters
        ----------
        inputs : Sequence[OcrInput]
        asset_id : str

        Returns
        -------
        OcrResult
        """
        ...


class NoopOcrAnalyzer:
    """Represent NoopOcrAnalyzer."""

    def __init__(
        self,
        *,
        status: str = "disabled",
        reason: str | None = None,
        version: str = "noop",
    ) -> None:
        """Initialize the instance.

        Parameters
        ----------
        status : str, optional
        reason : str | None, optional
        version : str, optional

        Returns
        -------
        None
        """
        self._status = status
        self._reason = reason
        self._version = version

    @property
    def version(self) -> str:
        """Version.

        Returns
        -------
        str
        """
        return self._version

    def analyze(
        self,
        inputs: Sequence[OcrInput],
        *,
        asset_id: str,
    ) -> OcrResult:
        """Analyze.

        Parameters
        ----------
        inputs : Sequence[OcrInput]
        asset_id : str

        Returns
        -------
        OcrResult
        """
        _ = inputs
        return OcrResult(
            asset_id=asset_id,
            available=False,
            status=self._status,
            analyzer_version=self.version,
            error_code=self._reason,
        )


RapidOcrEngineFactory = Callable[[OcrModelPaths], Any]


class RapidOcrAnalyzer:
    """RapidOCR wrapper that keeps image data in memory.

    The model files are supplied by MediaRefinery's catalog installer; this
    class does not download weights or execute remote model code.
    """

    def __init__(
        self,
        *,
        model_paths: OcrModelPaths,
        model_sha256: str,
        language: str = "en",
        engine_factory: RapidOcrEngineFactory | None = None,
        max_inputs: int = 4,
        max_text_chars: int = 20_000,
    ) -> None:
        """Initialize the instance.

        Parameters
        ----------
        model_paths : OcrModelPaths
        model_sha256 : str
        language : str, optional
        engine_factory : RapidOcrEngineFactory | None, optional
        max_inputs : int, optional
        max_text_chars : int, optional

        Returns
        -------
        None
        """
        self.model_paths = model_paths
        self.model_sha256 = model_sha256
        self.language = language
        self._engine_factory = engine_factory
        self._engine: Any | None = None
        self._max_inputs = max(1, int(max_inputs))
        self._max_text_chars = max(1, int(max_text_chars))

    @property
    def version(self) -> str:
        """Version.

        Returns
        -------
        str
        """
        return "rapidocr-onnxruntime"

    def analyze(
        self,
        inputs: Sequence[OcrInput],
        *,
        asset_id: str,
    ) -> OcrResult:
        """Analyze.

        Parameters
        ----------
        inputs : Sequence[OcrInput]
        asset_id : str

        Returns
        -------
        OcrResult
        """
        input_list = [item for item in inputs if item.image_bytes][: self._max_inputs]
        if not input_list:
            return OcrResult(
                asset_id=asset_id,
                available=False,
                status="no_input",
                analyzer_version=self.version,
                model_sha256=self.model_sha256,
            )

        try:
            engine = self._require_engine()
            lines: list[OcrLine] = []
            for item in input_list:
                image = _bytes_to_ndarray(item.image_bytes)
                raw = engine(image)
                lines.extend(_rapidocr_lines(raw, item))
        except ImportError as exc:
            return self._error_result(asset_id, "dependency_missing", exc)
        except Exception as exc:
            return self._error_result(asset_id, type(exc).__name__, exc)

        lines = _dedupe_lines(lines)
        text = _truncate_text("\n".join(line.text for line in lines), self._max_text_chars)
        confidences = [
            line.confidence
            for line in lines
            if line.confidence is not None
        ]
        confidence = (
            sum(confidences) / len(confidences)
            if confidences
            else None
        )
        source_frames = tuple(
            sorted(
                {
                    line.frame_index
                    for line in lines
                    if line.frame_index is not None
                }
            )
        )
        return OcrResult(
            asset_id=asset_id,
            available=bool(text),
            status="local" if text else "no_text",
            text=text,
            confidence=confidence,
            language=self.language,
            script="latin" if self.language == "en" else None,
            source_frames=source_frames,
            analyzer_version=self.version,
            model_sha256=self.model_sha256,
            lines=tuple(lines),
        )

    def _require_engine(self) -> Any:
        if self._engine is not None:
            return self._engine
        if self._engine_factory is not None:
            self._engine = self._engine_factory(self.model_paths)
            return self._engine
        module = import_module("rapidocr_onnxruntime")
        engine_cls = cast(Any, module).RapidOCR
        kwargs: dict[str, object] = {
            "det_model_path": str(self.model_paths.detector),
            "rec_model_path": str(self.model_paths.recognizer),
            "rec_keys_path": str(self.model_paths.dictionary),
        }
        if self.model_paths.classifier is not None:
            kwargs["use_angle_cls"] = True
            kwargs["cls_model_path"] = str(self.model_paths.classifier)
        self._engine = engine_cls(**kwargs)
        return self._engine

    def _error_result(
        self,
        asset_id: str,
        code: str,
        exc: BaseException,
    ) -> OcrResult:
        _ = exc
        return OcrResult(
            asset_id=asset_id,
            available=False,
            status="error",
            analyzer_version=self.version,
            model_sha256=self.model_sha256,
            error_code=code,
        )


def ocr_result_metadata(result: OcrResult) -> dict[str, str]:
    """Ocr result metadata.

    Parameters
    ----------
    result : OcrResult

    Returns
    -------
    dict[str, str]
    """
    metadata: dict[str, str] = {
        "ocr_status": result.status,
        "ocr_available": "true" if result.available else "false",
    }
    if result.text:
        metadata["ocr_text"] = result.text
    if result.confidence is not None:
        metadata["ocr_confidence"] = f"{result.confidence:.6f}"
    if result.language:
        metadata["ocr_language"] = result.language
    if result.script:
        metadata["ocr_script"] = result.script
    if result.source_frames:
        metadata["ocr_source_frames_json"] = json.dumps(list(result.source_frames))
    if result.analyzer_version:
        metadata["ocr_analyzer_version"] = result.analyzer_version
    if result.model_sha256:
        metadata["ocr_model_sha256"] = result.model_sha256
    if result.error_code:
        metadata["ocr_error_code"] = result.error_code
    if result.lines:
        metadata["ocr_lines_json"] = json.dumps(
            [
                {
                    "text": line.text,
                    "confidence": line.confidence,
                    "source": line.source,
                    "frame_index": line.frame_index,
                }
                for line in result.lines[:50]
            ],
            sort_keys=True,
        )
    return metadata


def _bytes_to_ndarray(image_bytes: bytes) -> Any:
    pil_module = import_module("PIL.Image")
    image_ops_module = import_module("PIL.ImageOps")
    np = import_module("numpy")
    with pil_module.open(BytesIO(image_bytes)) as image:
        image = image_ops_module.exif_transpose(image)
        image = image.convert("RGB")
        return np.asarray(image)


def _rapidocr_lines(raw: object, source: OcrInput) -> list[OcrLine]:
    rows = raw
    if isinstance(raw, (tuple, list)) and raw:
        rows = raw[0]
    if rows is None:
        return []
    if not isinstance(rows, list):
        return []
    out: list[OcrLine] = []
    for row in rows:
        for text, confidence in _find_text_confidences(row):
            text = _normalize_line(text)
            if not text:
                continue
            out.append(
                OcrLine(
                    text=text,
                    confidence=confidence,
                    source=source.source,
                    frame_index=source.frame_index,
                )
            )
    return out


def _find_text_confidences(value: object) -> list[tuple[str, float | None]]:
    if isinstance(value, (tuple, list)):
        if len(value) >= 2 and isinstance(value[0], str) and _is_number(value[1]):
            return [(value[0], float(value[1]))]
        if len(value) >= 3 and isinstance(value[1], str) and _is_number(value[2]):
            return [(value[1], float(value[2]))]
        parsed: list[tuple[str, float | None]] = []
        for item in value:
            parsed.extend(_find_text_confidences(item))
        return parsed
    elif isinstance(value, str):
        return [(value, None)]
    return []


def _dedupe_lines(lines: Sequence[OcrLine]) -> list[OcrLine]:
    seen: set[tuple[str, int | None]] = set()
    out: list[OcrLine] = []
    for line in lines:
        key = (_normalize_line(line.text).lower(), line.frame_index)
        if not key[0] or key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def _normalize_line(value: str) -> str:
    return " ".join(value.split())


def _truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip()


def _is_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


__all__ = [
    "NoopOcrAnalyzer",
    "OcrAnalyzer",
    "OcrInput",
    "OcrLine",
    "OcrModelPaths",
    "OcrResult",
    "RapidOcrAnalyzer",
    "ocr_result_metadata",
]
