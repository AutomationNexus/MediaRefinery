# Configuration

MediaRefinery has two configuration layers:

- **System settings** in SQLite (`/data/databases/config.db`) — Immich URL, public base URL, trusted proxies, demo mode, auto-scan, media sampling, OCR, and session rate limits;
- **Per-user settings** in SQLite (`/data/state.db`) — categories, policies, API keys, and auto-scan preferences.

The encryption master key lives at `/data/master.key` (created on first boot). Operators do not set application behaviour via environment variables. Use the dashboard (admin system settings) or the one-shot YAML import CLI. Compose files only need volumes and ports.

Do not commit `.env`, API keys, SQLite files, logs, thumbnails, frames, model weights, or user media.

## Runtime layout

```
/data/
  master.key                 # encryption key (created on first run if unset)
  state.db                   # per-user data (unchanged)
  databases/
    config.db                # system settings (SSOT for deployment knobs)
  tmp/                       # media sampling temp (when enabled)
```

In containers, `/data` is the fixed data root. On first boot, `config.db` is created and seeded with defaults when empty.

## System settings (`config.db`)

Edit these from the dashboard (admin) or `PATCH /api/admin/config/{key_path}`. Restart is not required for most keys; the in-memory app state reloads on patch.

| Key | Default (seed) | Description |
|-----|----------------|-------------|
| `system.immich_base_url` | `http://immich:2283` | Base URL of the Immich server (one URL per deployment). |
| `system.base_url` | `http://localhost:8080` | Public MediaRefinery URL. Use `https://...` for secure cookies and HSTS. |
| `system.trusted_proxies` | `""` | Comma-separated proxy IPs for client IP resolution. |
| `system.session_ttl_seconds` | `43200` | Sliding session lifetime (12 hours). |
| `system.revalidate_interval_seconds` | `300` | Minimum interval between Immich `/users/me` checks. |
| `system.login_rate_per_min` | `5` | Login attempts per minute per IP. |
| `system.auto_scan_enabled` | `true` | Global auto-scan scheduler (disabled automatically in demo mode). |
| `system.demo_mode` | `false` | Synthetic Immich data only; no real Immich connection. |
| `system.media_sampling.enabled` | `false` | Original video / animated GIF frame sampling. |
| `system.media_sampling.max_original_bytes` | `262144000` | Max download size for sampling. |
| `system.media_sampling.max_duration_seconds` | `300` | Max media duration for sampling. |
| `system.media_sampling.max_frames` | `3` | Frames extracted per asset. |
| `system.media_sampling.extraction_timeout_seconds` | `60` | ffmpeg timeout. |
| `system.media_sampling.ffmpeg_path` | `ffmpeg` | ffmpeg executable. |
| `system.ocr.enabled` | `true` | Local OCR when a bundle is installed. |
| `system.ocr.max_inputs` | `4` | Max preview/frame images per OCR call. |
| `system.ocr.max_text_chars` | `20000` | Max OCR text retained per asset. |

### Common use cases

**Production behind HTTPS**

1. Set `system.base_url` to `https://mediarefinery.example.com`.
2. Set `system.immich_base_url` to your internal Immich URL.
3. Set `system.trusted_proxies` to your reverse-proxy IPs if you rely on `X-Forwarded-For`.

**Demo / smoke testing**

1. Set `system.demo_mode` to `true`.
2. Set `system.immich_base_url` to any placeholder (for example `http://demo.invalid`).

**Migrating from legacy `MR_*` env**

```bash
mediarefinery config import-yaml /path/to/old-config.yml --data-dir /data
```

YAML and `templates/config*.yml` are import-only — the running service reads `config.db`, not YAML on disk.

## Master key (encryption)

Immich session tokens and stored API keys are encrypted with AES-256-GCM. The master key is read from `/data/master.key` (created `0600` on first run when the file is absent).

Generate a key file before first boot (optional — the service creates one automatically):

```bash
python -c "import secrets; from pathlib import Path; \
  p = Path('/data/master.key'); p.parent.mkdir(parents=True, exist_ok=True); \
  p.write_bytes(secrets.token_bytes(32)); p.chmod(0o600)"
```

Back up `master.key` with `state.db`. If both are lost, users must sign in again and save new API keys.

## Immich API Keys

Each user who runs real scans needs an Immich API key saved in the dashboard. The key is encrypted before it reaches `state.db`.

Recommended Immich permissions:

- `asset.read`
- `asset.view`
- `asset.download` when video/GIF sampling is enabled
- `asset.update` for Locked Folder moves
- `album.read`
- `album.create`
- `albumAsset.create`
- `tag.read`
- `tag.create`
- `tag.asset`
- `user.read`
- `server.about`

Exact permission names may change in Immich. Validate against your Immich version.

## Per-user dashboard settings

Settings are scoped per MediaRefinery user in `state.db`.

### Categories

Categories describe review buckets. The simple builder creates match-term categories; the JSON editor remains available for advanced rules.

Example:

```json
{
  "categories": [
    {
      "id": "receipts",
      "description": "Receipts and expense documents",
      "match_terms": ["receipt", "invoice", "subtotal"]
    },
    {
      "id": "family",
      "description": "Family review",
      "match_terms": ["family", "kids", "holiday"]
    }
  ]
}
```

Changing categories can mark existing scan results for reclassification when the active model changed.

### Policies

Policies map categories to safe actions. Supported actions are:

- `no_action`
- `manual_review`
- `add_to_review_album`
- `add_tag`
- `move_to_locked_folder`

Delete and trash actions are intentionally unsupported.

Example:

```json
{
  "receipts": {
    "image": {
      "on_match": ["add_to_review_album", "add_tag"]
    }
  },
  "family": {
    "image": {
      "on_match": ["manual_review"]
    }
  }
}
```

### Auto-Scan

Auto-scan is scheduled polling because Immich does not provide outbound webhooks for new uploads. A user must have:

- auto-scan enabled in Settings;
- a valid current Immich session;
- a stored Immich API key;
- an active classifier model;
- `system.auto_scan_enabled` true in `config.db`.

If Immich returns 401 for a user's session, MediaRefinery pauses that user's auto-scan setting.

### Account Deletion

The dashboard danger zone deletes the current MediaRefinery account. It removes sessions, stored API keys, scan runs, actions, errors, asset rows, asset overrides, and user configuration. Audit rows are anonymized to `user_deleted`.

This does not delete the Immich account or Immich media.

## Compose / orchestration only

Docker Compose examples should mount a persistent `/data` volume. They should not require `MR_IMMICH_BASE_URL` or other retired app env vars — configure those in `config.db` after first boot (or seed before start via `import-yaml`).
