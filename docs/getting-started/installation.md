# Installation

MediaRefinery is easiest to run as a Docker service. A Python source install is useful for development and local validation.

## Requirements

- A reachable Immich server (except in demo mode).
- Docker 24+ for container installs, or Python 3.11+ for source installs.
- Persistent storage for `/data`.
- HTTPS in front of MediaRefinery for real users.
- An Immich account that can sign in through the dashboard.
- An Immich API key for each user who wants to run real scans.

Optional features:

- `ffmpeg` for original video and animated GIF frame sampling.
- OCR runtime dependencies from the `ocr` extra when running from source.

## Docker Image

Run the published image with a persistent `/data` volume. System settings live in `config.db` inside that volume — not in `-e` flags:

```bash
docker run --rm \
  --name mediarefinery \
  -p 8765:8765 \
  -v mediarefinery_data:/data \
  ghcr.io/automationnexus/mediarefinery:2.1.0
```

Open `http://localhost:8765`. On first boot the service seeds `config.db` with defaults and creates `/data/master.key` if needed. Sign in as admin, then set **Immich URL** and **public base URL** in system settings (or use the API below).

## Docker Compose

From the repository root:

```bash
docker compose -f templates/docker-compose.example.yml up -d --build
docker compose -f templates/docker-compose.example.yml logs -f mediarefinery
```

After the container is healthy, configure Immich URL and base URL via the dashboard or:

```bash
curl -X PATCH http://127.0.0.1:8765/api/admin/config/immich_base_url \
  -H "Content-Type: application/json" \
  -d '{"value":"https://immich.example.com"}'
```

Check health:

```bash
curl --fail http://127.0.0.1:8765/api/health
curl --fail http://127.0.0.1:8765/api/health/ready
```

The Compose example builds from the local checkout. To run a published image instead, replace the service `build:` block with:

```yaml
image: ghcr.io/automationnexus/mediarefinery:2.1.0
```

## Source Install

Backend (Linux or WSL — the service expects `/data` by default):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[service,onnx,ocr]"
# Seed config.db under /data (requires writable /data or run as root in dev)
sudo mkdir -p /data/databases
sudo .\.venv\Scripts\python.exe -m mediarefinery.cli config import-yaml templates/config.example.yml --data-dir /data
.\.venv\Scripts\python.exe -m uvicorn --factory mediarefinery.service.app:create_app --host 0.0.0.0 --port 8080
```

Frontend development server:

```powershell
cd frontend
npm install
npm run dev
```

The Vite dev server runs on `http://localhost:5173` and proxies `/api` to the backend on `http://localhost:8080`.

To serve the production dashboard from FastAPI:

```powershell
cd frontend
npm run build
cd ..
.\.venv\Scripts\python.exe -m uvicorn --factory mediarefinery.service.app:create_app --host 0.0.0.0 --port 8080
```

## Demo Mode

Enable demo mode in `config.db` (synthetic Immich responses, no real credentials):

```bash
# After /data exists and config.db is seeded:
python -c "
from pathlib import Path
from mediarefinery.settings.load import ensure_config_db_seeded
repo = ensure_config_db_seeded(Path('/data'))
repo.upsert('system.demo_mode', True)
repo.upsert('system.immich_base_url', 'http://demo.invalid')
"
```

Or set `system.demo_mode: true` in YAML and run `mediarefinery config import-yaml`.

## Reverse Proxy

For real users:

- terminate HTTPS at the reverse proxy;
- set `system.base_url` to the HTTPS URL in `config.db`;
- forward `Host`, `X-Forwarded-Proto`, and client IP headers;
- set `system.trusted_proxies` to the proxy IPs when you rely on forwarded client IPs;
- keep MediaRefinery and Immich on private networks when possible.

The app sets secure cookies and HSTS when `system.base_url` starts with `https://`.

## First Run Wizard

After opening the dashboard:

1. Accept the setup terms.
2. Sign in with Immich credentials (skipped in demo mode).
3. Install a classifier model from the catalog.
4. Save an Immich API key.
5. Start the first scan.

The first user to sign in after setup acceptance becomes the MediaRefinery admin.

## Upgrading

Before upgrading:

1. Stop the service.
2. Back up `/data/state.db`, `/data/databases/config.db`, and `/data/master.key` together.
3. Pull or build the new image.
4. Start the service.
5. Check `/api/health/ready`.

Users upgrading from an older service install that used `/data/state-v2.db` should rename that file to `/data/state.db` while the service is stopped. Keep the matching `master.key`; encrypted sessions and API keys cannot be recovered without it.

## Migrating from `MR_*` environment variables

If you previously configured the service via `.env`, import once into `config.db`:

```bash
mediarefinery config import-yaml /path/to/exported-config.yml --data-dir /data
```

Remove retired `MR_*` app settings from Compose after import. Keep only volume mounts.
