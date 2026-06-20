# Immich API Compatibility

Status: `2.1.0` compatibility notes.

Endpoint shapes were recorded against Immich `2.7.5` on 2026-04-30 and smoke-checked again on 2026-05-10 and 2026-05-11. The service readiness check probes `GET /api/server/version` and, where publicly readable, `GET /api/server/about`, then reports `ok`, `unsupported`, or `fail` compatibility status.

## Current Compatibility Target

- Minimum tested Immich version: `2.7.5`.
- Maximum tested Immich version: `2.7.5`.
- Public release requirement: live smoke must pass against the documented Immich target before promoting a new release.

## Required Endpoints

| Endpoint | Used for | Auth | Notes |
|----------|----------|------|-------|
| `POST /api/auth/login` | Proxy login | email/password | Expected 201 with `accessToken`, `userId`, `userEmail`, `name`, `isAdmin`. |
| `POST /api/auth/logout` | Upstream logout | Bearer | Best-effort on MediaRefinery logout. |
| `GET /api/users/me` | Session revalidation | Bearer | 401 invalidates the MediaRefinery session. |
| `GET /api/server/version` | Readiness compatibility | none/client | Expected to report parseable `major`, `minor`, and `patch` fields. |
| `GET /api/server/about` | Readiness compatibility | none/client | Expected to report a parseable `version` when public; `401`/`403` is accepted when `server/version` reports a supported version. Other failures are degraded with an actionable reason. |
| `POST /api/search/metadata` | Asset listing, enriched metadata, and auto-scan polling | Bearer or API key depending on path | Used with pagination. Scans request `withExif=true`, `withPeople=true`, and `withStacked=true`; auto-scan uses `takenAfter`. |
| `POST /api/search/smart` | Dashboard semantic search | Bearer | Called only for `search_mode=semantic` with `query`, `page`, `size`, `withDeleted=false`, `withExif=true`, `withPeople=true`, and `withStacked=true`. Requires Immich Smart Search/ML to be available. MediaRefinery only returns matching rows already visible to the signed-in user. |
| `GET /api/assets/{id}/thumbnail` | Preview proxy | Bearer | Preview bytes are streamed, not persisted. |
| `GET /api/assets/{id}/original` | Opt-in video/GIF frame sampling, including OCR over sampled frames | API key | Used only when `system.media_sampling.enabled` is true in `config.db`. Originals are streamed to temp files with byte/duration/frame limits, sampled with ffmpeg, and deleted after analysis. Requires `asset.download` on Immich versions that enforce scoped API-key permissions. |
| `GET/POST /api/albums` and `PUT /api/albums/{id}/assets` | Review-album action | API key | Reused from the shared core pipeline. |
| `GET/POST /api/tags` and `PUT /api/tags/{id}/assets` | Tag action | API key | Reused from the shared core pipeline. |
| `PUT /api/assets/{id}` with `{visibility: "locked"}` | Move to Locked Folder | API key | Verified with `asset.update`; no PIN needed for the forward write. |
| `PUT /api/assets/{id}` with `{visibility: "timeline"}` | Revert Locked Folder move | PIN-unlocked Bearer | API key cannot see locked assets for revert on Immich 2.7.5. |
| `POST /api/auth/session/unlock` | PIN unlock for revert | Bearer plus PIN | MediaRefinery proxies this request; PIN is not logged or persisted. |

## Locked Folder Flow

Forward move:

1. The user stores an Immich API key with the needed permissions.
2. MediaRefinery uses that API key to call `PUT /api/assets/{id}` with `visibility: "locked"`.
3. No PIN is required for this write.

Revert:

1. The user submits a PIN to `POST /api/me/locked-folder/unlock`.
2. MediaRefinery decrypts the user's current Bearer session token.
3. MediaRefinery forwards the PIN to Immich `POST /api/auth/session/unlock`.
4. MediaRefinery reverts the relevant assets with `visibility: "timeline"`.
5. The PIN and unlocked Bearer are not stored, logged, or returned.

This backend proxy shape is the implementation today. Earlier notes that described a browser-direct PIN flow are outdated.

## API-Key Permission Expectations

For real scans and forward Locked Folder writes, a user API key needs permissions equivalent to:

- `asset.read`
- `asset.view`
- `asset.download` if original video/GIF sampling is enabled
- `asset.update`
- `album.read`
- `album.create`
- `albumAsset.create`
- `tag.read`
- `tag.create`
- `tag.asset`
- `user.read`
- `server.about`

MediaRefinery stores derived metadata such as dimensions, duration, Immich People names/IDs, duplicate IDs, local or metadata-supplied OCR text, review queues, primary classifier scores, acknowledged adult subtype scores when configured, safe video/GIF sampling status, and local event group membership/edit state. It does not store original file bytes, thumbnail bytes, OCR crops, or extracted frame bytes.

Event group rename, merge, split, remove, and reset operations are local MediaRefinery review-state edits. They do not call Immich album, person, trash, delete, or asset-move endpoints.

Exact Immich permission names may change across Immich releases; validate against the connected Immich version before release.

Dashboard semantic search uses the signed-in user's bearer session rather than a stored API key. Current Immich docs list `asset.read` for `POST /api/search/smart`.

## Auto-Scan Fallback

Immich `2.7.5` does not expose outbound webhooks for newly uploaded assets. MediaRefinery uses scheduled polling instead:

- `POST /api/search/metadata`
- `takenAfter` cursor
- `page` and `size`
- up to 10 pages per tick

401 disables that user's auto-scan setting. Network and 5xx failures keep the cursor unchanged.

## Readiness Behavior

`GET /api/health/ready` reports:

- `immich: ok` when Immich reports the tested `2.7.5` target from `server/version`; `server/about` may either match or return `401`/`403`.
- `immich: unsupported` when the version is older or newer than the tested range.
- `immich: fail` when `/api/server/version` is missing, unreachable, invalid JSON, or does not expose a parseable semantic version; `server/about` can also fail readiness if it returns an unexpected non-auth error or conflicts with `server/version`.

The endpoint remains HTTP 200 and uses `status: degraded` for unsupported or failed compatibility checks so operators get a clear process-health signal and an actionable compatibility reason without hiding the dashboard.

## 2026-05-10 Live Smoke

The smoke environment in `.env.smoke` was used locally without printing secrets. Results:

- Immich version endpoint reported `2.7.5`.
- Immich about endpoint reported `2.7.5` or was treated as auth-required, depending on the target's endpoint visibility.
- API-key-backed asset search returned one image page.
- Preview proxy source bytes were fetched for one image.
- Service proxy login succeeded for the smoke user.
- Service API-key validation and encrypted storage succeeded.
- Docker container readiness reached Immich successfully.

The smoke was deliberately bounded and did not walk the full library.

## 2026-05-11 Live Smoke

The `.env.smoke` environment was used without printing secrets. Results:

- CLI config validation and doctor passed.
- Immich API-key auth passed against the read-only check.
- Dry-run scan processed 17 assets with 0 errors and intended review-album actions only.
- Service proxy login, API-key validation/storage, MobileNet model install, active-model scan, asset detail review, and semantic search with explicit metadata fallback passed.
- Readiness accepted `server/about` as auth-required because `server/version` reported the supported `2.7.5` target.
