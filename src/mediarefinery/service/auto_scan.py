"""Auto-scan polling fallback.

Immich 2.7.5 does not expose outbound webhooks (see
``docs/reference/immich-api-compat.md``), so the service closes the
auto-scan-on-upload gap with scheduled polling: a single in-process
APScheduler coordinator job ticks every minute, walks the set of
users that have opted in, and for each user whose ``interval_minutes``
has elapsed calls Immich ``POST /api/search/metadata`` filtered by
``takenAfter = <last_seen_taken_at>``. New asset ids are fed into
the existing scan dispatcher exactly as the ``/scans`` route would.

Privacy / security invariants (threat-model T13, T15):

- The user's encrypted Immich Bearer is decrypted into a single
  local for the duration of one tick and rebound to ``None`` in
  ``finally``. It is never logged and never written to ``state.db``
  outside the existing ``sessions.encrypted_immich_token`` column.
- Asset ids that arrive in this tick are not logged; the only audit
  event produced per tick is ``auto_scan.tick`` with counts only
  (no per-asset detail). The dispatched scan run records per-asset
  decisions through the existing scanner / runner pipeline.
- 401 from Immich (session expired) flips ``enabled=FALSE`` for the
  user and stops the per-user retry loop until they re-auth.
- 5xx / network errors leave ``last_seen_taken_at`` and ``enabled``
  untouched so the next tick retries from the same cursor.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx

from . import auth as _auth
from . import runner as _runner
from . import scheduler as _scheduler
from .security import AesGcmCipher
from .state_store import StateStore

if TYPE_CHECKING:
    from .runner import RunnerFactories

log = logging.getLogger("mediarefinery.service.auto_scan")

DEFAULT_INTERVAL_MINUTES = 30
MIN_INTERVAL_MINUTES = 5
MAX_INTERVAL_MINUTES = 1440
COORDINATOR_INTERVAL_SECONDS = 60
MAX_PAGES_PER_TICK = 10
SEARCH_METADATA_PATH = "/api/search/metadata"


def clamp_interval(value: int) -> int:
    """Clamp interval.

    Parameters
    ----------
    value : int

    Returns
    -------
    int
    """
    return max(MIN_INTERVAL_MINUTES, min(MAX_INTERVAL_MINUTES, int(value)))


@dataclass(frozen=True)
class TickOutcome:
    """One coordinator tick's per-user accounting."""

    user_id: str
    status: str
    new_assets: int
    pages_walked: int
    submitted_run_id: int | None
    error_code: str | None


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_due(last_run_at: str | None, interval_minutes: int, now: datetime) -> bool:
    if last_run_at is None:
        return True
    try:
        last = datetime.fromisoformat(last_run_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return (now - last) >= timedelta(minutes=interval_minutes)


def _dispatch_scan(
    *,
    store: StateStore,
    user_id: str,
    runner_factories: RunnerFactories | None,
) -> int | None:
    """Mirror ``routers.create_scan`` auto-flip behavior.

    Uses the real runner when a model is active, synthetic otherwise.
    Returns ``run_id`` or ``None`` if dispatch was rejected (concurrency cap,
    quota).
    """
    try:
        if store.active_model_sha256() is not None:
            if not store.with_user(user_id).list_api_keys():
                log.info(
                    "auto-scan dispatch skipped",
                    extra={
                        "event": "auto_scan.api_key_required",
                        "user_id": user_id,
                    },
                )
                return None
            submitted = _runner.submit_real_scan(
                store=store,
                user_id=user_id,
                factories=runner_factories,
            )
        else:
            submitted = _scheduler.submit_scan(store=store, user_id=user_id)
    except _scheduler.ScanRejected as exc:
        log.info(
            "auto-scan dispatch rejected",
            extra={
                "event": "auto_scan.dispatch_rejected",
                "user_id": user_id,
                "reason": exc.reason,
            },
        )
        return None
    return submitted.run_id


def _search_metadata_page(
    *,
    base_url: str,
    bearer: str,
    taken_after: str | None,
    page: int,
    client: httpx.Client,
    page_size: int = 100,
) -> httpx.Response:
    body: dict[str, Any] = {
        "page": page,
        "size": page_size,
        "withDeleted": False,
        "withExif": False,
        "withPeople": False,
        "withStacked": False,
    }
    if taken_after is not None:
        body["takenAfter"] = taken_after
    return client.post(
        SEARCH_METADATA_PATH,
        json=body,
        headers={"Authorization": f"Bearer {bearer}"},
    )


def _extract_items_and_next(
    payload: Any,
) -> tuple[list[dict[str, Any]], int | None]:
    if not isinstance(payload, dict):
        return [], None
    assets = payload.get("assets")
    if not isinstance(assets, dict):
        return [], None
    items = assets.get("items") or []
    if not isinstance(items, list):
        items = []
    next_page = assets.get("nextPage")
    if next_page in (None, ""):
        return list(items), None
    try:
        return list(items), int(next_page)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return list(items), None


def _max_taken_at(
    items: list[dict[str, Any]], current_max: str | None
) -> str | None:
    out = current_max
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = item.get("fileCreatedAt") or item.get("localDateTime")
        if not isinstance(candidate, str) or not candidate:
            continue
        if out is None or candidate > out:
            out = candidate
    return out


DispatchScan = Callable[[StateStore, str], int | None]


def run_user_tick(
    *,
    store: StateStore,
    user_id: str,
    cipher: AesGcmCipher,
    immich_client: httpx.Client,
    base_url: str,
    last_seen_taken_at: str | None,
    runner_factories: RunnerFactories | None = None,
    dispatch: DispatchScan | None = None,
    now: datetime | None = None,
    max_pages: int = MAX_PAGES_PER_TICK,
) -> TickOutcome:
    """Run one polling tick for one user.

    Decrypts the user's most recent session Bearer, walks
    ``/api/search/metadata`` pages until exhausted or until
    ``max_pages`` is reached, dispatches a scan if any new asset id
    was returned, and persists the new cursor + status. Bearer is
    rebound to ``None`` in the ``finally`` block.
    """
    moment = now or _now_utc()
    now_iso = _iso(moment)
    scoped = store.with_user(user_id)
    sessions = scoped.list_sessions()
    if not sessions:
        scoped.record_auto_scan_tick(
            now_iso=now_iso,
            status="error",
            error_code="no_active_session",
            last_seen_taken_at=last_seen_taken_at,
            disable=True,
        )
        return TickOutcome(
            user_id=user_id,
            status="error",
            new_assets=0,
            pages_walked=0,
            submitted_run_id=None,
            error_code="no_active_session",
        )

    bearer: str | None = None
    pages_walked = 0
    new_assets = 0
    new_cursor = last_seen_taken_at
    submitted_run_id: int | None = None
    status = "ok"
    error_code: str | None = None
    disable = False
    advance_cursor = True

    try:
        bearer = _auth.decrypt_session_token(cipher=cipher, row=sessions[-1])
        page: int | None = 1
        while page is not None and pages_walked < max_pages:
            try:
                response = _search_metadata_page(
                    base_url=base_url,
                    bearer=bearer,
                    taken_after=last_seen_taken_at,
                    page=page,
                    client=immich_client,
                )
            except httpx.HTTPError:
                status = "error"
                error_code = "upstream_unreachable"
                advance_cursor = False
                break
            pages_walked += 1
            if response.status_code == 401:
                status = "error"
                error_code = "upstream_session_expired"
                advance_cursor = False
                disable = True
                break
            if response.status_code >= 500:
                status = "error"
                error_code = "upstream_unreachable"
                advance_cursor = False
                break
            if response.status_code >= 400:
                status = "error"
                error_code = "upstream_bad_request"
                advance_cursor = False
                break
            try:
                payload = response.json()
            except ValueError:
                status = "error"
                error_code = "upstream_invalid_json"
                advance_cursor = False
                break
            items, next_page = _extract_items_and_next(payload)
            new_assets += len(items)
            new_cursor = _max_taken_at(items, new_cursor)
            page = next_page

        if status == "ok" and new_assets > 0:
            if dispatch is not None:
                submitted_run_id = dispatch(store, user_id)
            else:
                submitted_run_id = _dispatch_scan(
                    store=store,
                    user_id=user_id,
                    runner_factories=runner_factories,
                )
    except ValueError:
        status = "error"
        error_code = "session_decrypt_failed"
        advance_cursor = False
    finally:
        # Defensive rebind so the local reference goes away ahead of
        # any logging or response-building. See module docstring.
        bearer = None

    persisted_cursor = new_cursor if advance_cursor else last_seen_taken_at
    scoped.record_auto_scan_tick(
        now_iso=now_iso,
        status=status,
        error_code=error_code,
        last_seen_taken_at=persisted_cursor,
        disable=disable,
    )
    scoped.write_audit(
        action="auto_scan.tick",
        details_json=_audit_details(
            status=status,
            new_assets=new_assets,
            pages_walked=pages_walked,
            submitted_run_id=submitted_run_id,
            error_code=error_code,
        ),
    )
    return TickOutcome(
        user_id=user_id,
        status=status,
        new_assets=new_assets,
        pages_walked=pages_walked,
        submitted_run_id=submitted_run_id,
        error_code=error_code,
    )


def _audit_details(
    *,
    status: str,
    new_assets: int,
    pages_walked: int,
    submitted_run_id: int | None,
    error_code: str | None,
) -> str:
    import json as _json

    return _json.dumps(
        {
            "status": status,
            "new_assets": new_assets,
            "pages_walked": pages_walked,
            "run_id": submitted_run_id,
            "error_code": error_code,
        },
        sort_keys=True,
    )


def coordinator_tick(
    *,
    store: StateStore,
    cipher: AesGcmCipher,
    immich_client: httpx.Client,
    base_url: str,
    runner_factories: RunnerFactories | None = None,
    dispatch: DispatchScan | None = None,
    now: datetime | None = None,
) -> list[TickOutcome]:
    """One coordinator pass: iterate enabled users, run due ones."""
    moment = now or _now_utc()
    rows = store.list_enabled_auto_scan()
    outcomes: list[TickOutcome] = []
    for row in rows:
        interval = clamp_interval(int(row["interval_minutes"]))
        if not _is_due(row["last_run_at"], interval, moment):
            continue
        try:
            outcome = run_user_tick(
                store=store,
                user_id=str(row["user_id"]),
                cipher=cipher,
                immich_client=immich_client,
                base_url=base_url,
                last_seen_taken_at=row["last_seen_taken_at"],
                runner_factories=runner_factories,
                dispatch=dispatch,
                now=moment,
            )
        except sqlite3.Error:
            log.exception(
                "auto-scan tick database error",
                extra={"event": "auto_scan.db_error", "user_id": row["user_id"]},
            )
            continue
        except Exception:
            # One user's failure must not affect others. Re-raise of
            # SystemExit / KeyboardInterrupt is preserved by Python's
            # default behaviour above the bare Exception clause.
            log.exception(
                "auto-scan tick failed",
                extra={"event": "auto_scan.tick_failed", "user_id": row["user_id"]},
            )
            continue
        outcomes.append(outcome)
    return outcomes


def make_coordinator_callable(
    *,
    store: StateStore,
    cipher: AesGcmCipher,
    immich_client: httpx.Client,
    base_url: str,
    runner_factories_provider: Callable[[], RunnerFactories | None] | None = None,
) -> Callable[[], None]:
    """Return a no-arg callable suitable for APScheduler.add_job."""

    def _tick() -> None:
        factories = (
            runner_factories_provider() if runner_factories_provider else None
        )
        coordinator_tick(
            store=store,
            cipher=cipher,
            immich_client=immich_client,
            base_url=base_url,
            runner_factories=factories,
        )

    return _tick


__all__ = [
    "COORDINATOR_INTERVAL_SECONDS",
    "DEFAULT_INTERVAL_MINUTES",
    "MAX_INTERVAL_MINUTES",
    "MAX_PAGES_PER_TICK",
    "MIN_INTERVAL_MINUTES",
    "TickOutcome",
    "clamp_interval",
    "coordinator_tick",
    "make_coordinator_callable",
    "run_user_tick",
]
