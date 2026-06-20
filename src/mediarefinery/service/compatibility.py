"""Immich compatibility checks used by service readiness."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypeAlias, cast

import httpx

CompatibilityStatus: TypeAlias = Literal["ok", "unsupported", "fail"]
VersionTuple: TypeAlias = tuple[int, int, int]

SUPPORTED_IMMICH_MIN_VERSION: VersionTuple = (2, 7, 5)
SUPPORTED_IMMICH_MAX_TESTED_VERSION: VersionTuple = (2, 7, 5)
IMMICH_COMPATIBILITY_SMOKE_DATE = "2026-05-11"

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


def check_immich_compatibility(
    client: httpx.Client, *, timeout: float = 2.0
) -> dict[str, Any]:
    """Probe Immich version/about endpoints and return a safe readiness body."""
    version_check, version_body = _request_json(
        client, "/api/server/version", timeout=timeout
    )
    about_check, about_body = _request_json(
        client, "/api/server/about", timeout=timeout, allow_auth_required=True
    )

    version_tuple = _extract_semver(version_body)
    about_tuple = _extract_semver(about_body)
    status, reason = _evaluate_compatibility(
        version_tuple=version_tuple,
        about_tuple=about_tuple,
        endpoint_checks=(version_check, about_check),
    )

    return {
        "status": status,
        "reason": reason,
        "min_version": _format_version(SUPPORTED_IMMICH_MIN_VERSION),
        "max_tested_version": _format_version(SUPPORTED_IMMICH_MAX_TESTED_VERSION),
        "last_live_smoke": IMMICH_COMPATIBILITY_SMOKE_DATE,
        "server_version": _format_version(version_tuple) if version_tuple else None,
        "server_about_version": _format_version(about_tuple) if about_tuple else None,
        "checks": {
            "server_version": version_check,
            "server_about": about_check,
        },
    }


def _request_json(
    client: httpx.Client,
    path: str,
    *,
    timeout: float,
    allow_auth_required: bool = False,
) -> tuple[dict[str, Any], Mapping[str, Any] | None]:
    try:
        response = client.get(path, timeout=timeout)
    except httpx.HTTPError:
        return (
            {
                "status": "fail",
                "http_status": None,
                "detail": "request_failed",
            },
            None,
        )

    check: dict[str, Any] = {
        "status": "ok" if response.status_code < 400 else "fail",
        "http_status": response.status_code,
        "detail": "ok",
    }
    if allow_auth_required and response.status_code in {401, 403}:
        check["status"] = "auth_required"
        check["detail"] = "auth_required"
        return check, None
    if response.status_code >= 500:
        check["detail"] = "server_error"
        return check, None
    if response.status_code >= 400:
        check["detail"] = "unexpected_status"
        return check, None

    try:
        payload = response.json()
    except ValueError:
        check["status"] = "fail"
        check["detail"] = "invalid_json"
        return check, None
    if not isinstance(payload, dict):
        check["status"] = "fail"
        check["detail"] = "invalid_json_shape"
        return check, None
    return check, cast(Mapping[str, Any], payload)


def _evaluate_compatibility(
    *,
    version_tuple: VersionTuple | None,
    about_tuple: VersionTuple | None,
    endpoint_checks: Sequence[Mapping[str, Any]],
) -> tuple[CompatibilityStatus, str]:
    version_check = endpoint_checks[0]
    about_check = endpoint_checks[1]
    if version_check.get("status") != "ok":
        return (
            "fail",
            "Immich compatibility endpoints did not return usable JSON; check "
            "system.immich_base_url in config.db and the /api/server/version shape.",
        )
    if about_check.get("status") not in {"ok", "auth_required"}:
        return (
            "fail",
            "Immich server/about did not return usable JSON or an auth-required "
            "status; check the /api/server/about shape.",
        )

    if version_tuple is None and about_tuple is None:
        return (
            "fail",
            "Immich did not report a parseable semantic version from server/version "
            "or server/about.",
        )

    if version_tuple is not None and about_tuple is not None and version_tuple != about_tuple:
        return (
            "fail",
            "Immich server/version and server/about reported different versions.",
        )

    detected = version_tuple or about_tuple
    assert detected is not None
    detected_text = _format_version(detected)
    min_text = _format_version(SUPPORTED_IMMICH_MIN_VERSION)
    max_text = _format_version(SUPPORTED_IMMICH_MAX_TESTED_VERSION)

    if detected < SUPPORTED_IMMICH_MIN_VERSION:
        return (
            "unsupported",
            f"Immich {detected_text} is older than the minimum tested {min_text}; "
            "upgrade Immich before treating this instance as release-ready.",
        )
    if detected > SUPPORTED_IMMICH_MAX_TESTED_VERSION:
        return (
            "unsupported",
            f"Immich {detected_text} is newer than the maximum tested {max_text}; "
            "run the live Immich smoke and update compatibility docs before release.",
        )
    return (
        "ok",
        f"Immich {detected_text} matches the tested compatibility target.",
    )


def _extract_semver(payload: Mapping[str, Any] | None) -> VersionTuple | None:
    if payload is None:
        return None

    major = _coerce_int(payload.get("major"))
    minor = _coerce_int(payload.get("minor"))
    patch = _coerce_int(payload.get("patch"))
    if major is not None and minor is not None and patch is not None:
        return (major, minor, patch)

    version = payload.get("version")
    if isinstance(version, str):
        return _parse_version_string(version)
    return None


def _parse_version_string(value: str) -> VersionTuple | None:
    match = _VERSION_RE.search(value)
    if match is None:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _format_version(value: VersionTuple) -> str:
    return ".".join(str(part) for part in value)


__all__ = [
    "IMMICH_COMPATIBILITY_SMOKE_DATE",
    "SUPPORTED_IMMICH_MAX_TESTED_VERSION",
    "SUPPORTED_IMMICH_MIN_VERSION",
    "check_immich_compatibility",
]
