"""Search provider abstractions for dashboard asset search."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

IMMICH_SMART_SEARCH_PATH = "/api/search/smart"
IMMICH_SMART_SEARCH_MAX_SIZE = 1000


@dataclass(frozen=True)
class SearchHit:
    """Represent SearchHit.

    Attributes
    ----------
    asset_id : str
    source : str
    score : float | None
    """

    asset_id: str
    source: str
    score: float | None = None


@dataclass(frozen=True)
class SearchPage:
    """Represent SearchPage.

    Attributes
    ----------
    hits : list[SearchHit]
    next_cursor : str | None
    unavailable_reason : str | None
    """

    hits: list[SearchHit]
    next_cursor: str | None
    unavailable_reason: str | None = None


class SearchUnavailable(RuntimeError):
    """Raised when a provider cannot serve a query without leaking details."""

    def __init__(self, reason: str):
        """Initialize the instance.

        Parameters
        ----------
        reason : str
        """
        self.reason = reason
        super().__init__(reason)


class SearchProvider(Protocol):
    """Represent SearchProvider."""

    source: str

    def search(
        self,
        *,
        query: str,
        cursor: str | None,
        page_size: int,
        queue: str | None = None,
        media_kind: str | None = None,
        event_id: str | None = None,
    ) -> SearchPage:
        """Search.

        Parameters
        ----------
        query : str
        cursor : str | None
        page_size : int
        queue : str | None, optional
        media_kind : str | None, optional
        event_id : str | None, optional

        Returns
        -------
        SearchPage
        """
        ...


class ImmichSmartSearchProvider:
    """Calls Immich Smart Search with the caller's bearer session."""

    source = "immich_smart_search"

    def __init__(self, *, client: httpx.Client, bearer_token: str):
        """Initialize the instance.

        Parameters
        ----------
        client : httpx.Client
        bearer_token : str
        """
        self._client = client
        self._bearer_token = bearer_token

    def search(
        self,
        *,
        query: str,
        cursor: str | None,
        page_size: int,
        queue: str | None = None,
        media_kind: str | None = None,
        event_id: str | None = None,
    ) -> SearchPage:
        """Search.

        Parameters
        ----------
        query : str
        cursor : str | None
        page_size : int
        queue : str | None, optional
        media_kind : str | None, optional
        event_id : str | None, optional

        Returns
        -------
        SearchPage
        """
        del queue  # Immich Smart Search cannot express MediaRefinery queues.
        del event_id  # Applied after Immich ranking by the scoped state lookup.
        clean_query = query.strip()
        if not clean_query:
            return SearchPage(hits=[], next_cursor=None)

        offset = _semantic_offset(cursor)
        page_size = _page_size(page_size)
        request_size = min(
            offset + page_size + 1,
            IMMICH_SMART_SEARCH_MAX_SIZE,
        )
        body: dict[str, object] = {
            "query": clean_query,
            "page": 1,
            "size": request_size,
            "withDeleted": False,
            "withExif": True,
            "withPeople": True,
            "withStacked": True,
        }
        asset_type = _immich_asset_type(media_kind)
        if asset_type is not None:
            body["type"] = asset_type

        try:
            response = self._client.post(
                IMMICH_SMART_SEARCH_PATH,
                json=body,
                headers={"Authorization": f"Bearer {self._bearer_token}"},
            )
        except httpx.HTTPError as exc:
            raise SearchUnavailable("immich_smart_search_unreachable") from exc

        if response.status_code == 404:
            raise SearchUnavailable("immich_smart_search_unsupported")
        if response.status_code in {400, 409, 422, 501, 503}:
            raise SearchUnavailable("immich_smart_search_unavailable")
        if response.status_code in {401, 403}:
            raise SearchUnavailable("immich_smart_search_forbidden")
        if response.status_code >= 500:
            raise SearchUnavailable("immich_smart_search_unavailable")
        if response.status_code != 200:
            raise SearchUnavailable("immich_smart_search_unavailable")

        try:
            data = response.json()
        except ValueError as exc:
            raise SearchUnavailable("immich_smart_search_invalid_response") from exc

        assets_page = _assets_page(data)
        items = assets_page.get("items") or []
        if not isinstance(items, list):
            raise SearchUnavailable("immich_smart_search_invalid_response")

        all_hits = _hits_from_items(items, self.source)
        page_hits = all_hits[offset : offset + page_size + 1]
        has_more = len(page_hits) > page_size
        hits = page_hits[:page_size]
        next_cursor = f"semantic:{offset + page_size}" if has_more else None
        return SearchPage(hits=hits, next_cursor=next_cursor)


