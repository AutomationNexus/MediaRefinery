"""Service-level configuration loaded from ``config.db``.

Per-user configuration (categories, policies) lives in ``state.db``.
Deployment-wide knobs (Immich URL, public base URL, trusted proxies,
demo mode, sampling, OCR) live in ``config.db`` under ``system.*``.
The encryption master key is resolved from ``/data/master.key`` in
``service.security`` (not here).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 8080
DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60  # 12h sliding
DEFAULT_REVALIDATE_INTERVAL_SECONDS = 5 * 60  # 5min cap on Immich /users/me hits
DEFAULT_LOGIN_RATE_PER_MIN = 5
DEFAULT_DATA_DIR = Path("/data")
DEFAULT_MEDIA_SAMPLING_MAX_ORIGINAL_BYTES = 250 * 1024 * 1024
DEFAULT_MEDIA_SAMPLING_MAX_DURATION_SECONDS = 300
DEFAULT_MEDIA_SAMPLING_MAX_FRAMES = 3
DEFAULT_MEDIA_SAMPLING_EXTRACTION_TIMEOUT_SECONDS = 60
DEFAULT_MEDIA_SAMPLING_FFMPEG_PATH = "ffmpeg"
DEFAULT_OCR_MAX_INPUTS = 4
DEFAULT_OCR_MAX_TEXT_CHARS = 20_000


@dataclass(frozen=True)
class MediaSamplingConfig:
    """Represent MediaSamplingConfig.

    Attributes
    ----------
    enabled : bool
    max_original_bytes : int
    max_duration_seconds : int
    max_frames : int
    extraction_timeout_seconds : int
    temp_dir : Path | None
    ffmpeg_path : str
    """

    enabled: bool = False
    max_original_bytes: int = DEFAULT_MEDIA_SAMPLING_MAX_ORIGINAL_BYTES
    max_duration_seconds: int = DEFAULT_MEDIA_SAMPLING_MAX_DURATION_SECONDS
    max_frames: int = DEFAULT_MEDIA_SAMPLING_MAX_FRAMES
    extraction_timeout_seconds: int = DEFAULT_MEDIA_SAMPLING_EXTRACTION_TIMEOUT_SECONDS
    temp_dir: Path | None = None
    ffmpeg_path: str = DEFAULT_MEDIA_SAMPLING_FFMPEG_PATH


@dataclass(frozen=True)
class OcrConfig:
    """Represent OcrConfig.

    Attributes
    ----------
    enabled : bool
    max_inputs : int
    max_text_chars : int
    """

    enabled: bool = True
    max_inputs: int = DEFAULT_OCR_MAX_INPUTS
    max_text_chars: int = DEFAULT_OCR_MAX_TEXT_CHARS


@dataclass(frozen=True)
class ServiceConfig:
    """Represent ServiceConfig.

    Attributes
    ----------
    immich_base_url : str
    base_url : str
    data_dir : Path
    trusted_proxies : tuple[str, ...]
    session_ttl_seconds : int
    revalidate_interval_seconds : int
    login_rate_per_min : int
    cookie_secure : bool
    demo_mode : bool
    auto_scan_enabled : bool
    state_db_path_override : Path | None
    media_sampling : MediaSamplingConfig
    ocr : OcrConfig
    """

    immich_base_url: str
    base_url: str
    data_dir: Path
    trusted_proxies: tuple[str, ...]
    session_ttl_seconds: int
    revalidate_interval_seconds: int
    login_rate_per_min: int
    cookie_secure: bool
    demo_mode: bool = False
    auto_scan_enabled: bool = False
    state_db_path_override: Path | None = None
    media_sampling: MediaSamplingConfig = field(default_factory=MediaSamplingConfig)
    ocr: OcrConfig = field(default_factory=OcrConfig)

    @property
    def state_db_path(self) -> Path:
        """State db path.

        Returns
        -------
        Path
        """
        if self.state_db_path_override is not None:
            return self.state_db_path_override
        return self.data_dir / "state.db"

    @property
    def master_key_path(self) -> Path:
        """Master key path.

        Returns
        -------
        Path
        """
        return self.data_dir / "master.key"


def load_service_config(
    *,
    data_dir: Path | None = None,
    state_db_path_override: Path | None = None,
) -> ServiceConfig:
    """Load deployment settings from config.db (not operator environment variables)."""
    from mediarefinery.settings.load import load_system_config

    return load_system_config(
        data_dir,
        state_db_path_override=state_db_path_override,
    )


__all__ = [
    "DEFAULT_OCR_MAX_INPUTS",
    "DEFAULT_OCR_MAX_TEXT_CHARS",
    "DEFAULT_MEDIA_SAMPLING_EXTRACTION_TIMEOUT_SECONDS",
    "DEFAULT_MEDIA_SAMPLING_FFMPEG_PATH",
    "DEFAULT_MEDIA_SAMPLING_MAX_DURATION_SECONDS",
    "DEFAULT_MEDIA_SAMPLING_MAX_FRAMES",
    "DEFAULT_MEDIA_SAMPLING_MAX_ORIGINAL_BYTES",
    "DEFAULT_LOGIN_RATE_PER_MIN",
    "DEFAULT_REVALIDATE_INTERVAL_SECONDS",
    "DEFAULT_SESSION_TTL_SECONDS",
    "MediaSamplingConfig",
    "OcrConfig",
    "ServiceConfig",
    "load_service_config",
]
