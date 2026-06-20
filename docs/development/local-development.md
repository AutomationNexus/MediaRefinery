# Development

This guide is for contributors working on MediaRefinery locally.

Read [SECURITY.md](https://github.com/automationnexus/MediaRefinery/blob/dev/SECURITY.md) before touching auth, model downloads, scan execution, logging, preview bytes, Locked Folder flows, or Immich write actions.

## Repository Layout

| Path | Purpose |
|------|---------|
| `src/mediarefinery/` | Python package. |
| `src/mediarefinery/service/` | FastAPI service, routers, state store, auth, security, scheduler, and runner wiring. |
| `frontend/` | React/Vite/TypeScript dashboard. |
| `docs/` | User, operator, release, security, and architecture docs. |
| `templates/` | Example environment, Compose, and advanced YAML templates. |
| `tests/` | Python tests. |
| `.github/workflows/` | CI, frontend, Docker, and release workflows. |

## Backend Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,service,onnx,ocr]"
```

Run the backend (Linux/WSL recommended — runtime data defaults to `/data`):

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,service,onnx,ocr]"
# Seed config.db (example: demo mode)
sudo mkdir -p /data/databases
sudo .\.venv\Scripts\python.exe -c "
from pathlib import Path
from mediarefinery.settings.load import ensure_config_db_seeded
from mediarefinery.settings.defaults import default_nested_config
n = default_nested_config()
n['system']['immich_base_url'] = 'http://demo.invalid'
n['system']['demo_mode'] = True
ensure_config_db_seeded(Path('/data')).bulk_upsert(n)
"
.\.venv\Scripts\python.exe -m uvicorn --factory mediarefinery.service.app:create_app --host 127.0.0.1 --port 8080
```

System settings are read from `/data/databases/config.db`, not `MR_*` environment variables.

**Contributor / CI harness only** (not operator configuration):

| Variable | Purpose |
|----------|---------|
| `MEDIAREFINERY_DATA_DIR` | Redirect `/data` to a temp directory in pytest and e2e |
| `MEDIAREFINERY_CONFIG` | Default path for `mediarefinery config validate` CLI |

The encryption master key is stored at `/data/master.key` (auto-created on first boot). Tests inject `data_dir` via fixtures or `create_app(config=...)`.

## Frontend Setup

```powershell
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://localhost:8080`.

Build production assets:

```powershell
cd frontend
npm run build
```

The build writes to `src/mediarefinery/web/`. That directory is ignored by git but included in built wheels when present.

## Quality Gates

Run before opening a pull request:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=mediarefinery --cov-report=term-missing:skip-covered --cov-report=json:tmp\coverage.json --cov-fail-under=90
.\.venv\Scripts\python.exe tools\check_coverage.py --json tmp\coverage.json --minimum 90
.\.venv\Scripts\python.exe -m ruff check src tests tools
.\.venv\Scripts\python.exe -m compileall -q src tests tools
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit --local --skip-editable

cd frontend
npm run typecheck
npm test
npm run build
npm audit
```

## Docker Validation

```powershell
docker build -t mediarefinery:dev-smoke .
docker run --rm -d --name mediarefinery-dev-smoke -p 18765:8765 `
  -v mediarefinery_dev_data:/data `
  mediarefinery:dev-smoke
# After health is up, PATCH system.demo_mode and system.immich_base_url in config.db
# or use import-yaml — see docs/admin/configuration.md
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:18765/api/health
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:18765/api/health/ready
docker rm -f mediarefinery-dev-smoke
```

## Package Validation

```powershell
Remove-Item -Recurse -Force tmp\release-wheel -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force tmp\release-wheel | Out-Null
.\.venv\Scripts\python.exe -m pip wheel . --no-deps -w tmp\release-wheel
```

Inspect that the wheel contains:

- `mediarefinery/web/index.html`;
- `mediarefinery/web/assets/*`;
- `docs/models/catalog.json`;
- model license docs.

## Privacy Rules For Changes

- Do not commit secrets, `.env` files, SQLite databases, logs, thumbnails, frames, originals, model weights, or user media.
- Do not log passwords, API keys, session tokens, CSRF tokens, Locked Folder PINs, preview bytes, originals, or sampled frames.
- Keep tenant-scoped reads and writes behind `StateStore.with_user`.
- Require CSRF on authenticated state-changing routes.
- Keep model downloads HTTPS-only and SHA256-pinned.
- Document user-visible behavior changes.

## Pull Requests

Use the [pull request template](https://github.com/automationnexus/MediaRefinery/blob/dev/.github/PULL_REQUEST_TEMPLATE.md).

Good PRs include:

- the user-visible behavior change;
- tests or manual verification;
- privacy/security impact when relevant;
- docs updates for behavior changes;
- screenshots only when they do not reveal private media.

## Release Work

Release validation is tracked in [../releases/release-checklist.md](../releases/release-checklist.md). The release workflow publishes GHCR images and GitHub releases on semantic-version tags such as `2.1.0`.
