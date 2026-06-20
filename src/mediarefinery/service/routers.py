"""HTTP routers for the MediaRefinery service."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import auth as _auth
from . import auto_scan as _auto_scan
from . import locked_folder as _locked_folder
from . import model_catalog as _catalog
from . import model_lifecycle as _lifecycle
from . import production as _production
from . import runner as _runner
from . import scheduler as _scheduler
from . import search as _search
from .compatibility import check_immich_compatibility
from .config import ServiceConfig
from .deps import (
    client_ip,
    get_cipher,
    get_current_user,
    get_immich_client,
    get_service_config,
    get_signer,
    get_state,
    require_admin,
    require_csrf,
)
from .security import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    AesGcmCipher,
    InMemoryRateLimiter,
    SessionCookieSigner,
    issue_csrf_token,
)
from .state_store import StateStore

log = logging.getLogger("mediarefinery.service")


class LoginRequest(BaseModel):
    """Represent LoginRequest."""

    email: str = Field(..., min_length=3, max_length=320)
    password: str = Field(..., min_length=1, max_length=4096)


class MeResponse(BaseModel):
    """Represent MeResponse."""

    user_id: str
    email: str
    name: str | None = None
    is_admin: bool


class CategoriesPayload(BaseModel):
    """Represent CategoriesPayload."""

    categories: dict[str, Any]


class PoliciesPayload(BaseModel):
    """Represent PoliciesPayload."""

    policies: dict[str, Any]


class LockedFolderUnlockPayload(BaseModel):
    """Represent LockedFolderUnlockPayload."""

    run_id: int = Field(..., gt=0)
    pin: str = Field(..., min_length=1, max_length=64)


class ApiKeyPayload(BaseModel):
    """Represent ApiKeyPayload."""

    api_key: str = Field(..., min_length=1, max_length=4096)
    label: str | None = Field(None, max_length=128)
    validate_api_key: bool = False


class ScanResponse(BaseModel):
    """Represent ScanResponse."""

    run_id: int
    status: str


class InstallModelPayload(BaseModel):
    """Represent InstallModelPayload."""

    model_id: str = Field(..., min_length=1, max_length=128)
    license_accepted: bool = Field(...)


class AdultSubtypeProfilePayload(BaseModel):
    """Represent AdultSubtypeProfilePayload."""

    model_id: str = Field(..., min_length=1, max_length=128)
    name: str | None = Field(None, max_length=128)
    model_path: str = Field(..., min_length=1, max_length=4096)
    output_labels: list[str] = Field(..., min_length=1, max_length=256)
    thresholds: dict[str, float] = Field(default_factory=dict)
    admin_acknowledgement: bool = Field(...)
    input_size: int = Field(224, ge=1, le=4096)
    input_mean: tuple[float, float, float] | None = None
    input_std: tuple[float, float, float] | None = None
    input_name: str | None = Field(None, max_length=128)
    output_name: str | None = Field(None, max_length=128)


class AssetCategoryOverridePayload(BaseModel):
    """Represent AssetCategoryOverridePayload."""

    category_id: str | None = Field(default=None, max_length=128)


class EventRenamePayload(BaseModel):
    """Represent EventRenamePayload."""

    title: str = Field(..., min_length=1, max_length=160)


class EventMergePayload(BaseModel):
    """Represent EventMergePayload."""

    target_event_id: str = Field(..., min_length=1, max_length=256)
    source_event_ids: list[str] = Field(..., min_length=1, max_length=50)


class EventSplitPayload(BaseModel):
    """Represent EventSplitPayload."""

    title: str = Field(..., min_length=1, max_length=160)
    asset_ids: list[str] = Field(..., min_length=1, max_length=500)


class AutoScanSettingsPayload(BaseModel):
    """Represent AutoScanSettingsPayload."""

    enabled: bool
    interval_minutes: int = Field(
        ...,
        ge=_auto_scan.MIN_INTERVAL_MINUTES,
        le=_auto_scan.MAX_INTERVAL_MINUTES,
    )


_ASSET_PREVIEW_PATH = "/api/assets/{asset_id}/thumbnail"


def _set_auth_cookies(
    response: Response,
    *,
    config: ServiceConfig,
    signed_session: str,
    csrf: str,
    ttl_seconds: int,
) -> None:
    cookie_kwargs: dict[str, Any] = {
        "secure": config.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": ttl_seconds,
    }
    response.set_cookie(
        SESSION_COOKIE_NAME,
        signed_session,
        httponly=True,
        **cookie_kwargs,
    )
    # CSRF cookie must be readable by JS to be echoed in the X-CSRF-Token
    # header (double-submit pattern), so HttpOnly is intentionally false.
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf,
        httponly=False,
        **cookie_kwargs,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.delete_cookie(CSRF_COOKIE_NAME, path="/")


def build_auth_router() -> APIRouter:
    """Build auth router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.post("/login", status_code=status.HTTP_200_OK)
    def login(
        body: LoginRequest,
        request: Request,
        response: Response,
        state: Annotated[StateStore, Depends(get_state)],
        cipher: Annotated[AesGcmCipher, Depends(get_cipher)],
        signer: Annotated[SessionCookieSigner, Depends(get_signer)],
        config: Annotated[ServiceConfig, Depends(get_service_config)],
        immich: Annotated[httpx.Client, Depends(get_immich_client)],
    ) -> dict[str, Any]:
        """Login.

        Parameters
        ----------
        body : LoginRequest
        request : Request
        response : Response
        state : Annotated[StateStore, Depends(get_state)]
        cipher : Annotated[AesGcmCipher, Depends(get_cipher)]
        signer : Annotated[SessionCookieSigner, Depends(get_signer)]
        config : Annotated[ServiceConfig, Depends(get_service_config)]
        immich : Annotated[httpx.Client, Depends(get_immich_client)]

        Returns
        -------
        dict[str, Any]
        """
        ip = client_ip(request, config)
        limiter: InMemoryRateLimiter = request.app.state.login_limiter
        if not limiter.check(ip):
            log.warning("login rate-limited", extra={"event": "login.ratelimited", "ip": ip})
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                detail="too many login attempts",
            )

        try:
            result = _auth.proxy_login(
                immich_base_url=config.immich_base_url,
                email=body.email,
                password=body.password,
                client=immich,
            )
        except _auth.InvalidCredentials:
            log.info("login rejected", extra={"event": "login.rejected", "ip": ip})
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
            ) from None
        except _auth.AuthError as exc:
            log.error("login upstream failure", extra={"event": "login.upstream_error"})
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, detail="upstream Immich unreachable"
            ) from exc

        # First-user-becomes-admin bootstrap: if no admin exists yet,
        # promote this login to admin regardless of Immich isAdmin.
        # Subsequent logins inherit Immich isAdmin.
        promote_first_admin = state.admin_count() == 0
        state.upsert_user(
            user_id=result.user_id,
            email=result.email,
            name=result.name,
            is_admin=result.is_admin or promote_first_admin,
        )
        if promote_first_admin and not result.is_admin:
            state.promote_to_admin(result.user_id)
            log.info(
                "first user promoted to admin",
                extra={"event": "bootstrap.first_admin", "user_id": result.user_id},
            )

        session_id = _auth.mint_session_id()
        encrypted = cipher.encrypt(result.access_token.encode("utf-8"))
        expires_at = _auth.session_expiry(ttl_seconds=config.session_ttl_seconds)
        _auth.persist_session(
            conn=state._conn,
            user_id=result.user_id,
            session_id=session_id,
            encrypted_token=encrypted,
            expires_at=expires_at,
        )
        scoped = state.with_user(result.user_id)
        scoped.write_audit(action="login")

        signed = signer.sign(session_id)
        csrf = issue_csrf_token()
        _set_auth_cookies(
            response,
            config=config,
            signed_session=signed,
            csrf=csrf,
            ttl_seconds=config.session_ttl_seconds,
        )
        log.info(
            "login ok",
            extra={"event": "login.ok", "user_id": result.user_id, "ip": ip},
        )
        return {
            "user_id": result.user_id,
            "email": result.email,
            "name": result.name,
            "is_admin": result.is_admin,
        }

    @router.post(
        "/logout",
        dependencies=[Depends(require_csrf)],
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def logout(
        request: Request,
        response: Response,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
        cipher: Annotated[AesGcmCipher, Depends(get_cipher)],
        config: Annotated[ServiceConfig, Depends(get_service_config)],
        immich: Annotated[httpx.Client, Depends(get_immich_client)],
    ) -> Response:
        """Logout.

        Parameters
        ----------
        request : Request
        response : Response
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]
        cipher : Annotated[AesGcmCipher, Depends(get_cipher)]
        config : Annotated[ServiceConfig, Depends(get_service_config)]
        immich : Annotated[httpx.Client, Depends(get_immich_client)]

        Returns
        -------
        Response
        """
        session_id = request.state.session_id
        row = _auth.lookup_session(conn=state._conn, session_id=session_id)
        if row is not None:
            try:
                token = _auth.decrypt_session_token(cipher=cipher, row=row)
                _auth.proxy_logout(
                    immich_base_url=config.immich_base_url,
                    access_token=token,
                    client=immich,
                )
            except ValueError:
                pass  # encrypted token unreadable; revoke our row anyway
        _auth.revoke_session(conn=state._conn, session_id=session_id)
        scoped = state.with_user(user_id)
        scoped.write_audit(action="logout")
        _clear_auth_cookies(response)
        log.info("logout ok", extra={"event": "logout.ok", "user_id": user_id})
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def build_me_router() -> APIRouter:
    """Build me router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(tags=["me"])

    @router.get("/me", response_model=MeResponse)
    def me(
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> MeResponse:
        """Me.

        Parameters
        ----------
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        MeResponse
        """
        cursor = state._conn.execute(
            "SELECT user_id, email, name, is_admin FROM users WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="user not found")
        return MeResponse(
            user_id=row["user_id"],
            email=row["email"],
            name=row["name"],
            is_admin=bool(row["is_admin"]),
        )

    @router.delete("/me", dependencies=[Depends(require_csrf)])
    def delete_me(
        request: Request,
        response: Response,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
        cipher: Annotated[AesGcmCipher, Depends(get_cipher)],
        config: Annotated[ServiceConfig, Depends(get_service_config)],
        immich: Annotated[httpx.Client, Depends(get_immich_client)],
    ) -> Response:
        """Idempotent purge of the calling user (threat-model T20).

        Sessions, API keys, runs, actions, errors, assets, and per-user
        config rows are deleted; the encrypted Bearer / API key blobs
        are zeroed in place first so a recovered DB page does not yield
        decryptable ciphertext. Audit-log rows are anonymized in place
        by rewriting ``user_id`` to the sentinel ``"user_deleted"`` —
        the threat model accepts either delete-or-anonymize, and
        anonymize-in-place preserves audit-trail integrity. Finally the
        ``users`` row is deleted and the caller's session cookies are
        cleared.
        """
        session_id = request.state.session_id
        row = _auth.lookup_session(conn=state._conn, session_id=session_id)
        if row is not None:
            try:
                token = _auth.decrypt_session_token(cipher=cipher, row=row)
                _auth.proxy_logout(
                    immich_base_url=config.immich_base_url,
                    access_token=token,
                    client=immich,
                )
            except ValueError:
                pass  # encrypted token unreadable; purge anyway
        _auth.revoke_session(conn=state._conn, session_id=session_id)

        state.with_user(user_id).purge()
        _clear_auth_cookies(response)
        log.info("account purged", extra={"event": "me.delete", "user_id": user_id})
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def build_me_config_router() -> APIRouter:
    """Build me config router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(prefix="/me", tags=["me"])

    @router.get("/categories")
    def get_categories(
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Return categories.

        Parameters
        ----------
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        active_sha = state.active_model_sha256()
        last_seen = scoped.last_seen_model_sha256()
        needs_reclassify = bool(
            active_sha is not None
            and last_seen is not None
            and active_sha != last_seen
        )
        return {
            "categories": scoped.get_config()["categories"],
            "active_model_sha256": active_sha,
            "last_seen_model_sha256": last_seen,
            "needs_reclassify": needs_reclassify,
        }

    @router.put("/categories", dependencies=[Depends(require_csrf)])
    def put_categories(
        body: CategoriesPayload,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Put categories.

        Parameters
        ----------
        body : CategoriesPayload
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        scoped.set_categories(body.categories)
        scoped.write_audit(action="categories.update")
        return {"categories": scoped.get_config()["categories"]}

    @router.get("/policies")
    def get_policies(
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Return policies.

        Parameters
        ----------
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        return {"policies": state.with_user(user_id).get_config()["policies"]}

    @router.put("/policies", dependencies=[Depends(require_csrf)])
    def put_policies(
        body: PoliciesPayload,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Put policies.

        Parameters
        ----------
        body : PoliciesPayload
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        scoped.set_policies(body.policies)
        scoped.write_audit(action="policies.update")
        return {"policies": scoped.get_config()["policies"]}

    @router.post("/api-key", dependencies=[Depends(require_csrf)], status_code=201)
    def put_api_key(
        body: ApiKeyPayload,
        request: Request,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
        cipher: Annotated[AesGcmCipher, Depends(get_cipher)],
    ) -> dict[str, Any]:
        """Put api key.

        Parameters
        ----------
        body : ApiKeyPayload
        request : Request
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]
        cipher : Annotated[AesGcmCipher, Depends(get_cipher)]

        Returns
        -------
        dict[str, Any]
        """
        if body.validate_api_key:
            validator = getattr(request.app.state, "api_key_validator", None)
            if validator is None:
                raise HTTPException(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="api key validation is not configured",
                )
            try:
                validator(body.api_key)
            except _production.ApiKeyValidationError as exc:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    detail=f"api_key_invalid:{exc}",
                ) from exc
        scoped = state.with_user(user_id)
        encrypted = cipher.encrypt(body.api_key.encode("utf-8"))
        key_id = scoped.store_api_key(encrypted_key=encrypted, label=body.label)
        scoped.write_audit(action="api_key.store")
        return {"id": key_id, "label": body.label}

    @router.get("/api-key")
    def list_api_keys(
        request: Request,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """List api keys.

        Parameters
        ----------
        request : Request
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        rows = state.with_user(user_id).list_api_keys()
        return {
            "required_for_scans": bool(
                getattr(request.app.state, "runner_requires_api_key", False)
            ),
            "api_keys": [
                {"id": int(row["id"]), "label": row["label"], "created_at": row["created_at"]}
                for row in rows
            ]
        }

    @router.get("/auto-scan")
    def get_auto_scan(
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Return auto scan.

        Parameters
        ----------
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        return state.with_user(user_id).get_auto_scan()

    @router.put("/auto-scan", dependencies=[Depends(require_csrf)])
    def put_auto_scan(
        body: AutoScanSettingsPayload,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Put auto scan.

        Parameters
        ----------
        body : AutoScanSettingsPayload
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        scoped.set_auto_scan(
            enabled=body.enabled,
            interval_minutes=_auto_scan.clamp_interval(body.interval_minutes),
        )
        scoped.write_audit(action="auto_scan.settings.update")
        return scoped.get_auto_scan()

    @router.post(
        "/locked-folder/unlock",
        dependencies=[Depends(require_csrf)],
    )
    def unlock_locked_folder(
        body: LockedFolderUnlockPayload,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
        cipher: Annotated[AesGcmCipher, Depends(get_cipher)],
        immich: Annotated[httpx.Client, Depends(get_immich_client)],
        config: Annotated[ServiceConfig, Depends(get_service_config)],
    ) -> dict[str, Any]:
        # Threat-model T09 / T10:
        # - PIN flows request -> Immich without being logged or stored.
        # - The PIN-unlocked Bearer is held in a local for the
        #   duration of this handler only and rebound to None before
        #   the response is built. It never reaches state.db.
        """Unlock locked folder.

        Parameters
        ----------
        body : LockedFolderUnlockPayload
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]
        cipher : Annotated[AesGcmCipher, Depends(get_cipher)]
        immich : Annotated[httpx.Client, Depends(get_immich_client)]
        config : Annotated[ServiceConfig, Depends(get_service_config)]

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        if scoped.get_run(body.run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="scan not found")
        sessions = scoped.list_sessions()
        if not sessions:
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, detail="no active session"
            )
        # Use the most recently created session row.
        session_row = sessions[-1]
        bearer: str | None = _auth.decrypt_session_token(
            cipher=cipher, row=session_row
        )
        assert bearer is not None  # decrypt_session_token raises on failure

        locked_asset_ids = [
            str(row["asset_id"])
            for row in scoped.list_actions()
            if int(row["run_id"]) == body.run_id
            and row["action_name"] == "move_to_locked_folder"
            and row["success"] == 1
        ]
        if not locked_asset_ids:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="no locked-folder actions to revert",
            )

        try:
            try:
                outcome = _locked_folder.unlock_and_revert(
                    immich_base_url=config.immich_base_url,
                    bearer=bearer,
                    pin=body.pin,
                    asset_ids=locked_asset_ids,
                    client=immich,
                )
            except _locked_folder.InvalidPin:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, detail="invalid pin"
                ) from None
            except _locked_folder.UpstreamUnavailable:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, detail="upstream Immich unreachable"
                ) from None
            except _locked_folder.UnlockError:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, detail="upstream Immich error"
                ) from None
        finally:
            # Defensive zeroing: rebind so the local reference goes away
            # ahead of the response serialiser. Python strings are
            # immutable so we cannot wipe the original bytes; what we
            # can guarantee is that no callsite below this comment
            # holds the value, and no caller of this endpoint ever
            # sees it.
            bearer = None

        for asset_id in locked_asset_ids:
            if asset_id in outcome.failed_asset_ids:
                continue
            scoped.write_audit(
                action="asset.unlocked",
                target_asset_id=asset_id,
                run_id=body.run_id,
                after_state="timeline",
            )
        scoped.write_audit(action="scan.undo", run_id=body.run_id)

        return {
            "run_id": body.run_id,
            "reverted": outcome.reverted_count,
            "failed_asset_ids": list(outcome.failed_asset_ids),
        }

    return router