class MetadataSearchProvider:
    """Local metadata/OCR/analysis search fallback.

    This provider intentionally emits ``score=None`` so clients do not mistake
    metadata fallback ordering for a semantic similarity score.
    """

    def __init__(self, *, scoped_state: Any, source: str = "metadata"):
        """Initialize the instance.

        Parameters
        ----------
        scoped_state : Any
        source : str, optional
        """
        self._scoped_state = scoped_state
        self.source = source

    def search(
        self,
        *,
        query: str,
        cursor: str | None,
        page_size: int,
        queue: str | None = None,
        media_kind: str | None = None,
        event_id: str | None = None,
    ) -> SearchPage:
        """Search.

        Parameters
        ----------
        query : str
        cursor : str | None
        page_size : int
        queue : str | None, optional
        media_kind : str | None, optional
        event_id : str | None, optional

        Returns
        -------
        SearchPage
        """
        rows, next_cursor = self._scoped_state.list_review_assets_paginated(
            cursor=cursor,
            page_size=page_size,
            queue=queue,
            media_kind=media_kind,
            event_id=event_id,
            q=query.strip() or None,
        )
        return SearchPage(
            hits=[
                SearchHit(asset_id=str(row["asset_id"]), source=self.source, score=None)
                for row in rows
            ],
            next_cursor=next_cursor,
        )


def annotate_search_rows(
    rows: list[dict[str, Any]],
    hits: list[SearchHit],
) -> list[dict[str, Any]]:
    """Annotate search rows.

    Parameters
    ----------
    rows : list[dict[str, Any]]
    hits : list[SearchHit]

    Returns
    -------
    list[dict[str, Any]]
    """
    hits_by_id = {hit.asset_id: hit for hit in hits}
    annotated: list[dict[str, Any]] = []
    for row in rows:
        hit = hits_by_id.get(str(row.get("asset_id")))
        next_row = dict(row)
        next_row["search_source"] = hit.source if hit is not None else None
        next_row["search_score"] = hit.score if hit is not None else None
        annotated.append(next_row)
    return annotated


def _semantic_offset(cursor: str | None) -> int:
    if cursor is None or cursor == "":
        return 0
    prefix = "semantic:"
    if not cursor.startswith(prefix):
        raise ValueError("semantic cursor is invalid")
    try:
        offset = int(cursor[len(prefix) :])
    except ValueError as exc:
        raise ValueError("semantic cursor is invalid") from exc
    if offset < 0:
        raise ValueError("semantic cursor is invalid")
    return offset


def _page_size(value: int) -> int:
    return max(1, min(int(value), 100))


def _immich_asset_type(media_kind: str | None) -> str | None:
    if media_kind in {"image", "gif"}:
        return "IMAGE"
    if media_kind == "video":
        return "VIDEO"
    return None


def _assets_page(data: object) -> Mapping[str, Any]:
    if not isinstance(data, Mapping):
        raise SearchUnavailable("immich_smart_search_invalid_response")
    assets = data.get("assets")
    if not isinstance(assets, Mapping):
        raise SearchUnavailable("immich_smart_search_invalid_response")
    return assets


def _hits_from_items(items: list[object], source: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        asset_id = item.get("id")
        if not isinstance(asset_id, str) or not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        hits.append(SearchHit(asset_id=asset_id, source=source, score=_score(item)))
    return hits


def _score(item: Mapping[str, Any]) -> float | None:
    for key in ("score", "searchScore", "_score", "similarity"):
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
    return None
