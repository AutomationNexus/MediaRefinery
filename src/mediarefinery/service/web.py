"""Dashboard static-asset serving and security-headers middleware.

The frontend Vite project at ``frontend/`` builds into
``src/mediarefinery/web/`` so the wheel ships the dashboard as static
assets. This module mounts that directory on the FastAPI app and
attaches the security headers required by the threat model:

- T01 (XSS / cookie theft): a strict Content-Security-Policy with no
  inline script and no third-party origins.
- T11 (no third-party CDNs / fonts / analytics): ``connect-src``,
  ``script-src``, ``style-src``, ``font-src``, ``img-src`` are all
  ``'self'`` with the minimum extras required by Vite output
  (``data:`` for tiny inlined images).
- T18 (CSRF): ``form-action 'self'`` and ``frame-ancestors 'none'``.

Intentionally permissive bits, with reasoning:

- ``style-src 'self' 'unsafe-inline'``: Tailwind generates a single
  bundled stylesheet at build time, but Headless UI and React's
  ``style={{...}}`` props emit inline ``style`` attributes. CSP3
  ``'unsafe-inline'`` *for styles only* is the standard accommodation
  and does not enable script execution. Re-evaluate with hashes if we
  want to tighten further.
- ``img-src 'self' data: blob:``: ``blob:`` is needed for any future
  preview-this-asset flow that pipes Immich-fetched bytes into an
  ``<img>`` without round-tripping through our backend.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from fastapi import FastAPI

CSP_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "font-src 'self'",
        "connect-src 'self'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
)


def _build_middleware_class() -> type:
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import Response as StarletteResponse
    from starlette.types import ASGIApp

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        """Attach CSP and adjacent security headers to every response."""

        def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
            super().__init__(app)
            self._hsts = hsts

        async def dispatch(
            self,
            request: StarletteRequest,
            call_next: Any,
        ) -> StarletteResponse:
            response = await call_next(request)
            response.headers.setdefault("Content-Security-Policy", CSP_POLICY)
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            response.headers.setdefault("Referrer-Policy", "same-origin")
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault(
                "Permissions-Policy",
                "camera=(), microphone=(), geolocation=(), interest-cohort=()",
            )
            if self._hsts:
                response.headers.setdefault(
                    "Strict-Transport-Security",
                    "max-age=31536000; includeSubDomains",
                )
            return cast(StarletteResponse, response)

    return SecurityHeadersMiddleware


def default_web_root() -> Path:
    """Return the bundled dashboard directory shipped with the wheel."""
    return Path(__file__).resolve().parent.parent / "web"


def mount_web(app: FastAPI, *, web_root: Path, hsts: bool) -> None:
    """Attach security-headers middleware and optionally mount the dashboard bundle.

    The middleware is registered unconditionally — even if the bundle
    has not been built (e.g. running pure backend tests) the API
    responses still carry CSP. The static mount only happens when
    ``index.html`` is present, so a missing bundle is not a 500 at
    boot.
    """
    from starlette.staticfiles import StaticFiles

    app.add_middleware(_build_middleware_class(), hsts=hsts)  # type: ignore[arg-type]  # BaseHTTPMiddleware factory shape
    index = web_root / "index.html"
    if index.is_file():
        app.mount(
            "/",
            StaticFiles(directory=str(web_root), html=True),
            name="web",
        )


__all__ = ["CSP_POLICY", "default_web_root", "mount_web"]
