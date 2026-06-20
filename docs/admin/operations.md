# Operations

This runbook covers day-to-day operation of the MediaRefinery service. For first install steps, read [../getting-started/installation.md](../getting-started/installation.md). For system and per-user settings, read [configuration.md](configuration.md). Review [../security/threat-model.md](../security/threat-model.md) before exposing an instance beyond local validation.

## Runtime Shape

- Backend: FastAPI served by uvicorn.
- Frontend: Vite build served as static files by FastAPI.
- Worker: in-process APScheduler coordinator for optional auto-scan polling.
- System settings: SQLite at `/data/databases/config.db`.
- Per-user state: SQLite at `/data/state.db`.
- Analysis: derived asset signals are stored in SQLite (`asset_analysis`); media bytes are not persisted.
- Encryption: `/data/master.key` (auto-created on first boot).
- Container port: `8765`.
- Local development port: commonly `8080`.

## System configuration

Deployment knobs (Immich URL, base URL, demo mode, sampling, OCR, rate limits) live in `config.db`. See [configuration.md](configuration.md#system-settings-configdb). Edit via the dashboard admin UI or `PATCH /api/admin/config/{key_path}`.

## Local Service

Seed `config.db` under `/data` (see [development/local-development.md](../development/local-development.md)), then:

```powershell
.\.venv\Scripts\python.exe -m uvicorn --factory mediarefinery.service.app:create_app --host 0.0.0.0 --port 8080
```

Demo mode: set `system.demo_mode` true in `config.db` before starting (see [configuration.md](configuration.md#common-use-cases)).

## Docker

```bash
docker compose -f templates/docker-compose.example.yml up -d --build
curl --fail http://127.0.0.1:8765/api/health
# configure system.immich_base_url / system.base_url via dashboard or admin API
```

Notes:

- The image runs as the unprivileged `mediarefinery` user.
- `/data` must persist across container recreates.
- `/config` is reserved for future operator-mounted service configuration.
- The Dockerfile installs service, ONNX classifier, and OCR runtime dependencies.
- `system.media_sampling.enabled` is true in `config.db`. With sampling enabled, originals are downloaded through Immich to a bounded temp file, sampled with ffmpeg, classified with the active model's video aggregation strategy, and deleted after analysis. Oversized, too-long, missing-ffmpeg, and extraction failures fall back to preview classification and are recorded in asset analysis.
- OCR runs locally when the `rapidocr-ppocrv5-english-onnx` bundle is installed from the catalog and `system.ocr.enabled` is true.
- Adult subtype classification is a separate optional model slot. The curated catalog does not ship an act-level model; admins can register a local ONNX profile with explicit output labels, thresholds, preprocessing, and an acknowledgement from the Models tab.
- Semantic asset search calls Immich `POST /api/search/smart` with the signed-in user's bearer token when the dashboard search mode is `semantic`. Returned Immich assets are mapped back to rows already visible to that MediaRefinery user; unknown or other-user rows are skipped. If Immich Smart Search is missing or unavailable, the response falls back to local metadata/OCR search and labels the source as `metadata_fallback`.
- Event groups are stored locally from derived analysis metadata. Manual rename, merge, split, remove, and reset operations change only MediaRefinery review state and audit rows; they do not rename Immich albums or move/delete media.
- Public release images are published by the release workflow on semantic-version tags. For `2.1.0`, the expected stable tags are `2.1.0`, `2.1`, `2`, and `latest`.

## Master Key

Resolution order:

1. `/data/master.key`, exactly 32 raw bytes.
2. First-run generation of `/data/master.key` with mode `0600`.

Back up `state.db` and `master.key` together. A database backup without the matching key cannot decrypt stored Immich tokens or API keys.

## Upgrade Notes

Before upgrading, stop the service and back up `/data/state.db` plus `/data/master.key` from the same point in time.

If an older service install used `/data/state-v2.db`, rename it to `/data/state.db` while the service is stopped. Keep the matching master key.

## Backup

For a running SQLite database, use SQLite's online backup API rather than copying the file directly:

```bash
docker exec mediarefinery sqlite3 /data/state.db \
  ".backup '/data/backups/state.$(date -u +%Y%m%dT%H%M%SZ).db'"
```

If `sqlite3` is not present in the image, use a sidecar container with the `/data` volume mounted.

Cold backup:

```bash
docker compose -f templates/docker-compose.example.yml stop mediarefinery
docker run --rm -v mediarefinery_data:/data -v "$PWD/backups:/out" \
  alpine sh -c "tar czf /out/data-$(date -u +%Y%m%dT%H%M%SZ).tgz -C / data"
docker compose -f templates/docker-compose.example.yml start mediarefinery
```

Encrypt backups at rest. They contain secrets.

## Restore

1. Stop the service.
2. Restore `state.db` and `master.key` from the same backup snapshot.
3. Ensure the service user can read/write the data directory and that `master.key` is not world-readable.
4. Start the service.
5. Check `/api/health` and then test login with one known account.

## Health

`GET /api/health` returns liveness:

```json
{"status":"ok"}
```

`GET /api/health/ready` checks SQLite plus Immich `GET /api/server/version` and, when publicly readable, `GET /api/server/about`. Some Immich deployments require auth for `server/about`; readiness accepts that when `server/version` reports a supported version. It returns HTTP 200 with a status body such as:

```json
{
  "status": "ok",
  "db": "ok",
  "immich": "ok",
  "compatibility": {
    "status": "ok",
    "reason": "Immich 2.7.5 matches the tested compatibility target.",
    "min_version": "2.7.5",
    "max_tested_version": "2.7.5",
    "last_live_smoke": "2026-05-11",
    "server_version": "2.7.5",
    "server_about_version": "2.7.5"
  }
}
```

or:

```json
{
  "status": "degraded",
  "db": "ok",
  "immich": "unsupported",
  "compatibility": {
    "status": "unsupported",
    "reason": "Immich 2.8.0 is newer than the maximum tested 2.7.5; run the live Immich smoke and update compatibility docs before release."
  }
}
```

Operators should treat `degraded` as a readiness warning, not as process death. `immich: unsupported` means the connected Immich version is outside the tested range; `immich: fail` means the load-bearing version endpoint was unreachable, returned an unexpected shape, or `server/about` returned an unexpected non-auth failure.

## Logging

The service emits JSON logs to stdout through `configure_json_logging`. Docker log rotation is host-side. For the default Docker `json-file` driver, configure a cap such as:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "5",
    "compress": "true"
  }
}
```

## Auto-Scan Polling

Immich 2.7.5 does not expose outbound webhooks for new assets. MediaRefinery therefore has a polling coordinator:

- One APScheduler job runs every 60 seconds.
- Each user's stored settings decide whether that user is due.
- Polling calls Immich `POST /api/search/metadata` using the user's session bearer.
- 401 disables that user's auto-scan setting.
- 5xx and network errors keep the cursor unchanged so the next tick retries.

Auto-scan dispatches through the same production runner factories as manual scans. A user must have an active installed model and a stored Immich API key; otherwise the coordinator skips that user and waits for the next due window.

## Model Slots

Model registry rows use task-specific active slots:

- `primary_safety`: the active image/video classifier used for the primary scan result. Older `classifier` rows are still read for compatibility.
- `adult_subtype`: optional admin-registered ONNX profile for detailed adult subtype review queues. Binary-only `sfw/nsfw` models cannot be activated here. Labels must come from the profile's configured `output_labels`, unknown outputs fail closed, and low-confidence top labels enter `review_needed`.
- `ocr`: optional RapidOCR/PaddleOCR bundle for derived text extraction.
- `semantic_embedding`: reserved for a future local CLIP/SigLIP embedding bundle.

Adult subtype profile schema:

```json
{
  "model_id": "local-subtypes",
  "name": "Local Subtypes",
  "model_path": "/data/imports/subtypes.onnx",
  "output_labels": ["user_label_one", "user_label_two"],
  "thresholds": {"user_label_one": 0.7, "user_label_two": 0.8},
  "admin_acknowledgement": true,
  "input_size": 224,
  "input_mean": [0.0, 0.0, 0.0],
  "input_std": [1.0, 1.0, 1.0],
  "input_name": null,
  "output_name": null
}
```

Registration copies the server-local ONNX file into `/data/models/<model_id>.onnx`, computes a SHA256, stores only model/profile metadata in SQLite, and activates the `adult_subtype` slot. Subtype labels are review signals only; primary-category policies remain the only path to actions.

## Asset Search

`GET /api/me/assets` accepts `q`, `search_mode`, `queue`, `media_kind`, and `event_id`:

- `search_mode=metadata` searches stored analysis JSON, derived OCR text, and asset IDs.
- `search_mode=semantic` first calls Immich Smart Search with `POST /api/search/smart`, body `{"query": "...", "page": 1, "size": N, "withDeleted": false, "withExif": true, "withPeople": true, "withStacked": true}` and the current user's bearer token.
- Semantic results include `search_source` and `search_score` per asset. Immich does not guarantee a numeric score in all responses, so score can be `null` while ordering still follows Immich's returned rank.
- If Smart Search is unavailable, the API returns metadata/OCR matches with `search_source=metadata_fallback`, `search_score=null`, and `search_unavailable_reason` set to a safe reason code.
- `event_id` restricts results to assets currently assigned to that local event group.
- Local CLIP/SigLIP embeddings are intentionally not enabled because no ONNX embedding bundle has been license-reviewed and SHA256-pinned.

## Event Groups

Automatic event grouping is derived during asset analysis and stored in two local tables:

- `event_groups`: one row per reviewable group, keyed by `event_id`; automatic rows keep the analyzer `auto_key`, title, status, sort timestamp, and source metadata.
- `asset_event_memberships`: one current local event assignment per asset, including the original automatic event key and whether the assignment is automatic, manual, or manually removed.

Automatic grouping uses the available derived signals only: date bucket, city/country, Immich People names or IDs, album names, and metadata semantic terms. MediaRefinery does not create a biometric identity store and does not call Immich write APIs for event edits.

Manual conflict policy:

- Renaming an automatic event marks the group `manual`; later rescans refresh source metadata but do not overwrite the title unless the event is reset.
- Merging events pins current memberships to the target event and marks source events as merged. New assets matching a merged source key join the target until reset.
- Splitting selected assets creates a manual event and pins only those selected assets.
- Removing an asset records a local `removed` membership so a rescan does not immediately re-add it.
- Resetting an event replays automatic grouping from the latest stored analysis for that event's assets.

Every manual event edit writes an `audit_log` row with an `event.*` action. Event APIs are tenant scoped under `/api/me/events`.

## Asset Analysis

Each completed classification now records additive signals for review/search:

- media kind (`image`, `video`, or `gif`), MIME type, dimensions, duration, albums, and basic dates;
- safety label, confidence, threshold, and `review_needed`;
- Immich People names/IDs when `withPeople` returns them;
- duplicate IDs/checksums and a preview hash;
- quality flags such as low resolution, plus optional blur/brightness scores when Pillow and NumPy are available;
- media sampling status, source, sampled frame count, aggregation method, and a safe error code when preview fallback was needed;
- OCR status, text, confidence, source frame indexes, analyzer version, and OCR model hash when local OCR or trusted metadata provides text;
- document/screenshot/receipt/invoice heuristics from filename, metadata, and OCR text if present;
- adult subtype status, labels, confidence, thresholds, and model hash when an acknowledged subtype profile is active and the primary classifier marks the asset sensitive;
- metadata-based semantic terms for search and automatic event group keys.

The curated AdamCodd model remains binary `sfw/nsfw`; MediaRefinery does not invent detailed adult labels from it.
