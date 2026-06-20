from __future__ import annotations

import copy
from pathlib import Path

import pytest

from mediarefinery.config import (
    ALLOWED_ACTIONS,
    CONFIG_SCHEMA_VERSION,
    DESTRUCTIVE_ACTIONS,
    ConfigError,
    discover_config_path,
    load_config,
    validate_config_data,
)


def _example_config() -> dict:
    return copy.deepcopy(load_config("templates/config.example.yml").raw)


def test_generic_example_config_is_valid() -> None:
    """Test generic example config is valid."""
    config = load_config("templates/config.example.yml")

    assert config.raw["version"] == CONFIG_SCHEMA_VERSION
    assert config.active_profile_name == "default"
    assert "needs_review" in config.category_ids


def test_config_version_one_is_the_current_yaml_contract() -> None:
    """Test config version one is the current yaml contract."""
    data = _example_config()

    config = validate_config_data(data)

    assert config.raw["version"] == 1


def test_discover_config_path_uses_example_then_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test discover config path uses example then default."""
    assert discover_config_path().as_posix().endswith("templates/config.example.yml")

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MEDIAREFINERY_CONFIG", raising=False)

    assert discover_config_path() == Path("config.yml")


@pytest.mark.parametrize("bad_version", [None, "1", True, 2])
def test_config_version_must_be_supported_yaml_schema(bad_version: object) -> None:
    """Test config version must be supported yaml schema."""
    data = _example_config()
    if bad_version is None:
        del data["version"]
    else:
        data["version"] = bad_version

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    errors = "\n".join(exc_info.value.errors)
    assert "version:" in errors
    assert "YAML config schema" in errors or "version 1" in errors


def test_sensitive_preset_example_config_is_valid() -> None:
    """Test sensitive preset example config is valid."""
    config = load_config("templates/config.preset.sensitive.example.yml")

    assert config.raw["preset"] == "sensitive-content-review"
    assert "explicit" in config.category_ids


def test_custom_non_sensitive_taxonomy_fixture_is_valid() -> None:
    """Test custom non sensitive taxonomy fixture is valid."""
    config = load_config("tests/fixtures/config.custom-taxonomy.yml")

    assert config.active_profile_name == "document_sorter"
    assert {"receipt", "screenshot", "document", "other"} == config.category_ids
    assert config.policies["document"]["image"]["on_match"] == ["archive"]


def test_validation_collects_mapping_duplicate_and_url_errors() -> None:
    """Test validation collects mapping duplicate and url errors."""
    data = _example_config()
    data["categories"].append({"id": "ok"})
    data["classifier_profiles"]["default"]["output_mapping"]["orphan"] = "missing"
    data["integration"]["immich"]["url"] = "not-a-url"

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    errors = "\n".join(exc_info.value.errors)
    assert "duplicate category id 'ok'" in errors
    assert "unknown category id 'missing'" in errors
    assert "integration.immich.url" in errors


def test_category_id_format_is_rejected() -> None:
    """Test category id format is rejected."""
    data = _example_config()
    data["categories"][0]["id"] = "Needs Review"

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert "categories[0].id: must match" in "\n".join(exc_info.value.errors)


def test_classifier_profile_selection_must_exist() -> None:
    """Test classifier profile selection must exist."""
    data = _example_config()
    data["classifier"]["profile"] = "missing_profile"

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert "classifier.profile: unknown profile 'missing_profile'" in "\n".join(
        exc_info.value.errors
    )


def test_unknown_policy_category_is_rejected() -> None:
    """Test unknown policy category is rejected."""
    data = _example_config()
    data["policies"]["not_a_category"] = {"image": {"on_match": ["no_action"]}}

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert "policies.not_a_category: unknown category id 'not_a_category'" in "\n".join(
        exc_info.value.errors
    )


def test_unknown_policy_action_is_rejected() -> None:
    """Test unknown policy action is rejected."""
    data = _example_config()
    data["policies"]["needs_review"]["image"]["on_match"] = ["email_operator"]

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert "unknown action 'email_operator'" in "\n".join(exc_info.value.errors)


def test_supported_action_registry_excludes_destructive_actions() -> None:
    """Test supported action registry excludes destructive actions."""
    assert ALLOWED_ACTIONS.isdisjoint(DESTRUCTIVE_ACTIONS)
    assert "delete" not in ALLOWED_ACTIONS
    assert "trash" not in ALLOWED_ACTIONS


def test_policy_media_type_must_be_supported() -> None:
    """Test policy media type must be supported."""
    data = _example_config()
    data["policies"]["needs_review"]["audio"] = {"on_match": ["manual_review"]}

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert "policies.needs_review.audio: media type must be one of image, video" in (
        "\n".join(exc_info.value.errors)
    )


def test_scanner_filter_shape_is_validated() -> None:
    """Test scanner filter shape is validated."""
    data = _example_config()
    data["scanner"]["mode"] = "everything"
    data["scanner"]["since"] = "not-a-date"
    data["scanner"]["include_albums"] = ["family", ""]
    data["scanner"]["exclude_albums"] = "archive"
    data["scanner"]["include_archived"] = "false"

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    errors = "\n".join(exc_info.value.errors)
    assert "scanner.mode: must be one of" in errors
    assert "scanner.since: must be an ISO8601 string or null" in errors
    assert "scanner.include_albums[1]: must be a non-empty string" in errors
    assert "scanner.exclude_albums: must be a list" in errors
    assert "scanner.include_archived: must be true or false" in errors


def test_scanner_mode_specific_requirements_are_validated() -> None:
    """Test scanner mode specific requirements are validated."""
    data = _example_config()
    data["scanner"]["mode"] = "album"

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert "scanner.include_albums: album mode requires at least one album" in (
        "\n".join(exc_info.value.errors)
    )

    data = _example_config()
    data["scanner"]["mode"] = "date_range"
    data["scanner"]["since"] = None

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert "scanner.since: date_range mode requires since" in "\n".join(
        exc_info.value.errors
    )


@pytest.mark.parametrize(
    "action",
    ["delete", "trash", "remove", "move-to-trash", "DELETE"],
)
def test_destructive_actions_are_rejected(action: str) -> None:
    """Test destructive actions are rejected."""
    data = _example_config()
    data["policies"]["needs_review"]["image"]["on_match"] = [action]

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert f"destructive action '{action}' is not supported" in "\n".join(
        exc_info.value.errors
    )


def test_archive_requires_explicit_enablement() -> None:
    """Test archive requires explicit enablement."""
    data = _example_config()
    data["policies"]["needs_review"]["image"]["on_match"] = ["archive"]

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert "archive requires actions.archive_enabled=true" in "\n".join(
        exc_info.value.errors
    )


def test_archive_is_valid_when_explicitly_enabled() -> None:
    """Test archive is valid when explicitly enabled."""
    data = _example_config()
    data["actions"]["archive_enabled"] = True
    data["policies"]["needs_review"]["image"]["on_match"] = ["archive"]

    config = validate_config_data(data)

    assert config.policies["needs_review"]["image"]["on_match"] == ["archive"]


def test_archive_enabled_must_be_boolean() -> None:
    """Test archive enabled must be boolean."""
    data = _example_config()
    data["actions"]["archive_enabled"] = "true"
    data["policies"]["needs_review"]["image"]["on_match"] = ["archive"]

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    errors = "\n".join(exc_info.value.errors)
    assert "actions.archive_enabled: must be true or false" in errors
    assert "archive requires actions.archive_enabled=true" in errors


def test_never_delete_cannot_be_disabled() -> None:
    """Test never delete cannot be disabled."""
    data = _example_config()
    data["actions"]["never_delete"] = False

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    assert "actions.never_delete: must be true" in "\n".join(exc_info.value.errors)


def test_video_config_shape_is_validated() -> None:
    """Test video config shape is validated."""
    data = _example_config()
    data["video"] = {
        "enabled": "yes",
        "frame_count": 0,
        "max_frames": False,
        "frame_strategy": "scene",
        "max_duration_seconds": False,
        "max_original_bytes": 0,
        "extraction_timeout_seconds": "soon",
        "ffmpeg_path": "",
    }

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    errors = "\n".join(exc_info.value.errors)
    assert "video.enabled: must be true or false" in errors
    assert "video.frame_count: must be a positive integer" in errors
    assert "video.max_frames: must be a positive integer" in errors
    assert "video.frame_strategy: must be one of uniform" in errors
    assert "video.max_duration_seconds: must be a positive integer" in errors
    assert "video.max_original_bytes: must be a positive integer" in errors
    assert "video.extraction_timeout_seconds: must be a positive integer" in errors
    assert "video.ffmpeg_path: must be a non-empty string" in errors


def test_video_mean_aggregation_is_valid_when_explicit() -> None:
    """Test video mean aggregation is valid when explicit."""
    data = _example_config()
    data["classifier_profiles"]["default"]["video_aggregation"] = "mean"

    config = validate_config_data(data)

    assert config.active_profile.video_aggregation == "mean"


def test_onnx_profile_options_are_validated() -> None:
    """Test onnx profile options are validated."""
    data = _example_config()
    data["classifier_profiles"]["default"].update(
        {
            "backend": "onnx",
            "model_path": "/models/operator-provided.onnx",
            "model_version": "operator-model",
            "input_size": 128,
            "input_mean": [0.1, 0.2, 0.3],
            "input_std": [0.9, 0.8, 0.7],
            "input_name": "pixels",
            "output_name": "scores",
        }
    )

    config = validate_config_data(data)

    profile = config.active_profile
    assert profile.backend == "onnx"
    assert profile.model_path == "/models/operator-provided.onnx"
    assert profile.model_version == "operator-model"
    assert profile.input_size == 128
    assert profile.input_mean == (0.1, 0.2, 0.3)
    assert profile.input_std == (0.9, 0.8, 0.7)
    assert profile.input_name == "pixels"
    assert profile.output_name == "scores"


def test_onnx_profile_rejects_bad_preprocessing_options() -> None:
    """Test onnx profile rejects bad preprocessing options."""
    data = _example_config()
    data["classifier_profiles"]["default"].update(
        {
            "backend": "onnx",
            "model_path": "",
            "model_version": "",
            "input_size": 0,
            "input_mean": [0.1, "bad", 0.3],
            "input_std": [1.0, 0.0, 1.0],
            "input_name": "",
            "output_name": "",
        }
    )

    with pytest.raises(ConfigError) as exc_info:
        validate_config_data(data)

    errors = "\n".join(exc_info.value.errors)
    assert "model_path: onnx backend requires" in errors
    assert "model_version: must be a non-empty string" in errors
    assert "input_size: must be a positive integer" in errors
    assert "input_mean[1]: must be a number" in errors
    assert "input_std[1]: must be greater than zero" in errors
    assert "input_name: must be a non-empty string" in errors
    assert "output_name: must be a non-empty string" in errors
