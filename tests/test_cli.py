from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from mediarefinery import cli
from mediarefinery.config import load_config


def test_module_help_lists_expected_commands() -> None:
    """Test module help lists expected commands."""
    result = subprocess.run(
        [sys.executable, "-m", "mediarefinery", "--help"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "config validate" in result.stdout
    assert "config" in result.stdout
    assert "doctor" in result.stdout
    assert "report" in result.stdout
    assert "scan" in result.stdout


@pytest.mark.parametrize(
    "config_path",
    [
        "templates/config.example.yml",
        "templates/config.preset.sensitive.example.yml",
        "tests/fixtures/config.custom-taxonomy.yml",
    ],
)
def test_config_validate_command_accepts_valid_configs(config_path: str) -> None:
    """Test config validate command accepts valid configs."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mediarefinery",
            "config",
            "validate",
            "--config",
            config_path,
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Config valid" in result.stdout


def test_doctor_checks_ffmpeg_when_video_is_enabled(tmp_path) -> None:
    """Test doctor checks ffmpeg when video is enabled."""
    data = yaml.safe_load(Path("templates/config.example.yml").read_text())
    data["scanner"]["media_types"] = ["video"]
    data["state"]["sqlite_path"] = str(tmp_path / "state.sqlite3")
    data["runtime"]["temp_dir"] = str(tmp_path / "frames")
    data["video"]["enabled"] = True
    data["video"]["ffmpeg_path"] = "definitely-missing-mediarefinery-ffmpeg"
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mediarefinery",
            "doctor",
            "--config",
            str(config_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Doctor: ffmpeg failed" in result.stderr
    assert "definitely-missing-mediarefinery-ffmpeg" not in result.stderr


def test_doctor_reports_invalid_config(tmp_path) -> None:
    """Test doctor reports invalid config."""
    config_path = tmp_path / "config.yml"
    config_path.write_text("categories: [", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mediarefinery",
            "doctor",
            "--config",
            str(config_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Doctor: config failed" in result.stderr
    assert "invalid YAML" in result.stderr


def test_scan_command_returns_partial_failure_exit_code(tmp_path) -> None:
    """Test scan command returns partial failure exit code."""
    data = yaml.safe_load(Path("templates/config.example.yml").read_text())
    data["state"]["sqlite_path"] = str(tmp_path / "state.sqlite3")
    data["runtime"]["temp_dir"] = str(tmp_path / "tmp")
    del data["policies"]["needs_review"]["image"]
    config_path = tmp_path / "partial.yml"
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mediarefinery",
            "scan",
            "--config",
            str(config_path),
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 4
    assert "Errors: 1" in result.stdout
    assert "event=asset.error" in result.stderr
    assert "error_code=missing_policy" in result.stderr


def test_scan_immich_http_requires_api_key_env_without_reporting_value(tmp_path) -> None:
    """Test scan immich http requires api key env without reporting value."""
    data = yaml.safe_load(Path("templates/config.example.yml").read_text())
    data["state"]["sqlite_path"] = str(tmp_path / "state.sqlite3")
    data["runtime"]["temp_dir"] = str(tmp_path / "tmp")
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    env = os.environ.copy()
    env.pop("IMMICH_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mediarefinery",
            "scan",
            "--config",
            str(config_path),
            "--immich-http",
            "--dry-run",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert result.returncode == 1
    assert "IMMICH_API_KEY" in result.stderr
    assert "replace_me" not in result.stderr


def test_report_path_helpers_cover_optional_branches(tmp_path) -> None:
    """Test report path helpers cover optional branches."""
    args = argparse.Namespace(state_path=None, output=str(tmp_path / "report.md"))
    assert cli._report_state_path(args, None) == "state.sqlite3"
    assert cli._report_output_path(args, None, 7) == tmp_path / "report.md"

    args.output = None
    assert cli._report_output_path(args, None, 7) is None

    config = load_config("templates/config.example.yml")
    config.raw["reports"]["enabled"] = False
    assert cli._report_output_path(args, config, 7) is None

    config.raw["reports"] = {"enabled": True, "output_dir": ""}
    assert cli._report_output_path(args, config, 7) is None
