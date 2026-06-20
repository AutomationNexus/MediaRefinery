# Threat Model

Status: current for the `2.1.x` release line.

This is not a formal security audit. It records the boundaries, controls, tests, and known gaps that matter before operating or changing MediaRefinery.

## Scope

In scope:

- FastAPI service and React dashboard.
- Immich proxy login and session handling.
- Per-user encrypted Immich session tokens and API keys.
- SQLite state under `/data`.
- Model catalog downloads and ONNX inference.
- Derived asset analysis and search metadata: classifier scores, acknowledged adult subtype profile scores, Immich People metadata, duplicate IDs, local or metadata-supplied OCR text, quality flags, video/GIF sampling status, semantic terms, Smart Search source/score indicators, event keys, and local event group edit state.
- Locked Folder lock and unlock/revert flows.
- Docker and Compose defaults in this repository.

Out of scope:

- Immich's own security.
- Operator host hardening.
- Reverse proxy configuration beyond documented expectations.
- User browser extensions or compromised client devices.

## Trust Boundaries

| Boundary | Notes |
|----------|-------|
| Browser to MediaRefinery | Should be HTTPS in real deployments. Cookies are signed; session cookie is HttpOnly. |
| MediaRefinery to Immich | Uses `system.immich_base_url` from `config.db`. User tokens/API keys are held server-side in `state.db`. |
| MediaRefinery to disk | `config.db`, `state.db`, model files, and master key live under `/data`. |
| MediaRefinery to model origins | Catalog URLs must be HTTPS and SHA256-pinned. |

## Implemented Controls

| Area | Current control |
|------|-----------------|
| Session cookies | Signed opaque session id; server-side session rows; logout revokes row and calls Immich logout. |
| Cookie transport | `system.base_url=https://...` in `config.db` makes auth cookies `Secure` and enables HSTS. |
| CSRF | Authenticated state-changing routes require `X-CSRF-Token` matching the readable `mr_csrf` cookie. Logout is covered. Bootstrap is unauthenticated and one-time by design. |
| Login abuse | In-memory per-IP rate limit, default 5 attempts per minute. |
| Stored secrets | AES-256-GCM encryption for Immich session tokens and stored API keys. Master key at `/data/master.key`. |
| Multi-tenancy | User-scoped tables carry `user_id`; most reads and writes go through `StateStore.with_user`. |
| Model downloads and profiles | Curated downloads use HTTPS-only URLs, SHA256 verification, size checks, partial-file cleanup, and license acceptance audit. User-supplied adult subtype profiles copy a server-local ONNX file into managed model storage, compute SHA256, require admin acknowledgement, and reject binary-only labels. |
| Static assets | Strict CSP with no inline script and no third-party origins. Inline style is allowed for React/Headless UI style attributes only. |
| Logs | Structured JSON logging; tests assert passwords, tokens, PINs, preview bytes, and sampled original/frame bytes are not emitted in key flows. |
| Account purge | `DELETE /api/me` deletes user-scoped rows and anonymizes audit rows to `user_deleted`. |
| Production scans | Active-model scans require a stored user API key, decrypt it server-side, use `HttpImmichClient`, and load active ONNX models through `ClassifierSessionCache`. Adult subtype inference uses a separate optional slot and runs only after the primary classifier marks an asset sensitive. |
| Asset bytes | Preview/original/frame bytes are kept ephemeral. Opt-in video/GIF sampling writes originals and frames only under temp directories with size/duration/frame limits and cleanup. Local OCR receives in-memory previews or sampled frames and persists derived text, confidence, frame indexes, and model metadata, not media bytes or crops. |
| Semantic search | Dashboard semantic search calls Immich Smart Search with the signed-in user's bearer token and maps returned IDs back to assets already present in that user's MediaRefinery action history. Search queries are forwarded to Immich but are not stored in SQLite. |
| Event groups | Event rename, merge, split, remove, and reset are local review-state edits. They are tenant scoped, CSRF-protected, audited as `event.*` actions, and do not call Immich write endpoints. |
| Adult subtype labels | Subtype labels come only from acknowledged profile output labels. Unknown outputs fail closed, low-confidence top labels enter `review_needed`, and subtype labels are not added to automatic action policy categories. |
| Face identity | MediaRefinery stores Immich People names/IDs only; it does not create a separate biometric identity database. |

## Accepted Residual Risks

| Gap | Risk |
|-----|------|
| Immich version compatibility is checked in readiness, not as a hard startup gate. | Unsupported Immich versions or endpoint-shape drift report `degraded`; operators must block release promotion until smoke passes. `server/about` may be auth-required, so `server/version` is the load-bearing unauthenticated version probe. |
| OCR text and people names increase SQLite sensitivity. | Operators must protect `/data/state.db`; account purge removes user-scoped analysis rows. |

## Verification Matrix

| Threat | Verified by |
|--------|-------------|
| XSS/cookie theft controls | `tests/service/test_web_static.py::test_security_headers_on_api_response`, cookie signer tests in `tests/service/test_security_helpers.py`. |
| CSRF on mutations | `tests/service/test_phase_b_e2e.py::test_csrf_required_for_mutations`. |
| Login secret handling | `tests/service/test_auth_proxy.py::test_privacy_login_failure_does_not_log_password`, `test_privacy_no_secrets_in_responses_or_logs`. |
| Login rate limit | `tests/service/test_auth_proxy.py::test_login_rate_limit_kicks_in`. |
| Encryption at rest | `tests/service/test_security.py`. |
| Multi-tenant isolation | `tests/service/test_state_store.py`, `tests/service/test_phase_b_e2e.py`, `tests/service/test_phase_d_e2e.py`, `tests/service/test_auto_scan.py`. |
| Model download integrity | `tests/service/test_model_catalog.py`, `tests/service/test_model_lifecycle.py`. |
| Locked Folder PIN privacy | `tests/service/test_locked_folder_unlock.py`, `tests/service/test_phase_d_e2e.py`. |
| Account purge | `tests/service/test_account_purge.py`. |
| Production scan wiring and API-key requirement | `tests/service/test_production_wiring.py`. |
| Asset analysis storage, OCR, adult subtype queues, and queue filtering | `tests/test_analysis.py`, `tests/test_ocr.py`, `tests/service/test_real_runner.py`. |
| Semantic search tenant mapping and fallback | `tests/service/test_semantic_search.py`. |
| Event group persistence, manual edit policy, audit, and tenant isolation | `tests/service/test_event_groups.py`, `frontend/src/test/Events.test.tsx`. |
| Frontend no-storage privacy | Frontend tests under `frontend/src/test/*`. |

## Release Rule

No release should be promoted until:

1. The known gaps above are closed or explicitly accepted in release notes.
2. Python tests, frontend tests, Ruff, frontend build, package wheel inspection, Docker build, live Immich smoke, and vulnerability audits all pass.
3. The README and user/operator docs describe current behavior and limitations without stale setup or safety guidance.
