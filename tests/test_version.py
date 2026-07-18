from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version
from unittest.mock import patch

import mediarefinery


def test_version_matches_installed_package_metadata():
    assert mediarefinery.__version__ == version("mediarefinery")


def test_version_falls_back_when_package_metadata_is_missing():
    try:
        with patch("importlib.metadata.version", side_effect=PackageNotFoundError):
            importlib.reload(mediarefinery)
        assert mediarefinery.__version__ == "0.0.0+dev"
    finally:
        importlib.reload(mediarefinery)
