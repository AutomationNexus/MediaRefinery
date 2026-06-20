# Troubleshooting

Start with the health endpoints:

```bash
curl http://127.0.0.1:8765/api/health
curl http://127.0.0.1:8765/api/health/ready
```

For Docker installs, inspect logs:

```bash
docker compose -f templates/docker-compose.example.yml logs -f mediarefinery
```

## The Dashboard Does Not Load

Check:

- the container or uvicorn process is running;
- the port is mapped correctly;
- the reverse proxy forwards to the service port;
- the frontend build exists when running from source.

From source, run:

```powershell
cd frontend
npm run build
cd ..
.\.venv\Scripts\python.exe -m uvicorn --factory mediarefinery.service.app:create_app --host 0.0.0.0 --port 8080
```

## Login Fails

Check:

- `system.immich_base_url` in `config.db` points to the Immich server, not the MediaRefinery URL;
- the Immich URL is reachable from inside the container;
- the user can sign in directly to Immich;
- cookies are not blocked by the browser;
- `system.base_url` matches the public HTTPS origin when behind a proxy.

Readiness can succeed even when a specific user's password is wrong. It only checks Immich compatibility endpoints.

## Readiness Is Degraded

`/api/health/ready` returns HTTP 200 even when status is `degraded`; read the body.

Common reasons:

- Immich is unreachable from the service.
- `system.immich_base_url` in `config.db` is wrong.
- Immich is newer or older than the tested compatibility target.
- Immich changed the shape of `/api/server/version`.
- `/api/server/about` returned an unexpected non-auth error.

See [../reference/immich-api-compat.md](../reference/immich-api-compat.md).

## Model Install Fails

Check:

- the server can reach the model URL;
- the catalog entry is marked installable;
- the admin accepted the license checkbox;
- `/data` is writable by the service user;
- the downloaded file was not intercepted or replaced by a proxy;
- there is enough disk space.

Hash mismatches are treated as failures. Do not bypass them; update the catalog only after reviewing the model source and expected SHA256.

## Scans Do Not Start

Check:

- a primary classifier model is installed and active;
- the user saved an Immich API key;
- the API key validates in Settings or the wizard;
- the API key has asset read/view permissions;
- Immich is reachable from the service;
- the user has visible assets in Immich.

If the API returns `409` with `api_key_required`, save or replace the user's Immich API key.

## Scans Complete But Assets Are Missing

Possible causes:

- the Immich account has no visible assets;
- filters in the Assets tab are hiding rows;
- only one page of results was scanned;
- auto-scan is waiting for the next poll interval;
- Immich returned assets without preview data;
- the scan was run under a different Immich user.

Use Runs to confirm the scan completed, then remove filters in Assets.

## Semantic Search Falls Back To Metadata

This is expected when Immich Smart Search or ML is unavailable. MediaRefinery then searches local metadata and OCR text and labels results as `metadata_fallback`.

Check Immich ML and Smart Search settings if you expect semantic search to work.

## OCR Text Is Missing

Check:

- an OCR model bundle is installed;
- `system.ocr.enabled` is true in `config.db`;
- the asset has an image preview or sampled frame;
- `system.ocr.max_inputs` is greater than zero;
- OCR runtime dependencies are installed when running from source.

OCR stores text only after a scan processes the asset.

## Video Or GIF Sampling Falls Back To Preview

Preview fallback is normal unless sampling is explicitly enabled.

Check:

- `system.media_sampling.enabled` is true;
- ffmpeg is installed and found at `system.media_sampling.ffmpeg_path`;
- the user's Immich API key has original download permission;
- the original is below the byte and duration limits;
- the temp directory is writable.

Failures are recorded as safe analysis warnings and temp files are cleaned up.

## Locked Folder Revert Fails

Check:

- the selected run actually moved assets to Locked Folder;
- the current user still has a valid Immich session;
- the PIN is correct;
- Immich still exposes the locked assets to the PIN-unlocked session;
- the assets were not deleted or otherwise changed in Immich.

The PIN is not stored. You must enter it for each revert request.

## Auto-Scan Stops

Auto-scan can pause for a user when Immich rejects the stored session.

Fix:

1. Sign out and sign in again.
2. Open Settings.
3. Re-enable auto-scan.
4. Confirm the interval.

The user still needs an active model and stored API key.

## Lost Master Key

If `/data/master.key` is lost, encrypted Immich session tokens and stored API keys cannot be recovered. Users must sign in again and save new API keys after a fresh setup or restore.

Back up `/data/state.db` and `/data/master.key` together.

## Upgrade Starts With Empty State

If you upgraded from an older service install that used `/data/state-v2.db`, set:

```bash
Rename `/data/state-v2.db` to `/data/state.db` while the service is stopped.
```

or stop the service and rename the file to `/data/state.db`. Keep the matching `master.key`.
