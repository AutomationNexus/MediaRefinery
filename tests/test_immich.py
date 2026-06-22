from __future__ import annotations

import json
from datetime import UTC
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mediarefinery.config import load_config
from mediarefinery.immich import (
    SYNTHETIC_IMAGE_PREVIEW_BYTES,
    AssetRef,
    HttpImmichClient,
    ImmichCapabilities,
    ImmichClient,
    ImmichClientConfigurationError,
    ImmichClientError,
    MockImmichClient,
    _album_names_from_response,
    _http_error,
    _immich_asset_type_filter,
    _mapping_or_empty,
    _media_type_from_response,
    _next_page_token,
    _optional_float,
    _optional_string,
    _parse_immich_datetime,
    _positive_float,
    _positive_int,
    _safe_asset_metadata,
    _search_assets_page,
    _should_retry,
    _unlink_if_exists,
    create_http_immich_client,
    mock_assets,
)


def test_mock_immich_lists_assets_with_pagination() -> None:
    """Test mock immich lists assets with pagination."""
    client = MockImmichClient(
        [
            AssetRef("a", "image"),
            AssetRef("b", "image"),
            AssetRef("c", "video"),
        ]
    )

    first, token = client.list_assets(page_size=2)
    second, next_token = client.list_assets(page_token=token, page_size=2)

    assert [asset.asset_id for asset in first] == ["a", "b"]
    assert [asset.asset_id for asset in second] == ["c"]
    assert next_token is None


def test_mock_immich_filters_by_media_type() -> None:
    """Test mock immich filters by media type."""
    client = MockImmichClient(
        [
            AssetRef("a", "image"),
            AssetRef("b", "video"),
        ]
    )

    assets, token = client.list_assets(media_types={"video"})

    assert [asset.asset_id for asset in assets] == ["b"]
    assert token is None


def test_mock_immich_smart_search_ranks_metadata_matches() -> None:
    """Test mock immich smart search ranks metadata matches."""
    client = MockImmichClient(
        [
            AssetRef("snow", "image", metadata={"city": "Alps"}),
            AssetRef("beach", "image", albums=("vacation",)),
            AssetRef("snow-trip", "image", metadata={"description": "snow vacation"}),
        ]
    )

    assets, token = client.smart_search("snow vacation", page_size=2)

    assert [asset.asset_id for asset in assets] == ["snow-trip", "beach"]
    assert token == "2"
    assert client.smart_search_requests == [
        {
            "query": "snow vacation",
            "page_token": None,
            "page_size": 2,
            "media_types": None,
        }
    ]


def test_mock_immich_can_find_create_and_add_to_review_album() -> None:
    """Test mock immich can find create and add to review album."""
    client = MockImmichClient(
        [
            AssetRef("a", "image"),
            AssetRef("b", "image"),
        ]
    )

    assert client.find_album_by_name("Review") is None
    album_id = client.create_or_get_album("Review")
    repeated_album_id = client.create_or_get_album("Review")
    client.add_to_album(album_id, ["a", "b"])

    assert repeated_album_id == album_id
    assert client.find_album_by_name("Review") == album_id
    assert client.album_assets("Review") == ("a", "b")
    assert client.album_create_requests == ["Review"]
    assert client.add_to_album_requests == [
        {"album_id": album_id, "asset_ids": ["a", "b"]}
    ]


def test_mock_immich_can_find_create_and_add_tag() -> None:
    """Test mock immich can find create and add tag."""
    client = MockImmichClient(
        [
            AssetRef("a", "image"),
            AssetRef("b", "image"),
        ],
        capabilities=ImmichCapabilities(tags=True),
    )

    assert client.find_tag_by_name("review") is None
    tag_id = client.create_or_get_tag("review")
    repeated_tag_id = client.create_or_get_tag("review")
    client.add_tag_to_asset("a", tag_id)

    assert repeated_tag_id == tag_id
    assert client.find_tag_by_name("review") == tag_id
    assert client.asset_tags("a") == (tag_id,)
    assert client.tag_create_requests == ["review"]
    assert client.add_tag_requests == [{"asset_id": "a", "tag_id": tag_id}]