def build_scans_router() -> APIRouter:
    """Build scans router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(prefix="/scans", tags=["scans"])

    @router.post("", dependencies=[Depends(require_csrf)], status_code=202)
    def create_scan(
        request: Request,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> ScanResponse:
        # When an active model is registered we run the real pipeline;
        # with no model installed we keep the synthetic runner so
        # contributors and CI can drive the multi-tenant invariants
        # without a model on disk.
        """Create scan.

        Parameters
        ----------
        request : Request
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        ScanResponse
        """
        runner_factories = getattr(request.app.state, "runner_factories", None)
        runner_requires_api_key = bool(
            getattr(request.app.state, "runner_requires_api_key", False)
        )
        try:
            if state.active_model_sha256() is not None:
                if runner_factories is None:
                    raise HTTPException(
                        status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="scan runner is not configured",
                    )
                if runner_requires_api_key and not state.with_user(user_id).list_api_keys():
                    raise HTTPException(
                        status.HTTP_409_CONFLICT,
                        detail="api_key_required",
                    )
                submitted = _runner.submit_real_scan(
                    store=state,
                    user_id=user_id,
                    factories=runner_factories,
                )
            else:
                submitted = _scheduler.submit_scan(
                    store=state, user_id=user_id
                )
        except _scheduler.ScanRejected as exc:
            if exc.reason == "concurrency_cap":
                raise HTTPException(
                    status.HTTP_409_CONFLICT, detail="scan already running"
                ) from exc
            if exc.reason == "daily_quota":
                raise HTTPException(
                    status.HTTP_429_TOO_MANY_REQUESTS, detail="daily scan quota exceeded"
                ) from exc
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=exc.reason) from exc
        return ScanResponse(run_id=submitted.run_id, status="running")

    @router.get("")
    def list_scans(
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """List scans.

        Parameters
        ----------
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        rows = state.with_user(user_id).list_runs()
        return {
            "scans": [
                {
                    "run_id": int(row["id"]),
                    "status": row["status"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                }
                for row in rows
            ]
        }

    @router.get("/{run_id}")
    def get_scan(
        run_id: int,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Return scan.

        Parameters
        ----------
        run_id : int
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        row = scoped.get_run(run_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="scan not found")
        actions = [
            {
                "action_name": action["action_name"],
                "asset_id": action["asset_id"],
                "success": None if action["success"] is None else bool(action["success"]),
                "error_code": action["error_code"],
            }
            for action in scoped.list_actions()
            if action["run_id"] == run_id
        ]
        return {
            "run_id": int(row["id"]),
            "status": row["status"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "summary_json": row["summary_json"],
            "actions": actions,
        }

    @router.post("/{run_id}/undo", dependencies=[Depends(require_csrf)])
    def undo_scan(
        run_id: int,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Undo scan.

        Parameters
        ----------
        run_id : int
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        if scoped.get_run(run_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="scan not found")
        try:
            reverted = scoped.revert_run_actions(run_id)
        except PermissionError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="scan not found") from None
        scoped.write_audit(action="scan.undo", run_id=run_id)
        return {"run_id": run_id, "reverted": reverted}

    return router


def build_audit_router() -> APIRouter:
    """Build audit router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(tags=["audit"])

    @router.get("/audit")
    def list_audit(
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """List audit.

        Parameters
        ----------
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        rows = state.with_user(user_id).list_audit()
        return {
            "entries": [
                {
                    "id": int(row["id"]),
                    "at": row["at"],
                    "action": row["action"],
                    "target_asset_id": row["target_asset_id"],
                    "run_id": row["run_id"],
                }
                for row in rows
            ]
        }

    return router


class BootstrapPayload(BaseModel):
    """Represent BootstrapPayload."""

    accept_terms: bool


def build_setup_router() -> APIRouter:
    """Build setup router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(prefix="/setup", tags=["setup"])

    @router.get("/bootstrap")
    def bootstrap_status(
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Bootstrap status.

        Parameters
        ----------
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        terms_accepted = bool(state.get_setting("terms_accepted"))
        users_exist = bool(state.list_users())
        admin_present = state.admin_count() > 0
        return {
            "terms_accepted": terms_accepted,
            "users_exist": users_exist,
            "admin_present": admin_present,
            "ready": terms_accepted and admin_present,
        }

    @router.post("/bootstrap", status_code=status.HTTP_200_OK)
    def bootstrap(
        body: BootstrapPayload,
        request: Request,
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        # Bootstrap is unauthenticated by design — it runs on a fresh
        # container before any user exists. Once the terms are
        # recorded, the endpoint refuses re-bootstrap to avoid an
        # anonymous reset of the system.
        """Bootstrap.

        Parameters
        ----------
        body : BootstrapPayload
        request : Request
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        if state.get_setting("terms_accepted"):
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail="bootstrap already completed"
            )
        if not body.accept_terms:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="terms must be accepted"
            )
        from datetime import datetime

        accepted_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        state.set_setting(
            "terms_accepted",
            {"accepted_at": accepted_at, "remote_ip": client_ip(
                request, request.app.state.config
            )},
        )
        log.info(
            "bootstrap completed",
            extra={"event": "bootstrap.complete", "accepted_at": accepted_at},
        )
        return {"terms_accepted": True, "accepted_at": accepted_at}

    return router


def build_models_router() -> APIRouter:
    """Build models router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(prefix="/models", tags=["models"])

    def _entry_to_dict(entry: _catalog.CatalogEntry, installed_sha: set[str]) -> dict[str, Any]:
        return {
            "id": entry.id,
            "name": entry.name,
            "kind": entry.kind,
            "task": entry.task,
            "status": entry.status,
            "description": entry.raw.get("description"),
            "license": entry.license,
            "license_url": entry.license_url,
            "size_bytes": entry.size_bytes,
            "sha256": entry.sha256,
            "presets": list(entry.presets),
            "installed": entry.sha256 in installed_sha,
            "installable": entry.installable,
        }

    @router.get("/catalog")
    def get_catalog(
        request: Request,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Return catalog.

        Parameters
        ----------
        request : Request
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        catalog_path = getattr(request.app.state, "catalog_path", None)
        try:
            entries = _catalog.load_catalog(catalog_path)
        except _catalog.CatalogError as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
            ) from exc
        installed_sha = {
            row.sha256
            for row in _lifecycle.list_installed(
                conn=state._conn, data_dir=request.app.state.config.data_dir
            )
        }
        return {"models": [_entry_to_dict(e, installed_sha) for e in entries]}

    @router.get("")
    def get_installed(
        request: Request,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Return installed.

        Parameters
        ----------
        request : Request
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        installed = _lifecycle.list_installed(
            conn=state._conn, data_dir=request.app.state.config.data_dir
        )
        return {
            "installed": [
                {
                    "id": m.id,
                    "name": m.name,
                    "version": m.version,
                    "sha256": m.sha256,
                    "license": m.license,
                    "kind": m.kind,
                    "active_slot": m.active_slot,
                    "active": m.active,
                    "present_on_disk": m.path is not None,
                }
                for m in installed
            ]
        }

    @router.post(
        "/adult-subtype-profile",
        dependencies=[Depends(require_csrf), Depends(require_admin)],
        status_code=status.HTTP_201_CREATED,
    )
    def register_adult_subtype_profile(
        body: AdultSubtypeProfilePayload,
        request: Request,
        user_id: Annotated[str, Depends(require_admin)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Register adult subtype profile.

        Parameters
        ----------
        body : AdultSubtypeProfilePayload
        request : Request
        user_id : Annotated[str, Depends(require_admin)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        try:
            installed = _lifecycle.register_adult_subtype_model(
                model_id=body.model_id,
                name=body.name,
                model_path=body.model_path,
                output_labels=body.output_labels,
                thresholds=body.thresholds,
                admin_acknowledged=body.admin_acknowledgement,
                input_size=body.input_size,
                input_mean=body.input_mean,
                input_std=body.input_std,
                input_name=body.input_name,
                output_name=body.output_name,
                data_dir=request.app.state.config.data_dir,
                conn=state._conn,
                actor_user_id=user_id,
            )
        except _lifecycle.HashMismatch as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=f"hash mismatch: {exc}"
            ) from exc
        except _lifecycle.InstallError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        cache = getattr(request.app.state, "classifier_cache", None)
        if cache is not None:
            cache.invalidate()
        return {
            "id": installed.id,
            "model_id": installed.version,
            "name": installed.name,
            "sha256": installed.sha256,
            "active": installed.active,
            "active_slot": installed.active_slot,
        }

    @router.post(
        "/install",
        dependencies=[Depends(require_csrf), Depends(require_admin)],
        status_code=status.HTTP_201_CREATED,
    )
    def install(
        body: InstallModelPayload,
        request: Request,
        user_id: Annotated[str, Depends(require_admin)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Install.

        Parameters
        ----------
        body : InstallModelPayload
        request : Request
        user_id : Annotated[str, Depends(require_admin)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        catalog_path = getattr(request.app.state, "catalog_path", None)
        try:
            entries = _catalog.load_catalog(catalog_path)
        except _catalog.CatalogError as exc:
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
            ) from exc
        entry = _catalog.find_entry(entries, body.model_id)
        if entry is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="unknown model id")
        if not entry.installable:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"model {entry.id} is not installable (status={entry.status})",
            )
        try:
            installed = _lifecycle.install_model(
                entry=entry,
                data_dir=request.app.state.config.data_dir,
                conn=state._conn,
                actor_user_id=user_id,
                license_accepted=body.license_accepted,
            )
        except _lifecycle.HashMismatch as exc:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, detail=f"hash mismatch: {exc}"
            ) from exc
        except _lifecycle.InstallError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {
            "id": installed.id,
            "model_id": installed.version,
            "name": installed.name,
            "sha256": installed.sha256,
            "active": installed.active,
        }

    @router.delete(
        "/{registry_id}",
        dependencies=[Depends(require_csrf), Depends(require_admin)],
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def uninstall(
        registry_id: int,
        request: Request,
        user_id: Annotated[str, Depends(require_admin)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> Response:
        """Uninstall.

        Parameters
        ----------
        registry_id : int
        request : Request
        user_id : Annotated[str, Depends(require_admin)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        Response
        """
        try:
            _lifecycle.uninstall_model(
                registry_id=registry_id,
                data_dir=request.app.state.config.data_dir,
                conn=state._conn,
                actor_user_id=user_id,
            )
        except _lifecycle.InstallError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        cache = getattr(request.app.state, "classifier_cache", None)
        if cache is not None:
            cache.invalidate()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


def build_events_router() -> APIRouter:
    """Build events router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(prefix="/me/events", tags=["events"])

    @router.get("")
    def list_events(
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """List events.

        Parameters
        ----------
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        return {"events": state.with_user(user_id).list_event_groups()}

    @router.get("/{event_id}")
    def get_event(
        event_id: str,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
        cursor: Annotated[str | None, Query(max_length=256)] = None,
        page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> dict[str, Any]:
        """Return event.

        Parameters
        ----------
        event_id : str
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]
        cursor : Annotated[str | None, Query(max_length=256)], optional
        page_size : Annotated[int, Query(ge=1, le=100)], optional

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        event = scoped.get_event_group(event_id)
        if event is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found")
        assets, next_cursor = scoped.list_event_assets_paginated(
            event_id=event_id,
            cursor=cursor,
            page_size=page_size,
        )
        return {"event": event, "assets": assets, "next_cursor": next_cursor}

    @router.post("/{event_id}/rename", dependencies=[Depends(require_csrf)])
    def rename_event(
        event_id: str,
        body: EventRenamePayload,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Rename event.

        Parameters
        ----------
        event_id : str
        body : EventRenamePayload
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        try:
            event = state.with_user(user_id).rename_event_group(
                event_id=event_id,
                title=body.title,
            )
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"event": event}

    @router.post("/merge", dependencies=[Depends(require_csrf)])
    def merge_events(
        body: EventMergePayload,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Merge events.

        Parameters
        ----------
        body : EventMergePayload
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        try:
            event = state.with_user(user_id).merge_event_groups(
                target_event_id=body.target_event_id,
                source_event_ids=body.source_event_ids,
            )
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"event": event}

    @router.post("/{event_id}/split", dependencies=[Depends(require_csrf)])
    def split_event(
        event_id: str,
        body: EventSplitPayload,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Split event.

        Parameters
        ----------
        event_id : str
        body : EventSplitPayload
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        try:
            event = state.with_user(user_id).split_event_group(
                event_id=event_id,
                asset_ids=body.asset_ids,
                title=body.title,
            )
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found") from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"event": event}

    @router.post(
        "/{event_id}/assets/{asset_id}/remove",
        dependencies=[Depends(require_csrf)],
    )
    def remove_event_asset(
        event_id: str,
        asset_id: str,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Remove event asset.

        Parameters
        ----------
        event_id : str
        asset_id : str
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        try:
            state.with_user(user_id).remove_asset_from_event(
                event_id=event_id,
                asset_id=asset_id,
            )
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event asset not found") from exc
        return {"event_id": event_id, "asset_id": asset_id, "removed": True}

    @router.post("/{event_id}/reset", dependencies=[Depends(require_csrf)])
    def reset_event(
        event_id: str,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Reset event.

        Parameters
        ----------
        event_id : str
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        try:
            result = state.with_user(user_id).reset_event_group(event_id=event_id)
        except LookupError as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="event not found") from exc
        return result

    return router


def build_assets_router() -> APIRouter:
    """Build assets router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(tags=["assets"])

    @router.get("/me/assets")
    def list_user_assets(
        request: Request,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
        cipher: Annotated[AesGcmCipher, Depends(get_cipher)],
        immich: Annotated[httpx.Client, Depends(get_immich_client)],
        cursor: Annotated[str | None, Query(max_length=256)] = None,
        page_size: Annotated[int, Query(ge=1, le=100)] = 25,
        queue: Annotated[str | None, Query(max_length=64)] = None,
        media_kind: Annotated[str | None, Query(max_length=32)] = None,
        event_id: Annotated[str | None, Query(max_length=256)] = None,
        q: Annotated[str | None, Query(max_length=256)] = None,
        search_mode: Annotated[str, Query(pattern="^(metadata|semantic)$")] = "metadata",
    ) -> dict[str, Any]:
        """List user assets.

        Parameters
        ----------
        request : Request
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]
        cipher : Annotated[AesGcmCipher, Depends(get_cipher)]
        immich : Annotated[httpx.Client, Depends(get_immich_client)]
        cursor : Annotated[str | None, Query(max_length=256)], optional
        page_size : Annotated[int, Query(ge=1, le=100)], optional
        queue : Annotated[str | None, Query(max_length=64)], optional
        media_kind : Annotated[str | None, Query(max_length=32)], optional
        event_id : Annotated[str | None, Query(max_length=256)], optional
        q : Annotated[str | None, Query(max_length=256)], optional
        search_mode : Annotated[str, Query(pattern='^(metadata|semantic)$')], optional

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        if search_mode == "semantic" and q and q.strip():
            return _semantic_asset_search_response(
                request=request,
                state=state,
                scoped=scoped,
                cipher=cipher,
                immich=immich,
                cursor=cursor,
                page_size=page_size,
                queue=queue,
                media_kind=media_kind,
                event_id=event_id,
                query=q,
            )
        if queue or media_kind or event_id or q:
            rows, next_cursor = scoped.list_review_assets_paginated(
                cursor=cursor,
                page_size=page_size,
                queue=queue,
                media_kind=media_kind,
                event_id=event_id,
                q=q,
            )
        else:
            rows, next_cursor = scoped.list_user_assets_paginated(
                cursor=cursor, page_size=page_size
            )
        return {
            "assets": _search.annotate_search_rows(rows, []),
            "next_cursor": next_cursor,
            "search_mode": search_mode,
            "search_source": "metadata",
            "search_unavailable_reason": None,
        }

    @router.get("/me/assets/{asset_id}")
    def get_user_asset_detail(
        asset_id: str,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Return user asset detail.

        Parameters
        ----------
        asset_id : str
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        if not scoped.asset_id_in_user_actions(asset_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="asset not found")
        analysis = scoped.get_asset_analysis(asset_id)
        if analysis is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, detail="asset analysis not found"
            )
        return {"asset_id": asset_id, "analysis": analysis}

    @router.post(
        "/me/assets/{asset_id}/category",
        dependencies=[Depends(require_csrf)],
    )
    def set_asset_category(
        asset_id: str,
        body: AssetCategoryOverridePayload,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
    ) -> dict[str, Any]:
        """Set asset category.

        Parameters
        ----------
        asset_id : str
        body : AssetCategoryOverridePayload
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]

        Returns
        -------
        dict[str, Any]
        """
        scoped = state.with_user(user_id)
        # Cross-tenant isolation: only assets that surfaced in this
        # user's runs can be overridden.
        if not scoped.asset_id_in_user_actions(asset_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="asset not found")
        before = scoped.set_asset_override(
            asset_id=asset_id, category_id=body.category_id, reason="manual"
        )
        scoped.write_audit(
            action="asset.category.override",
            target_asset_id=asset_id,
            before_state=before,
            after_state=body.category_id,
        )
        return {
            "asset_id": asset_id,
            "category_id": body.category_id,
            "before": before,
        }

    @router.get("/assets/{asset_id}/preview")
    def get_asset_preview(
        asset_id: str,
        request: Request,
        user_id: Annotated[str, Depends(get_current_user)],
        state: Annotated[StateStore, Depends(get_state)],
        cipher: Annotated[AesGcmCipher, Depends(get_cipher)],
        config: Annotated[ServiceConfig, Depends(get_service_config)],
        immich: Annotated[httpx.Client, Depends(get_immich_client)],
    ) -> StreamingResponse:
        # Authorisation gate: 404 (not 403) for any asset that is not
        # already in this user's actions table. Threat-model T05 + T13.
        """Return asset preview.

        Parameters
        ----------
        asset_id : str
        request : Request
        user_id : Annotated[str, Depends(get_current_user)]
        state : Annotated[StateStore, Depends(get_state)]
        cipher : Annotated[AesGcmCipher, Depends(get_cipher)]
        config : Annotated[ServiceConfig, Depends(get_service_config)]
        immich : Annotated[httpx.Client, Depends(get_immich_client)]

        Returns
        -------
        StreamingResponse
        """
        scoped = state.with_user(user_id)
        if not scoped.asset_id_in_user_actions(asset_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="asset not found")

        session_id = request.state.session_id
        row = _auth.lookup_session(conn=state._conn, session_id=session_id)
        if row is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="no session")
        bearer: str | None = _auth.decrypt_session_token(cipher=cipher, row=row)

        upstream_path = _ASSET_PREVIEW_PATH.format(asset_id=asset_id)
        try:
            req = immich.build_request(
                "GET",
                upstream_path,
                params={"size": "preview"},
                headers={"Authorization": f"Bearer {bearer}"},
            )
            upstream = immich.send(req, stream=True)
        except httpx.HTTPError as exc:
            bearer = None
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, detail="upstream Immich unreachable"
            ) from exc
        finally:
            # Defensive: drop the local reference. Python str is
            # immutable so we cannot zero the memory, but no callsite
            # below this line holds the bearer.
            bearer = None

        if upstream.status_code == 404:
            upstream.close()
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="asset not found")
        if upstream.status_code >= 400:
            upstream.close()
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY, detail="upstream Immich error"
            )

        media_type = upstream.headers.get("content-type", "application/octet-stream")

        def _iter() -> Iterator[bytes]:
            try:
                yield from upstream.iter_bytes()
            finally:
                upstream.close()

        response = StreamingResponse(_iter(), media_type=media_type)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Content-Disposition"] = "inline"
        return response

    return router


