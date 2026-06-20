"""FastAPI application factory and uvicorn entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from fastapi import FastAPI

from . import auto_scan as _auto_scan
from . import model_catalog as _catalog
from . import production as _production
from .classifier_cache import ClassifierSessionCache
from .config import ServiceConfig, load_service_config
from .security import (
    AesGcmCipher,
    InMemoryRateLimiter,
    SessionCookieSigner,
    configure_json_logging,
    derive_cookie_signing_key,
    load_or_create_master_key,
)
from .state_store import StateStore

API_PREFIX = "/api"


def create_app(
    *,
    config: ServiceConfig | None = None,
    web_root: Path | None = None,
) -> FastAPI:
    """Build the FastAPI app.

    *config* and *web_root* are injectable for tests; production callers
    omit them and load system settings from ``config.db``.
    """
    from fastapi import FastAPI

    if config is None:
        config = load_service_config()

    logger = configure_json_logging()
    if config.demo_mode:
        logger.warning(
            "demo_mode active - synthetic data only, do not connect a real Immich",
            extra={"event": "demo_mode.active"},
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        master_key = load_or_create_master_key(path=config.master_key_path)
        store = StateStore(config.state_db_path)
        store.initialize()
        cipher = AesGcmCipher(master_key.key)
        signing_key = derive_cookie_signing_key(master_key.key)
        signer = SessionCookieSigner(
            signing_key, max_age_seconds=config.session_ttl_seconds
        )
        if config.demo_mode:
            from .demo_fixtures import (
                build_demo_immich_client,
                build_demo_runner_factories,
                seed_demo_model,
            )

            immich_client = build_demo_immich_client(
                base_url=config.immich_base_url
            )
            seed_demo_model(store._conn)
            app.state.runner_factories = build_demo_runner_factories()
            app.state.runner_requires_api_key = False
            app.state.classifier_cache = None
        else:
            immich_client = httpx.Client(
                base_url=config.immich_base_url, timeout=10.0
            )
            catalog_path = getattr(app.state, "catalog_path", None)
            catalog = _catalog.load_catalog(catalog_path)
            classifier_cache = ClassifierSessionCache(
                models_dir=config.data_dir / "models",
                catalog=catalog,
            )
            app.state.classifier_cache = classifier_cache
            app.state.runner_factories = _production.build_runner_factories(
                store=store,
                cipher=cipher,
                config=config,
                classifier_cache=classifier_cache,
            )
            app.state.runner_requires_api_key = True
        app.state.api_key_validator = lambda api_key: _production.validate_api_key(
            config=config,
            api_key=api_key,
        )
        login_limiter = InMemoryRateLimiter(
            max_events=config.login_rate_per_min, window_seconds=60.0
        )
        app.state.config = config
        from mediarefinery.settings.load import load_nested_system_config

        app.state.system_config_nested = load_nested_system_config(config.data_dir)
        app.state.store = store
        app.state.cipher = cipher
        app.state.signer = signer
        app.state.immich_client = immich_client
        app.state.login_limiter = login_limiter
        app.state.auto_scan_scheduler = None
        if config.auto_scan_enabled:
            from apscheduler.schedulers.background import BackgroundScheduler

            scheduler = BackgroundScheduler(timezone="UTC")
            scheduler.add_job(
                _auto_scan.make_coordinator_callable(
                    store=store,
                    cipher=cipher,
                    immich_client=immich_client,
                    base_url=config.immich_base_url,
                    runner_factories_provider=lambda: getattr(
                        app.state, "runner_factories", None
                    ),
                ),
                trigger="interval",
                seconds=_auto_scan.COORDINATOR_INTERVAL_SECONDS,
                id="auto_scan_coordinator",
                max_instances=1,
                coalesce=True,
            )
            scheduler.start()
            app.state.auto_scan_scheduler = scheduler
        try:
            yield
        finally:
            if app.state.auto_scan_scheduler is not None:
                app.state.auto_scan_scheduler.shutdown(wait=False)
            immich_client.close()
            store.close()

    app = FastAPI(
        title="MediaRefinery",
        version="2.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
        lifespan=lifespan,
    )

    from .routers import (
        build_admin_config_router,
        build_assets_router,
        build_audit_router,
        build_auth_router,
        build_events_router,
        build_health_router,
        build_me_config_router,
        build_me_router,
        build_models_router,
        build_scans_router,
        build_setup_router,
    )

    app.include_router(build_setup_router(), prefix=API_PREFIX)
    app.include_router(build_auth_router(), prefix=API_PREFIX)
    app.include_router(build_me_router(), prefix=API_PREFIX)
    app.include_router(build_me_config_router(), prefix=API_PREFIX)
    app.include_router(build_scans_router(), prefix=API_PREFIX)
    app.include_router(build_audit_router(), prefix=API_PREFIX)
    app.include_router(build_events_router(), prefix=API_PREFIX)
    app.include_router(build_assets_router(), prefix=API_PREFIX)
    app.include_router(build_models_router(), prefix=API_PREFIX)
    app.include_router(build_admin_config_router(), prefix=API_PREFIX)
    app.include_router(build_health_router(), prefix=API_PREFIX)

    from .web import default_web_root, mount_web

    bundle_root = web_root if web_root is not None else default_web_root()
    mount_web(app, web_root=bundle_root, hsts=config.cookie_secure)
    return app


def run() -> None:
    """Run.

    Returns
    -------
    None
    """
    import uvicorn

    config = load_service_config()
    uvicorn.run(
        "mediarefinery.service.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8080,
        forwarded_allow_ips=",".join(config.trusted_proxies) or None,
    )


__all__ = ["API_PREFIX", "create_app", "run"]