def test_default_mock_assets_cover_sprint_003_metadata() -> None:
    """Test default mock assets cover sprint 003 metadata."""
    assets = mock_assets()

    assert {asset.media_type for asset in assets} == {"image", "video"}
    assert any(asset.archived for asset in assets)
    assert any(asset.favorite for asset in assets)
    assert any(asset.albums for asset in assets)
    assert all(asset.created_at is not None for asset in assets)


def test_default_mock_immich_pages_are_deterministic() -> None:
    """Test default mock immich pages are deterministic."""
    client = MockImmichClient()

    first, token = client.list_assets(page_size=2)
    second, token = client.list_assets(page_token=token, page_size=2)
    third, token = client.list_assets(page_token=token, page_size=2)

    assert [asset.asset_id for asset in first] == [
        "mock-image-001",
        "mock-image-002",
    ]
    assert [asset.asset_id for asset in second] == [
        "mock-image-003",
        "mock-video-001",
    ]
    assert [asset.asset_id for asset in third] == [
        "mock-image-archived-001",
        "mock-video-favorite-001",
    ]
    assert token is None


def test_mock_immich_returns_synthetic_preview_bytes() -> None:
    """Test mock immich returns synthetic preview bytes."""
    client = MockImmichClient([AssetRef("a", "image")])

    preview_bytes = client.get_preview_bytes("a")

    assert preview_bytes == SYNTHETIC_IMAGE_PREVIEW_BYTES
    assert preview_bytes.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.preview_requests == ["a"]


def test_mock_immich_can_override_preview_bytes_per_asset() -> None:
    """Test mock immich can override preview bytes per asset."""
    client = MockImmichClient(
        [AssetRef("a", "image"), AssetRef("b", "image")],
        preview_bytes_by_asset_id={"a": b"", "b": b"not-image"},
    )

    assert client.get_preview_bytes("a") == b""
    assert client.get_preview_bytes("b") == b"not-image"


def test_http_immich_server_probes_use_auth_only_when_needed() -> None:
    """Test http immich server probes use auth only when needed."""
    transport = _FakeTransport(
        [
            (200, {"res": "pong"}),
            (200, {"major": 2, "minor": 7, "patch": 5}),
            (200, {"version": "2.7.5", "licensed": True, "versionUrl": ""}),
            (200, {"search": True}),
        ]
    )
    client = _http_client(transport)

    assert client.ping_server()["res"] == "pong"
    assert client.server_version()["major"] == 2
    assert client.about()["version"] == "2.7.5"
    assert client.features()["search"] is True

    assert _path(transport.requests[0]) == "/api/server/ping"
    assert _header(transport.requests[0], "x-api-key") is None
    assert _path(transport.requests[1]) == "/api/server/version"
    assert _header(transport.requests[1], "x-api-key") is None
    assert _path(transport.requests[2]) == "/api/server/about"
    assert _header(transport.requests[2], "x-api-key") == "test-secret"
    assert _path(transport.requests[3]) == "/api/server/features"
    assert _header(transport.requests[3], "x-api-key") is None


def test_http_immich_lists_assets_with_search_metadata() -> None:
    """Test http immich lists assets with search metadata."""
    transport = _FakeTransport(
        [
            (
                200,
                {
                    "albums": {},
                    "assets": {
                        "count": 1,
                        "facets": [],
                        "items": [
                            {
                                "id": "asset-1",
                                "type": "IMAGE",
                                "checksum": "sha1-base64",
                                "isArchived": False,
                                "isFavorite": True,
                                "fileCreatedAt": "2026-04-01T10:00:00.000Z",
                                "updatedAt": "2026-04-02T11:00:00.000Z",
                                "visibility": "timeline",
                                "originalMimeType": "image/jpeg",
                                "originalPath": "/private/path/not-stored.jpg",
                            }
                        ],
                        "nextPage": 2,
                        "total": 3,
                    },
                },
            ),
        ]
    )
    client = _http_client(transport)

    assets, token = client.list_assets(page_size=50, media_types={"image"})

    request = transport.requests[0]
    body = _json_body(request)
    assert request.method == "POST"
    assert _path(request) == "/api/search/metadata"
    assert _query(request) == {}
    assert _header(request, "x-api-key") == "test-secret"
    assert "test-secret" not in str(request.url)
    assert body["page"] == 1
    assert body["size"] == 50
    assert body["type"] == "IMAGE"
    assert body["withDeleted"] is False
    assert body["withExif"] is True
    assert body["withPeople"] is True
    assert body["withStacked"] is True
    assert len(assets) == 1
    assert assets[0].asset_id == "asset-1"
    assert assets[0].media_type == "image"
    assert assets[0].checksum == "sha1-base64"
    assert assets[0].favorite is True
    assert assets[0].archived is False
    assert assets[0].metadata == {
        "mime_type": "image/jpeg",
        "visibility": "timeline",
    }
    assert token == "2"