def _semantic_asset_search_response(
    *,
    request: Request,
    state: StateStore,
    scoped: Any,
    cipher: AesGcmCipher,
    immich: httpx.Client,
    cursor: str | None,
    page_size: int,
    queue: str | None,
    media_kind: str | None,
    event_id: str | None,
    query: str,
) -> dict[str, Any]:
    bearer: str | None = None
    try:
        bearer = _current_bearer(request=request, state=state, cipher=cipher)
        provider = _search.ImmichSmartSearchProvider(
            client=immich,
            bearer_token=bearer,
        )
        page = provider.search(
            query=query,
            cursor=cursor,
            page_size=page_size,
            queue=queue,
            media_kind=media_kind,
            event_id=event_id,
        )
        rows = scoped.list_user_asset_rows_by_ids(
            [hit.asset_id for hit in page.hits],
            queue=queue,
            media_kind=media_kind,
            event_id=event_id,
        )
        return {
            "assets": _search.annotate_search_rows(rows, page.hits),
            "next_cursor": page.next_cursor,
            "search_mode": "semantic",
            "search_source": provider.source,
            "search_unavailable_reason": None,
        }
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except _search.SearchUnavailable as exc:
        fallback_provider = _search.MetadataSearchProvider(
            scoped_state=scoped,
            source="metadata_fallback",
        )
        page = fallback_provider.search(
            query=query,
            cursor=cursor if cursor and not cursor.startswith("semantic:") else None,
            page_size=page_size,
            queue=queue,
            media_kind=media_kind,
            event_id=event_id,
        )
        rows = scoped.list_user_asset_rows_by_ids(
            [hit.asset_id for hit in page.hits],
            queue=queue,
            media_kind=media_kind,
            event_id=event_id,
        )
        return {
            "assets": _search.annotate_search_rows(rows, page.hits),
            "next_cursor": page.next_cursor,
            "search_mode": "semantic",
            "search_source": fallback_provider.source,
            "search_unavailable_reason": exc.reason,
        }
    finally:
        bearer = None


