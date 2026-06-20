# HTTP API

MediaRefinery serves the dashboard and API from the same origin. The API prefix is `/api`.

The API is primarily for the bundled dashboard. It can be useful for local automation, but callers must follow the same authentication and CSRF rules as the browser.

## Authentication

`POST /api/auth/login` forwards credentials to Immich. On success, MediaRefinery sets:

- an HttpOnly signed session cookie;
- a readable `mr_csrf` cookie used by the double-submit CSRF check.

State-changing requests must include:

```http
X-CSRF-Token: <value of mr_csrf cookie>
```

All authenticated requests use browser cookies. The API does not expose bearer tokens for MediaRefinery sessions.

## Endpoints

### Setup

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/setup/bootstrap` | Returns first-run setup status. |
| `POST` | `/api/setup/bootstrap` | Records setup-term acceptance on a fresh server. |

### Auth And Account

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/auth/login` | Proxy login through Immich. |
| `POST` | `/api/auth/logout` | Revokes the MediaRefinery session and best-effort Immich logout. Requires CSRF. |
| `GET` | `/api/me` | Returns the current MediaRefinery user. |
| `DELETE` | `/api/me` | Deletes the current MediaRefinery account and user-scoped state. Requires CSRF. |

### User Configuration

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/me/categories` | Gets category configuration and active-model reclassify status. |
| `PUT` | `/api/me/categories` | Saves categories. Requires CSRF. |
| `GET` | `/api/me/policies` | Gets policy configuration. |
| `PUT` | `/api/me/policies` | Saves policies. Requires CSRF. |
| `POST` | `/api/me/api-key` | Validates and stores an encrypted Immich API key. Requires CSRF. |
| `GET` | `/api/me/api-key` | Lists stored API key summaries. |
| `GET` | `/api/me/auto-scan` | Gets auto-scan settings. |
| `PUT` | `/api/me/auto-scan` | Saves auto-scan settings. Requires CSRF. |
| `POST` | `/api/me/locked-folder/unlock` | Reverts Locked Folder moves for a run using a one-time PIN. Requires CSRF. |

### Scans

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/scans` | Starts a scan. Requires CSRF. |
| `GET` | `/api/scans` | Lists scan runs. |
| `GET` | `/api/scans/{run_id}` | Gets one run and its recorded actions. |
| `POST` | `/api/scans/{run_id}/undo` | Marks actions as reverted and reverts eligible Locked Folder moves. Requires CSRF. |

### Assets

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/me/assets` | Lists scanned assets with filters. |
| `GET` | `/api/me/assets/{asset_id}` | Gets stored analysis detail for one asset. |
| `POST` | `/api/me/assets/{asset_id}/category` | Saves a local category override. Requires CSRF. |
| `GET` | `/api/assets/{asset_id}/preview` | Streams an Immich preview through the service. Preview bytes are not persisted. |

`GET /api/me/assets` query parameters:

| Parameter | Description |
|-----------|-------------|
| `cursor` | Pagination cursor returned by the previous response. |
| `queue` | Review queue filter. |
| `media_kind` | `image`, `video`, or `gif`. |
| `event_id` | Restrict results to one local event group. |
| `q` | Search text. |
| `search_mode` | `metadata` or `semantic`. |

### Events

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/me/events` | Lists local event groups. |
| `GET` | `/api/me/events/{event_id}` | Gets one event and its assets. |
| `POST` | `/api/me/events/{event_id}/rename` | Renames a local event. Requires CSRF. |
| `POST` | `/api/me/events/merge` | Merges source events into a target event. Requires CSRF. |
| `POST` | `/api/me/events/{event_id}/split` | Splits selected assets into a new event. Requires CSRF. |
| `POST` | `/api/me/events/{event_id}/assets/{asset_id}/remove` | Removes an asset from a local event. Requires CSRF. |
| `POST` | `/api/me/events/{event_id}/reset` | Resets local edits back to automatic grouping. Requires CSRF. |

Event edits only affect MediaRefinery state. They do not change Immich albums, people, dates, or asset files.

### Models

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/models/catalog` | Lists model catalog entries. |
| `GET` | `/api/models` | Lists installed models. |
| `POST` | `/api/models/install` | Installs a catalog model. Admin and CSRF required. |
| `POST` | `/api/models/adult-subtype-profile` | Registers a local adult subtype profile. Admin and CSRF required. |
| `DELETE` | `/api/models/{registry_id}` | Uninstalls a model. Admin and CSRF required. |

### Audit

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/audit` | Lists audit entries for the current user. |

### Health

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Liveness check. |
| `GET` | `/api/health/ready` | SQLite and Immich compatibility readiness check. |

## OpenAPI

The generated OpenAPI document is served at:

```text
/api/openapi.json
```

Interactive docs are disabled in the production app surface.

## Error Handling

Common status codes:

| Status | Meaning |
|--------|---------|
| `400` | Invalid request body or unsupported operation. |
| `401` | No valid MediaRefinery session, invalid Immich login, or invalid Locked Folder PIN. |
| `403` | Non-admin user attempted an admin operation or CSRF failed. |
| `404` | Requested user-scoped resource was not found. |
| `409` | Operation conflicts with current state, such as missing API key or non-installable model. |
| `502` | Upstream Immich or model download failure. |