def test_http_immich_safely_flattens_rich_asset_metadata() -> None:
    """Test http immich safely flattens rich asset metadata."""
    transport = _FakeTransport(
        [
            (
                200,
                {
                    "albums": {},
                    "assets": {
                        "items": [
                            {
                                "id": "asset-1",
                                "type": "IMAGE",
                                "checksum": "sha1-base64",
                                "originalMimeType": "image/jpeg",
                                "originalFileName": "receipt.jpg",
                                "duplicateId": "dup-1",
                                "people": [
                                    {"id": "p1", "name": "Alice", "thumbnailPath": "/x"}
                                ],
                                "tags": [{"id": "t1", "value": "family"}],
                                "smartInfo": {
                                    "objects": ["person", "snow"],
                                    "tags": ["vacation"],
                                    "text": "receipt total tax",
                                },
                                "exifInfo": {
                                    "exifImageWidth": 1024,
                                    "exifImageHeight": 768,
                                    "fileSizeInByte": 12345,
                                    "city": "Berlin",
                                    "country": "Germany",
                                },
                            }
                        ],
                        "nextPage": None,
                    },
                },
            ),
        ]
    )
    client = _http_client(transport)

    assets, _ = client.list_assets(page_size=1)

    metadata = assets[0].metadata
    assert metadata["filename"] == "receipt.jpg"
    assert metadata["duplicate_id"] == "dup-1"
    assert metadata["exif_image_width"] == "1024"
    assert metadata["city"] == "Berlin"
    assert "Alice" in metadata["people_json"]
    assert "thumbnailPath" not in metadata["people_json"]
    assert "receipt total tax" == metadata["smart_text"]


def test_http_immich_smart_search_uses_documented_endpoint() -> None:
    """Test http immich smart search uses documented endpoint."""
    transport = _FakeTransport(
        [
            (
                200,
                {
                    "assets": {
                        "items": [
                            {
                                "id": "asset-1",
                                "type": "IMAGE",
                                "checksum": "sha1-base64",
                            }
                        ],
                        "nextPage": None,
                    },
                },
            ),
        ]
    )
    client = _http_client(transport)

    assets, token = client.smart_search("vacation in snow", page_size=25)

    request = transport.requests[0]
    body = _json_body(request)
    assert request.method == "POST"
    assert _path(request) == "/api/search/smart"
    assert _header(request, "x-api-key") == "test-secret"
    assert body["query"] == "vacation in snow"
    assert body["page"] == 1
    assert body["size"] == 25
    assert body["withDeleted"] is False
    assert body["withExif"] is True
    assert body["withPeople"] is True
    assert body["withStacked"] is True
    assert [asset.asset_id for asset in assets] == ["asset-1"]
    assert token is None


