# MediaRefinery Release Checklist

Status: `2.1.0` final release checklist. Do not promote a release until every command below passes or a residual risk is explicitly accepted in release notes.

**Versioning:** bump `project.version` in `pyproject.toml` before merging `dev` → `main`. `release.yml` tags **`v{project.version}`** on merge (it does not auto-increment from the previous git tag).

**Dependabot:** monthly grouped minor/patch PRs to `dev` (max 3 per ecosystem). Major bumps are reviewed individually. Use manual `chore/deps-batch` PRs before a release if needed.

Run from the repository root on a clean checkout unless a command says otherwise.

## Backend

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,service,onnx,ocr]"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m compileall -q src tests
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit --local --skip-editable
```

## Frontend

```powershell
cd frontend
npm ci
npm run typecheck
npm test -- --run
npm run build
npm audit
cd ..
```

## Docker Demo Health Smoke

Seed demo settings into a persistent volume, then start the container:

```powershell
docker build -t mediarefinery:2.1.0-smoke .
docker volume create mediarefinery-2-1-0-smoke-data
docker run --rm -v mediarefinery-2-1-0-smoke-data:/data mediarefinery:2.1.0-smoke python -c "from pathlib import Path; from mediarefinery.settings.load import ensure_config_db_seeded; from mediarefinery.settings.defaults import default_nested_config; n=default_nested_config(); n['system'].update({'demo_mode': True, 'immich_base_url': 'http://demo.invalid'}); ensure_config_db_seeded(Path('/data')).bulk_upsert(n)"
docker run --rm -d --name mediarefinery-2-1-0-smoke -p 18765:8765 -v mediarefinery-2-1-0-smoke-data:/data mediarefinery:2.1.0-smoke
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:18765/api/health
Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:18765/api/health/ready
docker rm -f mediarefinery-2-1-0-smoke
```

## Package Artifact Verification

```powershell
Remove-Item -Recurse -Force tmp\release-wheel -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force tmp\release-wheel | Out-Null
.\.venv\Scripts\python.exe -m pip wheel . --no-deps -w tmp\release-wheel
.\.venv\Scripts\python.exe -c "import pathlib, zipfile; wheel=next(pathlib.Path('tmp/release-wheel').glob('mediarefinery-2.1.0-*.whl')); names=set(zipfile.ZipFile(wheel).namelist()); assert 'mediarefinery/web/index.html' in names; assert any(n.startswith('mediarefinery/web/assets/') and n.endswith('.js') for n in names); assert any(n.endswith('docs/models/catalog.json') for n in names); print(wheel.name)"
```

## Live Immich Smoke

Use `.env.smoke` with short-lived test credentials and a disposable or deliberately non-private Immich account. Do not print the file contents.

The service half of this smoke seeds `config.db` on a Docker volume (`system.immich_base_url`, `system.auto_scan_enabled=false`) — not retired `MR_*` app environment variables.

```powershell
New-Item -ItemType Directory -Force tmp | Out-Null
Copy-Item templates\config.immich-smoke.example.yml tmp\immich-smoke.config.yml
# Edit tmp\immich-smoke.config.yml and .env.smoke locally before continuing.

Get-Content .env.smoke |
  Where-Object { $_ -and -not $_.TrimStart().StartsWith("#") } |
  ForEach-Object {
    $name, $value = $_ -split "=", 2
    Set-Item -Path "Env:$name" -Value $value
  }

.\.venv\Scripts\python.exe -m mediarefinery config validate --config $env:MEDIAREFINERY_CONFIG
.\.venv\Scripts\python.exe -m mediarefinery doctor --config $env:MEDIAREFINERY_CONFIG
.\.venv\Scripts\python.exe -m mediarefinery scan --config $env:MEDIAREFINERY_CONFIG --immich-http --dry-run

$vol = "mediarefinery-release-live-smoke-data"
docker volume create $vol
docker run --rm -e IMMICH_URL=$env:IMMICH_URL -v ${vol}:/data mediarefinery:2.1.0-smoke python -c "import os; from pathlib import Path; from mediarefinery.settings.load import ensure_config_db_seeded; from mediarefinery.settings.defaults import default_nested_config; n=default_nested_config(); n['system']['immich_base_url']=os.environ['IMMICH_URL'].rstrip('/'); n['system']['auto_scan_enabled']=False; ensure_config_db_seeded(Path('/data')).bulk_upsert(n)"
$service = Start-Process -FilePath "docker" -ArgumentList @("run", "--rm", "-d", "--name", "mediarefinery-release-live-smoke", "-p", "18766:8765", "-v", "${vol}:/data", "mediarefinery:2.1.0-smoke") -PassThru -WindowStyle Hidden
try {
  Start-Sleep -Seconds 8
  Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:18766/api/health
  Invoke-WebRequest -UseBasicParsing -Uri http://127.0.0.1:18766/api/health/ready
}
finally {
  docker rm -f mediarefinery-release-live-smoke 2>$null
}
```

The live smoke is passing only if the readiness body reports `status: ok`, `immich: ok`, and compatibility `status: ok`, and the dry-run scan exits without persisting media bytes.

Manual dashboard/API checks for the same live environment:

- login with `IMMICH_TEST_USER_EMAIL` / `IMMICH_TEST_USER_PASSWORD`;
- validate and store `IMMICH_API_KEY`;
- install one small classifier model or confirm an installed active model;
- run a dry-run service scan and inspect the Assets review list;
- verify OCR only when the OCR bundle is configured;
- try semantic search when Immich Smart Search is enabled, accepting explicit metadata fallback otherwise;
- include one image plus, when the smoke account has suitable disposable assets, one video and one GIF with sampling enabled;
- exercise one safe live action such as a review-album or tag action against the disposable smoke target.

## Release Artifact Names

- Git tag: `2.1.0`.
- Python wheel: `mediarefinery-2.1.0-py3-none-any.whl`.
- Docker local smoke image: `mediarefinery:2.1.0-smoke`.
- GHCR release tags from `.github/workflows/release.yml`: `2.1.0`, `2.1`, `2`, and `latest`.

## 2.1.0 Local Results

Last local run: 2026-05-11 for the 2.1.0 release workspace.

- Backend: `pytest -q` collected 370 tests -> 369 passed, 1 skipped; Ruff, `compileall`, `pip check`, and `pip_audit --local --skip-editable` passed.
- Frontend: `npm ci`, `npm run typecheck`, `npm test -- --run` (8 files / 43 tests), `npm run build`, and `npm audit` passed.
- Wheel: `mediarefinery-2.1.0-py3-none-any.whl`, SHA256 `bff583c7aef93e68529905eab5f1b511014c2b6d53519233b04575461128f554`; inspection confirmed web assets and model catalog docs.
- Docker: `mediarefinery:2.1.0-smoke` built; demo `/api/health` and `/api/health/ready` passed with compatibility `ok` (`config.db` seeded on volume with `system.demo_mode=true`).
- Live Immich: not repeated for the 2.1.0 route, docs, and state-naming refresh. The previous bounded 2026-05-11 smoke against Immich `2.7.5` covered CLI dry-run, service login, API-key validation/storage, MobileNet model install, scan, asset detail, semantic metadata fallback, and a safe review-album action.
- Caveat: the previous live smoke account exposed image assets only; a video-constrained sampling probe processed 0 assets with 0 errors. Automated tests and Docker smoke cover the 2.1.0 route change.