def _current_bearer(
    *,
    request: Request,
    state: StateStore,
    cipher: AesGcmCipher,
) -> str:
    session_id = request.state.session_id
    row = _auth.lookup_session(conn=state._conn, session_id=session_id)
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="no session")
    return _auth.decrypt_session_token(cipher=cipher, row=row)


def build_health_router() -> APIRouter:
    """Build health router.

    Returns
    -------
    APIRouter
    """
    router = APIRouter(tags=["health"])

    @router.get("/health")
    def health() -> dict[str, Any]:
        """Health.

        Returns
        -------
        dict[str, Any]
        """
        return {"status": "ok"}

    @router.get("/health/ready")
    def ready(
        state: Annotated[StateStore, Depends(get_state)],
        immich: Annotated[httpx.Client, Depends(get_immich_client)],
    ) -> dict[str, Any]:
        """Ready.

        Parameters
        ----------
        state : Annotated[StateStore, Depends(get_state)]
        immich : Annotated[httpx.Client, Depends(get_immich_client)]

        Returns
        -------
        dict[str, Any]
        """
        details = {"db": "ok", "immich": "unknown"}
        try:
            state._conn.execute("SELECT 1")
        except Exception:
            details["db"] = "fail"
        compatibility = check_immich_compatibility(immich, timeout=2.0)
        compatibility_status = str(compatibility["status"])
        details["immich"] = "ok" if compatibility_status == "ok" else compatibility_status
        ok = all(value == "ok" for value in details.values())
        return {
            "status": "ok" if ok else "degraded",
            **details,
            "compatibility": compatibility,
        }

    return router