def test_http_immich_gets_metadata_through_search_without_private_paths() -> None:
    """Test http immich gets metadata through search without private paths."""
    transport = _FakeTransport(
        [
            (
                200,
                {
                    "albums": {},
                    "assets": {
                        "count": 1,
                        "facets": [],
                        "items": [
                            {
                                "id": "asset-1",
                                "type": "VIDEO",
                                "checksum": "sha1-video",
                                "duration": "0:00:03.000000",
                                "isArchived": True,
                                "isFavorite": False,
                                "createdAt": "2026-04-01T10:00:00.000Z",
                                "updatedAt": "2026-04-02T11:00:00.000Z",
                                "visibility": "archive",
                                "originalPath": "/private/path/not-stored.mp4",
                            }
                        ],
                        "nextPage": None,
                        "total": 1,
                    },
                },
            ),
        ]
    )
    client = _http_client(transport)

    metadata = client.get_metadata("asset-1")

    assert _path(transport.requests[0]) == "/api/search/metadata"
    assert _json_body(transport.requests[0])["id"] == "asset-1"
    assert metadata["asset_id"] == "asset-1"
    assert metadata["media_type"] == "video"
    assert metadata["metadata"] == {
        "duration": "0:00:03.000000",
        "visibility": "archive",
    }
    assert "/private/path" not in json.dumps(metadata)


def test_http_immich_downloads_preview_thumbnail_bytes() -> None:
    """Test http immich downloads preview thumbnail bytes."""
    transport = _FakeTransport([(200, b"preview-bytes")])
    client = _http_client(transport)

    preview_bytes = client.get_preview_bytes("asset-1")

    request = transport.requests[0]
    assert preview_bytes == b"preview-bytes"
    assert request.method == "GET"
    assert _path(request) == "/api/assets/asset-1/thumbnail"
    assert _query(request) == {"size": ["preview"]}
    assert _header(request, "x-api-key") == "test-secret"
    assert "test-secret" not in str(request.url)


def test_http_immich_downloads_original_asset_bytes() -> None:
    """Test http immich downloads original asset bytes."""
    transport = _FakeTransport([(200, b"original-bytes")])
    client = _http_client(transport)

    original_bytes = client.download_asset_bytes("asset-1")

    request = transport.requests[0]
    assert original_bytes == b"original-bytes"
    assert request.method == "GET"
    assert _path(request) == "/api/assets/asset-1/original"
    assert _header(request, "x-api-key") == "test-secret"


def test_http_immich_streams_original_asset_to_file(tmp_path) -> None:
    """Test http immich streams original asset to file."""
    transport = _FakeTransport([(200, b"original-bytes")])
    client = _http_client(transport)
    destination = tmp_path / "original.media"

    written = client.download_asset_to_file("asset-1", destination, max_bytes=100)

    request = transport.requests[0]
    assert written == len(b"original-bytes")
    assert destination.read_bytes() == b"original-bytes"
    assert request.method == "GET"
    assert _path(request) == "/api/assets/asset-1/original"
    assert _header(request, "x-api-key") == "test-secret"


def test_http_immich_original_file_download_enforces_byte_limit(tmp_path) -> None:
    """Test http immich original file download enforces byte limit."""
    transport = _FakeTransport([(200, b"too-large")])
    client = _http_client(transport)
    destination = tmp_path / "original.media"

    with pytest.raises(ImmichClientError) as exc_info:
        client.download_asset_to_file("asset-1", destination, max_bytes=3)

    assert exc_info.value.error_code == "original_too_large"
    assert not destination.exists()


def test_http_immich_finds_creates_and_adds_to_review_album() -> None:
    """Test http immich finds creates and adds to review album."""
    transport = _FakeTransport(
        [
            (
                200,
                [
                    {"id": "album-1", "albumName": "Review"},
                    {"id": "album-ignored", "albumName": "Other"},
                ],
            ),
            (201, {"id": "album-2", "albumName": "New Review"}),
            (
                200,
                [
                    {"id": "asset-1", "success": True},
                    {"id": "asset-2", "success": False, "error": "duplicate"},
                ],
            ),
        ]
    )
    client = _http_client(transport)

    assert client.find_album_by_name("Review") == "album-1"
    assert client.create_album("New Review") == "album-2"
    client.add_to_album("album-2", ["asset-1", "asset-2"])

    assert [request.method for request in transport.requests] == [
        "GET",
        "POST",
        "PUT",
    ]
    assert [_path(request) for request in transport.requests] == [
        "/api/albums",
        "/api/albums",
        "/api/albums/album-2/assets",
    ]
    assert _json_body(transport.requests[1]) == {"albumName": "New Review"}
    assert _json_body(transport.requests[2]) == {"ids": ["asset-1", "asset-2"]}


