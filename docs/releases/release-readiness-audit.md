# Release Readiness Audit

Audit date: 2026-05-10; 2.1.0 refresh: 2026-05-11

Result: **ready for the `2.1.0` public release in this workspace after final CI passes. Maintainers should tag `2.1.0` only after the release branch checks pass.**

This audit checked the local repository, service code, frontend, packaging, docs, and available quality gates. It does not replace a formal security review.

2.1.0 update on 2026-05-11: repository metadata targets `2.1.0`, the public service API uses version-neutral `/api` routes, operator docs use version-neutral paths, and final release artifacts are expected under the `2.1.0` tag.

## Verification Run

2.1.0 checks run:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,service,onnx,ocr]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit --local --skip-editable
npm run typecheck
npm test -- --run
npm run build
npm audit
.\.venv\Scripts\python.exe -m pip wheel . --no-deps -w tmp\release-wheel
docker build -t mediarefinery:2.1.0-smoke .
docker volume create mediarefinery-2-1-0-smoke-data
docker run --rm -v mediarefinery-2-1-0-smoke-data:/data mediarefinery:2.1.0-smoke python -c "from pathlib import Path; from mediarefinery.settings.load import ensure_config_db_seeded; from mediarefinery.settings.defaults import default_nested_config; n=default_nested_config(); n['system'].update({'demo_mode': True, 'immich_base_url': 'http://demo.invalid'}); ensure_config_db_seeded(Path('/data')).bulk_upsert(n)"
docker run --rm -d --name mediarefinery-2-1-0-smoke -p 18765:8765 -v mediarefinery-2-1-0-smoke-data:/data mediarefinery:2.1.0-smoke
```

Observed results:

- Editable install: `mediarefinery==2.1.0` with `[dev,service,onnx,ocr]` passed on Python `3.13.11`; Docker Python `3.11` resolved the newer RapidOCR line.
- Python tests: 370 collected -> 369 passed, 1 skipped.
- Frontend tests: 8 files / 43 tests passed.
- Ruff `src tests`: passed.
- `compileall -q src tests`: passed.
- Frontend production build: passed.
- `npm ci`: passed and reported 0 vulnerabilities.
- `npm audit`: 0 vulnerabilities.
- Python vulnerability audit: 0 known vulnerabilities with `pip-audit --local --skip-editable`.
- Python package dependency consistency: `pip check` passed.
- Wheel: `tmp/release-wheel/mediarefinery-2.1.0-py3-none-any.whl`, SHA256 `bff583c7aef93e68529905eab5f1b511014c2b6d53519233b04575461128f554`.
- Wheel inspection confirmed `mediarefinery/web/index.html`, built JS/CSS assets, `docs/models/catalog.json`, and model license docs are present.
- Docker build: `mediarefinery:2.1.0-smoke` passed.
- Docker demo smoke: volume-seeded `config.db` with `system.demo_mode=true`; `/api/health` returned `{"status":"ok"}`; `/api/health/ready` returned `status=ok`, `immich=ok`, compatibility `status=ok`, `server_version=2.7.5`, and `last_live_smoke=2026-05-11`.
- Live Immich smoke: not repeated for the 2.1.0 route, docs, and state-naming refresh. The previous bounded 2026-05-11 smoke against Immich `2.7.5` covered CLI dry-run, service login, API-key validation/storage, MobileNet model install, scan, asset detail, semantic metadata fallback, and a safe review-album action.
- Previous video-constrained live sampling probe: processed 0 assets, 0 errors; the smoke account did not expose suitable video assets for sampling.

Checks with caveats:

- `pip-audit --local --skip-editable` skips the editable local `mediarefinery` project itself and audits installed third-party dependencies.
- The 2.1.0 live Immich smoke was not repeated because this release changes public route naming, documentation paths, and internal state naming only. Automated tests and Docker smoke covered the changed `/api` routes.
- The previous live smoke intentionally did not run an unbounded full-library scan. It checked 17 smoke-visible image assets, one service scan page, one asset detail, and a safe review-album action.
- The previous live smoke account did not expose video/GIF assets, so original frame sampling was revalidated by automated tests and Docker build only, not by a live video/GIF asset.
- Immich Smart Search was unreachable in the previous smoke environment; the service returned the documented `metadata_fallback` semantic-search path.

## Fixes Applied During Audit

- Packaged `src/mediarefinery/web/index.html` and `src/mediarefinery/web/assets/*` into wheels.
- Changed Docker install target from `.[service]` to `.[service,onnx]`.
- Required CSRF on `POST /api/auth/logout`.
- Updated backend tests for the logout CSRF behavior.
- Updated CI Docker smoke test to verify `mediarefinery.service.web.default_web_root()` inside the installed package.
- Pruned the public retired-CLI surface from package metadata and Docker/env examples. The underlying core pipeline module entry point remains for development validation.
- Wired production scan factories in service startup:
  - per-user stored API keys are decrypted for real scans;
  - `HttpImmichClient` is used for active-model scans;
  - `ClassifierSessionCache` loads the active ONNX model;
  - auto-scan uses the same production factories and skips users missing API keys.
- Added opt-in Immich API-key validation before storing dashboard-submitted keys.
- Added first-run wizard API-key setup before starting the first real scan.
- Added backend tests proving active-model scans require an API key and use configured production factories.
- Copied the model catalog into the Docker image so production startup can load it.
- Upgraded the frontend Vite/Vitest toolchain to clear the Vite/esbuild dev-server advisory.
- Added frontend and Python dependency audits to CI; CI also upgrades pip before running `pip-audit`.
- Fixed `.dockerignore` so Docker builds can copy the curated model catalog.
- Rebuilt a wheel locally and confirmed it contains:
  - `mediarefinery/web/index.html`
  - `mediarefinery/web/assets/index-*.css`
  - `mediarefinery/web/assets/index-*.js`
- Updated project, module, frontend, and FastAPI metadata to `2.1.0`.
- Moved the public service API to `/api` and refreshed frontend callers/tests.
- Moved operator docs to version-neutral `docs/` paths.
- Added wheel data files for `docs/models/catalog.json` and model license docs.
- Added an installed-data-files fallback for the model catalog loader.
- Added Python-version-specific RapidOCR dependency markers so the optional OCR extra resolves on Python 3.13 while Docker/Python 3.11 keeps the newer RapidOCR line.
- Relaxed readiness compatibility so `GET /api/server/about` may be auth-required when `GET /api/server/version` reports the supported Immich version.
- Updated the readiness smoke date to 2026-05-11.

## Release Blockers

No release-blocking product gap is intentionally left open for `2.1.0`.

The git tag is produced by the release workflow after maintainers commit the release tree. The expected tag is `2.1.0`; the expected Python artifact is `mediarefinery-2.1.0-py3-none-any.whl`; the expected container tags are `2.1.0`, `2.1`, `2`, and `latest`.

## Recommended Next Work

1. Run the complete [release-checklist.md](release-checklist.md) command set on the exact release commit.

2. Tag the release only after one final CI run, including Docker amd64/arm64 builds, passes.

## Accepted Residual Risks For 2.1.0

- MediaRefinery never performs automatic delete/trash actions.
- Third-party cloud inference is not used by default; local model downloads require SHA256 verification and license acceptance.
- Adult subtype output depends on an operator-supplied compatible ONNX profile. The curated catalog does not ship an act-level adult subtype model.
- Classifier, OCR, adult subtype, and semantic-search outputs are probabilistic review signals.
- Local CLIP/SigLIP embeddings remain deferred until a compatible SHA256-pinned ONNX bundle is selected.
