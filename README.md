# MediaRefinery

[![CI](https://github.com/automationnexus/MediaRefinery/actions/workflows/ci.yml/badge.svg?branch=dev)](https://github.com/automationnexus/MediaRefinery/actions/workflows/ci.yml)
[![Release](https://github.com/automationnexus/MediaRefinery/actions/workflows/release.yml/badge.svg)](https://github.com/automationnexus/MediaRefinery/actions/workflows/release.yml)
[![Nightly](https://github.com/automationnexus/MediaRefinery/actions/workflows/nightly.yml/badge.svg)](https://github.com/automationnexus/MediaRefinery/actions/workflows/nightly.yml)
[![Semgrep](https://github.com/automationnexus/MediaRefinery/actions/workflows/semgrep.yml/badge.svg?branch=dev)](https://github.com/automationnexus/MediaRefinery/actions/workflows/semgrep.yml)
[![Deploy Docs](https://github.com/automationnexus/MediaRefinery/actions/workflows/docs.yml/badge.svg?branch=main)](https://github.com/automationnexus/MediaRefinery/actions/workflows/docs.yml)
[![Security](https://img.shields.io/badge/security-policy-blue)](SECURITY.md)
[![GHCR](https://img.shields.io/badge/ghcr.io-mediarefinery-2496ED?logo=docker&logoColor=white)](https://github.com/automationnexus/MediaRefinery/pkgs/container/mediarefinery)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

MediaRefinery is a self-hosted review companion for [Immich](https://immich.app/). It runs beside your Immich server, classifies media locally with user-controlled models and categories, and helps you review sensitive, unwanted, duplicate, low-quality, document, OCR, people, and event-related media without sending media bytes to third-party inference services.

The public product is the web service: a FastAPI backend, React dashboard, SQLite state store, and same-origin `/api` HTTP API.

## Highlights

- Local-first media review for Immich libraries.
- Browser dashboard for setup, scans, assets, events, models, settings, runs, and audit logs.
- Immich proxy login with server-side sessions.
- Encrypted Immich session tokens and API keys at rest.
- SHA256-pinned model catalog with explicit license acceptance.
- Image classification through Immich previews.
- Optional bounded video and animated GIF frame sampling with ffmpeg.
- Optional local OCR through a pinned RapidOCR/PaddleOCR ONNX bundle.
- Immich Smart Search integration with local metadata/OCR fallback.
- Local event groups that can be renamed, merged, split, reset, and audited without changing Immich media.
- Safe actions only: review album, tag, manual review, and Immich Locked Folder. Automatic delete and trash actions are not supported.

## Status

Current release: `2.1.0`.

MediaRefinery is ready for self-hosted review workflows, but classification, OCR, subtype labels, and semantic search are probabilistic signals. Treat results as queues for human review, not as final truth.

## Quick Start With Docker

The published image is available from GHCR:

```bash
docker run --rm \
  --name mediarefinery \
  -p 8765:8765 \
  -v mediarefinery_data:/data \
  ghcr.io/automationnexus/mediarefinery:2.1.0
```

Open `http://localhost:8765`, complete the setup wizard, then set **Immich URL** and **public base URL** in system settings (`config.db` is seeded on first boot under `/data/databases/`).

For a Compose-based local setup:

```bash
docker compose -f templates/docker-compose.example.yml up -d --build
```

Read [docs/getting-started/installation.md](docs/getting-started/installation.md) before using the service for real users, especially the notes about `/data`, `config.db`, `master.key`, HTTPS, and upgrades.

## First Run

1. Open the dashboard.
2. Accept the setup terms.
3. Sign in with an Immich account. The first successful user becomes the MediaRefinery admin.
4. Install a classifier model from the catalog and accept its license.
5. Create an Immich API key for the signed-in user and save it in MediaRefinery.
6. Start a scan.
7. Review results in Assets, Events, Runs, and Audit.

See [docs/guides/dashboard.md](docs/guides/dashboard.md) for the dashboard workflow.

## What It Stores

MediaRefinery stores derived review state in SQLite: scan history, action audit rows, model metadata, encrypted tokens/API keys, custom categories, OCR text, classifier scores, event group state, and asset analysis metadata.

It does not store Immich originals, thumbnails, OCR crops, extracted video frames, passwords, or Locked Folder PINs.

Back up `/data/state.db` and `/data/master.key` together. Without the matching master key, encrypted tokens and API keys cannot be recovered.

## Documentation

| Need | Document |
|------|----------|
| Documentation hub | [docs/README.md](docs/README.md) |
| Install or upgrade | [docs/getting-started/installation.md](docs/getting-started/installation.md) |
| Configure environment and dashboard settings | [docs/admin/configuration.md](docs/admin/configuration.md) |
| Use the dashboard | [docs/guides/dashboard.md](docs/guides/dashboard.md) |
| Manage classifier, OCR, and subtype models | [docs/guides/models.md](docs/guides/models.md) |
| Operate backups, health checks, and production settings | [docs/admin/operations.md](docs/admin/operations.md) |
| HTTP API reference | [docs/reference/api.md](docs/reference/api.md) |
| Troubleshoot common issues | [docs/admin/troubleshooting.md](docs/admin/troubleshooting.md) |
| Immich endpoint compatibility | [docs/reference/immich-api-compat.md](docs/reference/immich-api-compat.md) |
| Threat model | [docs/security/threat-model.md](docs/security/threat-model.md) |
| Develop or contribute | [docs/development/local-development.md](docs/development/local-development.md) and [CONTRIBUTING.md](CONTRIBUTING.md) |
| Repository structure | [docs/development/repository-structure.md](docs/development/repository-structure.md) |
| Security policy | [SECURITY.md](SECURITY.md) |
| Support | [SUPPORT.md](SUPPORT.md) |

## Development

Backend:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,service,onnx,ocr]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests
```

Frontend:

```powershell
cd frontend
npm install
npm run typecheck
npm test
npm run build
```

For the full contributor workflow, see [docs/development/local-development.md](docs/development/local-development.md).

## Non-Goals

- No automatic deletion or trashing of Immich assets.
- No bundled model weights.
- No third-party cloud inference by default.
- No bypass of Immich access control.
- No separate face-recognition identity database.
- No claim of perfect classifier accuracy.

## License

MediaRefinery is released under the [MIT License](LICENSE).

<!-- final-nightly-verify -->