def test_http_immich_add_to_album_failure_is_sanitized() -> None:
    """Test http immich add to album failure is sanitized."""
    transport = _FakeTransport(
        [(200, [{"id": "asset-1", "success": False, "error": "no_permission"}])]
    )
    client = _http_client(transport)

    with pytest.raises(ImmichClientError) as exc_info:
        client.add_to_album("album-1", ["asset-1"])

    assert exc_info.value.error_code == "album_add_failed"
    assert "no_permission" not in str(exc_info.value)


def test_http_immich_finds_creates_and_adds_tag() -> None:
    """Test http immich finds creates and adds tag."""
    transport = _FakeTransport(
        [
            (
                200,
                [
                    {"id": "tag-1", "name": "review", "value": "review"},
                    {"id": "tag-ignored", "name": "Other", "value": "Other"},
                ],
            ),
            (201, {"id": "tag-2", "name": "New Review", "value": "New Review"}),
            (200, [{"id": "asset-1", "success": False, "error": "duplicate"}]),
        ]
    )
    client = _http_client(transport)

    assert client.find_tag_by_name("review") == "tag-1"
    assert client.create_tag("New Review") == "tag-2"
    client.add_tag_to_asset("asset-1", "tag-2")

    assert [request.method for request in transport.requests] == [
        "GET",
        "POST",
        "PUT",
    ]
    assert [_path(request) for request in transport.requests] == [
        "/api/tags",
        "/api/tags",
        "/api/tags/tag-2/assets",
    ]
    assert _json_body(transport.requests[1]) == {"name": "New Review"}
    assert _json_body(transport.requests[2]) == {"ids": ["asset-1"]}
    assert all(
        _header(request, "x-api-key") == "test-secret"
        for request in transport.requests
    )
    assert all("test-secret" not in str(request.url) for request in transport.requests)


def test_http_immich_create_or_get_tag_reuses_existing_value() -> None:
    """Test http immich create or get tag reuses existing value."""
    transport = _FakeTransport(
        [
            (
                200,
                [
                    {
                        "id": "tag-1",
                        "name": "review",
                        "value": "parent/review",
                    }
                ],
            ),
        ]
    )
    client = _http_client(transport)

    assert client.create_or_get_tag("parent/review") == "tag-1"

    assert len(transport.requests) == 1
    assert transport.requests[0].method == "GET"
    assert _path(transport.requests[0]) == "/api/tags"


def test_http_immich_add_tag_failure_is_sanitized() -> None:
    """Test http immich add tag failure is sanitized."""
    transport = _FakeTransport(
        [
            (
                200,
                [
                    {
                        "id": "asset-1",
                        "success": False,
                        "error": "no_permission",
                        "errorMessage": "api_key=leak-marker-value",
                    }
                ],
            )
        ]
    )
    client = _http_client(transport)

    with pytest.raises(ImmichClientError) as exc_info:
        client.add_tag_to_asset("asset-1", "tag-1")

    assert exc_info.value.error_code == "tag_add_failed"
    assert "no_permission" not in str(exc_info.value)
    assert "leak-marker-value" not in str(exc_info.value)


def test_http_immich_tag_invalid_responses_fail_closed() -> None:
    """Test http immich tag invalid responses fail closed."""
    find_transport = _FakeTransport([(200, {"tags": []})])
    create_transport = _FakeTransport([(201, {"name": "review"})])
    add_transport = _FakeTransport([(200, {"id": "asset-1", "success": True})])

    with pytest.raises(ImmichClientError) as find_exc:
        _http_client(find_transport).find_tag_by_name("review")
    with pytest.raises(ImmichClientError) as create_exc:
        _http_client(create_transport).create_tag("review")
    with pytest.raises(ImmichClientError) as add_exc:
        _http_client(add_transport).add_tag_to_asset("asset-1", "tag-1")

    assert find_exc.value.error_code == "invalid_tag_response"
    assert create_exc.value.error_code == "invalid_tag_response"
    assert add_exc.value.error_code == "invalid_tag_response"