def build_admin_config_router() -> APIRouter:
    """Admin system settings stored in config.db."""
    router = APIRouter(prefix="/admin/config", tags=["admin-config"])

    @router.get("")
    def get_system_config(request: Request) -> dict:
        nested = getattr(request.app.state, "system_config_nested", None) or {}
        return nested.get("system") or {}

    @router.patch("/{key_path:path}")
    def patch_system_config(
        key_path: str,
        body: dict,
        request: Request,
        user_id: Annotated[str, Depends(require_admin)],
    ) -> dict:
        del user_id
        from mediarefinery.settings.load import ensure_config_db_seeded

        data_dir = request.app.state.config.data_dir
        repo = ensure_config_db_seeded(data_dir)
        value = body.get("value")
        repo.upsert(f"system.{key_path}", value)
        request.app.state.system_config_nested = repo.get_nested()
        return {"key": key_path, "value": value}

    return router


__all__ = [
    "ApiKeyPayload",
    "AdultSubtypeProfilePayload",
    "AssetCategoryOverridePayload",
    "AutoScanSettingsPayload",
    "CategoriesPayload",
    "LoginRequest",
    "MeResponse",
    "PoliciesPayload",
    "ScanResponse",
    "BootstrapPayload",
    "build_admin_config_router",
    "build_assets_router",
    "build_audit_router",
    "build_auth_router",
    "build_events_router",
    "build_health_router",
    "build_me_config_router",
    "build_me_router",
    "build_models_router",
    "build_scans_router",
    "build_setup_router",
]
