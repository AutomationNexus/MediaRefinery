# Changelog

All notable changes to this project will be documented in this file.

This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation

- Reworked the README as a project front door with Docker quick start, first-run workflow, storage notes, and documentation map.
- Reorganized docs into getting-started, guides, admin, reference, security, development, releases, and architecture sections.
- Added user-facing installation, configuration, dashboard, model-management, API, troubleshooting, repository-structure, support, and maintainer guides.

## [2.1.0] - 2026-05-11

### Changed

- Moved the public service API to version-neutral `/api` routes.
- Renamed the service state module and default SQLite file to version-neutral names.
- Moved operator docs to top-level docs paths.
- Updated package, module, frontend, and FastAPI metadata to `2.1.0`.
- Updated release workflow tagging to use semantic-version tags such as `2.1.0`.

### Documentation

- Refreshed README first-use guidance, operations, release checklist, release audit, security, and compatibility docs for the 2.1.0 release.

## [2.0.0] - 2026-05-11

### Added

- Added optional local OCR through a SHA256-pinned RapidOCR/PaddleOCR ONNX English bundle, with searchable derived OCR text and asset-detail display.
- Added separate OCR model activation so installing an OCR bundle does not replace the active image classifier.
- Added dashboard semantic search through Immich Smart Search with explicit metadata/OCR fallback and per-result source/score fields.
- Added admin-registered adult subtype ONNX profiles in a separate model slot, with explicit labels, thresholds, fail-closed validation, and subtype review queues.
- Added persisted event groups with automatic date/place/people/album/semantic grouping, dashboard rename/merge/split/remove/reset controls, asset filtering by event, and audit entries for manual event edits.

### Fixed

- Set package, module, and FastAPI metadata to the `2.0.0` final release version.
- Added Immich readiness compatibility reporting for `/api/server/version` and `/api/server/about`.
- Included the built dashboard assets and model catalog docs in Python wheels via package/data files.
- Required CSRF validation on `POST /api/auth/logout`.
- Updated the Docker runtime install target to include ONNX dependencies.
- Tightened the Docker CI smoke check so it verifies the installed package web root, not only the source-tree bundle.
- Removed the retired CLI console entrypoint from package metadata so the public package exposes the service entrypoint only.
- Wired production scan factories in service startup so active-model scans use stored user API keys, `HttpImmichClient`, and `ClassifierSessionCache`.
- Required a stored Immich API key before active-model scans and skipped auto-scan users that are not ready.
- Added opt-in Immich API-key validation and a first-run wizard step for saving the key before the first scan.
- Copied the curated model catalog into the Docker image.
- Upgraded Vite/Vitest tooling to clear the Vite/esbuild audit advisory.
- Added CI dependency audits for frontend packages and Python packages.
- Fixed `.dockerignore` so Docker builds include the curated model catalog.
- Added Python-version-specific RapidOCR dependency markers so the optional OCR extra resolves on Python 3.13 while keeping the newer RapidOCR line on Python 3.11/3.12.
- Verified the Docker image and a bounded live Immich `2.7.5` smoke locally.

### Documentation

- Rewrote user-facing README, frontend README, security policy, operations notes, threat model, and Immich compatibility notes to remove stale phase language and current-state overclaims.
- Added [docs/releases/release-readiness-audit.md](docs/releases/release-readiness-audit.md) with local verification results and release blockers.
- Added [docs/releases/release-checklist.md](docs/releases/release-checklist.md) with the release verification commands.
- Pruned retired CLI guidance from user-facing Docker and environment examples.

### Status

- `2.0.0` public release.
- Known limitations remain explicit: no automatic delete/trash actions; no third-party cloud inference by default; adult subtype results require an operator-supplied compatible profile; classifier, OCR, and semantic-search results are probabilistic review signals.