def test_http_immich_real_adapter_supports_tags_and_keeps_archive_unsupported() -> None:
    """Test http immich real adapter supports tags and keeps archive unsupported."""
    transport = _FakeTransport([])
    client = _http_client(transport)

    assert client.capabilities.albums is True
    assert client.capabilities.tags is True
    assert client.capabilities.archive is False
    with pytest.raises(NotImplementedError):
        client.archive_asset("asset-1")
    assert transport.requests == []


def test_create_http_immich_client_uses_env_var_name_without_reporting_value() -> None:
    """Test create http immich client uses env var name without reporting value."""
    config = load_config("templates/config.example.yml")

    client = create_http_immich_client(
        config,
        environ={"IMMICH_API_KEY": "test-secret"},
    )

    assert isinstance(client, HttpImmichClient)
    with pytest.raises(ImmichClientConfigurationError) as exc_info:
        create_http_immich_client(config, environ={})
    assert "IMMICH_API_KEY" in str(exc_info.value)
    assert "test-secret" not in str(exc_info.value)


def test_immich_client_surface_has_no_delete_or_trash_methods() -> None:
    """Test immich client surface has no delete or trash methods."""
    method_names = (
        set(dir(ImmichClient))
        | set(dir(MockImmichClient))
        | set(dir(HttpImmichClient))
    )

    assert not {
        name
        for name in method_names
        if "delete" in name.lower() or "trash" in name.lower()
    }


def test_http_immich_edge_responses_and_helpers(tmp_path) -> None:
    """Test http immich edge responses and helpers."""
    assert str(ImmichClientError("failed", status_code=418)).endswith("(status 418)")
    with pytest.raises(ImmichClientConfigurationError):
        HttpImmichClient(base_url="", api_key="key")
    with pytest.raises(ImmichClientConfigurationError):
        HttpImmichClient(base_url="http://immich.invalid", api_key="")

    with pytest.raises(ImmichClientError) as list_exc:
        _http_client(_FakeTransport([(200, {"assets": {"items": "bad"}})])).list_assets()
    assert list_exc.value.error_code == "invalid_asset_search_response"
    with pytest.raises(KeyError):
        _http_client(_FakeTransport([(200, {"assets": {"items": []}})])).get_metadata("missing")
    assert _http_client(_FakeTransport([])).smart_search("   ") == ([], None)

    album_client = _http_client(_FakeTransport([(200, {"albums": []})]))
    with pytest.raises(ImmichClientError):
        album_client.find_album_by_name("Review")
    with pytest.raises(ImmichClientError):
        _http_client(_FakeTransport([(201, {"albumName": "Review"})])).create_album("Review")
    reuse_album = _http_client(_FakeTransport([(200, [{"id": "a1", "albumName": "Review"}])]))
    assert reuse_album.create_or_get_album("Review") == "a1"
    reuse_album.add_to_album("a1", [])
    assert len(reuse_album._transport.requests) == 1

    assert _http_client(_FakeTransport([])).find_tag_by_name("   ") is None
    with pytest.raises(ImmichClientError):
        _http_client(_FakeTransport([])).create_tag("")
    tag_client = _http_client(_FakeTransport([]))
    tag_client.add_tag_to_asset("", "tag-1")
    assert tag_client._transport.requests == []

    visibility_client = _http_client(_FakeTransport([(200, {})]))
    visibility_client.set_asset_visibility("asset 1", "locked")
    assert _path(visibility_client._transport.requests[0]) == "/api/assets/asset%201"
    with pytest.raises(ValueError):
        visibility_client.set_asset_visibility("", "locked")
    with pytest.raises(ValueError):
        visibility_client.set_asset_visibility("asset", "hidden")

    with pytest.raises(ImmichClientError) as json_exc:
        _http_client(_FakeTransport([(200, b"{bad")])).server_version()
    assert json_exc.value.error_code == "invalid_json_response"

    retry = _FakeTransport([(500, {}), (200, {"ok": True})])
    client = HttpImmichClient(
        base_url="https://immich.example.local",
        api_key="test-secret",
        transport=retry,
        max_retries=1,
        retry_backoff_seconds=0,
        sleep_func=lambda seconds: None,
    )
    assert client.server_version()["ok"] is True
    assert len(retry.requests) == 2

    with pytest.raises(ValueError):
        _http_client(_FakeTransport([])).list_assets(page_token="x")
    with pytest.raises(ValueError):
        _http_client(_FakeTransport([])).list_assets(page_token="0")

    assert _immich_asset_type_filter({"video"}) == "VIDEO"
    assert _positive_int(False, 7) == 7
    assert _positive_float("bad", 2.0) == 2.0
    assert _optional_float(0) is None
    assert _next_page_token("x") == "x"
    assert _next_page_token(0) is None
    with pytest.raises(ImmichClientError):
        _search_assets_page([])
    with pytest.raises(ImmichClientError):
        _search_assets_page({})
    assert _media_type_from_response(None) == "unknown"
    assert _safe_asset_metadata({"stack": {"id": "stack-1"}})["stack_id"] == "stack-1"
    assert _album_names_from_response({"albums": [{"albumName": "A"}, {}]}) == ("A",)
    assert _parse_immich_datetime("not-a-date") is None
    assert _parse_immich_datetime("2026-01-01T00:00:00").tzinfo == UTC
    assert _optional_string(1) is None
    assert _mapping_or_empty([]) == {}
    assert _should_retry(429) is True
    assert _http_error(401).error_code == "auth_failed"
    assert _http_error(404).error_code == "not_found"
    assert _http_error(418).error_code == "request_failed"

    stale = tmp_path / "stale.bin"
    stale.write_bytes(b"x")
    _unlink_if_exists(stale)
    assert not stale.exists()
    _unlink_if_exists(stale)


def test_mock_immich_edge_paths(tmp_path) -> None:
    """Test mock immich edge paths."""
    client = MockImmichClient(
        [AssetRef("a", "image")],
        capabilities=ImmichCapabilities(tags=False, archive=True, locked_folder=True),
        original_bytes_by_asset_id={"a": b""},
    )
    with pytest.raises(ValueError):
        client.list_assets(page_size=0)
    with pytest.raises(ValueError):
        client.smart_search("a", page_size=0)
    with pytest.raises(KeyError):
        client.get_metadata("missing")
    assert client.smart_search("   ") == ([], None)
    assert client.download_asset_bytes("a") == b""
    assert client.album_assets("missing") == ()

    album_id = client.create_album("Review")
    assert client.create_album("Review") == album_id
    with pytest.raises(KeyError):
        client.add_to_album("missing", ["a"])
    with pytest.raises(NotImplementedError):
        client.create_or_get_tag("review")
    with pytest.raises(NotImplementedError):
        client.create_tag("review")
    with pytest.raises(NotImplementedError):
        client.add_tag_to_asset("a", "tag-1")

    client.archive_asset("a")
    assert client.archived_asset_ids() == ("a",)
    client.set_asset_visibility("a", "locked")
    client.set_asset_visibility("a", "timeline")
    assert client.visibility_requests[-1] == {"asset_id": "a", "visibility": "timeline"}
    with pytest.raises(ValueError):
        client.set_asset_visibility("a", "bad")


class _FakeTransport(httpx.BaseTransport):
    def __init__(self, responses: list[tuple[int, object]]):
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("unexpected HTTP request")
        status, body = self._responses.pop(0)
        return httpx.Response(status, content=_response_body(body), request=request)


def _http_client(transport: _FakeTransport) -> HttpImmichClient:
    return HttpImmichClient(
        base_url="https://immich.example.local",
        api_key="test-secret",
        transport=transport,
        max_retries=0,
        retry_backoff_seconds=0,
    )


def _response_body(body: object) -> bytes:
    if isinstance(body, bytes):
        return body
    return json.dumps(body).encode("utf-8")


def _path(request) -> str:
    return urlparse(str(request.url)).path


def _query(request) -> dict[str, list[str]]:
    return parse_qs(urlparse(str(request.url)).query)


def _json_body(request) -> dict:
    return json.loads(request.content or b"{}")


def _header(request, name: str) -> str | None:
    return request.headers.get(name)
